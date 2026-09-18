# Third-party software

Grabbit's own code is MIT (see LICENSE). The release archive also contains
several independent programs, each under its own licence. Grabbit starts them
as separate processes and talks to them over a network port, a command line or
standard output — none of them is linked into Grabbit's code.

| Program | Where it is | Licence | Source |
|---|---|---|---|
| aria2 1.37.0 | `tools/aria2c.exe` | GPL-2.0-or-later (with OpenSSL exception) | https://github.com/aria2/aria2 plus the patch below |
| FFmpeg | `tools/ffmpeg/` | GPL-3.0-or-later (this is a GPL build) | https://github.com/yt-dlp/FFmpeg-Builds |
| gallery-dl | `tools/gallery-dl.exe` | GPL-2.0-only | https://github.com/mikf/gallery-dl |
| Deno | `tools/deno/deno.exe` | MIT | https://github.com/denoland/deno |

Bundled into `Grabbit.exe` itself:

| Library | Licence | Source |
|---|---|---|
| yt-dlp | Unlicense (public domain) | https://github.com/yt-dlp/yt-dlp |
| Qt for Python (PySide6) and Qt | LGPL-3.0 | https://download.qt.io/official_releases/QtForPython/ |
| CPython and its bundled modules | PSF-2.0 | https://www.python.org/ |

## Changes made to aria2

`build/patches/0001-dht-optional-token.patch` modifies aria2's source. That
patch is a derivative of aria2 and is therefore GPL-2.0-or-later, like aria2
itself, not MIT. It makes the `token` field of a DHT `get_peers` reply
optional, so the live bootstrap routers (which answer with a node list and no
token) can be used at all; without it a fresh DHT routing table can never
bootstrap. `build/build_aria2.sh` applies it before compiling.

## Getting the sources

The GPL programs above are shipped unmodified except for aria2, whose one
change is the patch in this repository. To rebuild any of them:

- **aria2** — `build/bootstrap.ps1` fetches the exact upstream release used
  here, and `build/build_aria2.sh` applies the patch and compiles it. Both the
  upstream source and the change are therefore available to anyone with this
  repository.
- **FFmpeg** — the binaries come straight from the yt-dlp FFmpeg-Builds release
  linked above, which publishes the source and build scripts for each build.
- **gallery-dl** — the binary is the project's own published Windows build,
  from the source repository linked above.

If you would rather not ship the GPL programs at all, delete `tools/` from a
release: Grabbit runs without them, losing torrents (aria2), video and audio
merging (FFmpeg) and photo-first sites (gallery-dl).

## Qt (LGPL-3.0)

Qt is dynamically linked and its libraries sit next to the executable in
`_internal/`. The LGPL's relinking requirement is met by this repository: the
build scripts rebuild `Grabbit.exe` against whatever Qt version is installed in
the build environment.
