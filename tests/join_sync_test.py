"""Two-node join/sync repro: fresh node joins a seeded node and must download blocks."""
import os
import sys
import tempfile
import time

os.environ.setdefault("ORI_LOG_CONSOLE", "1")
os.environ.setdefault("ORI_LOG_FILE", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from node import Node


def make_node(data_dir, p2p_port, seed_dns_host=""):
    cfg = Config(
        data_dir=data_dir,
        enable_p2p=True,
        p2p_host="127.0.0.1",
        p2p_port=p2p_port,
        api_host="127.0.0.1",
        coinbase_maturity=3,
        low_s_activation_height=0,
        max_side_branch_blocks=24,
        seed_dns_host=seed_dns_host,
        seed_peers=[],
    )
    n = Node(cfg)
    n.chain.load()
    return n


def main():
    root = tempfile.mkdtemp(prefix="ori_join_")
    a = make_node(os.path.join(root, "A"), 28033)
    b = make_node(os.path.join(root, "B"), 28034)

    a.start()
    b.start()

    # Seed node A with 25 blocks
    from block import BlockHeader
    from merkle import merkle_root
    from pow import hash_meets_target
    from tx import coinbase_tx
    from utils import unhexstr

    def safe_ts(node):
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

    def mine(node):
        tip = node.chain.tip()
        h = tip["height"] + 1
        base = node.cfg.block_reward_sats >> (h // node.cfg.halving_interval)
        cb = coinbase_tx(h, base, "ori1q" + "0" * 34)
        hdr = BlockHeader(version=1, prev_hash=unhexstr(tip["hash"]),
                          merkle_root=merkle_root([cb.txid()]),
                          timestamp=safe_ts(node), bits=node.chain.next_bits(), nonce=0)
        while not hash_meets_target(hdr.hash(), hdr.bits):
            hdr.nonce += 1
        ok, r, _hh = node.submit_raw_block(Block(hdr, [cb]).to_hex())
        assert ok, r

    from block import Block

    for i in range(25):
        mine(a)
        fake = safe_ts(a) + i  # ensure increasing
    print(f"A height={a.storage.height()}")

    # B joins A manually (same path as PeersDialog add-node)
    b.add_peer("127.0.0.1", 28033)

    deadline = time.time() + 40
    last_h = -1
    while time.time() < deadline:
        time.sleep(1)
        h = b.storage.height()
        if h != last_h:
            print(f"B height={h}  peers={b.network.peer_count()}  t={int(40-(deadline-time.time()))}s")
            last_h = h
        if h >= a.storage.height():
            break

    ok = b.storage.height() >= a.storage.height()
    print("\nJOIN_SYNC:", "OK" if ok else f"FAILED (B stuck at {b.storage.height()}, A={a.storage.height()})")
    a.stop(); b.stop()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
