"""Command-line entry point.

    vdl "https://www.youtube.com/watch?v=..."
    vdl "https://www.bilibili.com/video/BV..." -q 1080 --cookies-from-browser chrome
    vdl URL1 URL2 --audio-only
    vdl -a links.txt -o ~/Movies/Downloads

Quote every URL: zsh treats '?' and '&' as special characters.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import urlparse

from vdl import __version__
from vdl.bilibili import BiliBiliIE, clean_url
from vdl.options import AUDIO_FORMATS, BROWSERS, QUALITIES, DownloadRequest, build_ydl_opts

DEFAULT_OUTPUT = Path.home() / "Downloads" / "Videos"

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 130

# Bilibili answers bursts of requests with 412 and YouTube with 429; both
# usually clear within seconds. "No video formats found" is how a throttled
# YouTube response often surfaces. Anything else is treated as permanent.
TRANSIENT_ERRORS = ("no video formats found", "http error 412", "http error 429")
RETRY_DELAYS = (3, 10)


def is_transient(message: str) -> bool:
    text = message.lower()
    return any(marker in text for marker in TRANSIENT_ERRORS)


class LookupFailed(Exception):
    """yt-dlp reported an error and returned no info (it was told to keep going)."""


@dataclass
class RunSummary:
    saved: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vdl",
        description="Download videos from YouTube, Bilibili and 1,000+ other sites.",
        epilog=(
            "Put URLs in quotes. Bilibili may hold back its highest quality from "
            "logged-out viewers: log in to bilibili.com in your browser and add "
            "--cookies-from-browser chrome (or safari or firefox)."
        ),
    )
    p.add_argument("urls", nargs="*", metavar="URL", help="one or more video or playlist links")
    p.add_argument("-a", "--batch-file", type=Path,
                   help="text file with one URL per line (# starts a comment)")
    p.add_argument("-o", "--output-dir", type=Path, default=DEFAULT_OUTPUT,
                   help=f"where to save files (default: {DEFAULT_OUTPUT})")
    p.add_argument("-q", "--quality", choices=QUALITIES, default="best",
                   help="maximum video height (default: best available)")
    p.add_argument("-x", "--audio-only", action="store_true", help="save the audio track only")
    p.add_argument("--audio-format", choices=AUDIO_FORMATS, default="mp3",
                   help="audio file type with --audio-only (default: mp3)")
    p.add_argument("-p", "--playlist", action="store_true",
                   help="download the whole playlist or multi-part video, not just one item")
    p.add_argument("-s", "--subs", metavar="LANGS",
                   help="save subtitles, comma-separated language codes, e.g. en,zh-Hans")
    p.add_argument("--cookies-from-browser", choices=BROWSERS, metavar="BROWSER",
                   help=f"use your login from a browser ({', '.join(BROWSERS)})")
    p.add_argument("--cookies", type=Path, metavar="FILE", help="Netscape-format cookies.txt file")
    p.add_argument("--archive", type=Path, metavar="FILE",
                   help="record finished downloads in FILE and skip them next time")
    p.add_argument("--limit-rate", metavar="RATE", help="cap bandwidth, e.g. 500K or 2M")
    p.add_argument("--proxy", metavar="URL",
                   help='proxy to use, e.g. socks5://127.0.0.1:1080; "" connects '
                        "directly and ignores the system proxy")
    p.add_argument("-f", "--format", dest="raw_format", metavar="SPEC",
                   help="advanced: raw yt-dlp format string (overrides -q)")
    p.add_argument("-F", "--list-formats", action="store_true",
                   help="list the available qualities and exit")
    p.add_argument("-i", "--info", action="store_true",
                   help="show title, uploader and length without downloading")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="show every step yt-dlp takes (useful when reporting problems)")
    p.add_argument("--version", action="version", version=f"vdl {__version__}")
    return p


def read_batch_file(path: Path) -> list[str]:
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().lstrip("﻿")
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def is_url(text: str) -> bool:
    parsed = urlparse(text)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def collect_urls(args: argparse.Namespace) -> list[str]:
    urls = list(args.urls)
    if args.batch_file:
        urls += read_batch_file(args.batch_file)
    urls = [clean_url(u) for u in urls]
    # Drop repeats but keep the order they were given in.
    return list(dict.fromkeys(urls))


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def is_bilibili(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in ("bilibili.com", "b23.tv"))


def hint_for(error: str) -> str:
    text = error.lower()
    # YouTube says "has not made this video available in your country".
    if "geo" in text or "available in your country" in text:
        return "  hint: the uploader limits this video by region, or it was deleted"
    if re.search(r"\b(sign in|log ?in|login|cookies|age[- ]restricted|confirm your age)\b", text):
        return "  hint: this video needs a login. Try --cookies-from-browser chrome"
    if "ffmpeg" in text:
        return "  hint: install ffmpeg with:  brew install ffmpeg"
    if "unsupported url" in text:
        return "  hint: this site isn't supported, or the link isn't a video page"
    if "proxy" in text:
        return ('  hint: the proxy isn\'t reachable. Check System Settings > Network > '
                'Proxies, or bypass it with --proxy ""')
    if "preview" in text:
        return ("  hint: logged-out viewers only get a preview of this video. Try "
                "--cookies-from-browser chrome; some videos also need a paid membership")
    if "http error 403" in text or "http error 412" in text:
        return "  hint: the site blocked the request. Update yt-dlp (see README) or add cookies"
    return ""


def probe_duration(path: str) -> Optional[float]:
    """Real length of a saved file in seconds, or None if ffprobe can't tell."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        return float(out)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def is_truncated(expected: Optional[float], actual: Optional[float]) -> bool:
    # Sites round durations and trim a second or two of silence, so only a
    # clear shortfall counts: Bilibili previews are 10-30s of a full video.
    if not expected or actual is None:
        return False
    return actual < expected * 0.9 and expected - actual > 5


