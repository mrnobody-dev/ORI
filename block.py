import struct
from dataclasses import dataclass

from tx import Transaction, coinbase_height
from utils import hexstr, sha256d, varint_decode, varint_encode

MAX_BLOCK_HEADER_BYTES = 80


@dataclass
class BlockHeader:
    version: int = 1
    prev_hash: bytes = b"\x00" * 32
    merkle_root: bytes = b"\x00" * 32
    timestamp: int = 0
    bits: int = 0
    nonce: int = 0

    def serialize(self) -> bytes:
        return (
            struct.pack("<i", self.version)
            + self.prev_hash
            + self.merkle_root
            + struct.pack("<I", self.timestamp)
            + struct.pack("<I", self.bits)
            + struct.pack("<I", self.nonce)
        )

    @classmethod
    def parse(cls, data: bytes, pos: int = 0):
        if pos + MAX_BLOCK_HEADER_BYTES > len(data):
            raise ValueError("truncated block header")
        version = struct.unpack_from("<i", data, pos)[0]
        pos += 4
        prev = data[pos : pos + 32]
        pos += 32
        merkle = data[pos : pos + 32]
        pos += 32
        timestamp, bits, nonce = struct.unpack_from("<III", data, pos)
        pos += 12
        return cls(version, prev, merkle, timestamp, bits, nonce), pos

    def hash(self) -> bytes:
        return sha256d(self.serialize())

    def to_hex(self) -> str:
        return self.serialize().hex()

    @classmethod
    def from_hex(cls, hexed: str):
        raw = bytes.fromhex(hexed)
        header, pos = cls.parse(raw)
        if pos != len(raw):
            raise ValueError("trailing bytes in block header")
        return header

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "prev_hash": hexstr(self.prev_hash),
            "merkle_root": hexstr(self.merkle_root),
            "timestamp": self.timestamp,
            "bits": self.bits,
            "nonce": self.nonce,
        }


@dataclass
class Block:
    header: BlockHeader
    transactions: list = None

    def __post_init__(self):
        if self.transactions is None:
            self.transactions = []

    def serialize(self) -> bytes:
        parts = [self.header.serialize(), varint_encode(len(self.transactions))]
        for tx in self.transactions:
            parts.append(tx.serialize())
        return b"".join(parts)

    @classmethod
    def parse(cls, data: bytes, pos: int = 0):
        header, pos = BlockHeader.parse(data, pos)
        n_tx, pos = varint_decode(data, pos)
        if n_tx == 0:
            raise ValueError("block has no transactions")
        if n_tx > max(1, (len(data) - pos) // 10):
            raise ValueError("transaction count exceeds payload bounds")
        txs = []
        for _ in range(n_tx):
            tx, pos = Transaction.parse(data, pos)
            txs.append(tx)
        return cls(header, txs), pos

    def hash(self) -> bytes:
        return self.header.hash()

    def block_hash_hex(self) -> str:
        return hexstr(self.hash())

    def merkle_ok(self) -> bool:
        from merkle import merkle_root

        txids = [tx.txid() for tx in self.transactions]
        return merkle_root(txids) == self.header.merkle_root

    def size(self) -> int:
        return len(self.serialize())

    def to_hex(self) -> str:
        return self.serialize().hex()

    @classmethod
    def from_hex(cls, hexed: str):
        raw = bytes.fromhex(hexed)
        return cls.from_bytes(raw)

    @classmethod
    def from_bytes(cls, raw: bytes):
        block, pos = cls.parse(raw)
        if pos != len(raw):
            raise ValueError("trailing bytes in block")
        return block

    def to_dict(self, height: int = None) -> dict:
        return {
            "height": height,
            "hash": self.block_hash_hex(),
            **self.header.to_dict(),
            "tx_count": len(self.transactions),
            "txs": [tx.txid().hex() for tx in self.transactions],
            "size": self.size(),
        }
