#!/usr/bin/env python3
"""ORI PPLNS mining pool server.

Implements the pool protocol expected by `miner-ori.exe --pool` /
`miner_standalone.cpp`:

    GET  /pool/job?worker=<ori1addr>
         -> {job_id, height, reward_sats, bits, timestamp, prev_hash,
             coinbase_address, pool_target, node_target, txs[]}
    POST /pool/submit  {worker_addr, job_id, header_hex}
         -> 200 {"accepted":true,"is_block":bool,"pool_target":...}
    GET  /pool/stats   -> leaderboard, balances, blocks found

How it works:
- A background thread polls the configured ORI node's /mining/template
  (coinbase pays the POOL address).
- Miners get a much easier `pool_target` (vardiff per worker) and submit
  share headers.
- Every accepted share earns 1 PPLNS point (rolling window).
- When a share ALSO meets the real network target the pool assembles the
  full block and submits it to the node; the coinbase reward is credited
  to worker balances proportionally to points in the window (minus fee).

Run:
    BTPY_API_TOKEN=... POOL_NODE_URL=http://127.0.0.1:8000 \
        python -m uvicorn pool_server:app --port 9000
"""

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import hashlib
import struct

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from bech32 import validate_address
from merkle import merkle_root
from pow import target_from_bits
from tx import coinbase_tx
from utils import sha256d

# ── configuration ─────────────────────────────────────────────────────────

POOL_NODE_URL = os.environ.get("POOL_NODE_URL", "http://127.0.0.1:8000").rstrip("/")
POOL_NODE_TOKEN = os.environ.get("BTPY_API_TOKEN", "")
POOL_ADDRESS = os.environ.get("POOL_ADDRESS", "")       # payout address (required)
POOL_FEE_PCT = float(os.environ.get("POOL_FEE_PCT", "1.0"))
PPLNS_POINTS = int(os.environ.get("PPLNS_POINTS", "10000"))   # window size (shares)
POOL_DIFF_SHIFT = int(os.environ.get("POOL_DIFF_SHIFT", "12"))  # start: node_target * 2^shift
MIN_SHIFT = int(os.environ.get("POOL_MIN_SHIFT", "4"))          # hardest (node_target * 2^4)
MAX_SHIFT = int(os.environ.get("POOL_MAX_SHIFT", "24"))         # easiest
SHARE_FAST_SEC = float(os.environ.get("SHARE_FAST_SEC", "5"))   # harder if faster
SHARE_SLOW_SEC = float(os.environ.get("SHARE_SLOW_SEC", "45"))  # easier if slower
POOL_DATA_DIR = os.environ.get("POOL_DATA_DIR", "pool_data")


def _req(method: str, path: str, body: dict | None = None, timeout: int = 20):
    url = POOL_NODE_URL + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if POOL_NODE_TOKEN:
        req.add_header("X-API-Key", POOL_NODE_TOKEN)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode())


def _target_hex(t: int) -> str:
    return format(t, "064x")


def hexstr_bytes(reversed_hex: bytes) -> str:
    """Compare helper: header stores prev_hash in internal byte order;
    the node exposes display order (reversed). Convert accordingly."""
    return reversed_hex[::-1].hex()


