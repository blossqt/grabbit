"""yt-dlp integration: inspecting links, and downloading media through aria2.

A media download is driven by yt-dlp (it picks formats, merges video+audio,
converts audio, embeds thumbnails), but the actual bytes of plain http(s)
streams are fetched by the same aria2 process that handles torrents and file
downloads, through Aria2BridgeFD. Fragmented streams (HLS/DASH) stay with
yt-dlp's own downloader, which knows how to reassemble them.
"""

import itertools
import logging
import os
import subprocess
import threading
import time

from yt_dlp import YoutubeDL
from yt_dlp.downloader.common import FileDownloader
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.utils import DownloadCancelled, determine_protocol, traverse_obj

from . import extractors
from .mediaitems import MediaItem, ProbeResult
from .paths import find_tool
from .util import CREATE_NO_WINDOW, safe_filename, site_name

log = logging.getLogger(__name__)

PROBE_TIMEOUT = 120
INFO_FRESH_SECONDS = 3600  # media URLs go stale; re-extract after an hour

# What to suggest when a site refuses an anonymous download. The phone, which
# has no cookies to offer, clears it (grabbit_mobile/engine.py).
REFUSED_HINT = ('Options › Videos & photos › "Use cookies from" '
                '(Firefox works best) usually fixes this.')


class JobCancelled(DownloadCancelled):
    """The user removed the download."""


class JobPaused(DownloadCancelled):
    """The user paused a download that cannot be paused in place."""


# --------------------------------------------------------------------------- yt-dlp setup

class _Logger:
    """Feeds yt-dlp's chatter into a callback (per-task log)."""

    def __init__(self, sink=None):
        self.sink = sink

    def _write(self, message):
        if self.sink and message:
            try:
                self.sink(str(message))
            except Exception:
                pass

    def debug(self, message):
        if not str(message).startswith('[debug] '):
            self._write(message)

    def info(self, message):
        self._write(message)

    def warning(self, message):
        self._write(f'Warning: {message}')

    def error(self, message):
        self._write(str(message))


def base_params(settings, log_sink=None) -> dict:
    params = {
        'quiet': True,
        'no_warnings': False,
        'noprogress': True,
        'no_color': True,
        'logger': _Logger(log_sink),
        'noplaylist': True,
        'socket_timeout': 30,
        'retries': 5,
        'fragment_retries': 10,
        'concurrent_fragment_downloads': 8,
        'windowsfilenames': True,
        'continuedl': True,
        'overwrites': False,
        'ignoreerrors': False,
        'extractor_args': {},
    }
    deno = find_tool('deno')
    quickjs = find_tool('quickjs')
    if deno:
        params['js_runtimes'] = {'deno': {'path': deno}}
    elif quickjs:
        # The phone build carries QuickJS instead - Deno has no Android target.
        # Slower at YouTube's challenges, but it is a real JS engine.
        params['js_runtimes'] = {'quickjs': {'path': quickjs}}
    ffmpeg = find_tool('ffmpeg')
    if ffmpeg:
        params['ffmpeg_location'] = os.path.dirname(ffmpeg)
    if getattr(settings, 'user_agent', ''):
        params['http_headers'] = {'User-Agent': settings.user_agent}
    if getattr(settings, 'proxy', ''):
        params['proxy'] = settings.proxy
    cookies_file = getattr(settings, 'cookies_file', '')
    browser = getattr(settings, 'cookies_browser', '')
    if cookies_file and os.path.isfile(cookies_file):
        params['cookiefile'] = cookies_file
    elif browser:
        params['cookiesfrombrowser'] = (browser, None, None, None)
    return params


def make_ydl(settings, extra: dict | None = None, log_sink=None) -> YoutubeDL:
    params = base_params(settings, log_sink)
    params.update(extra or {})
    ydl = GrabbitYDL(params, auto_init='no_verbose_header')
    extractors.register(ydl)
    return ydl


