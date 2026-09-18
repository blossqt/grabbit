"""Extractor tweaks layered on top of yt-dlp.

yt-dlp is built around video, so it drops the photo slides of an Instagram
carousel and has no handler for TikTok photo posts at all. Both subclasses
below keep yt-dlp's own networking, cookies and parsing, and only add the
still images back, tagged with ``grabbit_kind`` so the UI can show them.

Everything here is best-effort: if a future yt-dlp reshuffles these internals,
register() logs the problem and Grabbit carries on with the stock extractors.
"""

import logging
import os
from urllib.parse import urlparse

log = logging.getLogger(__name__)

IMAGE_EXTS = ('jpg', 'jpeg', 'png', 'webp', 'heic', 'gif')


def _ext_from_url(url: str, default: str = 'jpg') -> str:
    ext = os.path.splitext(urlparse(url or '').path)[1].lstrip('.').lower()
    if ext == 'jpeg':
        return 'jpg'
    return ext if ext in IMAGE_EXTS else default


try:
    from yt_dlp.extractor.instagram import InstagramIE as _InstagramIE
    from yt_dlp.extractor.tiktok import TikTokIE as _TikTokIE
    from yt_dlp.utils import int_or_none, traverse_obj, url_or_none

    class InstagramIE(_InstagramIE):
        """Adds the photo slides that yt-dlp skips (both carousels and single posts)."""

        def _extract_product_media(self, product_media):
            info = super()._extract_product_media(product_media)
            if info.get('formats'):
                return info

            candidates = traverse_obj(product_media, (
                'image_versions2', 'candidates', lambda _, v: url_or_none(v['url'])))
            if not candidates:
                return info

            def area(candidate):
                return ((int_or_none(candidate.get('width')) or 0)
                        * (int_or_none(candidate.get('height')) or 0))

            formats = []
            for index, candidate in enumerate(sorted(candidates, key=area, reverse=True)):
                width = int_or_none(candidate.get('width'))
                height = int_or_none(candidate.get('height'))
                formats.append({
                    'url': candidate['url'],
                    'format_id': f'photo-{width}' if width else f'photo-{index}',
                    'ext': _ext_from_url(candidate['url']),
                    'width': width,
                    'height': height,
                    'quality': area(candidate) / 1e6,
                    'http_headers': {'Referer': 'https://www.instagram.com/'},
                })
            info['formats'] = formats
            info['grabbit_kind'] = 'image'
            return info

    class TikTokIE(_TikTokIE):
        """Handles /photo/ links and photo-mode slideshows posted under /video/."""

        _VALID_URL = (r'https?://www\.tiktokv?\.com/(?:embed|(?:share|@(?P<user_id>[\w.-]+)?)'
                      r'/(?:video|photo))/(?P<id>\d+)')

        def _real_extract(self, url):
            video_id, user_id = self._match_valid_url(url).group('id', 'user_id')
            video_url = self._create_url(user_id, video_id)

            if '/photo/' in url:
                photos = self._grabbit_photo_post(video_url, video_id)
                if photos:
                    return photos
                url = video_url

            info = super()._real_extract(url)
            # A slideshow reached through a /video/ link comes back as audio only.
            formats = info.get('formats') or []
            if (info.get('_type') not in ('playlist', 'multi_video') and formats
                    and all(f.get('vcodec') in (None, 'none') for f in formats)):
                photos = self._grabbit_photo_post(video_url, video_id, audio_info=info)
                if photos:
                    return photos
            return info

        def _grabbit_photo_post(self, video_url, video_id, audio_info=None):
            try:
                data, status = self._extract_web_data_and_status(video_url, video_id, fatal=False)
            except Exception as exc:  # network/parsing hiccup: fall back to stock behaviour
                self.write_debug(f'photo post lookup failed: {exc}')
                return None
            if status != 0 or not isinstance(data, dict):
                return None
            images = traverse_obj(data, ('imagePost', 'images', ..., {dict}))
            if not images:
                return None

            author = traverse_obj(data, ('author', 'uniqueId', {str})) or ''
            description = traverse_obj(data, ('desc', {str})) or ''
            title = (traverse_obj(data, ('imagePost', 'title', {str}))
                     or description or f'TikTok photos {video_id}')
            common = {
                'channel': author,
                'uploader': author,
                'uploader_id': traverse_obj(data, ('author', 'id', {str})),
                'description': description,
                'timestamp': int_or_none(data.get('createTime')),
                'webpage_url': video_url,
                'http_headers': {'Referer': 'https://www.tiktok.com/'},
            }

            entries = []
            for index, image in enumerate(images, start=1):
                urls = [u for u in traverse_obj(image, ('imageURL', 'urlList', ..., {url_or_none})) or []]
                if not urls:
                    continue
                best = next((u for u in urls if _ext_from_url(u, '') in ('jpg', 'png')), urls[0])
                width, height = int_or_none(image.get('imageWidth')), int_or_none(image.get('imageHeight'))
                entries.append({
                    **common,
                    'id': f'{video_id}_{index}',
                    'title': f'{title} – photo {index}',
                    'grabbit_kind': 'image',
                    'formats': [{
                        'url': best,
                        'format_id': 'photo',
                        'ext': _ext_from_url(best),
                        'width': width,
                        'height': height,
                        'http_headers': {'Referer': 'https://www.tiktok.com/'},
                    }],
                    'thumbnails': [{'url': best, 'width': width, 'height': height}],
                })
            if not entries:
                return None

            music_url = traverse_obj(data, ('music', 'playUrl', {url_or_none}))
            if music_url:
                music_title = traverse_obj(data, ('music', 'title', {str})) or 'audio'
                entries.append({
                    **common,
                    'id': f'{video_id}_audio',
                    'title': f'{music_title} (soundtrack)',
                    'grabbit_kind': 'audio',
                    'grabbit_optional': True,
                    'duration': int_or_none(traverse_obj(data, ('music', 'duration'))),
                    'formats': [{
                        'url': music_url,
                        'format_id': 'soundtrack',
                        'ext': 'mp3',
                        'vcodec': 'none',
                        'acodec': 'mp3',
                        'http_headers': {'Referer': 'https://www.tiktok.com/'},
                    }],
                    'thumbnails': traverse_obj(audio_info, ('thumbnails', ..., {dict})) or [],
                })

            return {
                '_type': 'playlist',
                'id': video_id,
                'title': title,
                'grabbit_kind': 'gallery',
                'thumbnail': traverse_obj(data, ('imagePost', 'cover', 'imageURL', 'urlList', 0, {url_or_none})),
                **common,
                'entries': entries,
            }

    _CUSTOM_EXTRACTORS = (InstagramIE, TikTokIE)
    IMPORT_ERROR = None

except Exception as exc:  # pragma: no cover - only if yt-dlp internals move
    _CUSTOM_EXTRACTORS = ()
    IMPORT_ERROR = exc
    log.warning('custom extractors unavailable, using stock yt-dlp: %s', exc)


def register(ydl) -> None:
    """Swap our extractors in for the stock ones on a YoutubeDL instance.

    The keys ('Instagram', 'TikTok') already exist, so this replaces them in
    place and leaves yt-dlp's matching order untouched.
    """
    for extractor_class in _CUSTOM_EXTRACTORS:
        try:
            ydl.add_info_extractor(extractor_class())
        except Exception as exc:  # pragma: no cover
            log.warning('could not register %s: %s', extractor_class.__name__, exc)
