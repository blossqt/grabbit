"""Ed25519 signatures, in plain Python.

Used to check that an update really came from whoever holds the release key
(see updates.py). Written out rather than imported because the obvious
libraries -- cryptography, PyNaCl -- would add megabytes of native code to the
exe for the sake of one signature check a day.

This follows the reference implementation in RFC 8032, section 6. It is not
constant-time, which is fine for what it does here: verifying is public-key
work, and signing only ever runs on the machine that publishes releases.

It is Homework Hub's module, unchanged but for this paragraph, so the two apps
decide whether to believe a release in exactly the same way. build/update_check.py
checks it against RFC 8032's own test vectors.
"""

from __future__ import annotations

import hashlib
import secrets

__all__ = ["generate_secret", "public_key", "sign", "verify"]

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493   # order of the base point
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _sha512_int(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little")


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


# Points are kept in extended coordinates (X, Y, Z, T) with x = X/Z, y = Y/Z
# and x*y = T/Z, which avoids an inversion on every addition.

def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f, g * h, f * g, e * h)


def _mul(scalar: int, point):
    result = (0, 1, 1, 0)   # the neutral element
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _equal(p, q) -> bool:
    # x1/z1 == x2/z2 and y1/z1 == y2/z2, cross-multiplied.
    if (p[0] * q[2] - q[0] * p[2]) % _P:
        return False
    return (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _recover_x(y: int, sign: int):
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(point) -> bytes:
    zinv = _inv(point[2])
    x = point[0] * zinv % _P
    y = point[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(data: bytes):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _expand(secret: bytes):
    if len(secret) != 32:
        raise ValueError("An Ed25519 secret key is 32 bytes.")
    digest = hashlib.sha512(secret).digest()
    a = int.from_bytes(digest[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, digest[32:]


def generate_secret() -> bytes:
    return secrets.token_bytes(32)


def public_key(secret: bytes) -> bytes:
    a, _prefix = _expand(secret)
    return _compress(_mul(a, _G))


def sign(secret: bytes, message: bytes) -> bytes:
    a, prefix = _expand(secret)
    public = _compress(_mul(a, _G))
    r = _sha512_int(prefix + message) % _L
    encoded_r = _compress(_mul(r, _G))
    h = _sha512_int(encoded_r + public + message) % _L
    s = (r + h * a) % _L
    return encoded_r + int.to_bytes(s, 32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """True only for a valid signature. Malformed input is just invalid."""
    if len(public) != 32 or len(signature) != 64:
        return False
    a = _decompress(public)
    if a is None:
        return False
    encoded_r = signature[:32]
    r = _decompress(encoded_r)
    if r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = _sha512_int(encoded_r + public + message) % _L
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))
