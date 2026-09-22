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

## The Android build

The APK ships the same kind of thing: separate programs, cross-compiled for
arm64 by the scripts in `build/android/`. They travel as `lib*.so` only because
Android refuses to execute a file from anywhere else; each one is an ordinary
executable and is still run as its own process.

| Program | In the APK | Licence | Source |
|---|---|---|---|
| aria2 1.37.0 | `libaria2c.so` | GPL-2.0-or-later (with OpenSSL exception) | as above, with the same patch |
| FFmpeg 8.0, ffprobe | `libffmpeg.so`, `libffprobe.so` | LGPL-2.1-or-later (see below) | https://github.com/FFmpeg/FFmpeg at tag `n8.0` |
| QuickJS-ng | `libquickjs.so` | MIT | https://github.com/quickjs-ng/quickjs |
| gallery-dl 1.32.13 | `gallery-dl/`, run by the APK's own Python (see below) | GPL-2.0-only | https://github.com/mikf/gallery-dl at tag `v1.32.13` |

Statically linked into those:

| Library | Licence | Goes into |
|---|---|---|
| OpenSSL 3.5.4 | Apache-2.0 | aria2, FFmpeg |
| LAME 3.100 | LGPL-2.0-or-later | FFmpeg, for MP3 |
| dav1d 1.5.1 | BSD-2-Clause | FFmpeg, for AV1 |
| zlib, expat, c-ares | Zlib, MIT, MIT | aria2 |

Unlike the Windows build, none of these come from someone else's release page:
`build/android/build_aria2.sh` and `build/android/build_tools.sh` fetch those
exact versions and compile them, so the source of every part is one script away.

The Android FFmpeg is **not** a GPL build — it is configured without
`--enable-gpl`, which leaves it LGPL-2.1-or-later. Because OpenSSL 3 is linked
in, the combination is offered under the LGPL's version 3 terms, which
Apache-2.0 permits. Everything in that binary is free software under a licence
that allows relinking, and the script above is what relinks it.

The Python side of the APK contains:

| Library | Licence | Source |
|---|---|---|
| CPython 3.14 | PSF-2.0 | built by python-for-android |
| Kivy, pyjnius, python-for-android | MIT | https://kivy.org |
| SDL2, SDL2_image, SDL2_mixer, SDL2_ttf | Zlib | https://libsdl.org |
| yt-dlp, yt-dlp-ejs | Unlicense | https://github.com/yt-dlp |
| certifi | MPL-2.0 | https://github.com/certifi/python-certifi |
| websockets | BSD-3-Clause | https://github.com/python-websockets/websockets |
| requests, urllib3, idna, charset-normalizer, filetype, six | Apache-2.0 / MIT | dependencies of Kivy |

gallery-dl is kept apart from Grabbit's code on the phone as it is on Windows.
It is a Python program, so it travels as its source's compiled form rather than
as a binary: in a folder of its own with the three libraries it needs, off the
app's import path. The APK carries a standalone Python interpreter
(`libpythonbin.so`), and Grabbit runs gallery-dl as its own process with it,
passing it a link on the command line and reading back the JSON it prints.
Grabbit never imports it. `build/android/build_apk.sh` names the exact
versions, whose source PyPI and the projects' repositories publish.

| Library, in `gallery-dl/` | Licence | Source |
|---|---|---|
| requests 2.34.2 | Apache-2.0 | https://github.com/psf/requests |
| urllib3 2.8.0 | MIT | https://github.com/urllib3/urllib3 |
| idna 3.20 | BSD-3-Clause | https://github.com/kjd/idna |

## Qt (LGPL-3.0)

Qt is dynamically linked and its libraries sit next to the executable in
`_internal/`. The LGPL's relinking requirement is met by this repository: the
build scripts rebuild `Grabbit.exe` against whatever Qt version is installed in
the build environment.
