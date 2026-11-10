"""Sync the Notion CMS database to the static website repo.

This is the ``site`` target of ``maia cms sync`` (the ``webflow`` target lives
in ``promaia.webflow.sync``). Blog Status semantics are the same as the
Webflow sync:

- **Live** — already published; left exactly as it is on the site.
- **To sync** — rendered and written, then Blog Status → "Update on sync".
- **Update on sync** — re-rendered when the Notion page changed.
- **Don't sync** — removed from the site if it is there.
- anything else (e.g. "temp") — ignored.

Pages ticked "KOii chat" are skipped while ``cms.site.skip_koii_chat_pages``
is on (the default). Only the scalar properties
listed in ``content.build_post_record`` are read; relations never are.

Every image is downloaded during the sync (Notion's signed S3 links expire
after about an hour) and committed next to the post, so the site serves it
from its own domain forever. With ``publish = "git-push"`` the changes are
committed and pushed to the site repo, whose CI builds and deploys it.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from promaia.cms_site import content as C
from promaia.cms_site.repo import (
    SiteRepoError,
    commit_and_push,
    ensure_checkout,
    resolve_checkout_dir,
)

logger = logging.getLogger(__name__)

SYNC_STATUSES = ["To sync", "Update on sync", "Don't sync", "Live"]


@dataclass
class SiteSyncResult:
    created: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    errors: int = 0
    commit: Optional[str] = None
    lines: List[str] = field(default_factory=list)

    def log(self, line: str) -> None:
        self.lines.append(line)
        print(line)

    def as_tuple(self):
        return self.created, self.updated, self.deleted, self.skipped, self.errors


class SiteStore:
    """Post records and media inside a site checkout."""

    def __init__(self, root: Path, content_dir: str, media_dir: str, media_url_prefix: str,
                 post_path: str = "/post/{slug}"):
        self.root = root
        self.content_rel = content_dir.strip("/")
        self.media_rel = media_dir.strip("/")
        self.content_dir = root / self.content_rel
        self.media_dir = root / self.media_rel
        self.media_url_prefix = media_url_prefix
        self.post_path = post_path
        self.by_slug: Dict[str, Dict[str, Any]] = {}
        self.reload()

    def reload(self) -> None:
        self.by_slug = {}
        if not self.content_dir.exists():
            return
        import json
        for f in sorted(self.content_dir.glob("*.json")):
            try:
                rec = json.loads(f.read_text())
            except Exception as e:
                logger.warning(f"Unreadable post file {f}: {e}")
                continue
            self.by_slug[rec.get("slug") or f.stem] = rec

    def find(self, notion_page_id: Optional[str], webflow_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """The post already on the site for this Notion page (by page id, then Webflow id)."""
        want = C.normalize_page_id(notion_page_id)
        if want:
            for rec in self.by_slug.values():
                if C.normalize_page_id(rec.get("notionPageId")) == want:
                    return rec
        if webflow_id:
            for rec in self.by_slug.values():
                if rec.get("webflowId") == webflow_id:
                    return rec
        return None

    def post_file(self, slug: str) -> Path:
        return self.content_dir / f"{slug}.json"

    def post_media_dir(self, slug: str) -> Path:
        return self.media_dir / slug

    def write_post(self, record: Dict[str, Any], images: Dict[str, C.StoredImage]) -> List[Path]:
        slug = record["slug"]
        written: List[Path] = []
        mdir = self.post_media_dir(slug)
        if images:
            mdir.mkdir(parents=True, exist_ok=True)
        for name, img in images.items():
            p = mdir / name
            if not p.exists() or p.read_bytes() != img.data:
                p.write_bytes(img.data)
                written.append(p)
        # Drop media this post no longer references (e.g. an image removed in Notion),
        # but keep anything the page's carried-over gallery still points at.
        keep = set(images) | {
            Path(g.get("src", "")).name for g in record.get("gallery") or [] if isinstance(g, dict)
        }
        if mdir.exists():
            for p in mdir.iterdir():
                if p.is_file() and p.name not in keep:
                    p.unlink()
        self.content_dir.mkdir(parents=True, exist_ok=True)
        pf = self.post_file(slug)
        text = C.dump_post(record)
        if not pf.exists() or pf.read_text() != text:
            pf.write_text(text)
            written.append(pf)
        self.by_slug[slug] = record
        return written

    def delete_post(self, slug: str) -> List[Path]:
        removed: List[Path] = []
        pf = self.post_file(slug)
        if pf.exists():
            pf.unlink()
            removed.append(pf)
        mdir = self.post_media_dir(slug)
        if mdir.exists():
            shutil.rmtree(mdir)
            removed.append(mdir)
        self.by_slug.pop(slug, None)
        return removed

    def add_redirect(self, old_slug: str, new_slug: str) -> Path:
        """Keep an old post URL working after a slug change (Cloudflare Pages _redirects)."""
        redirects = self.root / "public" / "_redirects"
        old = self.post_path.format(slug=old_slug)
        new = self.post_path.format(slug=new_slug)
        line = f"{old} {new} 301\n"
        current = redirects.read_text() if redirects.exists() else ""
        if line not in current:
            redirects.parent.mkdir(parents=True, exist_ok=True)
            redirects.write_text(current + ("" if current.endswith("\n") or not current else "\n") + line)
        return redirects


# ── default Notion / network hooks (replaced in tests) ──────────────

def _default_download(url: str):
    import requests
    resp = requests.get(url, timeout=60, headers={"User-Agent": "promaia-cms-sync/1.0"})
    resp.raise_for_status()
    return resp.content, resp.headers.get("content-type")


async def _default_fetch_pages(database_id: str, status_prop: str,
                               only_page_ids: Optional[Iterable[str]]) -> List[Dict[str, Any]]:
    if only_page_ids:
        from promaia.notion.client import ensure_default_client
        client = ensure_default_client()
        return [await client.pages.retrieve(page_id=pid) for pid in only_page_ids]
    from promaia.notion.pages import get_pages_by_blog_status
    return await get_pages_by_blog_status(database_id, status_prop, SYNC_STATUSES)


async def _default_fetch_html(page: Dict[str, Any]) -> str:
    from promaia.html_converter.converter import page_to_html
    from promaia.notion.pages import get_block_content
    # No persistent block cache: cached blocks carry image URLs that have expired.
    blocks = await get_block_content(page["id"], use_persistent_cache=False)
    return page_to_html(blocks)


async def _default_mark_update_on_sync(page_id: str, status_prop: str) -> bool:
    from promaia.notion.pages import update_page_properties_batch
    # Status only; the "Webflow ID" property is never touched by the site sync.
    return await update_page_properties_batch(
        page_id, status_property_name=status_prop, new_status="Update on sync"
    )


def _open_cache(dry_run: bool):
    from promaia.storage.sync_cache import SyncCache
    from promaia.utils.env_writer import get_cache_dir
    path = get_cache_dir() / "site_sync_cache.db"
    if dry_run and not path.exists():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    return SyncCache(db_path=str(path))


# ── main entry point ────────────────────────────────────────────────

async def sync_to_site(
    notion_database_id: Optional[str] = None,
    *,
    only_page_ids: Optional[List[str]] = None,
    dry_run: bool = False,
    publish: Optional[str] = None,
    checkout_dir: Optional[str] = None,
    force_update: bool = False,
    blog_status_property_name: str = "Blog Status",
    use_cache: bool = True,
    download: Optional[Callable[[str], Any]] = None,
    fetch_pages: Optional[Callable[..., Awaitable[List[Dict[str, Any]]]]] = None,
    fetch_html: Optional[Callable[[Dict[str, Any]], Awaitable[str]]] = None,
    mark_update_on_sync: Optional[Callable[[str, str], Awaitable[bool]]] = None,
    site_config: Optional[Dict[str, Any]] = None,
) -> SiteSyncResult:
    """Publish Notion CMS pages to the static site repo. See the module docstring.

    Args:
        notion_database_id: CMS database; defaults to the configured ``cms`` database.
        only_page_ids: sync just these pages (their Blog Status still decides what happens).
        dry_run: print the plan; write nothing, push nothing, change nothing in Notion.
        publish: override ``cms.site.publish`` ("git-push" or "none").
        checkout_dir: override ``cms.site.checkout_dir``.
        force_update: re-render "Update on sync" pages even if unchanged.
    """
    from promaia.config.cms_config import get_site_config

    cfg = dict(site_config or get_site_config())
    publish_mode = publish or cfg.get("publish", "git-push")
    if publish_mode not in ("git-push", "none"):
        raise ValueError(f"cms.site.publish must be 'git-push' or 'none', got {publish_mode!r}")
    root = resolve_checkout_dir(checkout_dir or cfg["checkout_dir"])
    repo_url, branch = cfg.get("repo_url", ""), cfg.get("branch", "main")

    download = download or _default_download
    fetch_pages = fetch_pages or _default_fetch_pages
    fetch_html = fetch_html or _default_fetch_html
    mark_update_on_sync = mark_update_on_sync or _default_mark_update_on_sync

    result = SiteSyncResult()
    prefix = "[dry run] " if dry_run else ""

    if notion_database_id is None and not only_page_ids:
        from promaia.utils.config_loader import get_notion_database_id
        notion_database_id = get_notion_database_id("cms")

    # 1. checkout
    if dry_run:
        state = "exists" if root.exists() else "would be cloned from " + repo_url
        result.log(f"{prefix}site checkout: {root} ({state})")
    else:
        try:
            state = ensure_checkout(root, repo_url, branch, pull=(publish_mode == "git-push"))
            result.log(f"site checkout: {root} ({state})")
        except SiteRepoError as e:
            result.log(f"error: {e}")
            result.errors += 1
            return result

    store = SiteStore(root, cfg["content_dir"], cfg["media_dir"], cfg["media_url_prefix"],
                      cfg.get("post_path", "/post/{slug}"))

    # 2. pages
    pages = await fetch_pages(notion_database_id, blog_status_property_name, only_page_ids)
    result.log(f"{prefix}{len(pages)} CMS page(s) to consider")

    cache = _open_cache(dry_run) if use_cache else None
    to_mark: List[str] = []
    to_cache: List[Dict[str, Any]] = []
    changed_titles: List[str] = []

    for page in pages:
        pid = page.get("id", "")
        title = C.page_title(page) or f"Page {pid[:8]}"
        action = C.plan_action(page, blog_status_property_name,
                               skip_koii_chat=cfg.get("skip_koii_chat_pages", True))
        status = C.blog_status(page, blog_status_property_name)
        try:
            if action == C.ACTION_SKIP_PRIVATE:
                result.skipped += 1
                result.log(f"{prefix}skip (\"KOii chat\" ticked; cms.site.skip_koii_chat_pages is on): {title}")
                continue
            if action == C.ACTION_SKIP_LIVE:
                result.skipped += 1
                result.log(f"{prefix}skip (Live → kept as published): {title}")
                continue
            if action == C.ACTION_SKIP_UNKNOWN:
                result.skipped += 1
                result.log(f"{prefix}skip (Blog Status {status!r}): {title}")
                continue

            webflow_id = C.prop_text(page, "Webflow ID") or None
            existing = store.find(pid, webflow_id)

            if action == C.ACTION_DELETE:
                if not existing:
                    result.skipped += 1
                    continue
                slug = existing.get("slug")
                if dry_run:
                    result.log(f"{prefix}would delete post /{slug} and its media: {title}")
                else:
                    store.delete_post(slug)
                    result.log(f"deleted post {slug}: {title}")
                    changed_titles.append(f"remove {title}")
                result.deleted += 1
                continue

            # create / update
            slug = C.page_slug(page)
            clash = store.by_slug.get(slug)
            if clash is not None and clash is not existing:
                result.errors += 1
                result.log(f"error: slug {slug!r} already belongs to another post; not overwriting ({title})")
                continue

            if (action == C.ACTION_UPDATE and existing and not force_update and cache
                    and not cache.should_process_page(pid, page)):
                result.skipped += 1
                result.log(f"{prefix}unchanged since last sync: {title}")
                continue

            html = await fetch_html(page)
            verb = "create" if action == C.ACTION_CREATE else "update"

            if dry_run:
                n_body = C.body_image_count(html)
                has_thumb = bool(C.thumbnail_source_url(page))
                result.log(
                    f"{prefix}would {verb} {store.content_rel}/{slug}.json "
                    f"({n_body} body image(s){' + thumbnail' if has_thumb else ''} → "
                    f"{store.media_rel}/{slug}/): {title}"
                )
                if existing and existing.get("slug") != slug:
                    result.log(f"{prefix}  slug change {existing.get('slug')} → {slug}; would add a redirect")
            else:
                media = C.MediaCollector(slug, store.media_url_prefix, download,
                                         cfg.get("image_max_width", 2000), cfg.get("image_quality", 82))
                thumb_url = C.thumbnail_source_url(page)
                thumbnail = None
                if thumb_url:
                    stored = media.add(thumb_url)
                    thumbnail = media.ref(stored) if stored else None
                body = C.rewrite_body_html(html, media)
                record = C.build_post_record(page, body, thumbnail, existing)
                if existing and existing.get("slug") != slug:
                    store.delete_post(existing["slug"])
                    store.add_redirect(existing["slug"], slug)
                    result.log(f"  slug changed {existing['slug']} → {slug}; redirect added")
                store.write_post(record, media.images)
                for w in media.warnings:
                    result.log(f"  warning: {w}")
                result.log(f"{verb}d post {slug} ({len(media.images)} image(s)): {title}")
                changed_titles.append(title)
                to_cache.append(page)
                if action == C.ACTION_CREATE:
                    to_mark.append(pid)
            if action == C.ACTION_CREATE:
                result.created += 1
            else:
                result.updated += 1
        except Exception as e:
            result.errors += 1
            result.log(f"error: {title}: {e}")

    if dry_run:
        result.log(f"{prefix}nothing written; Notion unchanged")
        if cache:
            cache.close()
        return result

    # 3. publish, then (only if that worked) record the new Notion statuses
    if changed_titles and publish_mode == "git-push":
        message = _commit_message(changed_titles)
        try:
            result.commit = commit_and_push(
                root, repo_url, branch,
                [store.content_rel, store.media_rel, "public/_redirects"], message,
            )
            result.log(f"pushed {result.commit} to {branch}; the site deploys from there"
                       if result.commit else "no file changes to commit")
        except SiteRepoError as e:
            result.errors += 1
            result.log(f"error: publish failed, Notion statuses left as they were: {e}")
            if cache:
                cache.close()
            return result
    elif changed_titles:
        result.log(f"files written to {root} (publish = none: not committed or pushed)")

    for pid in to_mark:
        if not await mark_update_on_sync(pid, blog_status_property_name):
            result.errors += 1
            result.log(f"error: could not set Blog Status to 'Update on sync' for {pid}")
    if cache:
        for page in to_cache:
            cache.update_cache(page["id"], page, None)
        cache.close()

    result.log(
        f"site sync done: {result.created} created, {result.updated} updated, "
        f"{result.deleted} deleted, {result.skipped} skipped, {result.errors} error(s)"
    )
    return result


def _commit_message(titles: List[str]) -> str:
    subject = "cms sync: " + ", ".join(titles)
    if len(subject) > 72:
        subject = subject[:69].rstrip() + "..."
    body = "\n".join(f"- {t}" for t in titles)
    return f"{subject}\n\n{body}\n"


def run_sync_to_site(**kwargs) -> SiteSyncResult:
    """Synchronous wrapper (for scripts)."""
    return asyncio.run(sync_to_site(**kwargs))