def _sha256d(b: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


class _Template:
    """Latest /mining/template snapshot from the node."""

    def __init__(self):
        self.lock = threading.Lock()
        self.data: dict | None = None
        self.fetched_at = 0.0
        self.last_error = ""
        self._stop = False
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self.refresh()
        self.thread.start()

    def refresh(self) -> bool:
        try:
            _, tpl = _req("GET", f"/mining/template?address={POOL_ADDRESS}")
            with self.lock:
                tpl["fetched_ts"] = int(time.time())
                prev = self.data
                tpl["job_seq"] = ((prev or {}).get("job_seq", 0) + 1) if (
                    prev is None or tpl.get("height") != prev.get("height")
                ) else prev["job_seq"]
                self.data = tpl
                self.fetched_at = time.time()
                self.last_error = ""
            print(f"[pool] node template OK height={tpl['height']} "
                  f"reward={tpl['reward_sats']}", flush=True)
            return True
        except Exception as exc:
            with self.lock:
                self.last_error = f"{type(exc).__name__}: {exc}"
            print(f"[pool] NODE UNREACHABLE ({POOL_NODE_URL}): {self.last_error}",
                  flush=True)
            return False

    def _loop(self):
        while not self._stop:
            time.sleep(5)
            self.refresh()

    def get(self, max_age: float = 60.0) -> dict | None:
        if self.data is None or time.time() - self.fetched_at > max_age:
            self.refresh()
        with self.lock:
            return self.data


TPL = _Template()

# ── ledger ────────────────────────────────────────────────────────────────

LEDGER_PATH = os.path.join(POOL_DATA_DIR, "ledger.json")


class Ledger:
    def __init__(self):
        os.makedirs(POOL_DATA_DIR, exist_ok=True)
        self.lock = threading.Lock()
        self.window: deque = deque(maxlen=PPLNS_POINTS)   # [(worker_addr)]
        self.balances: dict[str, int] = {}                 # addr -> sats credited
        self.total_blocks = 0
        self.total_shares = 0
        self.workers: dict[str, dict] = {}                 # addr -> vardiff state
        self._load()

    def _load(self):
        try:
            with open(LEDGER_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.window = deque(d.get("window", []), maxlen=PPLNS_POINTS)
            self.balances = d.get("balances", {})
            self.total_blocks = d.get("total_blocks", 0)
            self.total_shares = d.get("total_shares", 0)
        except Exception:
            pass

    def save(self):
        try:
            with open(LEDGER_PATH, "w", encoding="utf-8") as f:
                json.dump({
                    "window": list(self.window),
                    "balances": self.balances,
                    "total_blocks": self.total_blocks,
                    "total_shares": self.total_shares,
                }, f)
        except Exception:
            pass

    def add_share(self, worker: str):
        with self.lock:
            self.window.append(worker)
            self.total_shares += 1
            w = self.workers.setdefault(worker, {"shift": POOL_DIFF_SHIFT,
                                                 "last": time.time(), "shares": 0})
            now = time.time()
            dt = now - w["last"]
            w["last"] = now
            w["shares"] += 1
            if dt < SHARE_FAST_SEC:
                w["shift"] = max(MIN_SHIFT, w["shift"] - 1)
            elif dt > SHARE_SLOW_SEC:
                w["shift"] = min(MAX_SHIFT, w["shift"] + 1)
            new_shift = w["shift"]
            if self.total_shares % 25 == 0:
                self.save()
        return new_shift

    def credit_block(self, reward_sats: int) -> dict:
        with self.lock:
            total_pts = len(self.window)
            payout: dict[str, int] = {}
            if total_pts:
                net = reward_sats * (100.0 - POOL_FEE_PCT) / 100.0
                counts: dict[str, int] = {}
                for w in self.window:
                    counts[w] = counts.get(w, 0) + 1
                for w, pts in counts.items():
                    amt = int(net * pts / total_pts)
                    self.balances[w] = self.balances.get(w, 0) + amt
                    payout[w] = amt
            self.total_blocks += 1
            self.save()
            return payout


LEDGER = Ledger()


# ── share validation helpers ──────────────────────────────────────────────

def _parse_header(header_hex: str):
    raw = bytes.fromhex(header_hex)
    if len(raw) != 80:
        raise ValueError("header must be exactly 80 bytes")
    version = struct.unpack_from("<i", raw, 0)[0]
    prev_hash = raw[4:36]
    merkle = raw[36:68]
    ts, bits, nonce = struct.unpack_from("<III", raw, 68)
    return version, prev_hash, merkle, ts, bits, nonce


def _expected_merkle(tpl: dict) -> bytes:
    cb = coinbase_tx(int(tpl["height"]), int(tpl["reward_sats"]), POOL_ADDRESS)
    txids = [cb.txid()]
    for hx in tpl.get("txs", []):
        from tx import Transaction

        txids.append(Transaction.from_hex(hx).txid())
    return merkle_root(txids)


# ── FastAPI app ───────────────────────────────────────────────────────────

app = FastAPI(title="ORI PPLNS Pool", version="0.1.0")


@app.on_event("startup")
def _startup():
    if not POOL_ADDRESS:
        raise RuntimeError("POOL_ADDRESS env is required (pool payout address)")
    TPL.start()


class SubmitReq(BaseModel):
    worker_addr: str
    job_id: str
    header_hex: str


_submit_lock = threading.Lock()


@app.get("/")
def root():
    with TPL.lock:
        tpl_height = TPL.data["height"] if TPL.data else None
        node_err = TPL.last_error
    return {
        "name": "ORI PPLNS Pool (pool_server.py)",
        "node": POOL_NODE_URL,
        "node_reachable": node_err == "",
        "node_last_error": node_err,
        "node_tip_height": tpl_height,
        "pool_address": POOL_ADDRESS,
        "fee_pct": POOL_FEE_PCT,
        "pplns_points": PPLNS_POINTS,
        "blocks_found": LEDGER.total_blocks,
        "shares_accepted": LEDGER.total_shares,
        "workers": len(LEDGER.workers),
    }


@app.get("/pool/job")
def pool_job(worker: str = Query(...)):
    if not validate_address(worker):
        raise HTTPException(status_code=400, detail="invalid worker address")
    tpl = TPL.get()
    if tpl is None:
        with TPL.lock:
            err = TPL.last_error
        raise HTTPException(status_code=503,
                            detail=f"node template unavailable ({POOL_NODE_URL}): {err}")
    w = LEDGER.workers.get(worker) or {}
    shift = int(w.get("shift", POOL_DIFF_SHIFT))
    node_target = target_from_bits(int(tpl["bits"]))
    pool_target = min(node_target << shift, node_target) if shift == 0 else \
        node_target << shift
    job_id = f'{tpl["height"]}-{tpl["job_seq"]}'
    return {
        "job_id": job_id,
        "height": int(tpl["height"]),
        "reward_sats": int(tpl["reward_sats"]),
        "bits": int(tpl["bits"]),
        "timestamp": int(tpl["timestamp"]),
        "prev_hash": tpl["prev_hash"],
        "coinbase_address": POOL_ADDRESS,
        "pool_target": _target_hex(pool_target),
        "node_target": _target_hex(node_target),
        "pplns_points": PPLNS_POINTS,
        "txs": tpl.get("txs", []),
    }


@app.post("/pool/submit")
def pool_submit(body: SubmitReq):
    if not validate_address(body.worker_addr):
        raise HTTPException(status_code=400, detail="invalid worker address")
    tpl = TPL.get()
    if tpl is None:
        raise HTTPException(status_code=503, detail="node template unavailable")

    try:
        version, prev_hash, merkle, ts, bits, nonce = _parse_header(body.header_hex)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"bad header: {exc}")

    job_height, job_seq = body.job_id.split("-")
    if int(job_height) != int(tpl["height"]) or int(job_seq) != int(tpl["job_seq"]):
        raise HTTPException(status_code=400, detail="stale job — request a new one")
    if hexstr_bytes(prev_hash) != tpl["prev_hash"]:
        raise HTTPException(status_code=400, detail="prev_hash mismatch")
    if bits != int(tpl["bits"]):
        raise HTTPException(status_code=400, detail="bits mismatch")
    if abs(ts - time.time()) > 90:
        raise HTTPException(status_code=400, detail="timestamp out of range")
    if merkle != _expected_merkle(tpl):
        raise HTTPException(status_code=400, detail="merkle root mismatch")

    header80 = bytes.fromhex(body.header_hex)
    h = _sha256d(header80)
    node_target = target_from_bits(int(tpl["bits"]))
    w = LEDGER.workers.get(body.worker_addr) or {}
    shift = int(w.get("shift", POOL_DIFF_SHIFT))
    pool_target = node_target << shift

    is_block = int.from_bytes(h, "big") <= node_target
    # live operator log for PPLNS activity
    print(f"[share] worker={body.worker_addr[:16]}… "
          f"hash=0x{h.hex()[:16]} window={len(LEDGER.window)} "
          f"shift={shift} is_block={is_block}", flush=True)
    if not is_block:
        if int.from_bytes(h, "big") > pool_target:
            raise HTTPException(status_code=400, detail="above pool target (low difficulty share)")
        new_shift = LEDGER.add_share(body.worker_addr)
        wt = target_from_bits(int(tpl["bits"])) << new_shift
        with LEDGER.lock:
            balance = LEDGER.balances.get(body.worker_addr, 0)
        return {
            "accepted": True,
            "is_block": False,
            "pool_target": _target_hex(wt),
            "window_points": len(LEDGER.window),
            "balance_sats": balance,
            "shift": new_shift,
            "worker_shares": LEDGER.workers.get(body.worker_addr, {}).get("shares", 0),
        }

    # ── REAL BLOCK ── assemble & relay to the node ──
    with _submit_lock:
        # re-validate against fresh tip to avoid duplicate submissions
        cur_tpl = TPL.get(max_age=3600) or tpl
        if cur_tpl["height"] != tpl["height"]:
            raise HTTPException(status_code=400, detail="chain moved on — stale block")
        from block import Block, BlockHeader
        from tx import Transaction

        cb = coinbase_tx(int(tpl["height"]), int(tpl["reward_sats"]), POOL_ADDRESS)
        txs = [cb] + [Transaction.from_hex(hx) for hx in tpl.get("txs", [])]
        hdr = BlockHeader(version=version, prev_hash=prev_hash, merkle_root=merkle,
                          timestamp=ts, bits=bits, nonce=nonce)
        blk = Block(hdr, txs)
        try:
            _req("POST", "/mining/submit", {"block": blk.to_hex()})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:200]
            raise HTTPException(status_code=502, detail=f"node rejected block: {detail}")
        payout = LEDGER.credit_block(int(tpl["reward_sats"]))
        TPL.refresh()
        return {
            "accepted": True,
            "is_block": True,
            "height": int(tpl["height"]),
            "reward_sats": int(tpl["reward_sats"]),
            "payout": payout,
            "window_points": len(LEDGER.window),
            "balance_sats": LEDGER.balances.get(body.worker_addr, 0),
            "pool_target": _target_hex(node_target << shift),
        }


@app.get("/pool/stats")
def pool_stats():
    with LEDGER.lock:
        win_counts: dict[str, int] = {}
        for w in LEDGER.window:
            win_counts[w] = win_counts.get(w, 0) + 1
    return {
        "blocks_found": LEDGER.total_blocks,
        "shares_accepted": LEDGER.total_shares,
        "workers": sorted(LEDGER.workers.keys()),
        "window_points": len(LEDGER.window),
        "pplns_points_max": PPLNS_POINTS,
        "fee_pct": POOL_FEE_PCT,
        "leaderboard": [
            {"worker": k, "window_shares": v,
             "balance_sats": LEDGER.balances.get(k, 0)}
            for k, v in sorted(win_counts.items(), key=lambda kv: -kv[1])
        ],
        "balances": LEDGER.balances,
    }
