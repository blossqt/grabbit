# Grabbit

A paste-a-link downloader for Windows. One window, one box: drop in a YouTube
or TikTok link, an Instagram post, a magnet link or a plain file URL, and it
downloads. Photo carousels open a picker so you choose which slides you want.

The download engine is **aria2 1.37.0, compiled from source** (`aria2/`), driven
over its JSON-RPC interface. Site support comes from **yt-dlp**, with FFmpeg for
merging video and audio and Deno for YouTube's JavaScript challenges.

## Getting it

Download the zip from [Releases](../../releases), unpack it anywhere, and run
`Grabbit.exe`. Windows x64 only.

## Running it

    dist\Grabbit\Grabbit.exe

Everything it needs sits next to the exe in `tools\`; there is nothing to
install. Move the whole `Grabbit` folder wherever you like.

Settings, the download list and torrent metadata live in
`%LOCALAPPDATA%\Grabbit`. Dropping an empty `portable.txt` next to the exe keeps
that data in a `data\` folder beside it instead.

## What it handles

| You paste | What happens |
|---|---|
| YouTube, TikTok, Twitch, Vimeo… (the ~1,700 sites yt-dlp reads) | Preview card with quality and container pickers; video and audio are fetched separately and merged |
| Instagram post or carousel | Grid of every slide — click to include or exclude, double-click for a full-size preview |
| TikTok photo post | Same grid, plus the post's soundtrack as an optional item |
| YouTube playlist / channel | Every entry as a tickable thumbnail |
| Photo-first sites (X, Pinterest, Tumblr, imgur, DeviantArt, Bluesky, Wikimedia…) | Handed to gallery-dl when yt-dlp finds no video, and shown in the same picker |
| Any other web page | Grabbit reads the page itself and lists the videos, images, audio and documents it links to |
| `magnet:` link | Fetches the file list from the swarm, then asks which files to download |
| `.torrent` file or URL | File picker, then downloads and seeds |
| `.metalink` / `.meta4` | Every file, downloaded from all its mirrors at once and checked against its checksum |
| Direct file link | Downloads it with up to 16 connections |
| Anything else | Says so, and offers to save the page as a file |

## Watching before you download

Video cards have a **Watch** button, and any video in the list has **Watch** in
its right-click menu. It opens the full video in your own player — paused on the
first frame — so you can check it is the right thing before committing to a
download. Finished files play from disk; anything else streams.

Players cannot normally send the `Referer` and cookies these streams need, so
Grabbit serves the video from `127.0.0.1` and adds the headers itself, passing
range requests through so seeking still works. When a site only offers separate
video and audio tracks (most of YouTube), FFmpeg joins them as they play.

mpv, VLC, PotPlayer and MPC are started directly and told to start paused;
anything else gets a one-line playlist opened with your default player.

The transfer list has the usual qBittorrent furniture: status and type filters
with counts, sortable columns, progress bars, speed and ETA, per-download
details (files, peers, trackers, log), speed limits you can click in the status
bar, a tray icon, drag-and-drop, and a prompt when you copy a link.

## Rebuilding

Needs Python 3.12 and about 3 GB of disk for the toolchain. From the project
folder:

    powershell -ExecutionPolicy Bypass -File build\bootstrap.ps1     # aria2 source + MSYS2 + venv + ffmpeg + deno + gallery-dl
    set MSYSTEM=UCRT64 & set CHERE_INVOKING=1
    %LOCALAPPDATA%\GrabbitBuild\msys64\usr\bin\bash.exe -l build\build_aria2.sh
    powershell -ExecutionPolicy Bypass -File build\build_app.ps1     # -> dist\Grabbit\Grabbit.exe

`bootstrap.ps1` puts the compiler, the Python venv and the bundled binaries in
`%LOCALAPPDATA%\GrabbitBuild`, which you can delete once the build is done.

Checks that exercise the engine against real downloads:

    %LOCALAPPDATA%\GrabbitBuild\venv\Scripts\python.exe build\selftest.py --all

And the window itself — filters, speed graph, details tabs — offscreen,
with invented downloads, in a couple of seconds:

    %LOCALAPPDATA%\GrabbitBuild\venv\Scripts\python.exe build\ui_check.py

## The Android build

The APK carries the same engine — aria2, yt-dlp, FFmpeg — cross-compiled for
arm64, with a Kivy interface instead of Qt and QuickJS in place of Deno, which
has no Android target. Building it needs WSL (Ubuntu), a JDK, and roughly 15 GB:

    bash build/android/fetch_sdk.sh        # NDK, SDK command-line tools, adb
    bash build/android/build_aria2.sh      # aria2 + OpenSSL, zlib, expat, c-ares
    bash build/android/build_tools.sh      # QuickJS, LAME, FFmpeg and ffprobe
    bash build/android/setup_p4a.sh        # python-for-android
    bash build/android/build_apk.sh        # -> dist/android/Grabbit-*.apk

No Linux to hand? **Actions → Android APK → Run workflow** runs those same
five scripts on a GitHub runner and leaves the APK as a build artifact.
The compiled binaries are cached between runs, so only the first one takes
the full hour; tick *rebuild_natives* to compile them again anyway.

Then from Windows, with the phone plugged in and USB debugging on:

    powershell -File build\android\install_apk.ps1
    powershell -File build\android\device_test.ps1 -Grant

`device_test.ps1` is the phone's answer to `selftest.py`: it starts the engine
inside the app through `run-as` and downloads real things — a video, a TikTok,
a photo post, a GIF — reporting each one. It needs neither the screen nor the
lock code, so it can run while the phone sits on the desk.

The phone engine is plain Python, so it also runs on a desktop against the
desktop binaries — faster still, and enough to catch most mistakes:

    %LOCALAPPDATA%\GrabbitBuild\venv\Scripts\python.exe build\android\test_engine.py

So does the phone interface. `preview_ui.py` runs it in a phone-shaped
window against invented downloads, and `--check` taps through it and
reports what happened — which is where interface mistakes are cheapest
to find. It needs Kivy in the build venv (`pip install "kivy[base]"`):

    %LOCALAPPDATA%\GrabbitBuild\venv\Scripts\python.exe build\android\preview_ui.py
    %LOCALAPPDATA%\GrabbitBuild\venv\Scripts\python.exe build\android\preview_ui.py --check

Two facts about phones shape the code. Android 10 and later refuse to execute
anything from an app's data directory, so the binaries travel as `lib*.so` and
run from the native library directory, which stays executable. And Android 11
and later keep apps out of the shared Downloads folder without an explicit
grant, so Grabbit asks once and otherwise saves into its own folder — still
reachable over USB, just not listed in the Downloads app.

## Updates and releases

Both apps ask GitHub for the latest release a minute after they start and twice
a day after that; the phone also asks when it comes back from a long pause. When
it is newer than the copy that is running, the desktop shows a banner that
downloads the new zip, and the phone one that downloads the new APK — which
installs over the old app and keeps everything in it. **Help › Check for
updates** asks straight away; on the phone, touch the version line at the
bottom. **Settings › Interface** turns the automatic checks off.

To publish a release:

    powershell -ExecutionPolicy Bypass -File build\release.ps1 --version 1.3.0 --notes "What's new"

It makes sure the work is committed and pushed, runs the checks, builds the
Windows zip, takes the APK for the same commit from the Android workflow
(starting a build if there is none), refuses an APK signed with anything but
the release key, and asks before publishing. `--dry-run` does everything short
of publishing; `--no-build` reuses the Windows build already in `dist\Grabbit`.

Every APK is signed with one key, because Android only installs an update that
is signed like the app it replaces. `build/android/make_signing_key.py` made it
once: it lives in the repository's `ANDROID_KEYSTORE_B64` secret and in
`~\.grabbit\android\release.keystore`, and its public fingerprint is pinned in
`build/android/signing-sha256.txt`, which every build and every release is held
to. **Back the keystore up** — without it no installed copy can be updated
again. If the secret is ever lost, `make_signing_key.py --upload` puts it back.

The update checker has its own checks, against a stand-in for GitHub and then
the real thing:

    %LOCALAPPDATA%\GrabbitBuild\venv\Scripts\python.exe build\update_check.py

## Layout

    app/grabbit/        the application
      engine.py         aria2 process + task list + media jobs
      aria2rpc.py       JSON-RPC client and process supervision
      media.py          yt-dlp probing and downloads (streams routed through aria2)
      extractors.py     Instagram photo slides, TikTok photo posts
      gallerydl.py      photo-first sites, via gallery-dl
      pagescrape.py     reads media straight off an ordinary page
      analyze.py        works out what a pasted link is
      torrentmeta.py    .torrent / magnet parsing
      metalink.py       .metalink / .meta4 (mirrors + checksums)
      streamserver.py   serves a stream on localhost so any player can open it
      player.py         finds and launches your video player
      associations.py   magnet / .torrent registration (per-user, no admin)
      speeds.py         the speed history both graphs draw
      updates.py        whether a newer release is out (both apps ask it)
      ui/               Qt interface (speedgraph.py is the graph pane)
    android/            the phone build
      main.py           Kivy interface
      grabbit_mobile/   the same engine without Qt, plus Android's storage rules
        ui/             the desktop layout, folded into one column
    aria2/              aria2's source, fetched by bootstrap.ps1 (not in git)
    build/              build scripts, patches, self-test, release.ps1
      android/          cross-compilers for arm64 and the APK build
    vendor/aria2c.exe   the compiled engine
    dist/Grabbit/       the finished app

## Why downloads re-read the page

The preview and the download deliberately do **not** share extracted URLs. Some
sites (TikTok is one) tie their media URLs to the cookies of the session that
handed them out, so a URL captured during the preview gives a 403 when a
different session fetches it. Every download re-reads the page in the session
that will fetch the bytes; the preview's copy is only a fallback for when the
page cannot be read a second time. This is also why the local stream server
carries the session's cookies.

## Two fixes made along the way

**aria2 ignored DHT bootstrap replies.** `build/patches/0001-dht-optional-token.patch`
makes the `token` in a `get_peers` reply optional. The live bootstrap routers
answer with a node list and no token, and aria2 was rejecting those replies
wholesale, so a fresh routing table could never bootstrap and magnet links with
no working tracker found no peers. With the patch, the same magnet went from 0
peers to metadata in about a minute.

**IPv6 that does not work.** If a machine has no route to the IPv6 internet,
aria2 tries a tracker's IPv6 address, gets an instant "network unreachable" and
gives up before its IPv4 fallback runs — so torrents sit at zero peers. Grabbit
tests for real IPv6 connectivity at startup and disables it when there is none
(Options → Advanced → IPv6 to override).

## Worth knowing

- **YouTube needs Deno**, which is bundled. Without a JavaScript runtime yt-dlp
  can only reach some formats.
- **No account needed** for public posts on YouTube, TikTok (video and photo),
  Instagram and the rest. Cookies are only for genuinely private or login-walled
  content: Options → Videos & photos, where Firefox is the reliable choice
  because Chrome and Edge lock their cookie database while running.
- **Reddit** blocks anonymous requests from some networks outright ("blocked by
  network security"), which no downloader can work around; cookies or a
  different connection are the only fix.
- **Magnet links:** Options → Interface can make Grabbit the handler, so magnet
  links anywhere in Windows open here. Grabbit is also added to the "Open with"
  list for `.torrent` files without taking the association from another app.
- **Torrent speeds** improve a lot if you forward the listening port (Options →
  BitTorrent) — incoming peer connections are blocked by default on Windows.
- **Sites change.** yt-dlp is the part that ages; update it with
  `pip install -U yt-dlp` in the build venv and rebuild to refresh the bundle.

## Licences

Grabbit's own code is MIT (see `LICENSE`). The programs it ships in `tools/` —
aria2, FFmpeg, gallery-dl, Deno — each keep their own licence and run as
separate processes rather than being linked in. gallery-dl in particular is
GPL-2.0-only and is called through its command line for exactly that reason.
`THIRD-PARTY-NOTICES.md` lists every one of them, where its source is, and what
the aria2 patch changes.
