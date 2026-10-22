from pathlib import Path

import pytest
from yt_dlp import YoutubeDL

from vdl.options import (
    PLAYLIST_TEMPLATE,
    SINGLE_TEMPLATE,
    DownloadRequest,
    build_ydl_opts,
    format_selector,
    parse_rate,
)

OUT = Path("/tmp/vdl-test")


def opts(**kw):
    return build_ydl_opts(DownloadRequest(output_dir=OUT, **kw))


def keys(o):
    return [pp["key"] for pp in o["postprocessors"]]


def test_default_video_download():
    o = opts()
    assert o["format"] == "bv*+ba/b"
    assert o["outtmpl"] == SINGLE_TEMPLATE
    assert o["noplaylist"] is True
    assert o["merge_output_format"] == "mp4"
    assert o["paths"] == {"home": str(OUT), "temp": str(OUT / ".partial")}
    assert o["continuedl"] is True
    assert "proxy" not in o


def test_best_quality_prefers_separate_streams():
    assert format_selector("best", False).startswith("bv*+ba")


def test_capped_quality_falls_back_to_best_when_nothing_fits():
    fmt = format_selector("720", False)
    assert fmt.split("/")[0] == "bv*[height<=720]+ba"
    assert fmt.endswith("/bv*+ba/b")


def test_unknown_quality_rejected():
    with pytest.raises(ValueError, match="quality must be one of"):
        format_selector("999", False)


def test_video_prefers_widely_playable_codecs():
    assert opts()["format_sort"] == ["res", "fps", "vcodec:h264", "acodec:aac"]


def test_audio_only_extracts_and_skips_merge():
    o = opts(audio_only=True, audio_format="m4a")
    assert o["format"] == "bestaudio/best"
    assert "merge_output_format" not in o
    extract = o["postprocessors"][0]
    assert extract["key"] == "FFmpegExtractAudio"
    assert extract["preferredcodec"] == "m4a"


def test_audio_only_ignores_quality():
    assert opts(audio_only=True, quality="360")["format"] == "bestaudio/best"


def test_raw_format_overrides_quality():
    assert opts(raw_format="137+140", quality="480")["format"] == "137+140"


def test_raw_format_gets_no_codec_preference():
    assert "format_sort" not in opts(raw_format="137+140")


def test_playlist_uses_numbered_template():
    o = opts(playlist=True)
    assert o["outtmpl"] == PLAYLIST_TEMPLATE
    assert o["noplaylist"] is False


def test_subtitles_and_cookies_and_archive():
    o = opts(subtitles=["en", "zh-Hans"], cookies_from_browser="chrome",
             cookies_file=Path("c.txt"), archive_file=Path("done.txt"))
    assert o["writesubtitles"] and o["writeautomaticsub"]
    assert o["subtitleslangs"] == ["en", "zh-Hans"]
    assert "FFmpegSubtitlesConvertor" in keys(o)
    assert o["cookiesfrombrowser"] == ("chrome",)
    assert o["cookiefile"] == "c.txt"
    assert o["download_archive"] == "done.txt"


def test_deno_path_enables_js_runtime():
    assert opts(deno_path="/x/deno")["js_runtimes"] == {"deno": {"path": "/x/deno"}}
    assert "js_runtimes" not in opts()


def test_empty_proxy_forces_direct_connection():
    assert opts(proxy="")["proxy"] == ""


def test_proxy_unset_leaves_system_default():
    assert "proxy" not in opts(proxy=None)


def test_quiet_by_default_with_progress_and_title_line():
    o = opts()
    assert o["quiet"] is True and o["noprogress"] is False
    assert o["forceprint"] == {"before_dl": ["Downloading: %(title)s"]}


def test_verbose_and_simulate_skip_title_line():
    assert "forceprint" not in opts(verbose=True)
    assert "forceprint" not in opts(simulate=True)
    assert opts(verbose=True)["quiet"] is False


@pytest.mark.parametrize("text,expected", [
    ("500K", 512000),
    ("2M", 2097152),
    ("2MB/s", 2097152),
    ("1.5m", 1572864),
    ("1000", 1000),
])
def test_parse_rate(text, expected):
    assert parse_rate(text) == expected


@pytest.mark.parametrize("text", ["fast", "", "-2M", "M"])
def test_parse_rate_rejects_garbage(text):
    with pytest.raises(ValueError, match="not a valid rate"):
        parse_rate(text)


def test_templates_are_valid_for_yt_dlp():
    info = {"id": "BV1x", "title": "测试 / a:b?", "ext": "mp4", "uploader": "Up",
            "playlist_title": "List", "playlist_index": 7}
    with YoutubeDL({"outtmpl": SINGLE_TEMPLATE, "windowsfilenames": True}) as ydl:
        single = ydl.prepare_filename(info)
    with YoutubeDL({"outtmpl": PLAYLIST_TEMPLATE, "windowsfilenames": True}) as ydl:
        listed = ydl.prepare_filename(info)
    assert single.startswith("Up/") and single.endswith("[BV1x].mp4")
    assert listed.startswith("List/007 - ")
    # The slash in the title must not create an extra folder.
    assert single.count("/") == 1
