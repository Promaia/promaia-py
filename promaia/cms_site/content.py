"""Pure helpers for turning a Notion CMS page into a static-site post record.

Nothing here talks to Notion, GitHub or the network; the sync module passes
in already-fetched data and a downloader. That keeps the content contract
(``content/posts/<slug>.json`` + ``public/media/posts/<slug>/<name>``) easy to
test.

Content contract (read by the website repo):

    content/posts/<slug>.json  — one post, keys in POST_KEYS order
    public/media/posts/<slug>/<sha1[:12]>.<ext>  — its images, served at
        <media_url_prefix>/<slug>/<name>
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from bs4 import BeautifulSoup
from slugify import slugify

logger = logging.getLogger(__name__)

POST_KEYS = (
    "slug", "title", "description", "subtitle", "publishDate", "createdAt", "tag", "emojiTags",
    "author", "readTime", "featured", "isNewsletter", "thumbnail", "mainImage",
    "gallery", "html", "notionPageId", "webflowId", "source", "syncedAt",
)

FIGURE_CLASS = "w-richtext-figure-type-image w-richtext-align-fullwidth"

# Hosts whose URLs are short-lived signed links (Notion uploads).
_NOTION_HOST_HINTS = ("amazonaws.com", "notion.so", "notion-static.com", "notion.site")


# ── Notion property readers ─────────────────────────────────────────

def prop_text(page: Dict[str, Any], name: str) -> str:
    """Plain text of a title / rich_text property ("" if missing)."""
    prop = (page.get("properties") or {}).get(name) or {}
    kind = prop.get("type")
    if kind in ("title", "rich_text"):
        return "".join(t.get("plain_text", "") for t in prop.get(kind) or [] if t)
    return ""


def prop_select(page: Dict[str, Any], name: str) -> Optional[str]:
    """Name of a select / status property, or None."""
    prop = (page.get("properties") or {}).get(name) or {}
    kind = prop.get("type")
    if kind in ("select", "status"):
        return (prop.get(kind) or {}).get("name")
    return None


def prop_checkbox(page: Dict[str, Any], name: str) -> bool:
    prop = (page.get("properties") or {}).get(name) or {}
    return bool(prop.get("checkbox")) if prop.get("type") == "checkbox" else False


def prop_date(page: Dict[str, Any], name: str) -> Optional[str]:
    prop = (page.get("properties") or {}).get(name) or {}
    if prop.get("type") == "date":
        return (prop.get("date") or {}).get("start")
    return None


def _file_url(file_obj: Dict[str, Any]) -> Optional[str]:
    kind = file_obj.get("type")
    if kind == "external":
        return (file_obj.get("external") or {}).get("url") or None
    if kind == "file":
        return (file_obj.get("file") or {}).get("url") or None
    return None


def thumbnail_source_url(page: Dict[str, Any]) -> Optional[str]:
    """"Thumbnail Image" files property, falling back to the page cover."""
    prop = (page.get("properties") or {}).get("Thumbnail Image") or {}
    if prop.get("type") == "files":
        for f in prop.get("files") or []:
            url = _file_url(f or {})
            if url:
                return url
    cover = page.get("cover")
    if cover:
        return _file_url(cover)
    return None


def page_title(page: Dict[str, Any]) -> str:
    return prop_text(page, "Name") or prop_text(page, "Title")


def page_slug(page: Dict[str, Any]) -> str:
    """Slug property, else slugify(title) — the same rule the Webflow sync uses."""
    slug = prop_text(page, "Slug").strip()
    return slug or slugify(page_title(page))


def blog_status(page: Dict[str, Any], prop_name: str = "Blog Status") -> Optional[str]:
    return prop_select(page, prop_name)


def is_koii_chat(page: Dict[str, Any]) -> bool:
    """Pages flagged "KOii chat" are private chat material and never published."""
    return prop_checkbox(page, "KOii chat")


# ── status semantics (mirrors promaia.webflow.sync) ─────────────────

ACTION_SKIP_LIVE = "skip-live"
ACTION_SKIP_PRIVATE = "skip-koii-chat"
ACTION_SKIP_UNKNOWN = "skip-unknown-status"
ACTION_CREATE = "create"          # "To sync"
ACTION_UPDATE = "update"          # "Update on sync"
ACTION_DELETE = "delete"          # "Don't sync"


def plan_action(page: Dict[str, Any], status_prop: str = "Blog Status",
                skip_koii_chat: bool = True) -> str:
    """What the site sync does with a page, from its Blog Status.

    Live → keep as published; To sync → write (then status becomes Update on
    sync); Update on sync → rewrite if changed; Don't sync → remove from the
    site; anything else (e.g. "temp") → leave alone. With *skip_koii_chat*
    (config ``cms.site.skip_koii_chat_pages``, default on) pages ticked
    "KOii chat" are never written, whatever their status.
    """
    status = blog_status(page, status_prop)
    if status == "Don't sync":
        return ACTION_DELETE
    if status == "Live":
        return ACTION_SKIP_LIVE
    if status not in ("To sync", "Update on sync"):
        return ACTION_SKIP_UNKNOWN
    if skip_koii_chat and is_koii_chat(page):
        return ACTION_SKIP_PRIVATE
    return ACTION_CREATE if status == "To sync" else ACTION_UPDATE


# ── images ──────────────────────────────────────────────────────────

@dataclass
class StoredImage:
    """One image as it will be written under the post's media dir."""
    name: str
    data: bytes
    width: Optional[int]
    height: Optional[int]