def format_selection(quality: str, container: str) -> dict:
    """yt-dlp params for a quality preset from the settings/UI."""
    params: dict = {}
    postprocessors: list[dict] = []

    if quality == 'frame':
        # One picture, read from the sharpest stream there is (frames.py).
        from .frames import FULL_FORMAT
        params['format'] = FULL_FORMAT
        params['_postprocessors'] = postprocessors
        return params
    if quality == 'gif':
        # No audio in a GIF, so skip downloading it. 720p is plenty of detail
        # for something that gets scaled down and reduced to 256 colours.
        #
        # AV1 is asked for last: it has to be decoded to make a GIF, and a
        # software AV1 decoder is a large thing to carry on a phone for a
        # format that ends up with 256 colours anyway. Anything else will do
        # just as well here.
        params['format'] = ('bv*[vcodec!^=av01][height<=720]/b[vcodec!^=av01][height<=720]'
                            '/bv*[vcodec!^=av01]/b[vcodec!^=av01]'
                            '/bv*[height<=720]/b[height<=720]/bv*/b')
    elif quality == 'audio_mp3':
        params['format'] = 'ba/b'
        postprocessors.append({'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3',
                               'preferredquality': '0', 'nopostoverwrites': False})
    elif quality == 'audio_m4a':
        params['format'] = 'ba[ext=m4a]/ba/b'
        postprocessors.append({'key': 'FFmpegExtractAudio', 'preferredcodec': 'm4a',
                               'preferredquality': '0', 'nopostoverwrites': False})
    elif quality and quality != 'best' and quality.isdigit():
        height = int(quality)
        params['format'] = (f'bv*[height<={height}]+ba/b[height<={height}]'
                            f'/bv*[height<={height}]/bv*+ba/b')
    else:
        params['format'] = 'bv*+ba/b'

    if not quality.startswith('audio'):
        if container == 'mp4':
            params['format_sort'] = ['res', 'ext:mp4:m4a']
            params['merge_output_format'] = 'mp4'
        elif container == 'mkv':
            params['merge_output_format'] = 'mkv'
    params['_postprocessors'] = postprocessors
    return params


def download_params(settings, save_dir: str, quality: str | None = None,
                    container: str | None = None) -> dict:
    quality = quality or settings.video_quality
    container = container or settings.video_container
    selection = format_selection(quality, container)
    postprocessors = selection.pop('_postprocessors')
    is_audio = quality.startswith('audio')
    if quality in ('gif', 'frame'):
        # Cover art, chapters and subtitles mean nothing in a GIF or a picture.
        return {'paths': {'home': save_dir},
                'outtmpl': {'default': settings.filename_template},
                'noplaylist': True, 'postprocessors': [], **selection}

    params = {
        'paths': {'home': save_dir},
        'outtmpl': {'default': settings.filename_template},
        'noplaylist': True,
        **selection,
    }
    if settings.download_subtitles and not is_audio:
        params.update({
            'writesubtitles': True,
            'writeautomaticsub': True,
            'subtitleslangs': [s.strip() for s in settings.subtitle_langs.split(',') if s.strip()],
        })
        postprocessors.append({'key': 'FFmpegEmbedSubtitle', 'already_have_subtitle': False})
    if settings.embed_metadata:
        postprocessors.append({'key': 'FFmpegMetadata', 'add_metadata': True, 'add_chapters': True})
    if settings.embed_thumbnail:
        params['writethumbnail'] = True
        postprocessors.append({'key': 'EmbedThumbnail', 'already_have_thumbnail': False})
    params['postprocessors'] = postprocessors
    return params


# --------------------------------------------------------------------------- probing

def _pick_thumbnail(entry: dict, target: int = 480) -> tuple[str, str]:
    """(grid thumbnail, large preview) from an info dict."""
    thumbs = [t for t in (entry.get('thumbnails') or []) if t.get('url')]
    if not thumbs:
        single = entry.get('thumbnail') or ''
        return single, single
    sized = [t for t in thumbs if t.get('width')]
    if sized:
        grid = min(sized, key=lambda t: abs(int(t['width']) - target))['url']
        preview = max(sized, key=lambda t: int(t['width']))['url']
    else:
        grid = thumbs[-1]['url']
        preview = thumbs[-1]['url']
    return grid, preview


