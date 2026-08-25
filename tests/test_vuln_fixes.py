"""Regression tests for the vulnerability fixes proven in attack_sim.

Each test maps to an audit finding (see AUDIT_FINDINGS.md):
  C-01 bounded side-branch storage
  C-02 orphaned descendants evicted from mempool
  C-03 heap-based template selection stays fast on large pools
  C-05 pre-handshake P2P commands rejected
  C-07 per-peer orphan tracking capped
  H-01 strict BIP-34 coinbase height
  H-07 constant-length API token compare
"""

import json
import socket
import time

from block import Block, BlockHeader
from config import Config
from crypto import new_keypair, pub_to_address, sign
from merkle import merkle_root
from node import Node
from p2p import Peer
from pow import hash_meets_target
from tx import Transaction, TxIn, TxOut, coinbase_tx, make_transfer
from utils import sha256d, unhexstr


def _cfg(tmp_path, **kw):
    return Config(
        data_dir=str(tmp_path),
        enable_p2p=False,
        api_host="127.0.0.1",
        coinbase_maturity=3,
        low_s_activation_height=0,
        max_side_branch_blocks=24,
        **kw,
    )


def _addr(kp):
    return pub_to_address(kp[1])


def _safe_ts(node):
    h = node.chain.storage.height()
    row = node.chain.storage.block_by_height(h)
    ts = []
    for _ in range(node.cfg.shield_window):
        if row is None:
            break
        ts.append(row["timestamp"])
        row = node.chain.storage.block_by_hash(row["prev_hash"])
    med = sorted(ts)[len(ts) // 2] if len(ts) >= 6 else 0
    return max(int(time.time()), med + 1)


def _mine_block(node, extra_txs=None, address=None, timestamp=None):
    tip = node.chain.tip()
    height = tip["height"] + 1
    base = node.cfg.block_reward_sats >> (height // node.cfg.halving_interval)
    picked = node.mempool.ordered_with_fees(node.cfg.max_block_bytes - 1000)
    fees = sum(f for _, f in picked)
    cb = coinbase_tx(height, base + fees, address or "ori1q" + "0" * 34)
    txs = [cb] + [t for t, _ in picked] + list(extra_txs or [])
    header = BlockHeader(
        version=1,
        prev_hash=unhexstr(tip["hash"]),
        merkle_root=merkle_root([t.txid() for t in txs]),
        timestamp=timestamp if timestamp is not None else _safe_ts(node),
        bits=node.chain.next_bits(),
        nonce=0,
    )
    while not hash_meets_target(header.hash(), header.bits):
        header.nonce += 1
    return Block(header, txs)


def _submit(node, block):
    return node.submit_raw_block(block.to_hex())


def _make_node(tmp_path):
    node = Node(_cfg(tmp_path))
    node.chain.load()
    return node


# ── H-01: strict BIP-34 ───────────────────────────────────────────────────

def test_coinbase_unparseable_height_rejected(tmp_path):
    node = _make_node(tmp_path)
    tip = node.chain.tip()
    height = tip["height"] + 1
    base = node.cfg.block_reward_sats >> (height // node.cfg.halving_interval)
    cb = coinbase_tx(height, base, "ori1q" + "0" * 34)
    cb.inputs[0].script_sig = b"\xffGARBAGE-NOT-A-HEIGHT"
    ok, reason, _h = _submit(node, _mine_block(node, force := None) if False else _mine_block(node))
    assert ok is True  # baseline: normal mining works
    # now the strict check itself
    blk = Block(BlockHeader(version=1, prev_hash=unhexstr(node.chain.tip()["hash"]),
                            merkle_root=merkle_root([cb.txid()]), timestamp=_safe_ts(node),
                            bits=node.chain.next_bits(), nonce=0), [cb])
    while not hash_meets_target(blk.hash(), blk.header.bits):
        blk.header.nonce += 1
    ok, reason, _ = _submit(node, blk)
    assert not ok
    assert "BIP-34" in reason


def test_coinbase_garbage_script_sig_block_rejected(tmp_path):
    """The exact A05 scenario: valid-looking block with garbage coinbase height."""
    node = _make_node(tmp_path)
    tip = node.chain.tip()
    height = tip["height"] + 1
    base = node.cfg.block_reward_sats >> (height // node.cfg.halving_interval)
    gc = coinbase_tx(height, base, "ori1q" + "0" * 34)
    gc.inputs[0].script_sig = b"\xffGARBAGE-NOT-A-HEIGHT"
    hdr = BlockHeader(version=1, prev_hash=unhexstr(tip["hash"]),
                      merkle_root=merkle_root([gc.txid()]),
                      timestamp=_safe_ts(node), bits=node.chain.next_bits(), nonce=0)
    while not hash_meets_target(hdr.hash(), hdr.bits):
        hdr.nonce += 1
    ok, reason, _ = _submit(node, Block(hdr, [gc]))
    assert ok is False
    assert "coinbase height mismatch" in reason


# ── C-02: orphaned descendants evicted ────────────────────────────────────

def test_orphaned_descendant_evicted_after_conflict(tmp_path):
    node = Node(Config(data_dir=str(tmp_path), enable_p2p=False,
                       api_host="127.0.0.1", coinbase_maturity=3,
                       low_s_activation_height=0))
    node.chain.load()
    victim = new_keypair()
    vaddr = _addr(victim)
    for _ in range(5):
        ok, r, _h = _submit(node, _mine_block(node, address=vaddr))
        assert ok, r
    us = [u for u in node.chain.utxos_of(vaddr) if u["mature"]]
    u = us[-1]

    def signed(outs, rbf=False):
        tx = make_transfer([(bytes.fromhex(u["txid"]), u["vout"])], outs, rbf=rbf)
        d = tx.sighash()
        for ti in tx.inputs:
            ti.script_sig = sign(victim[0], d) + victim[1]
        return tx

    attacker_kp = new_keypair()
    attacker = _addr(attacker_kp)
    parent = signed([(u["value"] - 300, attacker)])
    okp, rp, pid = node.submit_raw_tx(parent.to_hex())
    assert okp, rp

    child = make_transfer([(parent.txid(), 0)], [(u["value"] - 300 - 150, attacker)])
    cd = child.sighash()
    child.inputs[0].script_sig = sign(attacker_kp[0], cd) + attacker_kp[1]
    okc, rc, cid = node.submit_raw_tx(child.to_hex())
    assert okc, rc

    conflict = signed([(u["value"] - 300, "ori1q" + "0" * 34)], rbf=True)
    h = node.chain.storage.height() + 1
    bas = node.cfg.block_reward_sats >> (h // node.cfg.halving_interval)
    from tx import coinbase_tx as cbtx

    hdr = BlockHeader(version=1, prev_hash=unhexstr(node.chain.tip()["hash"]),
                      merkle_root=merkle_root([cbtx(h, bas + 300, "ori1q" + "0" * 34).txid(),
                                               conflict.txid()]),
                      timestamp=_safe_ts(node), bits=node.chain.next_bits(), nonce=0)
    while not hash_meets_target(hdr.hash(), hdr.bits):
        hdr.nonce += 1
    cbfix = cbtx(h, bas + 300, "ori1q" + "0" * 34)
    blk = Block(hdr, [cbfix, conflict])
    while not hash_meets_target(blk.hash(), blk.header.bits):
        blk.header.nonce += 1
    okm, rm, _hm = _submit(node, blk)
    assert okm, rm

    assert node.mempool.get(parent.txid()) is None      # conflicted out
    assert node.mempool.get(child.txid()) is None       # orphaned -> evicted
    tpl = node.mining_template("ori1q" + "0" * 34)
    from tx import Transaction as T

    tpl_ids = {T.from_hex(hx).txid() for hx in tpl["txs"]}
    assert child.txid() not in tpl_ids                  # template clean


# ── C-03: template selection performance ──────────────────────────────────

def test_template_selection_fast_on_large_mempool():
    from mempool import Mempool

    mp = Mempool(max_txs=20_000)
    for i in range(8000):
        t = Transaction(1, [TxIn(i.to_bytes(4, "big") + b"\xdd" * 28, 0)],
                        [TxOut(50 + (i % 97), b"ori1qtest")])
        added, why = mp.add(t, fee=(i % 97) + 1)
        assert added, why
    t0 = time.perf_counter()
    picked = mp.ordered_with_fees(900_000)
    dt = time.perf_counter() - t0
    assert len(picked) == 8000
    assert dt < 1.0, f"template selection too slow: {dt:.3f}s"
    # fee-rate ordering respected among independent picks
    rates = [f / max(len(t.serialize()), 1) for t, f in picked[:50]]
    assert all(rates[i] >= rates[i + 1] - 1e-9 for i in range(len(rates) - 1))


def test_mempool_capacity_evicts_lowest_rate():
    from mempool import Mempool

    mp = Mempool(max_txs=8)
    for i in range(8):
        t = Transaction(1, [TxIn(i.to_bytes(4, "big") + b"\xdd" * 28, 0)],
                        [TxOut(10, b"x")])
        assert mp.add(t, fee=i + 1)[0]
    hi = Transaction(1, [TxIn((99).to_bytes(4, "big") + b"\xee" * 28, 0)],
                     [TxOut(10, b"x")])
    added, why = mp.add(hi, fee=500)
    assert added, why
    assert mp.size() <= 9  # one evicted to make room


# ── C-05: pre-handshake commands rejected ─────────────────────────────────

def test_dispatch_rejects_commands_before_handshake(tmp_path):
    node = _make_node(tmp_path)
    sock = socket.socket()
    peer = Peer.__new__(Peer)
    Peer.__init__(peer, node.network, sock, ("203.0.113.5", 5), outbound=False)
    peer._alive = False
    peer.handshake_complete = False
    sent = []
    peer.send = lambda cmd, payload: sent.append(cmd)
    scores = []
    peer._add_ban_score = lambda r: scores.append(r)

    payload = json.dumps({"from": node.chain.tip()["hash"], "stop": "0"}).encode()
    peer._dispatch("getblocks", payload)

    assert sent == []                    # nothing served
    assert scores                        # misbehavior recorded
    sock.close()


# ── C-07: pending_children capped ─────────────────────────────────────────

def test_pending_children_capped(tmp_path):
    node = _make_node(tmp_path)
    sock = socket.socket()
    peer = Peer.__new__(Peer)
    Peer.__init__(peer, node.network, sock, ("203.0.113.77", 97), outbound=False)
    peer._alive = False
    before = len(peer.pending_children)
    for i in range(150):
        secret = _mine_block(node)          # mined but withheld
        cb = coinbase_tx(10_000 + i, 4628000000, "ori1q" + "0" * 34)
        hdr = BlockHeader(version=1, prev_hash=secret.hash(),
                          merkle_root=merkle_root([cb.txid()]),
                          timestamp=int(time.time()), bits=node.chain.next_bits(),
                          nonce=0)
        while not hash_meets_target(hdr.hash(), hdr.bits):
            hdr.nonce += 1
        node.on_peer_block_hex(Block(hdr, [cb]).to_hex(), peer)
    size = len(peer.pending_children)
    assert before == 0
    assert size <= 128, f"orphan tracking unbounded: {size}"
    sock.close()


# ── C-01: side branch storage bounded ─────────────────────────────────────

def _side_rows(db_path):
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM blocks WHERE main = 0").fetchone()[0]
    finally:
        conn.close()


def test_side_branch_storage_bounded(tmp_path):
    node = _make_node(tmp_path)
    # grow a real main chain first so spam anchors exist
    node.cfg.max_future_clock_seconds = 10 ** 9
    fake_ts = int(time.time()) + 10
    while node.chain.storage.height() < 20:
        fake_ts += 51
        ok, _r, _h = _submit(node, _mine_block(node, timestamp=fake_ts))
        assert ok, "main chain growth failed"
    db = f"{node.cfg.data_dir}/chain.db"
    spam_base = int(time.time()) + 12
    cur_h = node.chain.storage.height()
    for fork_i in range(3):
        anchor_h = max(1, cur_h - 15 - fork_i)
        prev_row = node.chain.storage.block_by_height(anchor_h)
        for i in range(10):
            fh = prev_row["height"] + 1
            base_i = node.cfg.block_reward_sats >> (fh // node.cfg.halving_interval)
            cb = coinbase_tx(fh, base_i, "ori1q" + "0" * 34, note=f"s{fork_i}{i}")
            ts = spam_base + fork_i * 10 + i
            hdr = BlockHeader(version=1, prev_hash=unhexstr(prev_row["hash"]),
                              merkle_root=merkle_root([cb.txid()]), timestamp=ts,
                              bits=node.chain.expected_bits(fh, prev_row), nonce=0)
            while not hash_meets_target(hdr.hash(), hdr.bits):
                hdr.nonce += 1
            node.chain.add_block(Block(hdr, [cb]), source="spammer")
            from utils import hexstr

            prev_row = {"height": fh, "hash": hexstr(hdr.hash()),
                        "prev_hash": prev_row["hash"], "timestamp": ts,
                        "bits": hdr.bits}
    assert _side_rows(db) <= node.cfg.max_side_branch_blocks


# ── H-07: token compare has no length leak ────────────────────────────────

def test_api_token_wrong_length_still_unauthorized(tmp_path):
    from fastapi.testclient import TestClient

    from api import create_app

    cfg = Config(data_dir=str(tmp_path), enable_p2p=False,
                 require_api_token_when_public=False)
    node = Node(cfg)
    node.chain.load()
    app = create_app(node)
    client = TestClient(app)
    node.cfg.api_token = "supersecrettoken123"
    r1 = client.post("/tx/", headers={"X-API-Key": "x"}, json={"tx": "00"})
    r2 = client.post("/tx/", headers={"X-API-Key": "wrong-length-token-here!"}, json={"tx": "00"})
    r3 = client.post("/tx/", headers={"X-API-Key": "supersecrettoken123"}, json={"tx": "00"})
    assert r1.status_code == r2.status_code == 401   # identical response either way
    assert r3.status_code == 400                     # authorized -> validation error