def is_notion_hosted(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(h in host for h in _NOTION_HOST_HINTS)


def _looks_like_svg(data: bytes, url: str, content_type: Optional[str]) -> bool:
    if content_type and "svg" in content_type:
        return True
    if urllib.parse.urlparse(url).path.lower().endswith(".svg"):
        return True
    head = data[:512].lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2048].lower())


def process_image(data: bytes, url: str = "", content_type: Optional[str] = None,
                  max_width: int = 2000, quality: int = 82) -> StoredImage:
    """Normalise one downloaded image for the site.

    JPEG/PNG (and other raster formats Pillow reads) → WebP, EXIF orientation
    applied, scaled down so width ≤ max_width. GIF, WebP and SVG are kept
    byte-for-byte. The file name is the first 12 hex chars of sha1(original
    bytes) plus the extension, so re-syncing the same image is a no-op.
    """
    digest = hashlib.sha1(data).hexdigest()[:12]
    if _looks_like_svg(data, url, content_type):
        return StoredImage(f"{digest}.svg", data, None, None)

    from PIL import Image, ImageOps

    with Image.open(io.BytesIO(data)) as img:
        fmt = (img.format or "").upper()
        if fmt in ("GIF", "WEBP"):
            return StoredImage(f"{digest}.{fmt.lower()}", data, img.width, img.height)
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "A" in img.getbands() or img.mode == "P" else "RGB")
        if img.width > max_width:
            new_h = max(1, round(img.height * max_width / img.width))
            img = img.resize((max_width, new_h), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="WEBP", quality=quality, method=6)
        return StoredImage(f"{digest}.webp", out.getvalue(), img.width, img.height)


def media_url(media_url_prefix: str, slug: str, name: str) -> str:
    return f"{media_url_prefix.rstrip('/')}/{slug}/{name}"


# Downloader: url -> (bytes, content_type)
Downloader = Callable[[str], "tuple[bytes, Optional[str]]"]


class ImageFetchError(RuntimeError):
    pass


class MediaCollector:
    """Downloads and normalises images for one post, de-duplicating by URL."""

    def __init__(self, slug: str, media_url_prefix: str, download: Downloader,
                 max_width: int = 2000, quality: int = 82):
        self.slug = slug
        self.prefix = media_url_prefix
        self.download = download
        self.max_width = max_width
        self.quality = quality
        self.images: Dict[str, StoredImage] = {}   # name -> image
        self._by_url: Dict[str, StoredImage] = {}
        self.warnings: List[str] = []

    def add(self, url: str) -> Optional[StoredImage]:
        """Fetch *url* and store it; returns None if an external image can't be fetched.

        A Notion-hosted image that fails raises ImageFetchError: publishing the
        post anyway would leave a link that expires within the hour.
        """
        if url in self._by_url:
            return self._by_url[url]
        try:
            data, ctype = self.download(url)
            stored = process_image(data, url, ctype, self.max_width, self.quality)
        except Exception as e:
            if is_notion_hosted(url):
                raise ImageFetchError(f"could not fetch Notion image {_short(url)}: {e}") from e
            self.warnings.append(f"kept external image URL (fetch failed: {e}): {_short(url)}")
            return None
        self.images[stored.name] = stored
        self._by_url[url] = stored
        return stored

    def ref(self, stored: StoredImage) -> Dict[str, Any]:
        return {"src": media_url(self.prefix, self.slug, stored.name),
                "width": stored.width, "height": stored.height}