def find_deno() -> Optional[str]:
    """Deno from the bundled PyPI package, else whatever is on PATH."""
    try:
        from deno import find_deno_bin

        path = find_deno_bin()
        if path and Path(path).exists():
            return str(path)
    except (ImportError, OSError, RuntimeError):
        pass
    return shutil.which("deno")


def process_url(ydl, url: str, args: argparse.Namespace, req: DownloadRequest,
                summary: RunSummary, saved_before: int) -> None:
    if args.info:
        print_info(ydl, url)
        return
    if args.list_formats:
        info = ydl.extract_info(url, download=False)
        if info is None:
            raise LookupFailed("couldn't read this link")
        ydl.list_formats(info)
        return

    info = ydl.extract_info(url, download=True)
    if info is None:
        raise LookupFailed("download failed")

    if req.playlist and info.get("_type") == "playlist":
        # With ignoreerrors, a dead item comes back as None instead of raising.
        missing = sum(1 for entry in info.get("entries") or [] if entry is None)
        if missing:
            summary.failed.append((url, f"{missing} playlist item(s) failed"))
            return

    # An archive hit legitimately saves nothing, so only flag it otherwise.
    if len(summary.saved) == saved_before and not req.archive_file and not req.simulate:
        summary.failed.append((url, "nothing was saved"))


def print_info(ydl, url: str) -> None:
    info = ydl.extract_info(url, download=False, process=False)
    if info is None:
        raise LookupFailed("couldn't read this link")
    if info.get("_type") == "playlist":
        entries = list(info.get("entries") or [])
        print(f"Playlist: {info.get('title')}  ({len(entries)} items)")
        for n, entry in enumerate(entries, 1):
            entry = entry or {}
            print(f"  {n:3d}. {entry.get('title') or entry.get('url')}")
        return
    print(f"Title:    {info.get('title')}")
    print(f"Uploader: {info.get('uploader') or info.get('channel') or 'unknown'}")
    print(f"Length:   {format_duration(info.get('duration'))}")
    print(f"Site:     {info.get('extractor_key')}")


