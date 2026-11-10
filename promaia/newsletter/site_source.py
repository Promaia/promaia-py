"""Where the newsletter gets its "published" check, links and permanent images.

Two sources, picked by the cms target (``promaia.config.cms_config``):

- ``webflow`` — the legacy path in ``promaia.newsletter.commands`` (Webflow API).
- ``site`` — the static website: a post is published when
  ``{base_url}/feed/posts/<slug>.json`` answers 200; its thumbnail and body
  image URLs (served from the site's own domain) replace Notion's expiring
  S3 links in the email.

The "read on website" link is ``{base_url}/post/<slug>`` in both modes.
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from bs4 import BeautifulSoup

DEFAULT_POST_PATH = "/post/{slug}"
DEFAULT_FEED_PATH = "/feed/posts/{slug}.json"


def post_url(base_url: str, slug: Optional[str], post_path: str = DEFAULT_POST_PATH) -> str:
    base = base_url.rstrip("/")
    if not slug:
        return base + "/"
    return base + post_path.format(slug=slug)


def feed_url(base_url: str, slug: str, feed_path: str = DEFAULT_FEED_PATH) -> str:
    return base_url.rstrip("/") + feed_path.format(slug=slug)


def absolutize(base_url: str, src: Optional[str]) -> Optional[str]:
    if not src:
        return None
    return urllib.parse.urljoin(base_url.rstrip("/") + "/", src)


@dataclass
class SitePost:
    slug: str
    url: str
    cover_image: Optional[str]
    body_images: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)


def site_post_from_feed(data: Dict[str, Any], base_url: str,
                        post_path: str = DEFAULT_POST_PATH) -> SitePost:
    """Turn a feed record into absolute URLs for the email."""
    slug = data.get("slug") or ""
    cover = None
    for key in ("thumbnail", "mainImage"):
        img = data.get(key)
        src = img.get("src") if isinstance(img, dict) else img
        if src:
            cover = absolutize(base_url, src)
            break
    soup = BeautifulSoup(data.get("html") or "", "html.parser")
    body = [absolutize(base_url, i.get("src")) for i in soup.find_all("img") if i.get("src")]
    return SitePost(slug=slug, url=post_url(base_url, slug, post_path), cover_image=cover,
                    body_images=[b for b in body if b], data=data)


def fetch_site_post(base_url: str, slug: str, feed_path: str = DEFAULT_FEED_PATH,
                    post_path: str = DEFAULT_POST_PATH,
                    get: Optional[Callable[..., Any]] = None) -> Optional[SitePost]:
    """The post as published on the site, or None if the feed has no such post."""
    if not slug:
        return None
    if get is None:
        import requests
        get = requests.get
    resp = get(feed_url(base_url, slug, feed_path), timeout=20,
               headers={"User-Agent": "promaia-newsletter/1.0", "Cache-Control": "no-cache"})
    if getattr(resp, "status_code", 0) != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("slug") != slug:
        return None
    return site_post_from_feed(data, base_url, post_path)


def replace_images_by_position(html_content: str, hosted_images: List[str],
                               header_image: Optional[str] = None,
                               keep_prefixes: tuple = ()) -> tuple[str, int, int]:
    """Swap Notion image URLs in the email for permanently hosted ones, in order.

    Same algorithm the Webflow path uses: the n-th non-hosted <img> gets the
    n-th hosted URL. The header image and URLs that already start with one of
    *keep_prefixes* are left alone. Returns (html, replaced, unmapped).
    """
    soup = BeautifulSoup(html_content, "html.parser")
    idx = replaced = unmapped = 0
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if not src or (header_image and src == header_image):
            continue
        if keep_prefixes and src.startswith(keep_prefixes):
            continue
        if idx < len(hosted_images):
            img["src"] = hosted_images[idx]
            idx += 1
            replaced += 1
        else:
            unmapped += 1
    return str(soup), replaced, unmapped


_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")


def replace_markdown_images_by_position(markdown: str, hosted_images: List[str],
                                        keep_prefixes: tuple = ()) -> str:
    """Same positional swap as replace_images_by_position, on ``![alt](url)`` markdown.

    Used before the email is templated so the plain-text part carries the
    permanent URLs too.
    """
    idx = 0

    def swap(m):
        nonlocal idx
        url = m.group(2)
        if keep_prefixes and url.startswith(keep_prefixes):
            return m.group(0)
        if idx < len(hosted_images):
            new = hosted_images[idx]
            idx += 1
            return f"![{m.group(1)}]({new})"
        return m.group(0)

    return _MD_IMAGE.sub(swap, markdown)
