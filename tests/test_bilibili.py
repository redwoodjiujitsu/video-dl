"""Bilibili link cleanup and the API fallback for stripped pages: no network."""
import pytest
import yt_dlp

from vdl.bilibili import BiliBiliIE, clean_url

EMPTY_PAGE = "<html><script>if(window.__INITIAL_STATE__){}</script></html>"
DETAIL = {"code": 0, "data": {
    "View": {"bvid": "BV1Rso6B2E25", "aid": 1, "cid": 2, "title": "Gordon Whizzer Vs Underhook"},
    "Card": {"card": {"name": "BjjShare", "mid": "3"}},
    "Tags": [{"tag_name": "bjj"}],
}}


@pytest.mark.parametrize("url,expected", [
    ("https://www.bilibili.com/zh-Hans/video/BV1Rso6B2E25/?spm_id_from=333.1387.homepage.video_card.click",
     "https://www.bilibili.com/video/BV1Rso6B2E25/"),
    ("https://www.bilibili.com/zh-Hant/video/BV1x?p=7&vd_source=abc",
     "https://www.bilibili.com/video/BV1x?p=7"),
    ("https://m.bilibili.com/en/video/BV1x", "https://m.bilibili.com/video/BV1x"),
    ("https://www.bilibili.com/video/BV1x?p=2&t=30", "https://www.bilibili.com/video/BV1x?p=2&t=30"),
    # Only a language segment directly before /video/ is removed.
    ("https://www.bilibili.com/bangumi/play/ep123", "https://www.bilibili.com/bangumi/play/ep123"),
    # Other sites are left alone, tracking parameters and all.
    ("https://www.youtube.com/watch?v=abc&spm_id_from=x", "https://www.youtube.com/watch?v=abc&spm_id_from=x"),
    ("https://notbilibili.com/zh-Hans/video/BV1x", "https://notbilibili.com/zh-Hans/video/BV1x"),
])
def test_clean_url(url, expected):
    assert clean_url(url) == expected


@pytest.fixture
def extractor(monkeypatch):
    ie = BiliBiliIE(yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}))
    ie._video_ref = ("BV", "1Rso6B2E25")
    calls = []

    def fake_download_json(url, video_id, query=None, **kw):
        calls.append(query)
        return ie.detail

    monkeypatch.setattr(ie, "_download_json", fake_download_json)
    monkeypatch.setattr(ie, "_sign_wbi", lambda params, video_id: params)
    monkeypatch.setattr(ie, "write_debug", lambda *a, **kw: None)
    ie.detail, ie.calls = DETAIL, calls
    return ie


def search_state(ie, page):
    return ie._search_json(r"window\.__INITIAL_STATE__\s*=", page, "initial state",
                           "1Rso6B2E25", default=None)


def test_empty_page_is_filled_in_from_the_api(extractor):
    state = search_state(extractor, EMPTY_PAGE)
    assert state["videoData"]["title"] == "Gordon Whizzer Vs Underhook"
    assert state["upData"]["name"] == "BjjShare"
    assert state["tags"] == [{"tag_name": "bjj"}]
    assert extractor.calls == [{"platform": "web", "bvid": "BV1Rso6B2E25"}]


def test_normal_page_does_not_call_the_api(extractor):
    page = '<script>window.__INITIAL_STATE__={"videoData": {"bvid": "BV1"}};</script>'
    assert search_state(extractor, page) == {"videoData": {"bvid": "BV1"}}
    assert extractor.calls == []


def test_av_links_query_by_aid(extractor):
    extractor._video_ref = ("av", "170001")
    search_state(extractor, EMPTY_PAGE)
    assert extractor.calls == [{"platform": "web", "aid": "170001"}]


@pytest.mark.parametrize("detail", [
    None,                                                          # API request failed
    {"code": -404, "data": None},                                  # deleted video
    {"code": 0, "data": {"View": {"redirect_url": "https://www.bilibili.com/bangumi/play/ep1"}}},
])
def test_api_without_usable_data_falls_back_to_yt_dlp(extractor, detail):
    extractor.detail = detail
    assert search_state(extractor, EMPTY_PAGE) is None


def test_other_searches_are_untouched(extractor):
    assert extractor._search_json(r"window\.__playinfo__\s*=", EMPTY_PAGE, "play info",
                                  "1Rso6B2E25", default=None) is None
    assert extractor.calls == []