def _video_heights(entry: dict) -> list:
    heights = {f.get('height') for f in (entry.get('formats') or [])
               if f.get('height') and f.get('vcodec') not in (None, 'none')}
    return sorted((h for h in heights if h), reverse=True)


def _best_image_format(entry: dict) -> dict | None:
    formats = [f for f in (entry.get('formats') or []) if f.get('url')]
    if not formats:
        return None
    return max(formats, key=lambda f: (f.get('width') or 0) * (f.get('height') or 0))


def _item_from_entry(entry: dict, index: int, parent: dict | None = None) -> MediaItem:
    kind = entry.get('grabbit_kind') or ('audio' if entry.get('vcodec') == 'none' else 'video')
    if kind not in ('video', 'image', 'audio'):
        kind = 'video'
    grid, preview = _pick_thumbnail(entry)
    item = MediaItem(
        key=str(entry.get('id') or f'item{index}'),
        kind=kind,
        title=entry.get('title') or entry.get('alt_title') or f'Item {index}',
        thumbnail=grid,
        preview=preview,
        duration=entry.get('duration'),
        url=entry.get('webpage_url') or entry.get('url') or '',
        index=index,
        optional=bool(entry.get('grabbit_optional')),
    )
    if kind in ('image', 'audio'):
        best = _best_image_format(entry) if kind == 'image' else (entry.get('formats') or [None])[0]
        if best:
            item.direct_url = best.get('url', '')
            item.ext = best.get('ext') or ''
            item.width = best.get('width') or 0
            item.height = best.get('height') or 0
            item.filesize = best.get('filesize') or best.get('filesize_approx')
            headers = dict(entry.get('http_headers') or {})
            headers.update(best.get('http_headers') or {})
            item.headers = headers
        if kind == 'image' and item.direct_url:
            item.preview = item.direct_url
    else:
        item.heights = _video_heights(entry)
        item.filesize = entry.get('filesize') or entry.get('filesize_approx')

    if entry.get('formats') or entry.get('url') and entry.get('_type') not in ('url', 'url_transparent'):
        item.info = entry
    if parent:
        for key in ('extractor', 'extractor_key', 'webpage_url_basename', 'http_headers'):
            if key in parent and key not in entry:
                entry[key] = parent[key]
    return item


def probe(url: str, settings, max_entries: int = 400, log_sink=None) -> ProbeResult:
    """Inspect a link without downloading: what is it, and what's inside?"""
    result = ProbeResult(url=url, site=site_name(url))
    ydl = make_ydl(settings, {'skip_download': True, 'extract_flat': 'in_playlist'}, log_sink)
    try:
        with ydl:
            info = ydl.extract_info(url, download=False, process=False)
            for _ in range(4):  # follow short links / redirects (vm.tiktok.com etc.)
                if not isinstance(info, dict) or info.get('_type') not in ('url', 'url_transparent'):
                    break
                info = ydl.extract_info(info['url'], download=False, process=False,
                                        ie_key=info.get('ie_key'))
    except Exception as exc:
        result.error = _clean_error(exc)
        return result

    if not isinstance(info, dict):
        result.error = 'Nothing downloadable was found on that page'
        return result

    result.site = (info.get('extractor_key') or result.site or '').replace('Generic', '') or result.site
    result.title = info.get('title') or url
    result.uploader = (info.get('uploader') or info.get('channel') or info.get('uploader_id') or '')
    result.description = info.get('description') or ''
    result.is_live = bool(info.get('is_live'))
    result.thumbnail = _pick_thumbnail(info)[0]

    if info.get('_type') in ('playlist', 'multi_video'):
        entries = []
        for entry in itertools.islice(info.get('entries') or [], max_entries):
            if isinstance(entry, dict):
                entries.append(entry)
        result.items = [_item_from_entry(entry, i, info) for i, entry in enumerate(entries, 1)]
        if not result.items:
            result.error = 'This playlist is empty or needs an account'
            return result
        result.kind = 'gallery' if any(i.kind == 'image' for i in result.items) else 'playlist'
        if not result.thumbnail and result.items:
            result.thumbnail = result.items[0].thumbnail
    else:
        item = _item_from_entry(info, 1)
        result.items = [item]
        result.kind = 'gallery' if item.kind == 'image' else 'video'
        result.heights = item.heights
        if not result.thumbnail:
            result.thumbnail = item.thumbnail
    return result


