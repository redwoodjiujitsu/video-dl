# vdl

Download videos from YouTube, Bilibili and 1,000+ other sites from the
command line. Built on [yt-dlp](https://github.com/yt-dlp/yt-dlp).

```sh
vdl "https://www.youtube.com/watch?v=jNQXAC9IVRw"
```

Files go to `~/Downloads/Videos/<channel>/<title> [<id>].mp4`.

**Always put the URL in quotes.** zsh treats `?` and `&` as special characters
and will mangle an unquoted YouTube link.

## Common uses

| What you want | Command |
|---|---|
| Best quality video | `vdl "URL"` |
| Cap the quality | `vdl "URL" -q 1080` (also 2160, 1440, 720, 480, 360) |
| Audio only (mp3) | `vdl "URL" -x` (or `--audio-format m4a`, opus, flac, wav) |
| Several videos | `vdl "URL1" "URL2" "URL3"` |
| A list from a file | `vdl -a links.txt` (one URL per line, `#` for comments) |
| Whole playlist / multi-part Bilibili video | `vdl "URL" -p` |
| Subtitles | `vdl "URL" -s en,zh-Hans` |
| Somewhere else | `vdl "URL" -o ~/Movies` |
| Look before downloading | `vdl "URL" --info` or `vdl "URL" -F` (list qualities) |
| Skip videos already downloaded | `vdl -a links.txt --archive done.txt` |
| Slow it down | `vdl "URL" --limit-rate 2M` |

Run `vdl --help` for everything.

## Bilibili

Logged-out viewers get lower quality, and some videos only give them a
10–30 second **preview**. vdl compares each saved file's real length with the
video's length and reports a preview as `FAILED` rather than letting it look
like a success.

vdl also handles two Bilibili problems that yt-dlp doesn't yet (October 2026):

- Links with a language in them (`/zh-Hans/video/BV...`) are rewritten to the
  plain `/video/BV...` form, and click-tracking parameters are dropped.
- When Bilibili sends a page with the video data stripped out ("Unable to
  extract initial state"), vdl gets the same data from Bilibili's API. Run
  with `-v` to see when this happens.

To get full videos and full quality, log in to bilibili.com in your browser,
then:

```sh
vdl "https://www.bilibili.com/video/BV..." --cookies-from-browser chrome
```

(`safari`, `firefox`, `edge` and `brave` also work. Safari may ask macOS for
permission the first time.) Videos for paid members still need a paid account.

## When something fails

vdl keeps going when one link fails, retries sites that briefly refuse
requests (Bilibili's HTTP 412, YouTube's 429), and prints a `FAILED` line with
a hint for each problem at the end. It exits with status 1 if anything failed.

- **Proxy errors:** if a system proxy in System Settings > Network >
  (your network) > Details > Proxies stops responding, add `--proxy ""` to
  connect directly.
- **"Sign in to confirm you're not a bot" / age-restricted:** add
  `--cookies-from-browser chrome`.
- **Downloads suddenly break on a site that used to work:** sites change their
  players often. Update yt-dlp first:
  ```sh
  ~/Downloads/video-dl/.venv/bin/pip install -U "yt-dlp[default]"
  ```
- **Anything else:** rerun with `-v` to see every step yt-dlp takes.

Pressing Ctrl-C stops cleanly. Partial downloads are kept in `.partial` and
resume on the next run.

## Setup

Requires Python 3.9+ and ffmpeg (`brew install ffmpeg`). YouTube also needs a
JavaScript runtime; deno is installed from PyPI as a dependency (Homebrew
can't build it on macOS 13).

```sh
cd ~/Downloads/video-dl
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
ln -s "$PWD/.venv/bin/vdl" /usr/local/bin/vdl
```

Tests run without network access:

```sh
.venv/bin/python -m pytest
```

Only download videos you have the right to save; check each site's terms.
