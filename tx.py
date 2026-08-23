import struct
from dataclasses import dataclass

from utils import sha256d, varint_decode, varint_encode

SIGHASH_ALL = 0x00000001
NULL_HASH = b"\x00" * 32
MAX_SCRIPT_LEN = 16_384
RBF_SEQUENCE = 0xFFFFFFFD  # Signals RBF opt-in (BIP-125)
LOCKTIME_THRESHOLD = 500_000_000  # block height vs unix timestamp (BIP-113)


@dataclass
class TxIn:
    prev_txid: bytes
    prev_vout: int
    script_sig: bytes = b""
    sequence: int = 0xFFFFFFFF

    def serialize(self) -> bytes:
        return (
            self.prev_txid
            + struct.pack("<I", self.prev_vout)
            + varint_encode(len(self.script_sig))
            + self.script_sig
            + struct.pack("<I", self.sequence)
        )

    @classmethod
    def parse(cls, data: bytes, pos: int = 0):
        txid = data[pos : pos + 32]
        pos += 32
        vout = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        slen, pos = varint_decode(data, pos)
        script = data[pos : pos + slen]
        pos += slen
        seq = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        return cls(txid, vout, script, seq), pos


@dataclass
class TxOut:
    value: int
    script_pubkey: bytes

    def serialize(self) -> bytes:
        return (
            struct.pack("<Q", self.value)
            + varint_encode(len(self.script_pubkey))
            + self.script_pubkey
        )

    @classmethod
    def parse(cls, data: bytes, pos: int = 0):
        value = struct.unpack_from("<Q", data, pos)[0]
        pos += 8
        slen, pos = varint_decode(data, pos)
        script = data[pos : pos + slen]
        pos += slen
        return cls(value, script), pos


@dataclass
class Transaction:
    version: int = 1
    inputs: list = None
    outputs: list = None
    locktime: int = 0

    def __post_init__(self):
        if self.inputs is None:
            self.inputs = []
        if self.outputs is None:
            self.outputs = []

    def serialize(self, sign_input=None) -> bytes:
        parts = [struct.pack("<i", self.version)]
        parts.append(varint_encode(len(self.inputs)))
        for i, txin in enumerate(self.inputs):
            if sign_input is not None and i != sign_input:
                empty = TxIn(txin.prev_txid, txin.prev_vout, b"", txin.sequence)
                parts.append(empty.serialize())
            else:
                parts.append(txin.serialize())
        parts.append(varint_encode(len(self.outputs)))
        for txout in self.outputs:
            parts.append(txout.serialize())
        parts.append(struct.pack("<I", self.locktime))
        return b"".join(parts)

    @classmethod
    def parse(cls, data: bytes, pos: int = 0):
        version = struct.unpack_from("<i", data, pos)[0]
        pos += 4
        n_in, pos = varint_decode(data, pos)
        inputs = []
        for _ in range(n_in):
            txin, pos = TxIn.parse(data, pos)
            inputs.append(txin)
        n_out, pos = varint_decode(data, pos)
        outputs = []
        for _ in range(n_out):
            txout, pos = TxOut.parse(data, pos)
            outputs.append(txout)
        locktime = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        return cls(version, inputs, outputs, locktime), pos

    def txid(self) -> bytes:
        return sha256d(self.serialize())

    def sighash(self) -> bytes:
        return sha256d(self.serialize(sign_input=-1) + struct.pack("<I", SIGHASH_ALL))

    def is_coinbase(self) -> bool:
        return (
            len(self.inputs) == 1
            and self.inputs[0].prev_txid == NULL_HASH
            and self.inputs[0].prev_vout == 0xFFFFFFFF
        )

    def to_hex(self) -> str:
        return self.serialize().hex()

    @classmethod
    def from_hex(cls, hexed: str):
        raw = bytes.fromhex(hexed)
        tx, pos = cls.parse(raw)
        if pos != len(raw):
            raise ValueError("trailing bytes in transaction")
        return tx


def coinbase_tx(height: int, reward_sats: int, address: str, note: str = "") -> Transaction:
    hbytes = height.to_bytes((height.bit_length() + 7) // 8 or 1, "little")
    script = bytes([len(hbytes)]) + hbytes
    if note:
        script += note.encode()
    txin = TxIn(NULL_HASH, 0xFFFFFFFF, script)
    txout = TxOut(reward_sats, address.encode())
    return Transaction(1, [txin], [txout], 0)


def coinbase_height(tx: Transaction):
    script = tx.inputs[0].script_sig
    if not script or len(script) < 2:
        return None
    size = script[0]
    if size < 1 or size > 8 or len(script) < 1 + size:
        return None
    return int.from_bytes(script[1 : 1 + size], "little")


def make_transfer(inputs: list, outputs: list, locktime: int = 0, rbf: bool = False) -> Transaction:
    seq = RBF_SEQUENCE if rbf else 0xFFFFFFFF
    txins = [TxIn(txid, vout, sequence=seq) for txid, vout in inputs]
    txouts = [TxOut(value, addr.encode()) for value, addr in outputs]
    return Transaction(1, txins, txouts, locktime)