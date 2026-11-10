"""Tests for the static-site CMS target (promaia.cms_site) and the layered cms config."""
import asyncio
import hashlib
import io
import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from promaia.cms_site import content as C
from promaia.cms_site import repo as R
from promaia.cms_site.sync import SiteStore, sync_to_site
from promaia.config import cms_config


# ── helpers ─────────────────────────────────────────────────────────

def jpeg_bytes(w=40, h=20, color=(200, 10, 10), exif_orientation=None):
    img = Image.new("RGB", (w, h), color)
    out = io.BytesIO()
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        img.save(out, format="JPEG", exif=exif.tobytes())
    else:
        img.save(out, format="JPEG")
    return out.getvalue()


def gif_bytes():
    out = io.BytesIO()
    Image.new("P", (8, 6)).save(out, format="GIF")
    return out.getvalue()


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"></svg>'
NOTION_IMG = "https://prod-files-secure.s3.us-west-2.amazonaws.com/abc/img.jpg?X-Amz-Signature=1"
NOTION_IMG2 = "https://prod-files-secure.s3.us-west-2.amazonaws.com/abc/img2.png?X-Amz-Signature=2"


def rt(text):
    return [{"plain_text": text, "text": {"content": text}}]


def make_page(pid, title, status, slug="", koii_chat=False, webflow_id="", thumb=None, **extra):
    props = {
        "Name": {"type": "title", "title": rt(title)},
        "Slug": {"type": "rich_text", "rich_text": rt(slug) if slug else []},
        "Blog Status": {"type": "status", "status": {"name": status}},
        "KOii chat": {"type": "checkbox", "checkbox": koii_chat},
        "Webflow ID": {"type": "rich_text", "rich_text": rt(webflow_id) if webflow_id else []},
        "Description": {"type": "rich_text", "rich_text": rt("A description")},
        "Publish Date": {"type": "date", "date": {"start": "2026-11-09"}},
        "Tag": {"type": "select", "select": {"name": "essay"}},
        "Is newsletter": {"type": "checkbox", "checkbox": True},
        "Featured": {"type": "checkbox", "checkbox": False},
        "🧘‍♀️ Relevant Notes (Journal)": {"type": "relation", "relation": [{"id": "secret-journal"}]},
        "Thumbnail Image": {"type": "files", "files": [
            {"type": "file", "name": "t.jpg", "file": {"url": thumb}}] if thumb else []},
    }
    props.update(extra)
    return {"id": pid, "properties": props, "last_edited_time": "2026-11-09T00:00:00.000Z",
            "created_time": "2026-11-01T10:00:00.000Z", "cover": None}


def fake_download_factory(mapping, calls=None):
    def download(url):
        if calls is not None:
            calls.append(url)
        if url not in mapping:
            raise RuntimeError("404")
        return mapping[url], None
    return download


# ── config ──────────────────────────────────────────────────────────

def test_config_layers_and_defaults(tmp_path, monkeypatch):
    repo_conf = tmp_path / "cms.json"
    repo_conf.write_text(json.dumps({"webflow": {"retry_attempts": 9}}))
    data_conf = tmp_path / "promaia.config.json"
    data_conf.write_text(json.dumps({"databases": {}, "cms": {"target": "site", "site": {"base_url": "https://x.pages.dev/"}}}))
    monkeypatch.setattr(cms_config, "get_config_path", lambda: repo_conf)
    monkeypatch.setattr("promaia.utils.env_writer.get_config_path", lambda: data_conf)

    cfg = cms_config.load_cms_config(reload=True)
    assert cfg["target"] == "site"
    assert cfg["webflow"]["retry_attempts"] == 9
    assert cfg["webflow"]["max_concurrent_uploads"] == 8          # default kept
    assert cfg["site"]["branch"] == "main"                         # default kept
    assert cms_config.get_site_base_url() == "https://x.pages.dev"  # trailing slash dropped
    assert cms_config.get_site_base_url("https://y.dev/") == "https://y.dev"
    assert cms_config.get_cms_target("webflow") == "webflow"
    # defaults are never mutated by a merge
    assert cms_config.DEFAULT_CMS_CONFIG["target"] == "webflow"
    assert cms_config.DEFAULT_CMS_CONFIG["site"]["base_url"] == "https://www.koiib.com"
    # the data-dir file is read, never written
    assert json.loads(data_conf.read_text())["databases"] == {}
    cms_config.load_cms_config(reload=True)


