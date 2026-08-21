import threading

from bech32 import bech32_encode
from miner import _auth_headers, mine_one
from pow import bits_for_zeros, hash_meets_target, target_from_bits


def _easy_template():
    bits = bits_for_zeros(1)
    return {
        "height": 1,
        "reward_sats": 1000,
        "txs": [],
        "prev_hash": "00" * 32,
        "timestamp": 1,
        "bits": bits,
        "target": hex(target_from_bits(bits)),
    }


def test_miner_finds_valid_block_with_full_kernel():
    address = bech32_encode("ori", 0, b"\x04" * 20)

    block, stats = mine_one(
        _easy_template(),
        address,
        worker_count=2,
        batch_nonces=4096,
        kernel_name="full",
        refresh_seconds=5,
    )

    assert block is not None
    assert hash_meets_target(block.hash(), block.header.bits)
    assert stats["tries"] > 0


def test_miner_auth_header_includes_api_key():
    assert _auth_headers("secret")["X-API-Key"] == "secret"
    assert "X-API-Key" not in _auth_headers("")


def test_miner_external_cancel_returns_without_block():
    address = bech32_encode("ori", 0, b"\x05" * 20)
    cancel = threading.Event()
    cancel.set()

    block, stats = mine_one(
        _easy_template(),
        address,
        worker_count=1,
        found_event=cancel,
        batch_nonces=4096,
        kernel_name="full",
        refresh_seconds=5,
    )

    assert block is None
    assert stats["rate"] >= 0
