"""Newsletter: site-mode published check, permanent image mapping, link building."""
from promaia.newsletter import site_source as S

BASE = "https://koiib-website.pages.dev"

FEED = {
    "slug": "concert",
    "title": "Concert",
    "thumbnail": {"src": "/media/posts/concert/aaaaaaaaaaaa.webp", "width": 10, "height": 10},
    "mainImage": {"src": "/media/posts/concert/aaaaaaaaaaaa.webp", "width": 10, "height": 10},
    "html": '<p>x</p><figure><div><img src="/media/posts/concert/bbbbbbbbbbbb.webp"></div></figure>'
            '<figure><div><img src="/media/posts/concert/cccccccccccc.gif"></div></figure>',
}


class Resp:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


def test_post_and_feed_urls():
    assert S.post_url("https://www.koiib.com/", "my-slug") == "https://www.koiib.com/post/my-slug"
    assert S.post_url("https://www.koiib.com", None) == "https://www.koiib.com/"
    assert S.feed_url(BASE, "x") == BASE + "/feed/posts/x.json"


def test_site_post_absolutizes_images():
    post = S.site_post_from_feed(FEED, BASE)
    assert post.url == BASE + "/post/concert"
    assert post.cover_image == BASE + "/media/posts/concert/aaaaaaaaaaaa.webp"
    assert post.body_images == [BASE + "/media/posts/concert/bbbbbbbbbbbb.webp",
                                BASE + "/media/posts/concert/cccccccccccc.gif"]


def test_fetch_site_post_published_vs_missing():
    seen = []

    def get(url, **kw):
        seen.append(url)
        return Resp(200, FEED) if url.endswith("/concert.json") else Resp(404)

    assert S.fetch_site_post(BASE, "concert", get=get).slug == "concert"
    assert S.fetch_site_post(BASE, "nope", get=get) is None
    assert seen[0] == BASE + "/feed/posts/concert.json"
    # a feed answering with a different post is not "published"
    assert S.fetch_site_post(BASE, "concert", get=lambda u, **k: Resp(200, {"slug": "other"})) is None


def test_replace_images_by_position_keeps_header_and_hosted():
    notion1 = "https://prod-files-secure.s3.amazonaws.com/1.jpg?sig"
    notion2 = "https://prod-files-secure.s3.amazonaws.com/2.jpg?sig"
    header = BASE + "/media/posts/concert/aaaaaaaaaaaa.webp"
    html = f'<img src="{header}"><img src="{notion1}"><img src="{BASE}/already.webp"><img src="{notion2}">'
    post = S.site_post_from_feed(FEED, BASE)
    out, replaced, unmapped = S.replace_images_by_position(
        html, post.body_images, header_image=header, keep_prefixes=(BASE + "/",))
    assert "amazonaws" not in out and replaced == 2 and unmapped == 0
    assert out.index("bbbbbbbbbbbb") < out.index("cccccccccccc")


def test_replace_markdown_images_by_position():
    md = "a\n![one](https://s3.amazonaws.com/1.jpg?x)\nb\n![](https://s3.amazonaws.com/2.jpg)\n![three](https://s3.amazonaws.com/3.jpg)"
    out = S.replace_markdown_images_by_position(md, [BASE + "/m/1.webp", BASE + "/m/2.webp"])
    assert f"![one]({BASE}/m/1.webp)" in out and f"![]({BASE}/m/2.webp)" in out
    assert "3.jpg" in out          # nothing left to map it to: unchanged


def test_read_on_website_url_uses_config_base(monkeypatch):
    from promaia.newsletter import commands
    assert commands.read_on_website_url("https://www.koiib.com", "slug") == "https://www.koiib.com/post/slug"
    page = {"id": "x", "properties": {"Name": {"type": "title", "title": [{"text": {"content": "Hi There"}}]},
                                      "Slug": {"type": "rich_text", "rich_text": []}}}
    assert commands.get_page_slug(page) == "hi-there"


def test_check_site_published(monkeypatch):
    from promaia.newsletter import commands
    monkeypatch.setattr(commands, "fetch_site_post",
                        lambda base, slug, **kw: S.site_post_from_feed(FEED, base) if slug == "concert" else None)
    page = {"id": "x", "properties": {"Slug": {"type": "rich_text", "rich_text": [{"text": {"content": "concert"}}]}}}
    post, slug = commands.check_site_published(page, BASE)
    assert slug == "concert" and post.url == BASE + "/post/concert"
    page["properties"]["Slug"]["rich_text"][0]["text"]["content"] = "draft"
    post, slug = commands.check_site_published(page, BASE)
    assert post is None and slug == "draft"