def run(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(None if argv is None else list(argv))

    try:
        urls = collect_urls(args)
    except OSError as exc:
        parser.error(f"can't read batch file: {exc}")
    if not urls:
        parser.error("give at least one URL (or -a FILE)")
    bad = [u for u in urls if not is_url(u)]
    if bad:
        parser.error(f"not a web link: {', '.join(bad)}")
    if args.audio_only and args.raw_format:
        parser.error("--audio-only and --format can't be used together")

    looking_only = args.info or args.list_formats
    req = DownloadRequest(
        output_dir=args.output_dir.expanduser(),
        quality=args.quality,
        audio_only=args.audio_only,
        audio_format=args.audio_format,
        raw_format=args.raw_format,
        playlist=args.playlist,
        subtitles=[s.strip() for s in (args.subs or "").split(",") if s.strip()],
        cookies_from_browser=args.cookies_from_browser,
        cookies_file=args.cookies,
        archive_file=args.archive,
        rate_limit=args.limit_rate,
        proxy=args.proxy,
        deno_path=find_deno(),
        verbose=args.verbose,
        simulate=looking_only,
    )
    try:
        opts = build_ydl_opts(req)
    except ValueError as exc:
        parser.error(str(exc))

    if not req.simulate and not shutil.which("ffmpeg"):
        print("vdl: ffmpeg is required to merge video and audio. Install it with:  "
              "brew install ffmpeg", file=sys.stderr)
        return EXIT_USAGE

    if (not looking_only and not req.cookies_from_browser and not req.cookies_file
            and any(is_bilibili(u) for u in urls)):
        print("note: Bilibili may hold back its top quality tiers from logged-out "
              "viewers. Add --cookies-from-browser chrome if a download comes out "
              "lower than expected.\n", file=sys.stderr)

    if not looking_only:
        req.output_dir.mkdir(parents=True, exist_ok=True)

    import yt_dlp
    from yt_dlp.postprocessor.common import PostProcessor
    from yt_dlp.utils import DownloadError

    summary = RunSummary()
    errors: list[str] = []
    current_url = ""

    def on_file(info: dict) -> None:
        """Record a finished file and catch one shorter than the video.

        Bilibili hands logged-out viewers a 10-30 second preview of some
        videos without any error or warning, so the length is the only tell.
        """
        path = info.get("filepath")
        if not path:
            return
        summary.saved.append(path)
        expected, actual = info.get("duration"), probe_duration(path)
        if is_truncated(expected, actual):
            summary.failed.append((current_url, (
                f"only {format_duration(actual)} of {format_duration(expected)} "
                f"was saved: the site served a preview ({Path(path).name})")))

    class CheckSaved(PostProcessor):
        # Runs once per file after it is moved to its final name, so the path
        # is the real one (after merging and audio extraction).
        def __init__(self, downloader):
            super().__init__(downloader)
            self.on_file = on_file

        def run(self, info):
            self.on_file(info)
            return [], info

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.add_post_processor(CheckSaved(ydl), when="after_move")
            ydl.add_info_extractor(BiliBiliIE())

            # yt-dlp prints errors itself and, with ignoreerrors, returns None
            # instead of raising; keep the text so the summary can explain it.
            def remember_error(report_error):
                def wrapper(message, *a, **kw):
                    errors.append(str(message))
                    return report_error(message, *a, **kw)
                return wrapper

            ydl.report_error = remember_error(ydl.report_error)

            for url in urls:
                current_url = url
                for attempt in range(len(RETRY_DELAYS) + 1):
                    errors.clear()
                    saved_before = len(summary.saved)
                    try:
                        process_url(ydl, url, args, req, summary, saved_before)
                        break
                    except (LookupFailed, DownloadError) as exc:
                        raw = errors[-1] if errors else str(exc)
                        message = re.sub(r"\x1b\[[0-9;]*m", "", raw).removeprefix("ERROR: ")
                        if is_transient(message) and attempt < len(RETRY_DELAYS):
                            delay = RETRY_DELAYS[attempt]
                            print(f"  site refused the request, retrying in {delay}s "
                                  f"({attempt + 1}/{len(RETRY_DELAYS)})...", file=sys.stderr)
                            time.sleep(delay)
                            continue
                        summary.failed.append((url, message))
                        break
    except KeyboardInterrupt:
        print("\nStopped. Partial files are kept and will resume next run.", file=sys.stderr)
        if not looking_only:
            remove_empty_dirs(req.output_dir)
            print_summary(summary, req.output_dir)
        return EXIT_INTERRUPTED

    if looking_only:
        print_failures(summary)
    else:
        remove_empty_dirs(req.output_dir / ".partial")
        remove_empty_dirs(req.output_dir)
        print_summary(summary, req.output_dir)
    return EXIT_FAILED if summary.failed else EXIT_OK


def remove_empty_dirs(root: Path) -> None:
    """Delete root and its subfolders if they hold no files (resumable parts are kept)."""
    if not root.is_dir():
        return
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass
    try:
        root.rmdir()
    except OSError:
        pass


def print_summary(summary: RunSummary, output_dir: Path) -> None:
    if summary.saved:
        print(f"\nSaved {len(summary.saved)} file(s) under {output_dir}:")
        for filepath in summary.saved:
            try:
                shown = Path(filepath).relative_to(output_dir)
            except ValueError:
                shown = filepath
            print(f"  {shown}")
    print_failures(summary)


def print_failures(summary: RunSummary) -> None:
    for url, reason in summary.failed:
        hint = hint_for(reason)
        print(f"\nFAILED: {url}\n  {reason}" + (f"\n{hint}" if hint else ""), file=sys.stderr)


def main() -> int:
    return run()


if __name__ == "__main__":
    sys.exit(main())
