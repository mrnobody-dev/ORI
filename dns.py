import random
import socket
import struct

A_TYPE = 1


def _encode_name(name: str) -> bytes:
    out = b""
    for label in name.strip(".").split("."):
        if not label or len(label) > 63:
            raise ValueError("bad dns name")
        out += bytes([len(label)]) + label.encode()
    return out + b"\x00"


def build_query(name: str, qtype: int = A_TYPE) -> bytes:
    tid = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    return header + _encode_name(name) + struct.pack(">HH", qtype, 1)


def _skip_name(data: bytes, pos: int) -> int:
    while True:
        if pos >= len(data):
            raise ValueError("truncated dns name")
        length = data[pos]
        if length == 0:
            return pos + 1
        if length & 0xC0 == 0xC0:
            return pos + 2
        pos += 1 + length


def _read_name(data: bytes, pos: int) -> tuple:
    labels = []
    jumped = False
    end = None
    hops = 0
    while True:
        if pos >= len(data) or hops > 32:
            raise ValueError("truncated dns name")
        length = data[pos]
        if length & 0xC0 == 0xC0:
            if pos + 1 >= len(data):
                raise ValueError("truncated dns pointer")
            if not jumped:
                end = pos + 2
            pos = ((length & 0x3F) << 8) | data[pos + 1]
            jumped = True
            hops += 1
            continue
        if length == 0:
            if not jumped:
                end = pos + 1
            return ".".join(labels), end
        labels.append(data[pos + 1 : pos + 1 + length].decode(errors="replace"))
        pos += 1 + length


def parse_response(data: bytes) -> list:
    if len(data) < 12:
        raise ValueError("short dns response")
    ancount = struct.unpack_from(">H", data, 6)[0]
    pos = 12
    qdcount = struct.unpack_from(">H", data, 4)[0]
    for _ in range(qdcount):
        pos = _skip_name(data, pos)
        pos += 4
    answers = []
    for _ in range(ancount):
        _, pos = _read_name(data, pos)
        if pos + 10 > len(data):
            raise ValueError("truncated dns record")
        rtype, rclass, ttl, rdlen = struct.unpack_from(">HHIH", data, pos)
        pos += 10
        if pos + rdlen > len(data):
            raise ValueError("truncated dns rdata")
        rdata = data[pos : pos + rdlen]
        pos += rdlen
        if rtype == A_TYPE and rclass == 1 and rdlen == 4:
            answers.append(".".join(str(b) for b in rdata))
    return answers


def build_answer(query: bytes, answers: list, ttl: int = 60) -> bytes:
    if len(query) < 12:
        raise ValueError("short dns query")
    tid = struct.unpack_from(">H", query, 0)[0]
    header = struct.pack(">HHHHHH", tid, 0x8180, 1, len(answers), 0, 0)
    pos = 12
    qdcount = struct.unpack_from(">H", query, 4)[0]
    for _ in range(qdcount):
        pos = _skip_name(query, pos)
        pos += 4
    out = bytearray(header + query[12:pos])
    for ip in answers:
        out += b"\xc0\x0c"
        out += struct.pack(">HHIH", A_TYPE, 1, ttl, 4)
        out += bytes(int(part) for part in ip.split("."))
    return bytes(out)


def resolve_a(name: str, server_host: str, server_port: int = 5353, timeout: float = 3.0) -> list:
    query = build_query(name)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(query, (server_host, server_port))
        data, _ = sock.recvfrom(4096)
    finally:
        sock.close()
    return parse_response(data)