def test_config_defaults_to_webflow_and_rejects_unknown(tmp_path, monkeypatch):
    missing = tmp_path / "nope.json"
    bad = tmp_path / "promaia.config.json"
    bad.write_text(json.dumps({"cms": {"target": "wordpress"}}))
    monkeypatch.setattr(cms_config, "get_config_path", lambda: missing)
    monkeypatch.setattr("promaia.utils.env_writer.get_config_path", lambda: bad)
    assert cms_config.load_cms_config(reload=True)["target"] == "webflow"
    with pytest.raises(ValueError):
        cms_config.get_cms_target("wordpress")
    cms_config.load_cms_config(reload=True)


# ── slug, naming, images ────────────────────────────────────────────

def test_slug_rule_matches_webflow_sync():
    assert C.page_slug(make_page("1", "Hello, World ✨", "To sync")) == "hello-world"
    assert C.page_slug(make_page("1", "Hello", "To sync", slug="custom-slug")) == "custom-slug"


def test_image_names_are_deterministic_and_webp():
    data = jpeg_bytes()
    a = C.process_image(data, "https://x/a.jpg")
    b = C.process_image(data, "https://x/other-url.jpg")
    assert a.name == b.name == hashlib.sha1(data).hexdigest()[:12] + ".webp"
    assert Image.open(io.BytesIO(a.data)).format == "WEBP"
    assert (a.width, a.height) == (40, 20)


def test_image_width_is_capped_and_exif_applied():
    big = C.process_image(jpeg_bytes(3000, 1000), max_width=2000)
    assert (big.width, big.height) == (2000, 667)
    rotated = C.process_image(jpeg_bytes(40, 20, exif_orientation=6))
    assert (rotated.width, rotated.height) == (20, 40)


def test_gif_and_svg_copied_as_is():
    g = gif_bytes()
    out = C.process_image(g, "https://x/a.gif")
    assert out.name.endswith(".gif") and out.data == g
    s = C.process_image(SVG, "https://x/logo.svg")
    assert s.name.endswith(".svg") and s.data == SVG and s.width is None


# ── status semantics ────────────────────────────────────────────────

@pytest.mark.parametrize("status,chat,expected", [
    ("Live", False, C.ACTION_SKIP_LIVE),
    ("To sync", False, C.ACTION_CREATE),
    ("Update on sync", False, C.ACTION_UPDATE),
    ("Don't sync", False, C.ACTION_DELETE),
    ("temp", False, C.ACTION_SKIP_UNKNOWN),
    ("To sync", True, C.ACTION_SKIP_PRIVATE),
    ("Update on sync", True, C.ACTION_SKIP_PRIVATE),
    ("Don't sync", True, C.ACTION_DELETE),
    ("Live", True, C.ACTION_SKIP_LIVE),
])
def test_plan_action(status, chat, expected):
    assert C.plan_action(make_page("p", "T", status, koii_chat=chat)) == expected


def test_koii_chat_skip_can_be_turned_off():
    page = make_page("p", "T", "Update on sync", koii_chat=True)
    assert C.plan_action(page, skip_koii_chat=False) == C.ACTION_UPDATE


# ── HTML rewriting ──────────────────────────────────────────────────

