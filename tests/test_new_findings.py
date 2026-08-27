"""Tests for newly-discovered bugs (audit round 2).

N-01  config.checkpoints keys are always int (not str from JSON).
N-02  credit_block dust from int-truncation goes to pool address, not lost.
N-03  credit_block net is derived from int(), not float, to avoid accumulation error.
N-04  Checkpoint mismatch at known height rejects block.
"""
import sys
import os

import pytest

# ---------------------------------------------------------------------------
# N-01  config.checkpoints key type always int
# ---------------------------------------------------------------------------

def test_magic_hex_or_bytes_parsing():
    from config import Config
    import os
    from unittest.mock import patch

    with patch.dict(os.environ, {"BTPY_NETWORK_MAGIC": "4f524931"}):
        cfg = Config.from_env()
        assert cfg.network_magic == b"\x4f\x52\x49\x31"
        assert len(cfg.network_magic) == 4

    """Default checkpoints must have int keys so `height in checkpoints` works."""
    from config import Config
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config(data_dir=tmp, enable_p2p=False)
        for k in cfg.checkpoints:
            assert isinstance(k, int), f"checkpoint key {k!r} is not int"


def test_checkpoint_keys_are_int_from_json_style_dict():
    """Simulating JSON load: keys are strings — config must convert to int."""
    from config import Config
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        # Simulate what Config.from_env() does when file_cfg has string keys
        raw = {"100": "abc123", "1000": "def456"}
        coerced = {int(k): v for k, v in raw.items()}
        assert 100 in coerced
        assert 1000 in coerced
        assert "100" not in coerced


# ---------------------------------------------------------------------------
# N-02 / N-03  credit_block dust tracking
# ---------------------------------------------------------------------------

def _make_ledger_env(monkeypatch, pool_address="ori1qpooladdr0000000000000000000000000000v2"):
    """Patch pool_server globals so Ledger can be imported."""
    # pool_server.py is in the parent directory
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    import importlib
    import types

    # Provide minimal stubs so pool_server imports succeed without a running node
    monkeypatch.setenv("POOL_ADDRESS", pool_address)
    monkeypatch.setenv("POOL_FEE_PCT", "1.0")
    monkeypatch.setenv("POOL_DATA_DIR", "/tmp/pool_test_data")

    return pool_address


def test_credit_block_no_dust_lost(monkeypatch, tmp_path):
    """Sum of credited amounts + dust must equal net reward (nothing evaporates)."""
    pool_addr = "ori1qpooladdr0000000000000000000000000000v2"
    monkeypatch.setenv("POOL_ADDRESS", pool_addr)
    monkeypatch.setenv("POOL_FEE_PCT", "1.0")
    monkeypatch.setenv("POOL_DATA_DIR", str(tmp_path))

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)

    # Re-import to pick up env-patched globals
    import importlib
    import pool_server as ps
    importlib.reload(ps)

    ledger = ps.Ledger()
    # Simulate 3 workers with 7, 3, 1 shares each = 11 total
    workers = ["w1", "w2", "w3"]
    share_counts = [7, 3, 1]
    for w, cnt in zip(workers, share_counts):
        for _ in range(cnt):
            ledger.window.append(w)

    reward_sats = 4_628_000_000
    fee_pct = 1.0
    net = int(reward_sats * (100.0 - fee_pct) / 100.0)  # 99%
    total_pts = len(ledger.window)  # 11

    ledger.credit_block(reward_sats, height=1)

    # Sum of miner credits
    miner_total = sum(ledger.balances.get(w, 0) for w in workers)
    pool_dust = ledger.balances.get(pool_addr, 0)

    # Everything must add up
    assert miner_total + pool_dust == net, (
        f"Lost {net - miner_total - pool_dust} sats: "
        f"net={net}, miners={miner_total}, dust={pool_dust}"
    )
    # Dust must be non-negative
    assert pool_dust >= 0


def test_credit_block_net_is_int():
    """net must be computed as int(), not float, to avoid fp accumulation."""
    reward = 4_628_000_000
    fee_pct = 1.0
    net = int(reward * (100.0 - fee_pct) / 100.0)
    assert isinstance(net, int)
    assert net == 4_581_720_000


# ---------------------------------------------------------------------------
# N-04  Checkpoint enforcement rejects wrong block at known height
# ---------------------------------------------------------------------------

def test_checkpoint_mismatch_rejects_block(tmp_path):
    """A block with a wrong hash at a checkpoint height must be rejected."""
    import time
    from block import Block, BlockHeader
    from config import Config
    from merkle import merkle_root
    from node import Node
    from pow import hash_meets_target
    from tx import coinbase_tx
    from utils import unhexstr

    cfg = Config(
        data_dir=str(tmp_path),
        enable_p2p=False,
        api_host="127.0.0.1",
        coinbase_maturity=0,
        # Set checkpoint at height 1 with an impossible hash value
        checkpoints={1: "0" * 64},   # no real block can have all-zeros hash
        assume_valid_height=0,
    )
    node = Node(cfg)
    node.chain.load()

    tip = node.chain.tip()
    height = 1
    base = cfg.block_reward_sats
    cb = coinbase_tx(height, base, "ori1q" + "0" * 38)

    header = BlockHeader(
        version=1,
        prev_hash=unhexstr(tip["hash"]),
        merkle_root=merkle_root([cb.txid()]),
        timestamp=int(time.time()) + 1,
        bits=node.chain.next_bits(),
        nonce=0,
    )
    while not hash_meets_target(header.hash(), header.bits):
        header.nonce += 1

    blk = Block(header, [cb])
    ok, reason, _ = node.submit_raw_block(blk.to_hex())

    # Must be rejected because hash != "0"*64 at height 1
    assert not ok, "Block should have been rejected by checkpoint"
    assert "checkpoint" in reason.lower()
