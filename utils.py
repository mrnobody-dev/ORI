import hashlib
import struct
import time


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def ripemd160(data: bytes) -> bytes:
    try:
        h = hashlib.new("ripemd160")
        h.update(data)
        return h.digest()
    except ValueError:
        return sha256(data)[:20]


def hash160(data: bytes) -> bytes:
    return ripemd160(sha256(data))


def varint_encode(n: int) -> bytes:
    if n < 0xFD:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


def varint_decode(data: bytes, pos: int = 0):
    first = data[pos]
    pos += 1
    if first < 0xFD:
        return first, pos
    if first == 0xFD:
        return struct.unpack_from("<H", data, pos)[0], pos + 2
    if first == 0xFE:
        return struct.unpack_from("<I", data, pos)[0], pos + 4
    return struct.unpack_from("<Q", data, pos)[0], pos + 8


def hexstr(raw: bytes) -> str:
    return raw[::-1].hex()


def unhexstr(hexed: str) -> bytes:
    return bytes.fromhex(hexed)[::-1]


def now() -> int:
    return int(time.time())


def log_info(msg: str):
    import datetime
    import sys
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{ts} {msg}", file=sys.stderr)