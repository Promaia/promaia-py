# koiib.com off Webflow — site CMS target + newsletter rework

**Goal:** Koii's personal site (www.koiib.com) moves off Webflow onto a
static Next.js site on Cloudflare Pages (repo `koiibenvenutto/koiib-website`).
Notion's 🗃️ CMS database stays the editor. `maia cms sync` gains a second
target, `site`, that writes posts into the site repo instead of the Webflow
CMS, and the newsletter stops depending on Webflow for its "published"
check, its "read on website" link and its permanent image URLs.

**Branch:** `koiib-site-cms` off `koii-prod` (worktree
`Documents/dev/promaia-py-koiib-site-cms`). Not pushed. Webflow stays the
default target, so merging changes nothing until the cms config says
`"target": "site"`.

---

## Design

**Content lives in the site repo, as JSON, with its images next to it.**

- `content/posts/<slug>.json` — one record per post: `slug, title,
  description, subtitle, publishDate, createdAt, tag, emojiTags, author, readTime,
  featured, isNewsletter, thumbnail, mainImage, gallery, html, notionPageId,
  webflowId, source, syncedAt` (images are `{src, width, height}`; `html` uses
  site-relative image paths; `createdAt` = the Notion page's created_time,
  the tie-breaker when posts share a publish date). The site builds pages from these at build time
  and also publishes them as `/feed/posts/<slug>.json` + `/feed/posts.json`.
