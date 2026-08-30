import hashlib
import os

from ecdsa import SECP256k1, SigningKey, VerifyingKey
from ecdsa.util import sigdecode_string, sigencode_string

from bech32 import address_to_program, bech32_encode, validate_address
from config import Config
from utils import hash160

_ORDER = SECP256k1.order
_HALF_ORDER = _ORDER // 2


def sig_is_low_s(sig: bytes) -> bool:
    if len(sig) != 64:
        return False
    r = int.from_bytes(sig[:32], "big")
    s = int.from_bytes(sig[32:], "big")
    return 0 < r < _ORDER and 0 < s <= _HALF_ORDER


def new_keypair() -> tuple:
    sk = SigningKey.generate(curve=SECP256k1)
    return sk.to_string(), _compressed_pubkey(sk.get_verifying_key().to_string())


def pub_from_priv(priv: bytes) -> bytes:
    """Derive compressed public key from private key"""
    sk = SigningKey.from_string(priv, curve=SECP256k1)
    vk = sk.get_verifying_key()
    return _compressed_pubkey(vk.to_string())


def pub_to_address(pub: bytes, cfg: Config = None) -> str:
    cfg = cfg or Config()
    return bech32_encode(cfg.network_hrp, 0, hash160(pub))


def _compressed_pubkey(pub64: bytes) -> bytes:
    x = pub64[:32]
    y = pub64[32:]
    prefix = b"\x02" if y[-1] % 2 == 0 else b"\x03"
    return prefix + x


def _decompressed_pubkey(pub: bytes) -> bytes:
    if len(pub) == 65 and pub[0] == 0x04:
        return pub[1:]
    if len(pub) == 64:
        return pub
    if len(pub) == 33 and pub[0] in (0x02, 0x03):
        curve = SECP256k1.curve
        p = curve.p()
        x = int.from_bytes(pub[1:], "big")
        alpha = (pow(x, 3, p) + 7) % p
        y = pow(alpha, (p + 1) // 4, p)
        if (y % 2) != (pub[0] - 2):
            y = p - y
        return x.to_bytes(32, "big") + y.to_bytes(32, "big")
    raise ValueError("invalid public key")


def sign(priv: bytes, digest: bytes) -> bytes:
    sk = SigningKey.from_string(priv, curve=SECP256k1)
    sig = sk.sign_digest_deterministic(
        digest, hashfunc=hashlib.sha256, sigencode=sigencode_string
    )
    s = int.from_bytes(sig[32:], "big")
    if s > _HALF_ORDER:
        sig = sig[:32] + (_ORDER - s).to_bytes(32, "big")
    return sig


def verify(pub: bytes, digest: bytes, sig: bytes) -> bool:
    try:
        vk = VerifyingKey.from_string(_decompressed_pubkey(pub), curve=SECP256k1)
        return vk.verify_digest(sig, digest, sigdecode=sigdecode_string)
    except Exception:
        return False