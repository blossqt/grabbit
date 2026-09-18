"""Reading .metalink / .meta4 files.

A metalink lists mirrors and checksums for one or more files. Grabbit hands
each file to aria2 with all of its mirrors at once (aria2 downloads from
several in parallel) plus the checksum, so a corrupted mirror is caught.

Both the current format (RFC 5854, .meta4) and the older v3 layout are read.
"""

import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field

# Digests aria2 understands, strongest first.
CHECKSUM_TYPES = ('sha-512', 'sha-256', 'sha-1', 'md5')


@dataclass
class MetalinkFile:
    name: str = ''
    size: int = 0
    urls: list = field(default_factory=list)
    checksum: str = ''          # 'sha-256=<digest>', ready for aria2


def _tag(element) -> str:
    return element.tag.rpartition('}')[2].lower()


def _children(element, name: str):
    return [child for child in element.iter() if _tag(child) == name]


def parse(data: bytes) -> list:
    """Every file described by a metalink document."""
    root = ElementTree.fromstring(data)
    files = []
    for node in _children(root, 'file'):
        entry = MetalinkFile(name=(node.get('name') or '').strip())
        for child in node:
            tag = _tag(child)
            if tag == 'size' and (child.text or '').strip().isdigit():
                entry.size = int(child.text.strip())
            elif tag == 'url' and (child.text or '').strip():
                entry.urls.append(child.text.strip())
            elif tag == 'resources':            # v3 nests the mirrors
                for resource in child:
                    if _tag(resource) == 'url' and (resource.text or '').strip():
                        entry.urls.append(resource.text.strip())

        digests = {}
        for hash_node in _children(node, 'hash'):
            hash_type = (hash_node.get('type') or '').lower()
            value = (hash_node.text or '').strip()
            if hash_type and value:
                digests[hash_type] = value
        for hash_type in CHECKSUM_TYPES:
            if hash_type in digests:
                entry.checksum = f'{hash_type}={digests[hash_type]}'
                break

        if entry.urls:
            if not entry.name:
                entry.name = entry.urls[0].rsplit('/', 1)[-1] or 'download'
            files.append(entry)
    return files


def looks_like_metalink(data: bytes) -> bool:
    head = data[:400].lstrip().lower()
    return head.startswith(b'<?xml') and (b'metalink' in head or b'meta4' in head)