- `public/media/posts/<slug>/<sha1[:12]>.<ext>` — every image, downloaded
  during the sync (Notion's signed S3 links expire after ~1 hour).
  JPEG/PNG → WebP (q82, width ≤ 2000, EXIF orientation applied); GIF, WebP,
  SVG copied as-is. Names come from the original bytes, so re-syncs don't churn.
- HTML comes from the existing `page_to_html` converter, so posts render the
  way they did on Webflow (same callout markup, same figure classes).

**Why in the repo and not R2:** no extra service, credentials or billing to
set up; images are served from koiib.com by the same Pages deploy as the
pages that use them; content and images are versioned together and roll
back together; the volume (a few hundred images, tens of MB as WebP) is far
inside Pages' limits (20,000 files, 25 MiB per file).

**Publishing:** with `publish = "git-push"` the sync commits the changed
content/media and pushes to `main`; the site repo's GitHub Actions workflow
builds and deploys to Cloudflare Pages. Git auth uses the new `github`
integration (fine-grained PAT, Contents read/write on the site repo), passed
to git as an `http.extraheader` env var — never stored in `.git/config`.

**Newsletter:** in `site` mode a post is "published" when
`{base_url}/feed/posts/<slug>.json` answers 200. The cover image and body
images come from that record (absolute URLs on the site) and replace the
Notion URLs by position — in the markdown (so the plain-text part is clean
too) and in the HTML. In both modes the "read on website" and "Subscribe"
links use `cms.site.base_url` (default `https://www.koiib.com`; it was
hard-coded to `www.koiibenvenutto.com`, which only 301s to koiib.com).

## Config

Layered: `DEFAULT_CMS_CONFIG` ← `conf/cms.json` ← the `cms` section of
`<data dir>/promaia.config.json` (read-only; the loader never writes it).

```json
"cms": {
  "target": "webflow",            // "site" after cutover
  "site": {
    "base_url": "https://www.koiib.com",
    "repo_url": "https://github.com/koiibenvenutto/koiib-website.git",
    "branch": "main",
    "checkout_dir": "sites/koiib-website",   // relative → under get_data_dir()
    "content_dir": "content/posts",
    "media_dir": "public/media/posts",
    "media_url_prefix": "/media/posts",
    "post_path": "/post/{slug}",
    "feed_path": "/feed/posts/{slug}.json",
    "publish": "git-push",          // or "none"
    "image_max_width": 2000,
    "image_quality": 82,
    "skip_koii_chat_pages": true
  }
}
```

## Blog Status semantics (same as the Webflow sync)

| Blog Status | Site sync |
|---|---|
| Live | left exactly as published |
| To sync | written; after a successful push, status → "Update on sync" |
| Update on sync | rewritten when the Notion page changed (own cache: `cache/site_sync_cache.db`) |
| Don't sync | post file + media removed if present |
| anything else (temp) | ignored |

Extra rules: the "Webflow ID" property is never touched in site mode;
relation properties (🧘‍♀️ Relevant Notes) are never read; pages ticked
"KOii chat" are skipped while `skip_koii_chat_pages` is on — note that 16 of
the 30 posts that were public on Webflow have it ticked (it looks like a
chat-context flag, not a privacy flag), which only matters if one of them is
set back to "Update on sync". A slug change moves the post and appends
`/post/<old> /post/<new> 301` to `public/_redirects`. A slug already used by
a different post is an error, never an overwrite. A Notion-hosted image that
can't be downloaded fails that page (nothing half-published).

**Warning — queued drafts:** on 2026-11-09 two April drafts sit at Blog
Status "To sync" and are NOT on the public site: "Never Not Trusted an
Instinct…" (`348d1339-6967-8119-a3d3-c5b1fe66d68f`) and "The Stranger Who
Spent $488" (`341d1339-6967-81e8-8c84-ed243a9657f7`). A full `maia cms sync`
(either target) would publish them. Until Koii decides, use `--page` or move
them to "temp"/"Don't sync".

## Usage

```bash
maia cms sync --target site --dry-run                 # plan only, touches nothing
maia cms sync --target site --page <notion-page-id>   # just this page
maia cms sync --target site --no-publish --checkout ~/dev-2/koiib-website
maia news test --target site --base-url https://koiib-website.pages.dev \
               --page <notion-page-id> --email koii.create@gmail.com
```

`--page/--dry-run/--no-publish/--checkout` are refused with the webflow
target so they can never widen into a full Webflow sync.

---

## Phase 1: Site target

### Config + credentials

- [x] Layered cms config with `target` and `site` keys (`promaia/config/cms_config.py`)
- [x] `github` API-key integration registered in `promaia.auth`

### Sync

- [x] `promaia/cms_site/content.py` — record builder, slug rule, status plan, image normalisation, HTML rewrite
- [x] `promaia/cms_site/repo.py` — clone / fast-forward / commit / push with token via env
- [x] `promaia/cms_site/sync.py` — `sync_to_site()` with `only_page_ids`, `dry_run`, `publish`, `checkout_dir`
- [x] `maia cms sync` dispatches on target; `--target/--page/--dry-run/--no-publish/--checkout`
- [x] Tests: config layering, naming, status semantics, HTML rewrite, full sync on a temp checkout, git against a local bare repo (`tests/test_cms_site.py`)
- [x] Smoke run against real Notion into a scratch checkout (Update-on-sync page with 4 images; no Notion writes)

## Phase 2: Newsletter

- [x] Base URL from `cms.site.base_url` for "read on website" and "Subscribe" links, both targets
- [x] Site mode: published check via the feed, site-hosted cover + body images (markdown and HTML), no Webflow calls
- [x] `news test --page/--target/--base-url`
- [x] Tests (`tests/test_newsletter_site.py`) + stubbed-Resend run against a local copy of a synced post
- [ ] Real test send to koii.create@gmail.com against the `*.pages.dev` preview (`news test --target site --base-url … --page …`)

## Phase 3: Cutover (needs Koii's go-ahead; after the concert newsletter is out)

- [ ] Koii creates a fine-grained GitHub token (koiib-website, Contents read/write); `maia auth configure github` on the laptop data dir and on kb
- [ ] `CLOUDFLARE_API_TOKEN` repo secret on `koiibenvenutto/koiib-website` (Pages: Edit) so pushes deploy
- [ ] Re-run the Webflow import on the site repo to catch posts published via Webflow after 2026-11-09
- [ ] Decide the two queued April drafts (publish, or move off "To sync")
- [ ] Merge `koiib-site-cms` into `koii-prod` (deploys kb) and pull it into `~/promaia/promaia-fresh`
- [ ] Add `"cms": {"target": "site"}` to `promaia.config.json` on kb and the laptop (hand edit; nothing writes this section)
- [ ] First real `maia cms sync` with `--dry-run`, then for real; check the Actions deploy

**Rollback:** set `"target": "webflow"` again (works only while Webflow
still serves the site and the API key is valid). Content already in the site
repo stays; nothing in Notion needs undoing because the site sync only ever
changes Blog Status "To sync" → "Update on sync", which the Webflow sync
treats the same way.

## Open questions

- Is "KOii chat" really a privacy flag? If not, set `skip_koii_chat_pages` to false.
- Should the newsletter refuse to send (not just warn) when the post is not on the site yet?
- The newsletter's plain-text part still shows the cover image URL line; fine with site URLs, confirm Koii wants it.
- Old emails already sent point at Webflow CDN image URLs, which may stop working once Webflow is gone; nothing in Promaia can fix those.
