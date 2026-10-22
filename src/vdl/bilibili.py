"""Bilibili workarounds that yt-dlp doesn't have yet.

Both are needed as of October 2026:

* Links copied from the site's language switcher (/zh-Hans/video/BV...)
  don't match yt-dlp's Bilibili extractor, so they fall through to the
  generic one and fail with "The extracted extension ('html') is unusual".
* Bilibili sometimes serves logged-out visitors a page with the video data
  stripped out, and yt-dlp stops with "Unable to extract initial state". The
  same data is still available from Bilibili's own API, which is where the
  site's player gets it.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from yt_dlp.extractor import bilibili
from yt_dlp.utils import traverse_obj

# A language segment before /video/: /zh-Hans/, /zh-Hant/, /en/ and so on.
LANGUAGE_PREFIX = re.compile(r"^/[a-z]{2}(?:-[A-Za-z]{2,4})?(?=/video/)")
# Query parameters the site adds to track where a click came from.
TRACKING_PARAMS = {"spm_id_from", "vd_source", "from_spmid", "share_source",
                   "share_medium", "share_plat", "share_session_id", "share_tag",
                   "timestamp", "unique_k", "up_id", "bbid", "ts"}


def clean_url(url: str) -> str:
    """Rewrite a bilibili.com video link into the form yt-dlp recognises.

    Other links are returned unchanged.
    """
    parts = urlparse(url)
    host = parts.netloc.lower()
    if host != "bilibili.com" and not host.endswith(".bilibili.com"):
        return url
    path = LANGUAGE_PREFIX.sub("", parts.path)
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if k not in TRACKING_PARAMS])
    return urlunparse(parts._replace(path=path, query=query))


class BiliBiliIE(bilibili.BiliBiliIE):
    """yt-dlp's Bilibili extractor, with an API fallback for stripped pages.

    The class name matches yt-dlp's so registering it replaces the original
    in place (yt-dlp keys extractors by class name).
    """

    _video_ref: tuple[str, str] = ("", "")

    def _real_extract(self, url):
        self._video_ref = self._match_valid_url(url).group("prefix", "id")
        return super()._real_extract(url)

    def _search_json(self, start_pattern, string, name, video_id, **kwargs):
        found = super()._search_json(start_pattern, string, name, video_id, **kwargs)
        if found or name != "initial state":
            return found
        return self._state_from_api(video_id) or found

    def _state_from_api(self, video_id):
        """Build the parts of the page state the extractor reads, from the API."""
        prefix, ref = self._video_ref
        query = {"platform": "web"}
        if prefix.upper() == "BV":
            query["bvid"] = "BV" + ref
        else:
            query["aid"] = ref
        self.write_debug("page has no video data; asking the Bilibili API instead")
        detail = self._download_json(
            "https://api.bilibili.com/x/web-interface/wbi/view/detail", video_id,
            note="Page came back empty; downloading video details from the API",
            errnote="Bilibili API refused the request", fatal=False,
            query=self._sign_wbi(query, video_id),
            headers={"Referer": "https://www.bilibili.com/"})
        view = traverse_obj(detail, ("data", "View", {dict}))
        # A redirect means a bangumi episode; let yt-dlp's own handling of the
        # empty page send it to the right extractor.
        if not view or traverse_obj(view, "redirect_url"):
            return None
        return {
            "videoData": view,
            "upData": traverse_obj(detail, ("data", "Card", "card", {dict})) or {},
            "tags": traverse_obj(detail, ("data", "Tags", {list})) or [],
        }