def test_rewrite_body_html_to_site_relative_paths():
    data1, data2 = jpeg_bytes(color=(1, 2, 3)), jpeg_bytes(color=(9, 9, 9))
    calls = []
    media = C.MediaCollector("my-post", "/media/posts",
                             fake_download_factory({NOTION_IMG: data1, NOTION_IMG2: data2}, calls))
    html = (f'<p>hi</p><figure>\n<img src="{NOTION_IMG}" alt="cap">\n<figcaption>cap</figcaption></figure>'
            f'<figure><img src="{NOTION_IMG2}" alt=""></figure>'
            f'<figure><img src="{NOTION_IMG}" alt="again"></figure>')
    out = C.rewrite_body_html(html, media)
    name1 = hashlib.sha1(data1).hexdigest()[:12] + ".webp"
    assert f'src="/media/posts/my-post/{name1}"' in out
    assert "amazonaws" not in out
    assert out.count('class="w-richtext-figure-type-image w-richtext-align-fullwidth"') == 3
    assert "<figcaption>cap</figcaption>" in out
    assert "<figure><figure" not in out.replace("\n", "")
    assert calls.count(NOTION_IMG) == 1          # de-duplicated
    assert len(media.images) == 2


def test_failed_notion_image_is_an_error_but_external_is_kept():
    media = C.MediaCollector("p", "/media/posts", fake_download_factory({}))
    out = C.rewrite_body_html('<img src="https://example.com/x.png">', media)
    assert 'src="https://example.com/x.png"' in out and media.warnings
    with pytest.raises(C.ImageFetchError):
        C.rewrite_body_html(f'<img src="{NOTION_IMG}">', media)


def test_record_has_contract_keys_and_no_relations():
    page = make_page("abc-123", "Title", "To sync", webflow_id="wf1")
    rec = C.build_post_record(page, "<p>x</p>", {"src": "/m/a.webp", "width": 1, "height": 1})
    assert tuple(rec.keys()) == C.POST_KEYS
    assert C.POST_KEYS.index("createdAt") == C.POST_KEYS.index("publishDate") + 1
    assert rec["createdAt"] == "2026-11-01T10:00:00.000Z"
    assert rec["source"] == "notion-sync" and rec["notionPageId"] == "abc-123"
    assert rec["thumbnail"] == rec["mainImage"]
    assert "secret-journal" not in C.dump_post(rec)
    assert C.dump_post(rec).endswith("}\n")


# ── full sync against a temp checkout (no git, no Notion) ───────────

def run_sync(tmp_path, pages, mapping, **kw):
    marked = []
    html_by_id = {p["id"]: f'<p>{p["id"]}</p><figure><img src="{NOTION_IMG}" alt=""></figure>' for p in pages}

    async def fetch_pages(db, prop, only):
        return [p for p in pages if not only or p["id"] in only]

    async def fetch_html(page):
        return html_by_id[page["id"]]

    async def mark(pid, prop):
        marked.append(pid)
        return True

    cfg = dict(cms_config.DEFAULT_CMS_CONFIG["site"])
    res = asyncio.run(sync_to_site(
        "db", checkout_dir=str(tmp_path), publish="none", use_cache=False,
        download=fake_download_factory(mapping), fetch_pages=fetch_pages, fetch_html=fetch_html,
        mark_update_on_sync=mark, site_config=cfg, **kw,
    ))
    return res, marked


def seed_post(tmp_path, slug, notion_id=None, webflow_id=None):
    store = SiteStore(tmp_path, "content/posts", "public/media/posts", "/media/posts")
    rec = {k: None for k in C.POST_KEYS}
    rec.update(slug=slug, title=slug, html="<p>old</p>", notionPageId=notion_id, webflowId=webflow_id,
               gallery=[], source="webflow-import")
    store.write_post(rec, {"old.webp": C.StoredImage("old.webp", b"x", 1, 1)})
    return store


