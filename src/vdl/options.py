"""Turn command-line choices into yt-dlp options.

Kept free of I/O so every combination can be unit tested without touching
the network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360")
AUDIO_FORMATS = ("mp3", "m4a", "opus", "flac", "wav")
BROWSERS = ("chrome", "safari", "firefox", "edge", "brave", "chromium", "opera", "vivaldi")

# Files land in a folder per channel; the id keeps re-uploads with the same
# title from overwriting each other. .150B caps the title at 150 bytes so long
# CJK titles stay under the filesystem's 255-byte limit.
SINGLE_TEMPLATE = "%(uploader,channel|Unknown)s/%(title).150B [%(id)s].%(ext)s"
PLAYLIST_TEMPLATE = (
    "%(playlist_title,playlist_id|Playlist)s/"
    "%(playlist_index|0)03d - %(title).150B [%(id)s].%(ext)s"
)


@dataclass
class DownloadRequest:
    output_dir: Path
    quality: str = "best"
    audio_only: bool = False
    audio_format: str = "mp3"
    raw_format: Optional[str] = None
    playlist: bool = False
    subtitles: list[str] = field(default_factory=list)
    cookies_from_browser: Optional[str] = None
    cookies_file: Optional[Path] = None
    archive_file: Optional[Path] = None
    rate_limit: Optional[str] = None
    # None leaves the system proxy in effect; "" forces a direct connection.
    proxy: Optional[str] = None
    deno_path: Optional[str] = None
    simulate: bool = False
    verbose: bool = False


def format_selector(quality: str, audio_only: bool) -> str:
    """yt-dlp format string for the requested quality.

    Prefers separate best video + best audio (how YouTube and Bilibili serve
    anything above 360p) and falls back to a single combined file.
    """
    if audio_only:
        return "bestaudio/best"
    if quality not in QUALITIES:
        raise ValueError(f"quality must be one of {', '.join(QUALITIES)}")
    if quality == "best":
        return "bv*+ba/b"
    height = int(quality)
    # The last alternative keeps the download from failing outright when a
    # video has nothing at or below the cap (e.g. a vertical short).
    return f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b"


def parse_rate(rate: str) -> int:
    """'2M' -> 2097152 bytes/s. Accepts K, M, G suffixes or a plain number."""
    text = rate.strip().upper().removesuffix("/S").removesuffix("B")
    multipliers = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}
    factor = 1
    if text and text[-1] in multipliers:
        factor = multipliers[text[-1]]
        text = text[:-1]
    try:
        value = int(float(text) * factor)
    except ValueError:
        raise ValueError(f"not a valid rate: {rate} (try 500K or 2M)") from None
    if value <= 0:
        raise ValueError(f"not a valid rate: {rate} (try 500K or 2M)")
    return value


def build_ydl_opts(req: DownloadRequest) -> dict[str, Any]:
    template = PLAYLIST_TEMPLATE if req.playlist else SINGLE_TEMPLATE
    fmt = req.raw_format or format_selector(req.quality, req.audio_only)

    opts: dict[str, Any] = {
        "format": fmt,
        # Fragments are written to .partial so an interrupted download never
        # leaves a half-file that looks finished next to the real ones.
        "paths": {"home": str(req.output_dir), "temp": str(req.output_dir / ".partial")},
        "outtmpl": template,
        "noplaylist": not req.playlist,
        "windowsfilenames": True,
        "retries": 10,
        "fragment_retries": 10,
        "continuedl": True,
        "concurrent_fragment_downloads": 4,
        # Keep going past a dead playlist item; failures are counted by the CLI.
        "ignoreerrors": "only_download",
        "simulate": req.simulate,
        "quiet": not req.verbose,
        "noprogress": False,
        "postprocessors": [],
    }

    if not req.verbose and not req.simulate:
        # quiet hides yt-dlp's own chatter, so say what is being fetched.
        opts["forceprint"] = {"before_dl": ["Downloading: %(title)s"]}

    if req.audio_only:
        opts["postprocessors"].append({
            "key": "FFmpegExtractAudio",
            "preferredcodec": req.audio_format,
            "preferredquality": "0",
        })
    else:
        opts["merge_output_format"] = "mp4"
        if not req.raw_format:
            # H.264/AAC plays everywhere (QuickTime, iPhone, TVs); AV1 and VP9
            # don't. Height and frame rate still win, so quality isn't lost.
            opts["format_sort"] = ["res", "fps", "vcodec:h264", "acodec:aac"]

    opts["postprocessors"].append({"key": "FFmpegMetadata", "add_metadata": True})

    if req.subtitles:
        opts.update({
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": req.subtitles,
            "subtitlesformat": "srt/best",
        })
        opts["postprocessors"].append({"key": "FFmpegSubtitlesConvertor", "format": "srt"})

    if req.cookies_from_browser:
        opts["cookiesfrombrowser"] = (req.cookies_from_browser,)
    if req.cookies_file:
        opts["cookiefile"] = str(req.cookies_file)
    if req.archive_file:
        opts["download_archive"] = str(req.archive_file)
    if req.rate_limit:
        opts["ratelimit"] = parse_rate(req.rate_limit)
    if req.deno_path:
        opts["js_runtimes"] = {"deno": {"path": req.deno_path}}
    if req.proxy is not None:
        opts["proxy"] = req.proxy

    return opts
