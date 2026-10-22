"""CLI behaviour with yt-dlp replaced by a scripted fake: no network access."""
from pathlib import Path

import pytest
import yt_dlp
from yt_dlp.utils import DownloadError

from vdl import cli

YT = "https://www.youtube.com/watch?v=abc"
BILI = "https://www.bilibili.com/video/BV1GJ411x7h7"


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL. `script` maps URL -> list of outcomes,
    one per attempt: a dict (info; saves a file), "nothing", a str error, or
    an exception instance to raise."""

    script: dict = {}
    calls: list = []

    def __init__(self, opts):
        self.opts = opts
        self.params = opts  # read by PostProcessor's constructor
        self.post_processors = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def add_info_extractor(self, ie):
        pass

    def add_post_processor(self, pp, when="post_process"):
        assert when == "after_move"
        self.post_processors.append(pp)

    def report_error(self, message, *a, **kw):
        pass

    def list_formats(self, info):
        print("FORMATS", info["id"])

    def extract_info(self, url, download=True, process=True):
        FakeYDL.calls.append((url, download))
        outcomes = FakeYDL.script[url]
        outcome = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, str) and outcome != "nothing":
            self.report_error(f"\x1b[0;31mERROR:\x1b[0m {outcome}")
            return None
        if outcome == "nothing":
            return {"id": "x", "title": "t"}
        if download and outcome.get("_type") != "playlist":
            path = str(Path(self.opts["paths"]["home"]) / "Chan" / f"{outcome['id']}.mp4")
            # Skip PostProcessor.run's progress wrapper, which needs a real
            # YoutubeDL; the wiring itself is covered by a live download.
            for pp in self.post_processors:
                pp.on_file({**outcome, "filepath": path})
        return outcome


@pytest.fixture(autouse=True)
def fake_env(monkeypatch, tmp_path):
    FakeYDL.script = {}
    FakeYDL.calls = []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cli, "find_deno", lambda: None)
    # Saved files are "complete" unless a test says otherwise.
    monkeypatch.setattr(cli, "probe_duration", lambda path: None)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    return tmp_path


def run(tmp_path, *args):
    return cli.run([*args, "-o", str(tmp_path / "out")])


def video(vid="abc", **kw):
    return {"id": vid, "title": "A video", "uploader": "Chan", "duration": 65,
            "extractor_key": "Youtube", **kw}


# --- argument checks -------------------------------------------------------

def test_requires_a_url(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path)
    assert exc.value.code == cli.EXIT_USAGE
    assert "give at least one URL" in capsys.readouterr().err


def test_rejects_non_links(tmp_path, capsys):
    with pytest.raises(SystemExit):
        run(tmp_path, "youtube.com/watch?v=abc")
    assert "not a web link" in capsys.readouterr().err


def test_audio_only_and_format_conflict(tmp_path, capsys):
    with pytest.raises(SystemExit):
        run(tmp_path, YT, "-x", "-f", "140")
    assert "can't be used together" in capsys.readouterr().err


def test_bad_rate_is_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        run(tmp_path, YT, "--limit-rate", "fast")
    assert exc.value.code == cli.EXIT_USAGE
    assert "not a valid rate" in capsys.readouterr().err


def test_batch_file_skips_comments_and_dedupes(tmp_path):
    batch = tmp_path / "links.txt"
    batch.write_text(f"﻿# my list\n{YT}\n\n  {BILI}  \n{YT}\n", encoding="utf-8")
    args = cli.build_parser().parse_args([YT, "-a", str(batch)])
    assert cli.collect_urls(args) == [YT, BILI]


def test_bilibili_links_are_cleaned_before_use():
    args = cli.build_parser().parse_args([
        "https://www.bilibili.com/zh-Hans/video/BV1Rso6B2E25/?spm_id_from=333.1387",
        "https://www.bilibili.com/video/BV1Rso6B2E25/",
    ])
    assert cli.collect_urls(args) == ["https://www.bilibili.com/video/BV1Rso6B2E25/"]


def test_missing_ffmpeg_stops_before_download(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    FakeYDL.script = {YT: [video()]}
    assert run(tmp_path, YT) == cli.EXIT_USAGE
    assert "brew install ffmpeg" in capsys.readouterr().err
    assert FakeYDL.calls == []


# --- helpers ---------------------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [(None, "unknown"), (65, "1:05"), (3725, "1:02:05")])
def test_format_duration(seconds, expected):
    assert cli.format_duration(seconds) == expected


@pytest.mark.parametrize("url,expected", [
    (BILI, True),
    ("https://b23.tv/abc", True),
    (YT, False),
])
def test_is_bilibili(url, expected):
    assert cli.is_bilibili(url) is expected


def test_hint_for_login_errors():
    assert "--cookies-from-browser" in cli.hint_for("Sign in to confirm you're not a bot")
    assert "--cookies-from-browser" in cli.hint_for("This video is age-restricted")


def test_hint_does_not_match_age_inside_other_words():
    assert cli.hint_for("Unable to download webpage: message truncated") == ""


def test_geo_restriction_hint():
    assert "region" in cli.hint_for("The uploader has not made this video available in your country")


def test_proxy_errors_get_a_hint():
    assert '--proxy ""' in cli.hint_for("Unable to connect to proxy")


# --- downloading -----------------------------------------------------------

def test_successful_download_reports_saved_file(tmp_path, capsys):
    FakeYDL.script = {YT: [video()]}
    assert run(tmp_path, YT) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Saved 1 file(s)" in out
    assert "Chan/abc.mp4" in out


def test_one_failure_does_not_stop_the_rest(tmp_path, capsys):
    other = "https://www.youtube.com/watch?v=def"
    FakeYDL.script = {YT: ["Video unavailable"], other: [video("def")]}
    assert run(tmp_path, YT, other) == cli.EXIT_FAILED
    captured = capsys.readouterr()
    assert "Saved 1 file(s)" in captured.out
    assert f"FAILED: {YT}" in captured.err
    assert "Video unavailable" in captured.err
    assert "ERROR" not in captured.err.split("FAILED")[1]


def test_transient_site_refusal_is_retried(tmp_path, capsys):
    FakeYDL.script = {BILI: ["HTTP Error 412: Precondition Failed", video("BV1")]}
    assert run(tmp_path, BILI) == cli.EXIT_OK
    assert "retrying in 3s (1/2)" in capsys.readouterr().err
    assert len(FakeYDL.calls) == 2


def test_gives_up_after_all_retries(tmp_path, capsys):
    FakeYDL.script = {YT: ["HTTP Error 429: Too Many Requests"]}
    assert run(tmp_path, YT) == cli.EXIT_FAILED
    assert len(FakeYDL.calls) == len(cli.RETRY_DELAYS) + 1
    assert "FAILED" in capsys.readouterr().err


def test_permanent_error_is_not_retried(tmp_path):
    FakeYDL.script = {YT: [DownloadError("ERROR: Unsupported URL: x")]}
    assert run(tmp_path, YT) == cli.EXIT_FAILED
    assert len(FakeYDL.calls) == 1


def test_playlist_counts_missing_items(tmp_path, capsys):
    pl = {"_type": "playlist", "id": "PL", "title": "List", "entries": [video(), None, None]}
    FakeYDL.script = {YT: [pl]}
    assert run(tmp_path, YT, "-p") == cli.EXIT_FAILED
    assert "2 playlist item(s) failed" in capsys.readouterr().err


def test_preview_only_download_is_reported_as_failed(tmp_path, monkeypatch, capsys):
    # Seen live: BV1jL41167ZG is 11:36 but logged out Bilibili served 10s.
    monkeypatch.setattr(cli, "probe_duration", lambda path: 10.08)
    FakeYDL.script = {BILI: [video("BV1", duration=696)]}
    assert run(tmp_path, BILI) == cli.EXIT_FAILED
    err = capsys.readouterr().err
    assert f"FAILED: {BILI}" in err
    assert "only 0:10 of 11:36 was saved" in err
    assert "--cookies-from-browser" in err


def test_full_length_download_passes_length_check(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "probe_duration", lambda path: 694.5)
    FakeYDL.script = {BILI: [video("BV1", duration=696)]}
    assert run(tmp_path, BILI) == cli.EXIT_OK


@pytest.mark.parametrize("expected,actual,short", [
    (696, 10.08, True),     # Bilibili preview
    (19, 19.06, False),     # container slightly longer than reported
    (30, 26, False),        # small trim, under 5s
    (None, 10, False),      # site didn't report a length
    (696, None, False),     # ffprobe unavailable
])
def test_is_truncated(expected, actual, short):
    assert cli.is_truncated(expected, actual) is short


def test_bilibili_without_cookies_prints_quality_note(tmp_path, capsys):
    FakeYDL.script = {BILI: [video("BV1")]}
    run(tmp_path, BILI)
    assert "Bilibili may hold back" in capsys.readouterr().err


def test_bilibili_note_hidden_with_cookies(tmp_path, capsys):
    FakeYDL.script = {BILI: [video("BV1")]}
    run(tmp_path, BILI, "--cookies-from-browser", "chrome")
    assert "Bilibili may hold back" not in capsys.readouterr().err


# --- info mode -------------------------------------------------------------

def test_info_mode_does_not_create_output_dir(tmp_path, capsys):
    FakeYDL.script = {YT: [video()]}
    assert run(tmp_path, YT, "--info") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Title:    A video" in out and "Length:   1:05" in out
    assert not (tmp_path / "out").exists()
    assert FakeYDL.calls == [(YT, False)]


def test_info_on_unreadable_link_reports_instead_of_crashing(tmp_path, capsys):
    FakeYDL.script = {YT: ["Video unavailable"]}
    assert run(tmp_path, YT, "--info") == cli.EXIT_FAILED
    assert "FAILED" in capsys.readouterr().err


# --- cleanup ---------------------------------------------------------------

def test_remove_empty_dirs_removes_root_when_clean(tmp_path):
    root = tmp_path / "out"
    (root / ".partial" / "a").mkdir(parents=True)
    cli.remove_empty_dirs(root)
    assert not root.exists()


def test_remove_empty_dirs_keeps_partial_downloads(tmp_path):
    root = tmp_path / "out"
    (root / ".partial").mkdir(parents=True)
    (root / ".partial" / "v.f137.mp4.part").write_bytes(b"x")
    (root / "empty").mkdir()
    cli.remove_empty_dirs(root)
    assert (root / ".partial" / "v.f137.mp4.part").exists()
    assert not (root / "empty").exists()
