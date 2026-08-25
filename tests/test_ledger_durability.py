"""Ledger durability tests: survive restart, corruption falls back to .bak."""
import json
import os
import sys
import tempfile

os.environ.setdefault("ORI_LOG_CONSOLE", "0")
os.environ.setdefault("ORI_LOG_FILE", "0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crypto import new_keypair, pub_to_address  # noqa: E402


def make_ledger(data_dir):
    os.environ["POOL_ADDRESS"] = pub_to_address(new_keypair()[1])
    os.environ["POOL_DATA_DIR"] = data_dir
    import importlib

    import pool_server

    importlib.reload(pool_server)
    return pool_server.Ledger(), pool_server


def main():
    root = tempfile.mkdtemp(prefix="ori_ledger_")
    dd = os.path.join(root, "pool")

    # ── 1. accumulate state, then "restart" (fresh Ledger instance) ──────
    led, ps = make_ledger(dd)
    w1 = pub_to_address(new_keypair()[1])
    for _ in range(7):
        led.add_share(w1)
    with led.lock:
        led.credit_block(4628000000, height=100)
    led.save()
    bal_before = dict(led.balances)
    win_before = len(led.window)
    hist_before = len(led.blocks_history)

    led2, _ = make_ledger(dd)  # simulated redeploy
    assert dict(led2.balances) == bal_before, "balances lost on restart"
    assert len(led2.window) == win_before, "window lost on restart"
    assert len(led2.blocks_history) == hist_before, "block history lost"
    assert led2.workers[w1]["shares"] >= 1, "worker counters lost"
    print(f"[ok] restart survival: balances={bal_before} window={win_before} "
          f"history={hist_before}")

    # ── 2. corrupt primary -> must fall back to .bak, NOT zero ───────────
    with open(os.path.join(dd, "ledger.json"), "w") as f:
        f.write("{CORRUPTED!!!")
    led3, _ = make_ledger(dd)
    assert dict(led3.balances) == bal_before, ".bak fallback failed"
    assert len(led3.window) == win_before
    print("[ok] corrupt primary recovered from ledger.json.bak")

    # ── 3. atomic write: no .tmp debris; corrupt primary never poisons .bak
    led3.add_share(w1)
    assert not os.path.exists(os.path.join(dd, "ledger.json.tmp"))
    snap = json.load(open(os.path.join(dd, "ledger.json")))
    bak = json.load(open(os.path.join(dd, "ledger.json.bak")))
    # .bak must still hold the last GOOD state (pre-corruption), primary +1
    assert snap["total_shares"] == bak["total_shares"] + 1
    assert bak["balances"] == bal_before
    print("[ok] atomic write: tmp cleaned; .bak preserved good state "
          "(corrupt primary was NOT rotated into backup)")

    print("LEDGER_DURABILITY_OK")


if __name__ == "__main__":
    main()
