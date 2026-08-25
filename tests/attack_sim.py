#!/usr/bin/env python3
"""ORI blockchain attack simulation suite.

Runs adversarial scenarios against an in-process node and reports
DEFENDED / VULNERABLE / PERF / OK per attack. Run from project root:

    .venv\\Scripts\\python.exe tests\\attack_sim.py

NOTE: utxo["txid"] values are RAW byte-order hex -> rebuild with
bytes.fromhex(); unhexstr() is ONLY for display-order (header) hashes.
"""

import os
import shutil
import sys
import tempfile
import time

os.environ.setdefault("ORI_LOG_CONSOLE", "0")
os.environ.setdefault("ORI_LOG_FILE", "0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bech32 import bech32_encode  # noqa: E402
from block import Block, BlockHeader  # noqa: E402
from config import Config  # noqa: E402
from crypto import new_keypair, pub_to_address, sign  # noqa: E402
from crypto import sig_is_low_s  # noqa: E402
from merkle import merkle_root  # noqa: E402
from node import Node  # noqa: E402
from pow import bits_for_zeros, hash_meets_target  # noqa: E402
from tx import Transaction, TxIn, TxOut, coinbase_tx, make_transfer  # noqa: E402
from utils import sha256d, unhexstr, hexstr  # noqa: E402

RESULTS = []


def report(name, status, detail=""):
    RESULTS.append((name, status, detail))
    print(f"[{status:10s}] {name}" + (f" â€” {detail}" if detail else ""))


def make_node(data_dir):
    cfg = Config(
        data_dir=data_dir,
        enable_p2p=False,
        api_host="127.0.0.1",
        coinbase_maturity=3,
        coinbase_maturity_activation_height=0,
        low_s_activation_height=0,
        # Small side-branch cap so the boundedness defense is observable fast
        max_side_branch_blocks=24,
    )
    node = Node(cfg)
    node.chain.load()
    return node


def addr_for(kp):
    return pub_to_address(kp[1])


def safe_ts(node):
    """Timestamp > median-time-past of last 11 main blocks (and sane upper bound)."""
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


def mine_block(node, extra_txs=None, address=None, timestamp=None, force_coinbase=None):
    tip = node.chain.tip()
    height = tip["height"] + 1
    base = node.cfg.block_reward_sats >> (height // node.cfg.halving_interval)
    if force_coinbase is not None:
        cb = force_coinbase
        picked = []
    else:
        picked = node.mempool.ordered_with_fees(node.cfg.max_block_bytes - 1000)
        fees = sum(f for _, f in picked)
        cb = coinbase_tx(height, base + fees, address or GENESIS_ADDR)
    txs = [cb] + [tx for tx, _f in picked] + list(extra_txs or [])
    header = BlockHeader(
        version=1,
        prev_hash=unhexstr(tip["hash"]),
        merkle_root=merkle_root([t.txid() for t in txs]),
        timestamp=timestamp if timestamp is not None else safe_ts(node),
        bits=node.chain.next_bits(),
        nonce=0,
    )
    while not hash_meets_target(header.hash(), header.bits):
        header.nonce += 1
        if header.nonce >= 2 ** 32:
            raise RuntimeError("nonce space exhausted")
    return Block(header, txs)


def submit(node, block):
    return node.submit_raw_block(block.to_hex())


_ORDER = None


def sig_order():
    global _ORDER
    if _ORDER is None:
        from ecdsa import SECP256k1

        _ORDER = SECP256k1.order
    return _ORDER


def sign_spend(sender_kp, utxo_list, outs, rbf=False):
    priv, pub = sender_kp
    tx = make_transfer(
        [(bytes.fromhex(u["txid"]), u["vout"]) for u in utxo_list], outs, rbf=rbf
    )
    digest = tx.sighash()
    for txin in tx.inputs:
        txin.script_sig = sign(priv, digest) + pub
    return tx


def fund(node, kp, nblocks=5):
    a = addr_for(kp)
    for _ in range(nblocks):
        ok, reason, _h = submit(node, mine_block(node, address=a))
        assert ok, reason
    return a


def mature_utxos(node, addr):
    return [u for u in node.chain.utxos_of(addr) if u["mature"]]


GENESIS_KP = new_keypair()
GENESIS_ADDR = addr_for(GENESIS_KP)


def main():
    tmp = tempfile.mkdtemp(prefix="ori_attack_")
    try:
        run_all(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    vuln = [r for r in RESULTS if r[1] == "VULNERABLE"]
    print("\n" + "=" * 72)
    print(f"TOTAL: {len(RESULTS)}  |  DEFENDED/OK: {len(RESULTS)-len(vuln)-sum(1 for r in RESULTS if r[1]=='PERF')}  |  "
          f"PERF probes: {sum(1 for r in RESULTS if r[1]=='PERF')}  |  VULNERABLE: {len(vuln)}")
    print("=" * 72)
    for name, _, detail in vuln:
        print(f"  !! {name}: {detail}")


def run_all(root):
    node = make_node(os.path.join(root, "n1"))
    victim_kp = new_keypair()
    attacker_kp = new_keypair()
    aaddr = addr_for(attacker_kp)

    # -- Consensus-level attacks ------------------------------------
    b = mine_block(node)
    b.header.nonce += 1
    ok, reason, _h = submit(node, b)
    report("A01 invalid-PoW block", "DEFENDED" if not ok else "VULNERABLE", reason)

    b = mine_block(node)
    b.header.merkle_root = sha256d(b"evil")
    ok, reason, _h = submit(node, b)
    report("A02 forged-merkle block", "DEFENDED" if not ok else "VULNERABLE", reason)

    tip = node.chain.tip()
    height = tip["height"] + 1
    base = node.cfg.block_reward_sats >> (height // node.cfg.halving_interval)
    fat_cb = coinbase_tx(height, base * 100, GENESIS_ADDR)
    ok, reason, _h = submit(node, mine_block(node, force_coinbase=fat_cb))
    report("A03 coinbase inflation x100",
           "DEFENDED" if not ok else "VULNERABLE", reason)

    wrong_h = coinbase_tx(height + 999, base, GENESIS_ADDR)
    ok, reason, _h = submit(node, mine_block(node, force_coinbase=wrong_h))
    report("A04 coinbase claims wrong height (BIP-34)",
           "DEFENDED" if not ok else "VULNERABLE", reason)

    gc = coinbase_tx(height, base, GENESIS_ADDR)
    gc.inputs[0].script_sig = b"\xffGARBAGE-NOT-A-HEIGHT"
    ok, reason, _h = submit(node, mine_block(node, force_coinbase=gc))
    report("A05 coinbase UNPARSEABLE height (strict BIP-34 gap)",
           "DEFENDED" if not ok else "VULNERABLE",
           reason + ("  <-- block ACCEPTED" if ok else ""))

    b = mine_block(node)
    b.header.bits = bits_for_zeros(1)
    b.header.nonce = 0
    while not hash_meets_target(b.hash(), b.header.bits):
        b.header.nonce += 1
    ok, reason, _h = submit(node, b)
    report("A06 under-difficulty bits (easy block)",
           "DEFENDED" if not ok else "VULNERABLE", reason)

    ok, reason, _h = submit(node, mine_block(node, timestamp=int(time.time()) + 660))
    report("A07 far-future timestamp",
           "DEFENDED" if not ok else "VULNERABLE", reason)

    # -- Fund victim (5 mature coinbases) --------------------------
    vaddr = fund(node, victim_kp, 6)

    # -- Signature / policy level ----------------------------------
    def fresh_victim_utxo():
        """Return a mature victim UTXO not already claimed by mempool txs."""
        claimed = set(node.mempool._inputs.keys())
        us = [x for x in mature_utxos(node, vaddr)
              if (bytes.fromhex(x["txid"]), x["vout"]) not in claimed]
        assert us, "no free victim utxo"
        return us[len(us) - 1]

    u = fresh_victim_utxo()
    evil_kp = new_keypair()
    tx = sign_spend(victim_kp, [u], [(u["value"] - 100, aaddr), (100, vaddr)])
    good_digest = tx.sighash()
    tx.inputs[0].script_sig = sign(evil_kp[0], good_digest) + victim_kp[1]
    ok, reason, _ = node.submit_raw_tx(tx.to_hex())
    report("A09 forged signature (attacker key)",
           "DEFENDED" if not ok else "VULNERABLE", reason)

    u = fresh_victim_utxo()
    tx2 = sign_spend(victim_kp, [u], [(u["value"] - 100, aaddr), (100, vaddr)])
    s_low = tx2.inputs[0].script_sig[:64]
    assert sig_is_low_s(s_low)
    s = int.from_bytes(s_low[32:], "big")
    malleated = s_low[:32] + (sig_order() - s).to_bytes(32, "big")
    tx2.inputs[0].script_sig = malleated + victim_kp[1]
    ok, reason, _ = node.submit_raw_tx(tx2.to_hex())
    report("A10 high-S malleated signature",
           "DEFENDED" if not ok else "VULNERABLE", reason)

    u = fresh_victim_utxo()
    tx3 = sign_spend(victim_kp, [u], [(u["value"] * 10, aaddr)])
    ok, reason, _ = node.submit_raw_tx(tx3.to_hex())
    report("A11 overspend (outputs>inputs)",
           "DEFENDED" if not ok else "VULNERABLE", reason)

    u = fresh_victim_utxo()
    tx4 = make_transfer([(bytes.fromhex(u["txid"]), u["vout"]),
                         (bytes.fromhex(u["txid"]), u["vout"])],
                        [(u["value"], aaddr)])
    sig4 = sign(victim_kp[0], tx4.sighash()) + victim_kp[1]
    for ti in tx4.inputs:
        ti.script_sig = sig4
    ok, reason, _ = node.submit_raw_tx(tx4.to_hex())
    report("A12 duplicate input in one tx",
           "DEFENDED" if not ok else "VULNERABLE", reason)

    # immaturity: mine ONE fresh coinbase, try to spend immediately
    akp2 = new_keypair()
    a2 = fund(node, akp2, 1)
    au = node.chain.utxos_of(a2)[0]  # immature by construction
    txi = sign_spend(akp2, [au], [(au["value"] - 50, aaddr)])
    ok, reason, _ = node.submit_raw_tx(txi.to_hex())
    report("A13 spend immature coinbase",
           "DEFENDED" if not ok else "VULNERABLE", reason)

    # -- Mempool double spend without RBF signal ------------------â”€
    u = fresh_victim_utxo()
    t_a = sign_spend(victim_kp, [u], [(u["value"] - 200, aaddr)])
    ok1, r1, _ = node.submit_raw_tx(t_a.to_hex())
    t_b = sign_spend(victim_kp, [u], [(u["value"] - 200, GENESIS_ADDR)])
    ok2, r2, _ = node.submit_raw_tx(t_b.to_hex())
    verdict = "DEFENDED" if (ok1 and not ok2) else "VULNERABLE"
    report("A14 mempool double-spend (no RBF)", verdict,
           f"first={'ok' if ok1 else r1}; second={'ACCEPTED!' if ok2 else r2}")

    # -- C-02 orphaned descendant poisons miner template ----------â”€
    u = fresh_victim_utxo()
    parent = sign_spend(victim_kp, [u], [(u["value"] - 300, aaddr)])
    okp, _, pid = node.submit_raw_tx(parent.to_hex())
    child = make_transfer([(parent.txid(), 0)],
                          [(u["value"] - 300 - 150, GENESIS_ADDR)])
    child.inputs[0].script_sig = sign(attacker_kp[0], child.sighash()) + attacker_kp[1]
    okc, rc_, cid = node.submit_raw_tx(child.to_hex())
    # attacker confirms CONFLICTING version of parent directly on-chain
    # (force_coinbase keeps mempool txs out of this block)
    conflict = sign_spend(victim_kp, [u], [(u["value"] - 300, GENESIS_ADDR)], rbf=True)
    h2 = node.chain.storage.height() + 1
    bas2 = node.cfg.block_reward_sats >> (h2 // node.cfg.halving_interval)
    okm, reasonm, _hm = submit(
        node,
        mine_block(node, extra_txs=[conflict],
                   force_coinbase=coinbase_tx(h2, bas2 + 300, GENESIS_ADDR)))
    child_alive = node.mempool.get(child.txid()) is not None
    tpl = node.mining_template(GENESIS_ADDR)
    tpl_ids = {Transaction.from_hex(hx).txid() for hx in tpl["txs"]}
    dbg = f"okp={okp} okc={okc} mined={okm}/{reasonm}"
    if child_alive:
        if child.txid() in tpl_ids:
            report("C-02 orphaned descendant poisons miner template", "VULNERABLE",
                   f"parent double-spent on-chain ({reasonm if not okm else 'mined'}), "
                   f"child {cid[:12]}â€¦ STILL in mempool AND selected into template")
        else:
            report("C-02 orphaned descendant poisons miner template", "DEFENDED",
                   "child remained in mempool but excluded from template")
    else:
        report("C-02 orphaned descendant poisons miner template", "DEFENDED",
               "child evicted together with conflicted parent " + dbg)

    # -- R-01 honest more-work reorg must be accepted --------------
    tip_before = node.chain.tip()
    depth = 3
    cur_h = node.chain.storage.height()
    anchor = node.chain.storage.block_by_height(cur_h - depth)
    prev_row = anchor
    fork_ts0 = int(time.time()) + 10
    last_result = (False, "not-run", None)
    for i in range(depth + 2):  # longer than pruned segment => more work
        fh = prev_row["height"] + 1
        bas_i = node.cfg.block_reward_sats >> (fh // node.cfg.halving_interval)
        cbf = coinbase_tx(fh, bas_i, GENESIS_ADDR)
        hdr = BlockHeader(version=1, prev_hash=unhexstr(prev_row["hash"]),
                          merkle_root=merkle_root([cbf.txid()]),
                          timestamp=fork_ts0 + i,
                          bits=node.chain.expected_bits(fh, prev_row),
                          nonce=0)
        while not hash_meets_target(hdr.hash(), hdr.bits):
            hdr.nonce += 1
        blkf = Block(hdr, [cbf])
        last_result = node.chain.add_block(blkf, source="test")
        prev_row = {"height": fh, "hash": hexstr(blkf.hash()),
                    "prev_hash": prev_row["hash"], "timestamp": hdr.timestamp,
                    "bits": hdr.bits}
    ok_r, reason_r, h_r = last_result
    good = (ok_r and node.chain.tip()["hash"] != tip_before["hash"]
            and node.chain.storage.height() == tip_before["height"] + 2)
    report("R-01 honest more-work reorg accepted", "OK" if good else "VULNERABLE",
           f"final={ok_r}/{reason_r} tip_h={node.chain.storage.height()} "
           f"(was {tip_before['height']})")

    # -- C-01 disk-fill via weak-fork junk ------------------------â”€
    db_path = os.path.join(node.cfg.data_dir, "chain.db")
    before_side = count_side_rows(db_path)
    before_size = os.path.getsize(db_path)
    cur_h = node.chain.storage.height()
    spam_base = int(time.time()) + 12
    for fork_i in range(3):
        anchor_h = max(1, cur_h - 15 - fork_i)
        prev_row = node.chain.storage.block_by_height(anchor_h)
        for i in range(10):
            fh = prev_row["height"] + 1
            bas_i = node.cfg.block_reward_sats >> (fh // node.cfg.halving_interval)
            cbf = coinbase_tx(fh, bas_i, GENESIS_ADDR, note=f"spam-{fork_i}-{i}")
            ts = spam_base + fork_i * 10 + i
            hdr = BlockHeader(version=1, prev_hash=unhexstr(prev_row["hash"]),
                              merkle_root=merkle_root([cbf.txid()]), timestamp=ts,
                              bits=node.chain.expected_bits(fh, prev_row), nonce=0)
            while not hash_meets_target(hdr.hash(), hdr.bits):
                hdr.nonce += 1
            node.chain.add_block(Block(hdr, [cbf]), source="spammer")
            prev_row = {"height": fh, "hash": hexstr(hdr.hash()),
                        "prev_hash": prev_row["hash"], "timestamp": ts, "bits": hdr.bits}
    delta = count_side_rows(db_path) - before_side
    after_size = os.path.getsize(db_path)
    side_now = count_side_rows(db_path)
    cap = node.cfg.max_side_branch_blocks
    bounded = side_now <= cap and delta <= cap + 4  # small slack for reorg leftovers
    report("C-01 weak-fork spam storage is BOUNDED",
           "DEFENDED" if bounded else "VULNERABLE",
           f"+{delta} junk side rows, total side rows={side_now} "
           f"(cap={cap}, FIFO-evicted beyond cap; db {before_size//1024}KB->{after_size//1024}KB)")

    # -- C-07 pending_children unbounded --------------------------â”€
    from p2p import Peer
    import socket as _socket
    sock = _socket.socket()
    peer = Peer.__new__(Peer)
    Peer.__init__(peer, node.network, sock, ("203.0.113.77", 97), outbound=False)
    peer._alive = False  # ensure run loop never starts
    pc_before = len(peer.pending_children)
    ORPHANS = 150  # exceed the 128 cap to prove boundedness
    for i in range(ORPHANS):
        # secret parent mined but NEVER submitted (withheld), child sent first
        secret = mine_block(node, address=GENESIS_ADDR)
        child_hdr = BlockHeader(version=1, prev_hash=secret.hash(),
                                merkle_root=merkle_root([child_cb(i).txid()]),
                                timestamp=int(time.time()), bits=node.chain.next_bits(),
                                nonce=0)
        while not hash_meets_target(child_hdr.hash(), child_hdr.bits):
            child_hdr.nonce += 1
        child_blk = Block(child_hdr, [child_cb(i)])
        node.on_peer_block_hex(child_blk.to_hex(), peer)
    grown = len(peer.pending_children) - pc_before
    capped = len(peer.pending_children) <= 128
    report("C-07 orphan tracking bounded per peer",
           "DEFENDED" if capped else "VULNERABLE",
           f"after {ORPHANS} withheld-parent blocks: size={len(peer.pending_children)} "
           f"(cap=128, FIFO-evicted)" if capped else
           f"unbounded: size={len(peer.pending_children)} after {ORPHANS}")

    # -- C-03 template selection quadratic blowup ------------------
    mp = node.mempool
    with mp._lock:
        for attr in ("_txs", "_fees", "_inputs", "_ancestors", "_descendants",
                     "_clusters", "_cluster_txs", "_cluster_fee", "_cluster_size",
                     "_tx_sizes", "_times"):
            getattr(mp, attr).clear()
        for i in range(8000):
            t = Transaction(1, [TxIn((b"\xee" * 31) + bytes([i % 256, i // 256 % 256,
                                                      i // 65536 % 256])[:2] + bytes([i >> 16 & 255, i >> 24 & 255])
                                     if False else ((i).to_bytes(4, "big") + b"\xdd" * 28),
                                     0, sequence=0xFFFFFFFF)],
                            [TxOut(50 + (i % 97), GENESIS_ADDR.encode())])
            mp.add(t, fee=(i % 97) + 1)
    n = mp.size()
    t0 = time.perf_counter()
    picked = mp.ordered_with_fees(900_000)
    dt = time.perf_counter() - t0
    report("C-03 template selection cost @8k txs", "PERF",
           f"{dt*1000:.0f} ms for {n} txs -> extrapolates to ~{dt*1000*(100000/max(n,1))**2/1000:.0f}s at 100k cap")

    # -- C-04 getheaders loads entire chain per request ------------
    with mp._lock:
        for attr in ("_txs", "_fees", "_inputs", "_ancestors", "_descendants",
                     "_clusters", "_cluster_txs", "_cluster_fee", "_cluster_size",
                     "_tx_sizes", "_times"):
            getattr(mp, attr).clear()
    # synthetic clock so bulk growth doesn't trip future-limit nor crush
    # difficulty via compressed timestamps
    node.cfg.max_future_clock_seconds = 10 ** 9
    grow_to(node, 260, clock_step=51)
    class Sink:
        addr = ("203.0.113.9", 1)
        sent = []
        def send(self, cmd, payload):
            self.sent.append(len(payload))
    sp = Sink()
    genesis_hash = node.chain.genesis_hash()
    times = []
    sizes = []
    for k in range(6):
        sp.sent.clear()
        t0 = time.perf_counter()
        node.network.reply_headers(sp, genesis_hash, "0" * 64, 200)
        times.append(time.perf_counter() - t0)
        sizes.append(sum(sp.sent))
    h = node.chain.storage.height()
    report("C-04 getheaders loads ENTIRE chain per request", "PERF",
           f"{(sum(times)/len(times))*1000:.1f} ms/call serving 200 headers "
           f"on {h}-block chain (all_blocks() parsed every call)")

    # -- C-05 commands processed BEFORE handshake ------------------
    sock2 = _socket.socket()
    peer2 = Peer.__new__(Peer)
    Peer.__init__(peer2, node.network, sock2, ("203.0.113.5", 5), outbound=False)
    peer2._alive = False
    peer2.handshake_complete = False
    sent_cmds = []
    peer2.send = lambda cmd, payload: sent_cmds.append(cmd)
    payload = ('{"from":"%s","stop":"0"}' % node.chain.tip()["hash"]).encode()
    served = False
    try:
        peer2._dispatch("getblocks", payload)
        served = "inv" in sent_cmds
    except Exception as exc:
        served = f"error:{exc}"
    report("C-05 commands processed BEFORE version handshake",
           "VULNERABLE" if served is True else "DEFENDED",
           "getblocks served full inventory pre-handshake" if served is True else str(served))

    # -- H-08 localhost CLI usability on QT-launched node ----------
    # Fix: the desktop wallet binds its embedded API to 127.0.0.1 by
    # default, so local CLI tools are NOT blocked (private bind never
    # requires a token). A deliberately public bind without token still
    # blocks mutations by design.
    from fastapi.testclient import TestClient
    from api import create_app

    n2 = make_node(os.path.join(root, 'n2'))
    n2.cfg.api_host = '127.0.0.1'  # QT controller default
    client = TestClient(create_app(n2))
    r = client.post('/tx/', json={'tx': '00'})
    report('H-08 local CLI works on loopback-bound node',
           'DEFENDED' if r.status_code != 403 else 'VULNERABLE',
           f'POST /tx/ from localhost -> HTTP {r.status_code} '
           + ('(400 malformed = endpoint reachable)' if r.status_code == 400 else str(r.status_code)))

    # -- End-to-end legit transfer sanity --------------------------
    with mp._lock:
        for attr in ("_txs", "_fees", "_inputs", "_ancestors", "_descendants",
                     "_clusters", "_cluster_txs", "_cluster_fee", "_cluster_size",
                     "_tx_sizes", "_times"):
            getattr(mp, attr).clear()
    recv_kp = new_keypair()
    raddr = addr_for(recv_kp)
    u = None
    us = mature_utxos(node, vaddr)
    if not us:
        fund(node, victim_kp, 4)
        us = mature_utxos(node, vaddr)
    u = us[-1]
    t = sign_spend(victim_kp, [u], [(u["value"] - 400, raddr), (300, vaddr)])
    okt, rt, tid = node.submit_raw_tx(t.to_hex())
    okb = False
    if okt:
        okb, _rb2, _hb2 = submit(node, mine_block(node))
    okb = False
    rb_reason = ""
    if okt:
        okb, rb_reason, _hb2 = submit(node, mine_block(node))
    bal = node.chain.balance(raddr)
    report("E2E normal transfer confirm", "OK" if bal == u["value"] - 400 else "VULNERABLE",
           f"recipient balance={bal} (tx_ok={okt}/{rt}, block_ok={okb}/{rb_reason})")


def child_cb(i):
    bas = 4628000000
    return coinbase_tx(10_000 + i, bas, GENESIS_ADDR, note="orphan-bait")


class _SecretTipView:
    """Mine on top of current tip WITHOUT committing, to create withheld blocks."""
    def __init__(self, node):
        self.node = node

    def tip(self):
        return self.node.chain.tip()

    def next_bits(self):
        return self.node.chain.next_bits()


def node_secret_view(node):
    return _SecretTipView(node)


def grow_to(node, target, clock_step=0):
    fake_ts = None
    if clock_step:
        from pow import target_from_bits
        fake_ts = safe_ts(node)
    while node.chain.storage.height() < target:
        ts = None
        if fake_ts is not None:
            fake_ts += clock_step
            ts = fake_ts
        ok, _r, _h = submit(node, mine_block(node, timestamp=ts))
        if not ok:
            break


def count_side_rows(db_path):
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM blocks WHERE main = 0").fetchone()[0]
    finally:
        conn.close()


if __name__ == "__main__":
    main()


