"""What a video or a sound can be saved as - its type, then its format, then
its quality - each offering only what the one before it allows, and only
what the source actually has.

A track with no picture is offered as audio alone; a video's resolutions are
the ones it comes in, with their real dimensions; an audio file is offered no
higher a sample rate or bitrate than the original has. The desktop's Add
dialog asks these questions, and choice() turns the answers into what a
download is queued with (media.py reads them back).
"""

from dataclasses import dataclass, field

from .frames import IMAGE_TYPES

TYPES = [('video', 'Video'), ('audio', 'Audio'), ('gif', 'GIF'), ('image', 'Image')]

VIDEO_FORMATS = [('mp4', 'MP4'), ('mkv', 'MKV'), ('webm', 'WebM'), ('any', 'Original')]
AUDIO_FORMATS = [('mp3', 'MP3'), ('m4a', 'M4A (AAC)'), ('opus', 'Opus'), ('flac', 'FLAC'),
                 ('wav', 'WAV'), ('best', 'Original (not converted)')]
LOSSLESS = ('flac', 'wav')

BITRATES = [320, 256, 192, 160, 128, 96, 64]                  # kbps
SAMPLE_RATES = [192000, 176400, 96000, 88200, 48000, 44100, 32000, 22050]
GIF_WIDTHS = [720, 480, 360, 240]

_CODECS = {'mp4a': 'AAC', 'aac': 'AAC', 'opus': 'Opus', 'vorbis': 'Vorbis', 'mp3': 'MP3',
           'flac': 'FLAC', 'alac': 'ALAC', 'ac-3': 'AC-3', 'ec-3': 'E-AC-3', 'pcm': 'PCM'}


@dataclass
class Stream:
    """One of the source's sounds on its own."""
    id: str
    codec: str = ''
    kbps: int = 0
    rate: int = 0

    def describe(self) -> str:
        bits = [self.codec or 'Audio']
        if self.kbps:
            bits.append(f'{self.kbps} kbps')
        if self.rate:
            bits.append(khz(self.rate))
        return ' · '.join(bits)


@dataclass
class Source:
    """What the source has: its picture sizes, tallest first, and its sounds."""
    sizes: list = field(default_factory=list)         # [(width, height)]
    streams: list = field(default_factory=list)       # [Stream], best first
    picture: bool = True
    webm: bool = False
    rate: int = 0                                     # its best sound's, in Hz
    kbps: int = 0
    live: bool = False


def khz(rate: int) -> str:
    return f'{rate / 1000:g} kHz'


def _codec(name: str) -> str:
    name = (name or '').lower()
    for key, label in _CODECS.items():
        if name.startswith(key):
            return label
    return name.upper() if name and name != 'none' else ''


def source_of(item, live: bool = False) -> Source:
    """Read the source's formats, as the link was read (item.info)."""
    formats = [f for f in ((item.info or {}).get('formats') or [])
               if f.get('ext') != 'mhtml']                     # storyboards, not video
    source = Source(live=live)
    widest = {}
    for f in formats:
        if f.get('vcodec') not in (None, 'none') or (f.get('vcodec') is None and f.get('height')):
            height = int(f.get('height') or 0)
            if height:
                widest[height] = max(widest.get(height, 0), int(f.get('width') or 0))
            if f.get('ext') == 'webm':
                source.webm = True
    source.sizes = sorted(((w, h) for h, w in widest.items()), key=lambda s: -s[1])
    if not source.sizes and not formats:
        # Read without its formats - a playlist's entry: what the link said.
        source.sizes = [(0, h) for h in sorted(set(item.heights or []), reverse=True)]

    seen = set()
    sounds = [f for f in formats if f.get('acodec') not in (None, 'none')]
    for f in sorted(sounds, key=lambda f: -(f.get('abr') or f.get('tbr') or 0)):
        if f.get('vcodec') not in (None, 'none') or f.get('height'):
            continue                                           # sound with a picture
        stream = Stream(str(f.get('format_id') or ''), _codec(f.get('acodec')),
                        int(f.get('abr') or f.get('tbr') or 0), int(f.get('asr') or 0))
        key = (stream.codec, stream.kbps, stream.rate)
        if stream.id and key not in seen:
            seen.add(key)
            source.streams.append(stream)
    source.rate = max([int(f.get('asr') or 0) for f in sounds] or [0])
    source.kbps = max([int(f.get('abr') or 0) for f in sounds] or [0])
    # Every format a sound alone - SoundCloud, a podcast: nothing to see.
    source.picture = bool(source.sizes) or (not formats and item.kind != 'audio')
    if formats and not source.sizes and all(f.get('vcodec') == 'none' for f in formats):
        source.picture = False
    return source