def _short(url: str, n: int = 70) -> str:
    return url if len(url) <= n else url[: n - 3] + "..."


def rewrite_body_html(html: str, media: MediaCollector) -> str:
    """Download every <img> in *html* and point it at the site's media path.

    Each image ends up in the same figure shape the Webflow sync produced:
    ``<figure class="w-richtext-figure-type-image w-richtext-align-fullwidth">
    <div><img src=… alt=…></div>[<figcaption>…</figcaption>]</figure>``.
    Links to Notion-hosted files (signed S3 URLs) are downloaded the same way.
    """
    soup = BeautifulSoup(html, "html.parser")
    for img in list(soup.find_all("img")):
        src = img.get("src") or ""
        if not src.startswith(("http://", "https://")):
            continue
        stored = media.add(src)
        new_src = media.ref(stored)["src"] if stored else src
        alt = img.get("alt") or ""

        old_figure = img.find_parent("figure")
        caption = old_figure.find("figcaption") if old_figure else None

        figure = soup.new_tag("figure")
        figure["class"] = FIGURE_CLASS
        wrapper = soup.new_tag("div")
        new_img = soup.new_tag("img", src=new_src)
        new_img["alt"] = alt
        if stored and stored.width and stored.height:
            new_img["width"] = str(stored.width)
            new_img["height"] = str(stored.height)
        new_img["loading"] = "lazy"
        wrapper.append(new_img)
        figure.append(wrapper)
        if caption is not None:
            figure.append(caption.extract())
        (old_figure or img).replace_with(figure)

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith(("http://", "https://")) and is_notion_hosted(href) and _is_signed_file(href):
            stored = media.add(href)
            if stored:
                a["href"] = media.ref(stored)["src"]
    return str(soup)


def _is_signed_file(url: str) -> bool:
    q = urllib.parse.urlparse(url).query
    return "X-Amz-" in q or "amazonaws.com" in url


def body_image_count(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    return sum(1 for i in soup.find_all("img") if (i.get("src") or "").startswith(("http://", "https://")))


# ── the post record ─────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_post_record(page: Dict[str, Any], html: str, thumbnail: Optional[Dict[str, Any]],
                      existing: Optional[Dict[str, Any]] = None,
                      synced_at: Optional[str] = None) -> Dict[str, Any]:
    """Assemble the JSON record for one post (keys in POST_KEYS order).

    Only the listed scalar properties are read: relations (e.g. the journal
    "Relevant Notes" relation) and every other property never reach the site.
    """
    existing = existing or {}
    publish_date = prop_date(page, "Publish Date") or datetime.now(timezone.utc).date().isoformat()
    record = {
        "slug": page_slug(page),
        "title": page_title(page),
        "description": prop_text(page, "Description"),
        "subtitle": existing.get("subtitle") or "",
        "publishDate": publish_date,
        # Tie-breaker for posts sharing a publish date (the site sorts by
        # publishDate desc, then createdAt desc, as Webflow did).
        "createdAt": page.get("created_time") or existing.get("createdAt"),
        "tag": prop_select(page, "Tag"),
        "emojiTags": prop_text(page, "Emoji Tags") or None,
        "author": prop_text(page, "Author Name") or None,
        "readTime": prop_text(page, "Read Time Estimate") or None,
        "featured": prop_checkbox(page, "Featured"),
        "isNewsletter": prop_checkbox(page, "Is newsletter"),
        "thumbnail": thumbnail,
        "mainImage": thumbnail,
        # Notion has no gallery field; keep one carried over from the Webflow import.
        "gallery": existing.get("gallery") or [],
        "html": html,
        "notionPageId": page.get("id"),
        "webflowId": prop_text(page, "Webflow ID") or existing.get("webflowId") or None,
        "source": "notion-sync",
        "syncedAt": synced_at or now_iso(),
    }
    return {k: record[k] for k in POST_KEYS}


def dump_post(record: Dict[str, Any]) -> str:
    return json.dumps({k: record.get(k) for k in POST_KEYS}, indent=2, ensure_ascii=False) + "\n"


def normalize_page_id(page_id: Optional[str]) -> str:
    return re.sub(r"[^0-9a-f]", "", (page_id or "").lower())
