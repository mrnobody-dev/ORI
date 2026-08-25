"""Automated payout thread — runs every 60 s, pays mature blocks via PPLNS."""
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

# ORI modules live in the parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crypto import sign
from tx import make_transfer
from pool_pplns import calculate_pplns


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(base_url: str, path: str, token: str = "") -> dict:
    url = f"{base_url}{path}"
    hdrs = {"Content-Type": "application/json"}
    if token:
        hdrs["X-API-Key"] = token
    req = urllib.request.Request(url, headers=hdrs, method="GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _post(base_url: str, path: str, body: dict, token: str = "") -> dict:
    url = f"{base_url}{path}"
    hdrs = {"Content-Type": "application/json"}
    if token:
        hdrs["X-API-Key"] = token
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


# ── TX signing ────────────────────────────────────────────────────────────────

def _sign_tx(tx, priv_hex: str, pub_hex: str):
    priv   = bytes.fromhex(priv_hex)
    pub    = bytes.fromhex(pub_hex)
    digest = tx.sighash()
    for txin in tx.inputs:
        txin.script_sig = sign(priv, digest) + pub
    return tx


# ── Payer main loop ───────────────────────────────────────────────────────────

def run_payer(db, config: dict, stop_event, log_fn=None):
    """Call in a daemon thread.  Runs until stop_event is set."""

    node_url        = config["node_url"]
    node_token      = config.get("node_token", "")
    wallet          = config["pool_wallet"]   # {address, priv_hex, pub_hex}
    pool_address    = wallet["address"]
    pool_fee_pct    = config.get("pool_fee_pct", 1.0)
    pplns_n         = config.get("pplns_n", 1_000_000)
    min_payout      = config.get("min_payout_sats", 100_000_000)  # 1 ORI

    def log(msg):
        txt = f"[payer] {msg}"
        if log_fn:
            log_fn(txt)
        else:
            print(txt, flush=True)

    while not stop_event.is_set():
        try:
            _tick(db, node_url, node_token, wallet, pool_address,
                  pool_fee_pct, pplns_n, min_payout, log)
        except Exception as exc:
            log(f"tick error: {exc}")
        stop_event.wait(60)


def _tick(db, node_url, node_token, wallet, pool_address,
          pool_fee_pct, pplns_n, min_payout, log):
    try:
        stats = _get(node_url, "/stats", node_token)
    except Exception as exc:
        log(f"node unreachable: {exc}")
        return

    current_height = stats.get("height", 0)
    if not current_height:
        return

    mature = db.get_unpaid_mature_blocks(current_height)
    if not mature:
        return

    log(f"{len(mature)} mature block(s) to process")
    for blk in mature:
        try:
            _pay_block(db, blk, node_url, node_token, wallet, pool_address,
                       pool_fee_pct, pplns_n, min_payout, log)
        except Exception as exc:
            log(f"block {blk['height']} error: {exc}")


def _pay_block(db, blk, node_url, node_token, wallet, pool_address,
               pool_fee_pct, pplns_n, min_payout, log):
    h           = blk["height"]
    reward_sats = blk["reward_sats"]
    block_ts    = blk["timestamp"]

    log(f"paying block {h} (reward {reward_sats} sats)")

    payouts, total_shares = calculate_pplns(
        db, h, block_ts, reward_sats, pool_fee_pct, pplns_n)

    if not payouts:
        log(f"block {h}: no shares in PPLNS window")
        db.mark_block_paid(h, "no_shares")
        return

    log(f"block {h}: {len(payouts)} workers, {total_shares} shares in window")

    # Filter workers below minimum payout threshold
    eligible = [p for p in payouts if p["net_sats"] >= min_payout]
    if not eligible:
        log(f"block {h}: all amounts below minimum ({min_payout} sats)")
        db.insert_payouts(payouts)
        db.mark_block_paid(h, "below_minimum")
        return

    # Fetch pool UTXOs
    try:
        addr_data = _get(node_url, f"/address/{pool_address}", node_token)
    except Exception as exc:
        log(f"block {h}: cannot fetch pool UTXOs: {exc}")
        return

    utxos = [u for u in addr_data.get("utxos", []) if u.get("mature", True)]
    if not utxos:
        log(f"block {h}: no mature pool UTXOs yet")
        return

    total_needed = sum(p["net_sats"] for p in eligible)
    available    = sum(u["value"] for u in utxos)
    if available < total_needed:
        log(f"block {h}: insufficient balance ({available} < {total_needed})")
        return

    # Greedy UTXO selection — fee rate 0.46 sat/vB (tier 3)
    FEE_RATE = 0.46
    outputs = [(p["net_sats"], p["worker_addr"]) for p in eligible]

    selected, sel_total = [], 0
    for u in sorted(utxos, key=lambda x: x["value"], reverse=True):
        selected.append(u)
        sel_total += u["value"]
        # Estimate: 10 base + 180/input + 34/output (change counts as +1)
        est = 10 + 180 * len(selected) + 34 * (len(outputs) + 1)
        fee = math.ceil(est * FEE_RATE)
        if sel_total >= total_needed + fee:
            break

    est  = 10 + 180 * len(selected) + 34 * (len(outputs) + 1)
    fee  = math.ceil(est * FEE_RATE)
    change = sel_total - total_needed - fee

    if change < 0:
        log(f"block {h}: cannot cover fee, skipping")
        return

    all_outputs = list(outputs)
    if change >= 1000:                         # dust threshold
        all_outputs.append((change, pool_address))

    tx = make_transfer(
        inputs=[(bytes.fromhex(u["txid"]), u["vout"]) for u in selected],
        outputs=all_outputs,
    )
    tx = _sign_tx(tx, wallet["priv_hex"], wallet["pub_hex"])

    try:
        result = _post(node_url, "/tx/", {"tx": tx.to_hex()}, node_token)
    except Exception as exc:
        log(f"block {h}: TX submission failed: {exc}")
        return

    txid = result.get("txid", "")
    if not txid:
        log(f"block {h}: node rejected TX: {result}")
        return

    log(f"block {h}: payout sent! txid={txid} fee={fee} sats")
    db.insert_payouts(payouts)
    db.mark_payouts_paid(h, txid)
    db.mark_block_paid(h, txid)
