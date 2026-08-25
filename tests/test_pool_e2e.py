"""End-to-end pool server test: node + pool + fake miners (share & block paths)."""
import hashlib
import json
import os
import struct
import sys
import tempfile
import threading
import time

os.environ.setdefault("ORI_LOG_CONSOLE", "0")
os.environ.setdefault("ORI_LOG_FILE", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import urllib.request

from config import Config
from fastapi.testclient import TestClient
from merkle import merkle_root
from node import Node
from pow import hash_meets_target, target_from_bits, bits_for_zeros
from tx import coinbase_tx
from utils import unhexstr


def make_node(data_dir):
    cfg = Config(data_dir=data_dir, enable_p2p=False, api_host="127.0.0.1",
                 coinbase_maturity=3, low_s_activation_height=0)
    n = Node(cfg)
    n.chain.load()
    return n


def main():
    root = tempfile.mkdtemp(prefix="ori_pool_")
    node = make_node(os.path.join(root, "node"))

    # pool env must be set BEFORE importing pool_server
    from crypto import new_keypair, pub_to_address

    pool_addr = pub_to_address(new_keypair()[1])
    worker = pub_to_address(new_keypair()[1])
    os.environ["POOL_NODE_URL"] = "http://node.local"
    os.environ["POOL_ADDRESS"] = pool_addr
    os.environ["POOL_DATA_DIR"] = os.path.join(root, "pool")
    os.environ["POOL_DIFF_SHIFT"] = "8"  # easy shares for the test

    # monkeypatch the node HTTP layer: route _req directly into the Node
    import pool_server as ps

    def fake_req(method, path, body=None, timeout=20):
        from fastapi.testclient import TestClient

        client = TestClient(create_app_cached())
        if method == "GET":
            r = client.get(path)
        else:
            r = client.post(path, json=body)
        return r.status_code, r.json()

    _app_holder = {}

    def create_app_cached():
        return _app_holder["app"]

    from api import create_app as real_create_app

    _app_holder["app"] = real_create_app(node)
    ps._req = fake_req

    ps.TPL.refresh()
    assert ps.TPL.get() is not None, "template fetch failed"
    client = TestClient(ps.app)

    tpl = ps.TPL.get()
    height = int(tpl["height"])
    bits = int(tpl["bits"])
    prev_hash_display = tpl["prev_hash"]
    ts = int(tpl["timestamp"])
    job_id = f"{height}-{tpl['job_seq']}"

    job = client.get("/pool/job", params={"worker": worker}).json()
    assert job["coinbase_address"] == pool_addr
    pool_target = int(job["pool_target"], 16)
    node_target = target_from_bits(bits)
    assert pool_target > node_target, "pool target must be easier"

    # build header with correct merkle (coinbase -> POOL address + template txs)
    from tx import Transaction

    cb = coinbase_tx(height, int(tpl["reward_sats"]), pool_addr)
    txs = [cb] + [Transaction.from_hex(hx) for hx in tpl.get("txs", [])]
    mroot = merkle_root([t.txid() for t in txs])
    prev_internal = bytes.fromhex(prev_hash_display)[::-1]

    def mine_header(target_int):
        nonce = 0
        while True:
            hdr80 = struct.pack("<i", 1) + prev_internal + mroot + \
                struct.pack("<III", ts, bits, nonce)
            h = hashlib.sha256(hashlib.sha256(hdr80).digest()).digest()
            if int.from_bytes(h, "big") <= target_int:
                return hdr80
            nonce += 1
            if nonce > 40_000_000:
                raise RuntimeError("no solution")

    # 1) submit a SHARE (meets pool target only)
    share_hdr = mine_header(pool_target).hex()
    r = client.post("/pool/submit", json={
        "worker_addr": worker, "job_id": job_id, "header_hex": share_hdr})
    assert r.status_code == 200, r.text
    assert r.json()["is_block"] is False

    stats = client.get("/pool/stats").json()
    assert stats["shares_accepted"] >= 1
    lb = {e["worker"]: e for e in stats["leaderboard"]}
    assert lb[worker]["window_shares"] >= 1

    # 2) tampered header rejected (wrong merkle)
    bad = bytearray(mine_header(pool_target))
    bad[36] ^= 1
    r = client.post("/pool/submit", json={
        "worker_addr": worker, "job_id": job_id, "header_hex": bytes(bad).hex()})
    assert r.status_code == 400 and "merkle" in r.json()["detail"]

    # 3) stale/unknown job rejected
    r = client.post("/pool/submit", json={
        "worker_addr": worker, "job_id": "1-99", "header_hex": share_hdr})
    assert r.status_code == 400 and "stale" in r.json()["detail"]

    # 3b) jobs are unique per request (anti duplicate-nonce loop)
    j1 = client.get("/pool/job", params={"worker": worker}).json()
    j2 = client.get("/pool/job", params={"worker": worker}).json()
    assert j1["job_id"] != j2["job_id"], "job ids must be unique per request"

    # 3c) duplicate header rejected even under a fresh valid job
    j3 = client.get("/pool/job", params={"worker": worker}).json()
    tgt3 = int(j3["pool_target"], 16)
    hdr_dup = bytearray(bytes.fromhex(share_hdr))
    hdr_dup[68:72] = struct.pack("<I", j3["timestamp"] + 5)  # distinct ts
    while True:
        h = hashlib.sha256(hashlib.sha256(bytes(hdr_dup)).digest()).digest()
        if int.from_bytes(h, "big") <= tgt3:
            break
        nonce = struct.unpack_from("<I", hdr_dup, 76)[0] + 1
        struct.pack_into("<I", hdr_dup, 76, nonce)
    r = client.post("/pool/submit", json={
        "worker_addr": worker, "job_id": j3["job_id"], "header_hex": hdr_dup.hex()})
    assert r.status_code == 200, r.text          # first time accepted
    r = client.post("/pool/submit", json={
        "worker_addr": worker, "job_id": j3["job_id"], "header_hex": hdr_dup.hex()})
    assert r.status_code == 400 and "duplicate" in r.json()["detail"]

    # 4) REAL BLOCK path (mine at full network difficulty)
    block_hdr = mine_header(node_target).hex()
    before = node.storage.height()
    r = client.post("/pool/submit", json={
        "worker_addr": worker, "job_id": job_id, "header_hex": block_hdr})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_block"] is True, body
    assert node.storage.height() == before + 1, "block not relayed to node"
    payout = body["payout"]
    assert payout.get(worker, 0) > 0, "worker credited"

    stats2 = client.get("/pool/stats").json()
    assert stats2["blocks_found"] == 1
    assert stats2["balances"][worker] > 0

    print("POOL_E2E_OK",
          f"| shares={stats2['shares_accepted']}",
          f"| blocks={stats2['blocks_found']}",
          f"| credited={stats2['balances'][worker]} sats")


if __name__ == "__main__":
    main()