def types(source: Source) -> list:
    if not source.picture:
        return [t for t in TYPES if t[0] == 'audio']
    if source.live:                     # no end to take a GIF or a frame from
        return [t for t in TYPES if t[0] in ('video', 'audio')]
    return list(TYPES)


def formats(kind: str, source: Source) -> list:
    if kind == 'video':
        # WebM only holds what the site has as WebM; anything else would fail.
        return [f for f in VIDEO_FORMATS if f[0] != 'webm' or source.webm]
    if kind == 'audio':
        return list(AUDIO_FORMATS)
    if kind == 'gif':
        return [('gif', 'GIF')]
    return list(IMAGE_TYPES)


def _size(width: int, height: int) -> str:
    """'1080p · 1920×1080', named by the shorter side, as a phone's tall
    video is still called 1080p."""
    short = min(width, height) if width else height
    tag = ' (8K)' if short >= 4320 else ' (4K)' if short >= 2160 else ''
    return f'{short}p · {width}×{height}{tag}' if width else f'{short}p{tag}'


def qualities(kind: str, fmt: str, source: Source, gif_width: int = 480, fps: int = 15) -> list:
    """(value, label) for each quality the type and format allow, best first."""
    if kind == 'video':
        best = 'Best available' + (f' · {source.sizes[0][0]}×{source.sizes[0][1]}'
                                   if source.sizes and source.sizes[0][0] else '')
        return [('best', best)] + [(str(h), _size(w, h)) for w, h in source.sizes[:8]]
    if kind == 'audio':
        if fmt == 'best':
            if not source.streams:
                return [('', 'Best available')]
            # Each one the very stream it names - the best too, rather than
            # whichever yt-dlp would favour, which need not be the one shown.
            first, *rest = source.streams
            return ([(f'id:{first.id}', f'Best · {first.describe()}')]
                    + [(f'id:{s.id}', s.describe()) for s in rest])
        if fmt in LOSSLESS:
            # A site that does not say: no compressed sound is above 48 kHz.
            ceiling = source.rate or 48001
            rates = [r for r in SAMPLE_RATES if r < ceiling]
            top = f'Original · {khz(source.rate)}' if source.rate else 'Original sample rate'
            return [('', top)] + [(str(r), khz(r)) for r in rates]
        # A bitrate past the first one above the original's adds nothing to it.
        ceiling = min([b for b in BITRATES if b >= source.kbps] or [BITRATES[0]])
        rates = [b for b in BITRATES if not source.kbps or b <= ceiling]
        return [('0', 'Highest · variable bitrate')] + [(str(b), f'{b} kbps') for b in rates]
    if kind == 'gif':
        widest = max([w for w, _ in source.sizes] or [0])
        widths = [w for w in GIF_WIDTHS if not widest or w <= widest] or [GIF_WIDTHS[-1]]
        if gif_width not in widths:
            widths = sorted(set(widths) | {min(gif_width, widest or gif_width)}, reverse=True)
        return [(str(w), f'{w} px wide · {fps} fps') for w in widths]
    size = (f' · {source.sizes[0][0]}×{source.sizes[0][1]}'
            if source.sizes and source.sizes[0][0] else '')
    return [('', f'Full size{size}')]


def remembered(quality: str, container: str) -> tuple:
    """(type, format) from a remembered quality and container - the
    settings', the phone's last choice."""
    quality = quality or 'best'
    if quality == 'gif':
        return 'gif', 'gif'
    if quality.startswith('audio_'):
        return 'audio', quality[len('audio_'):]
    if quality == 'frame':
        return 'image', IMAGE_TYPES[0][0]
    return 'video', container or 'mp4'


def choice(kind: str, fmt: str, quality: str) -> dict:
    """What a download is queued with: the quality and container it always
    had, and what only some choices need - a bitrate, a sample rate, one of
    the source's own sounds, a GIF's width, a picture's type."""
    if kind == 'video':
        return {'quality': quality or 'best', 'container': fmt}
    if kind == 'audio':
        chosen = {'quality': f'audio_{fmt}'}
        if quality.startswith('id:'):
            chosen['audio_format'] = quality[3:]
        elif quality and fmt in LOSSLESS:
            chosen['sample_rate'] = int(quality)
        elif quality:
            chosen['audio_quality'] = quality
        return chosen
    if kind == 'gif':
        return {'quality': 'gif', 'gif_width': int(quality or 0)}
    return {'quality': 'frame', 'frame_format': fmt}