# Prefer one stream a player can open by itself (and seek in); fall back to a
# separate video and audio track, which the stream server joins with ffmpeg.
PREVIEW_FORMAT = ('b[vcodec!=none][acodec!=none][protocol^=http]'
                  '/b[vcodec!=none][acodec!=none]'
                  '/bv*[protocol^=http]+ba[protocol^=http]'
                  '/bv*+ba/b/bv*')


def resolve_stream(url: str, settings, log_sink=None) -> dict:
    """Find a directly playable stream for a link, with the headers it needs.

    The URL is extracted here and played through Grabbit's local stream server,
    so it carries this session's cookies - the same reason downloads re-extract
    rather than reusing what the preview found.
    """
    ydl = make_ydl(settings, {'format': PREVIEW_FORMAT, 'skip_download': True,
                              'noplaylist': True}, log_sink)
    with ydl:
        info = ydl.extract_info(url, download=False)
        if info.get('_type') in ('playlist', 'multi_video'):
            entries = [e for e in (info.get('entries') or []) if isinstance(e, dict)]
            if not entries:
                raise RuntimeError('Nothing playable on that page')
            info = entries[0]
        requested = info.get('requested_formats') or [info]
        chosen = requested[0]
        stream_url = chosen.get('url') or info.get('url')
        if not stream_url:
            raise RuntimeError('No playable stream was offered')

        headers = dict(info.get('http_headers') or {})
        headers.update(chosen.get('http_headers') or {})
        try:
            cookie = ydl.cookiejar.get_cookie_header(stream_url)
            if cookie:
                headers['Cookie'] = cookie
        except Exception:
            pass

        # Split tracks: hand both back so they can be joined while playing.
        audio_url = ''
        if len(requested) > 1 and chosen.get('acodec') in (None, 'none'):
            audio_url = requested[1].get('url') or ''
        has_audio = bool(audio_url) or chosen.get('acodec') not in (None, 'none')
        extension = 'mkv' if audio_url else (chosen.get('ext') or info.get('ext') or 'mp4')
        title = info.get('title') or 'video'
        return {
            'url': stream_url,
            'audio_url': audio_url,
            'headers': headers,
            'title': title,
            'filename': f'{safe_filename(title, "video", 80)}.{extension}',
            'is_live': bool(info.get('is_live')),
            'has_audio': has_audio,
            'seekable': not audio_url,
            'height': chosen.get('height'),
        }


def convert_to_gif(source: str, settings, log_sink=None) -> str:
    """Turn a downloaded video into an animated GIF.

    Two passes: ffmpeg first works out one colour palette for the whole clip,
    then re-encodes using it. A GIF only holds 256 colours, and letting ffmpeg
    pick them per frame is what makes home-made GIFs look muddy and banded.
    """
    ffmpeg = find_tool('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('FFmpeg is needed to make a GIF')

    fps = max(1, int(getattr(settings, 'gif_fps', 15) or 15))
    width = max(64, int(getattr(settings, 'gif_width', 480) or 480))
    limit = max(0, int(getattr(settings, 'gif_max_seconds', 30) or 0))
    target = os.path.splitext(source)[0] + '.gif'
    palette = source + '.palette.png'
    scale = f'fps={fps},scale={width}:-1:flags=lanczos'
    duration = ['-t', str(limit)] if limit else []

    def run(args):
        result = subprocess.run([ffmpeg, '-y', '-v', 'error', '-nostdin', *args],
                                capture_output=True, text=True, errors='replace',
                                creationflags=CREATE_NO_WINDOW)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or '').strip().splitlines()[-1:][0]
                               if result.stderr else 'ffmpeg failed')

    try:
        run(['-i', source, *duration, '-vf', f'{scale},palettegen=stats_mode=diff', palette])
        run(['-i', source, '-i', palette, *duration,
             '-lavfi', f'{scale}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle',
             '-loop', '0', target])
    finally:
        try:
            os.remove(palette)
        except OSError:
            pass

    if log_sink:
        size = os.path.getsize(target) if os.path.exists(target) else 0
        log_sink(f'GIF ready: {os.path.basename(target)} ({size / 1048576:.1f} MiB, '
                 f'{fps} fps, {width}px wide)')
    return target


