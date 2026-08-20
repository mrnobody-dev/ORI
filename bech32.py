CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
CHARSET_REV = {c: i for i, c in enumerate(CHARSET)}
CHECKSUM_LEN = 6


def _polymod(values: list) -> int:
    GEN = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= GEN[i] if ((b >> i) & 1) else 0
    return chk


def _hrp_expand(hrp: str) -> list:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _verify_checksum(hrp: str, data: list) -> bool:
    return _polymod(_hrp_expand(hrp) + data) == 1


def _create_checksum(hrp: str, data: list) -> list:
    values = _hrp_expand(hrp) + data
    polymod = _polymod(values + [0, 0, 0, 0, 0, 0]) ^ 1
    return [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]


def _convertbits(data: list, frombits: int, tobits: int, pad: bool = True) -> list:
    acc = 0
    bits = 0
    ret = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or value >> frombits:
            raise ValueError("invalid data range")
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        raise ValueError("invalid padding")
    return ret


def bech32_encode(hrp: str, witver: int, program: bytes) -> str:
    data = [witver] + _convertbits(list(program), 8, 5)
    return hrp + "1" + "".join(CHARSET[d] for d in data + _create_checksum(hrp, data))


def bech32_decode(addr: str):
    if addr != addr.lower() and addr != addr.upper():
        raise ValueError("mixed case address")
    addr = addr.lower()
    pos = addr.rfind("1")
    if pos < 1 or pos + CHECKSUM_LEN + 7 > len(addr):
        raise ValueError("invalid separator or length")
    hrp = addr[:pos]
    data = []
    for c in addr[pos + 1 :]:
        if c not in CHARSET_REV:
            raise ValueError("invalid character")
        data.append(CHARSET_REV[c])
    if not _verify_checksum(hrp, data):
        raise ValueError("bad checksum")
    payload = data[:-CHECKSUM_LEN]
    witver = payload[0]
    program = bytes(_convertbits(payload[1:], 5, 8, pad=False))
    return hrp, witver, program


def address_to_program(addr: str) -> bytes:
    _, witver, program = bech32_decode(addr)
    if witver != 0:
        raise ValueError("unsupported witness version")
    if len(program) != 20:
        raise ValueError("expected 20-byte witness program")
    return program


def validate_address(addr: str, hrp: str = "ori") -> bool:
    try:
        h, witver, program = bech32_decode(addr)
    except ValueError:
        return False
    return h == hrp and witver == 0 and len(program) == 20
