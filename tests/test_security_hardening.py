from fastapi.testclient import TestClient

from api import create_app
from block import BlockHeader
from config import Config
from mempool import MAX_ANCESTORS, Mempool
from node import Node
from tx import Transaction, TxIn, TxOut


def test_public_api_mutation_requires_token(tmp_path):
    cfg = Config(data_dir=str(tmp_path), api_host="0.0.0.0", enable_p2p=False)
    node = Node(cfg)
    app = create_app(node)

    response = TestClient(app).post("/tx/", json={"tx": "00"})

    assert response.status_code == 403
    assert response.json()["detail"] == "api token required for public bind"


def test_block_header_hex_round_trip_uses_exact_80_bytes():
    header = BlockHeader(
        version=2,
        prev_hash=b"\x11" * 32,
        merkle_root=b"\x22" * 32,
        timestamp=1234567890,
        bits=0x1F00FFFF,
        nonce=42,
    )

    parsed = BlockHeader.from_hex(header.to_hex())

    assert parsed == header
    assert len(bytes.fromhex(header.to_hex())) == 80


def test_mempool_rejects_too_deep_ancestor_chain():
    mempool = Mempool(max_txs=10_000)
    prev_txid = b"\x01" * 32
    prev_vout = 0
    accepted = []

    for i in range(MAX_ANCESTORS):
        tx = Transaction(
            1,
            [TxIn(prev_txid, prev_vout, b"x" * 65)],
            [TxOut(1000 - i, b"ori1qtestaddress0000000000000000000000000")],
            0,
        )
        ok, _reason = mempool.add(tx, fee=10)
        accepted.append(ok)
        prev_txid = tx.txid()
        prev_vout = 0

    assert accepted == [True] * MAX_ANCESTORS

    too_deep = Transaction(
        1,
        [TxIn(prev_txid, prev_vout, b"x" * 65)],
        [TxOut(1, b"ori1qtestaddress0000000000000000000000000")],
        0,
    )

    added, reason = mempool.add(too_deep, fee=10)
    assert added is False
    assert "ancestors" in reason


def test_headers_must_connect_to_requested_locator(tmp_path):
    cfg = Config(data_dir=str(tmp_path), enable_p2p=False)
    node = Node(cfg)
    node.chain.load()
    peer = type("PeerStub", (), {})()
    peer.addr = ("203.0.113.10", 8033)
    peer.expected_headers_from = node.chain.tip()["hash"]
    peer.scores = []

    def add_score(reason):
        peer.scores.append(reason)

    peer._add_ban_score = add_score
    header = BlockHeader(
        version=1,
        prev_hash=b"\x22" * 32,
        merkle_root=b"\x33" * 32,
        timestamp=1,
        bits=node.chain.next_bits(),
        nonce=0,
    )

    node.network.on_peer_headers(peer, [header.to_hex()])

    assert peer.scores == ["invalid_header"]