def _clean_error(exc: Exception) -> str:
    message = str(exc)
    for prefix in ('ERROR: ', ):
        if message.startswith(prefix):
            message = message[len(prefix):]
    message = message.split('; please report this issue')[0]
    return message.strip() or exc.__class__.__name__


# --------------------------------------------------------------------------- downloading

class Aria2BridgeFD(FileDownloader):
    """A yt-dlp downloader that hands the URL to Grabbit's aria2 instance."""

    FD_NAME = 'aria2'

    def real_download(self, filename, info_dict):
        job = self.ydl.grabbit_job
        tmpfilename = self.temp_name(filename)
        folder = os.path.dirname(os.path.abspath(tmpfilename)) or '.'
        os.makedirs(folder, exist_ok=True)

        headers = dict(info_dict.get('http_headers') or {})
        try:
            cookie = self.ydl.cookiejar.get_cookie_header(info_dict['url'])
            if cookie:
                headers['Cookie'] = cookie
        except Exception:
            pass

        options = {
            'dir': folder,
            # './' guards against aria2 trimming leading/trailing spaces in names
            'out': './' + os.path.basename(tmpfilename),
            'header': [f'{key}: {value}' for key, value in headers.items() if value],
            'continue': 'true',
            'allow-overwrite': 'true',
            'auto-file-renaming': 'false',
            'always-resume': 'false',
            'remote-time': 'false',
        }
        self.report_destination(filename)
        started = time.time()
        gid = job.aria2_add(info_dict['url'], options)
        try:
            while True:
                job.check_interrupts(gid)
                status = job.aria2_status(gid)
                state = status.get('status')
                done = int(status.get('completedLength') or 0)
                total = int(status.get('totalLength') or 0)
                speed = int(status.get('downloadSpeed') or 0)
                self._hook_progress({
                    'status': 'downloading',
                    'downloaded_bytes': done,
                    'total_bytes': total or None,
                    'speed': speed or None,
                    'eta': (total - done) / speed if speed and total else None,
                    'filename': filename,
                    'tmpfilename': tmpfilename,
                    'elapsed': time.time() - started,
                }, info_dict)
                if state == 'complete':
                    break
                if state in ('error', 'removed'):
                    raise RuntimeError(status.get('errorMessage')
                                       or f'aria2 could not download this stream ({state})')
                time.sleep(0.4)
        finally:
            job.aria2_forget(gid)

        size = os.path.getsize(tmpfilename) if os.path.exists(tmpfilename) else 0
        self.try_rename(tmpfilename, filename)
        self._hook_progress({
            'status': 'finished',
            'downloaded_bytes': size,
            'total_bytes': size,
            'filename': filename,
            'elapsed': time.time() - started,
        }, info_dict)
        return True


class GrabbitYDL(YoutubeDL):
    """YoutubeDL that routes plain http(s) streams through aria2."""

    grabbit_job = None

    def dl(self, name, info, subtitle=False, test=False):
        job = self.grabbit_job
        # Formats that ask for browser impersonation (TikTok, some Cloudflare
        # sites) are checked against the TLS fingerprint of the client, which
        # aria2 cannot fake - those stay with yt-dlp's own downloader, exactly
        # as yt-dlp does for external downloaders.
        if (job is not None and not test and job.use_aria2
                and not info.get('fragments') and info.get('impersonate') is None):
            try:
                protocol = info.get('protocol') or determine_protocol(info)
            except Exception:
                protocol = ''
            if protocol in ('http', 'https'):
                downloader = Aria2BridgeFD(self, self.params)
                for hook in self._progress_hooks:
                    downloader.add_progress_hook(hook)
                new_info = self._copy_infodict(info)
                if new_info.get('http_headers') is None:
                    new_info['http_headers'] = self._calc_headers(new_info)
                return downloader.download(name, new_info, subtitle)
        return super().dl(name, info, subtitle=subtitle, test=test)