def test_sync_status_semantics(tmp_path):
    seed_post(tmp_path, "gone-post", notion_id="dddd")
    seed_post(tmp_path, "live-post", notion_id="llll")
    pages = [
        make_page("llll", "Live post", "Live", slug="live-post"),
        make_page("aaaa", "New post", "To sync", slug="new-post", thumb=NOTION_IMG2),
        make_page("uuuu", "Updated post", "Update on sync", slug="updated-post"),
        make_page("dddd", "Gone post", "Don't sync", slug="gone-post"),
        make_page("tttt", "Temp", "temp", slug="temp-post"),
        make_page("cccc", "Chat page", "To sync", slug="chat-page", koii_chat=True),
    ]
    mapping = {NOTION_IMG: jpeg_bytes(), NOTION_IMG2: jpeg_bytes(color=(0, 0, 255))}
    res, marked = run_sync(tmp_path, pages, mapping)

    posts = tmp_path / "content/posts"
    assert sorted(p.stem for p in posts.glob("*.json")) == ["live-post", "new-post", "updated-post"]
    assert (res.created, res.updated, res.deleted, res.errors) == (1, 1, 1, 0)
    assert res.skipped == 3                                  # live, temp, KOii chat
    assert not (tmp_path / "public/media/posts/gone-post").exists()
    assert json.loads((posts / "live-post.json").read_text())["html"] == "<p>old</p>"   # Live untouched
    new = json.loads((posts / "new-post.json").read_text())
    assert new["thumbnail"]["src"].startswith("/media/posts/new-post/")
    assert "amazonaws" not in new["html"]
    assert len(list((tmp_path / "public/media/posts/new-post").iterdir())) == 2
    assert marked == ["aaaa"]                                # only "To sync" → "Update on sync"


def test_dry_run_writes_nothing(tmp_path):
    pages = [make_page("aaaa", "New post", "To sync", slug="new-post")]
    res, marked = run_sync(tmp_path, pages, {NOTION_IMG: jpeg_bytes()}, dry_run=True)
    assert not (tmp_path / "content").exists() and marked == []
    assert any("would create content/posts/new-post.json (1 body image(s)" in l for l in res.lines)


def test_only_page_ids_limits_the_sync(tmp_path):
    pages = [make_page("aaaa", "A", "To sync", slug="a"), make_page("bbbb", "B", "To sync", slug="b")]
    res, marked = run_sync(tmp_path, pages, {NOTION_IMG: jpeg_bytes()}, only_page_ids=["bbbb"])
    assert [p.stem for p in (tmp_path / "content/posts").glob("*.json")] == ["b"] and marked == ["bbbb"]


def test_slug_clash_is_not_overwritten(tmp_path):
    seed_post(tmp_path, "taken", notion_id="other")
    res, marked = run_sync(tmp_path, [make_page("aaaa", "X", "To sync", slug="taken")], {NOTION_IMG: jpeg_bytes()})
    assert res.errors == 1 and marked == []
    assert json.loads((tmp_path / "content/posts/taken.json").read_text())["notionPageId"] == "other"


def test_import_matched_by_webflow_id_and_slug_change_redirects(tmp_path):
    seed_post(tmp_path, "old-slug", notion_id=None, webflow_id="wf9")
    page = make_page("eeee", "Renamed", "Update on sync", slug="new-slug", webflow_id="wf9")
    res, _ = run_sync(tmp_path, [page], {NOTION_IMG: jpeg_bytes()})
    assert res.errors == 0
    assert not (tmp_path / "content/posts/old-slug.json").exists()
    assert (tmp_path / "content/posts/new-slug.json").exists()
    assert "/post/old-slug /post/new-slug 301" in (tmp_path / "public/_redirects").read_text()


# ── git plumbing against a local bare repo ──────────────────────────

def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_checkout_commit_and_push_local(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_github_token", lambda: None)
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-b", "main", cwd=seed)
    (seed / "README").write_text("x")
    git("add", ".", cwd=seed)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init", cwd=seed)
    bare = tmp_path / "origin.git"
    git("clone", "--bare", str(seed), str(bare), cwd=tmp_path)

    work = tmp_path / "checkout"
    assert R.ensure_checkout(work, str(bare), "main", pull=True) == "cloned"
    assert R.ensure_checkout(work, str(bare), "main", pull=True) == "pulled"
    (work / "content/posts").mkdir(parents=True)
    (work / "content/posts/a.json").write_text("{}\n")
    sha = R.commit_and_push(work, str(bare), "main", ["content/posts", "public/media/posts"], "cms sync: A")
    assert sha
    log = subprocess.run(["git", "--git-dir", str(bare), "log", "--oneline", "-1"], capture_output=True, text=True).stdout
    assert "cms sync: A" in log
    assert R.commit_and_push(work, str(bare), "main", ["content/posts"], "noop") is None
    assert "extraheader" not in (work / ".git/config").read_text()