class _RecordFormats(PostProcessor):
    """Runs just before downloading, so the job knows how many parts to expect."""

    def __init__(self, job):
        super().__init__()
        self._job = job

    def run(self, info):
        try:
            self._job.note_formats(info)
        except Exception:
            pass
        return [], info


_PP_NOTES = {
    'Merger': 'Merging video and audio',
    'FFmpegMerger': 'Merging video and audio',
    'ExtractAudio': 'Converting audio',
    'FFmpegExtractAudio': 'Converting audio',
    'EmbedThumbnail': 'Embedding thumbnail',
    'FFmpegMetadata': 'Writing metadata',
    'FFmpegEmbedSubtitle': 'Embedding subtitles',
    'FFmpegVideoRemuxer': 'Remuxing',
    'FFmpegVideoConvertor': 'Converting video',
}


class MediaJob(threading.Thread):
    """Downloads one media item with yt-dlp, reporting progress as events."""

    def __init__(self, task, settings, callbacks):
        super().__init__(daemon=True, name=f'media-{task.id}')
        self.task_id = task.id
        self.source = task.source
        self.save_dir = task.save_dir or settings.download_dir
        self.media = dict(task.media or {})
        self.settings = settings
        self.use_aria2 = bool(getattr(settings, 'media_via_aria2', True))
        # callbacks provided by the engine (thread-safe)
        self._emit = callbacks['emit']            # (task_id, event, payload)
        self._aria2_add = callbacks['aria2_add']  # (task_id, url, options) -> gid
        self._aria2_status = callbacks['aria2_status']
        self._aria2_control = callbacks['aria2_control']  # (gid, action)
        self.cancelled = False
        self.paused = False
        self.parts: dict = {}
        self.expected_sizes: list = []
        self.part_count = 1
        self._gids: list = []
        self._last_progress = 0.0

    # -------------------------------------------------- aria2 bridge callbacks
    def aria2_add(self, url, options) -> str:
        gid = self._aria2_add(self.task_id, url, options)
        self._gids.append(gid)
        return gid

    def aria2_status(self, gid) -> dict:
        return self._aria2_status(gid)

    def aria2_forget(self, gid):
        if gid in self._gids:
            self._gids.remove(gid)
        self._aria2_control(gid, 'forget')

    def check_interrupts(self, gid=None):
        if self.cancelled:
            if gid:
                self._aria2_control(gid, 'remove')
            raise JobCancelled('removed')
        if self.paused:
            if gid:
                self._aria2_control(gid, 'pause')
                while self.paused and not self.cancelled:
                    time.sleep(0.25)
                if self.cancelled:
                    self._aria2_control(gid, 'remove')
                    raise JobCancelled('removed')
                self._aria2_control(gid, 'unpause')
            else:
                raise JobPaused('paused')

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def cancel(self):
        self.cancelled = True
        self.paused = False

    # ------------------------------------------------------------ progress
    def note_formats(self, info):
        formats = info.get('requested_formats') or [info]
        self.part_count = max(1, len(formats))
        self.expected_sizes = [(f.get('filesize') or f.get('filesize_approx') or 0) for f in formats]

    def _progress_hook(self, status):
        if self.cancelled:
            raise JobCancelled('removed')
        if self.paused and not self.use_aria2:
            raise JobPaused('paused')
        state = status.get('status')
        name = status.get('filename') or status.get('tmpfilename') or 'part'
        part = self.parts.setdefault(name, {'done': 0, 'total': 0})
        if state in ('downloading', 'finished'):
            part['done'] = status.get('downloaded_bytes') or part['done']
            part['total'] = (status.get('total_bytes') or status.get('total_bytes_estimate')
                             or part['total'] or part['done'])
        if state == 'finished':
            part['done'] = part['total'] = part['total'] or part['done']

        done = sum(p['done'] for p in self.parts.values())
        total = sum(p['total'] for p in self.parts.values())
        pending = max(0, self.part_count - len(self.parts))
        if pending and self.expected_sizes:
            total += sum(self.expected_sizes[len(self.parts):])
        now = time.time()
        if state == 'finished' or now - self._last_progress > 0.4:
            self._last_progress = now
            self._emit(self.task_id, 'progress', {
                'done': done, 'total': total,
                'speed': status.get('speed') or 0,
                'eta': status.get('eta'),
                'note': f'Downloading part {min(len(self.parts), self.part_count)} of {self.part_count}'
                        if self.part_count > 1 else '',
            })

    def _pp_hook(self, status):
        if self.cancelled:
            raise JobCancelled('removed')
        name = status.get('postprocessor') or ''
        if status.get('status') == 'started' and name in _PP_NOTES:
            self._emit(self.task_id, 'state', {'state': 'processing', 'note': _PP_NOTES[name]})

    def _log(self, message):
        self._emit(self.task_id, 'log', {'message': message})

    # ------------------------------------------------------------ main body
    def run(self):
        try:
            self._run()
        except JobCancelled:
            self._emit(self.task_id, 'cancelled', {})
        except JobPaused:
            self._emit(self.task_id, 'paused', {})
        except Exception as exc:
            log.exception('media job failed')
            self._emit(self.task_id, 'error', {'message': self._explain(_clean_error(exc))})

    def _explain(self, message: str) -> str:
        """Turn a site's refusal into something the user can act on."""
        lowered = message.lower()
        refused = any(sign in lowered for sign in (
            '403', 'forbidden', 'login', 'sign in', 'private', 'not available in your'))
        have_cookies = bool(getattr(self.settings, 'cookies_file', '')
                            or getattr(self.settings, 'cookies_browser', ''))
        if refused and not have_cookies:
            return f'{message} — the site refused an anonymous download.' + (
                f' {REFUSED_HINT}' if REFUSED_HINT else '')
        return message

    def _run(self):
        params = download_params(self.settings, self.save_dir,
                                 self.media.get('quality'), self.media.get('container'))
        params['progress_hooks'] = [self._progress_hook]
        params['postprocessor_hooks'] = [self._pp_hook]
        ydl = make_ydl(self.settings, params, self._log)
        ydl.grabbit_job = self
        ydl.add_post_processor(_RecordFormats(self), when='before_dl')

        with ydl:
            info = self._fetch_info(ydl)
            self.check_interrupts()
            if self.media.get('quality') == 'frame':
                self._save_frame(ydl, info)
                return
            self._emit(self.task_id, 'state', {'state': 'downloading', 'note': ''})
            result = ydl.process_ie_result(info, download=True)

        filepath = (traverse_obj(result, ('requested_downloads', 0, 'filepath'))
                    or result.get('filepath') or '')

        if self.media.get('quality') == 'gif' and filepath and os.path.exists(filepath):
            self.check_interrupts()
            self._emit(self.task_id, 'state', {'state': 'processing', 'note': 'Making the GIF'})
            video = filepath
            filepath = convert_to_gif(video, self.settings, self._log)
            try:
                os.remove(video)          # the source clip was only a means to an end
            except OSError:
                pass

        size = os.path.getsize(filepath) if filepath and os.path.exists(filepath) else 0
        self._emit(self.task_id, 'finished', {'filepath': filepath, 'size': size})

    def _save_frame(self, ydl, info: dict) -> None:
        """One frame, read from the stream at the moment that was chosen.

        FFmpeg seeks in the stream itself, so this costs the frame rather than
        the video. A stream it cannot seek in is downloaded instead, and the
        frame read from the file.
        """
        import copy

        from . import frames
        from .util import unique_path

        at = max(0.0, float(self.media.get('frame_at') or 0))
        kind = self.media.get('frame_format') or 'png'
        self._emit(self.task_id, 'state', {'state': 'downloading',
                                           'note': f'Reading the frame at {frames.clock(at)}'})
        processed = ydl.process_ie_result(copy.deepcopy(info), download=False)
        title = processed.get('title') or info.get('title') or 'frame'
        os.makedirs(self.save_dir, exist_ok=True)
        target = unique_path(self.save_dir, frames.frame_filename(title, at, kind))

        # The sharpest stream first, then the next best; and if the site turns
        # all of those away - YouTube does, now and then, with addresses it has
        # only just handed out - a second look at the page brings fresh ones.
        saved = False
        for look in range(2):
            if look:
                self.check_interrupts()
                info = self._fetch_info(ydl)
                self._emit(self.task_id, 'state', {
                    'state': 'downloading', 'note': f'Reading the frame at {frames.clock(at)}'})
                processed = ydl.process_ie_result(copy.deepcopy(info), download=False)
            for stream in frames.streams_from_info(processed, ydl.cookiejar):
                try:
                    frames.save(stream.url, stream.headers, at, target)
                    saved = True
                    break
                except frames.FrameError as exc:
                    self.check_interrupts()
                    which = f'the {stream.height}p stream' if stream.height else 'the stream'
                    self._log(f'Could not read the frame from {which}: {exc}')
            if saved:
                break
        if not saved:
            self._log('No stream would give up the frame; downloading the video to read it '
                      'from that instead.')
            self._emit(self.task_id, 'state', {'state': 'downloading', 'note': ''})
            result = ydl.process_ie_result(info, download=True)
            video = (traverse_obj(result, ('requested_downloads', 0, 'filepath'))
                     or result.get('filepath') or '')
            if not video or not os.path.exists(video):
                raise RuntimeError('The video could not be downloaded to read the frame from')
            try:
                self._emit(self.task_id, 'state', {'state': 'processing',
                                                   'note': f'Reading the frame at {frames.clock(at)}'})
                frames.save(video, None, at, target)
            finally:
                try:
                    os.remove(video)          # only ever a means to the picture
                except OSError:
                    pass
        self._log(f'Saved the frame at {frames.clock(at)}: {os.path.basename(target)}')
        self._emit(self.task_id, 'finished', {'filepath': target, 'size': os.path.getsize(target)})

    def _fetch_info(self, ydl) -> dict:
        """Read the page again with the session that is about to download it.

        Some sites (TikTok, for one) tie their media URLs to the cookies of the
        session that handed them out, so downloading a URL captured earlier in
        the preview gets a 403. The preview's copy is kept only as a fallback
        for when the page cannot be read a second time.
        """
        self._emit(self.task_id, 'state', {'state': 'extracting', 'note': ''})
        parent_url = self.media.get('parent_url')
        try:
            # A carousel slide has no page of its own, so go via the post.
            if parent_url and parent_url == self.source:
                return self._entry_from_parent(ydl, parent_url)
            return self._extract_url(ydl, self.source)
        except Exception as exc:
            cached = self.media.get('info')
            fetched_at = self.media.get('info_time') or 0
            if cached and time.time() - fetched_at < INFO_FRESH_SECONDS:
                self._log(f'Could not read the page again ({_clean_error(exc)}); '
                          'using the details from the preview.')
                return dict(cached)
            raise

    def _extract_url(self, ydl, url: str) -> dict:
        info = ydl.extract_info(url, download=False, process=False)
        for _ in range(4):
            if not isinstance(info, dict) or info.get('_type') not in ('url', 'url_transparent'):
                break
            info = ydl.extract_info(info['url'], download=False, process=False,
                                    ie_key=info.get('ie_key'))
        if not isinstance(info, dict):
            raise RuntimeError('Could not read this link any more')
        return info

    def _entry_from_parent(self, ydl, parent_url: str) -> dict:
        entry_id = self.media.get('entry_id')
        entry_index = self.media.get('entry_index')
        playlist = self._extract_url(ydl, parent_url)
        entries = list(itertools.islice(playlist.get('entries') or [], 500))
        if not entries:
            return playlist
        match = None
        for index, entry in enumerate(entries, 1):
            if entry_id and str(entry.get('id')) == str(entry_id):
                match = entry
                break
            if entry_index and index == entry_index:
                match = entry
        if match is None:
            raise RuntimeError('That item is no longer in the post')
        for key in ('extractor', 'extractor_key', 'webpage_url', 'http_headers'):
            match.setdefault(key, playlist.get(key))
        return match
