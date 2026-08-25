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

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
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
    """Persistent PPLNS state.

    Durability guarantees (hardened after redeploy-loss report):
    - every accepted share is flushed atomically (tmp + fsync + os.replace)
    - previous good copy kept as ledger.json.bak; corrupt primary falls back
      to it instead of silently starting from zero
    - worker vardiff/share counters persisted alongside balances/window"""

    def __init__(self):
        os.makedirs(POOL_DATA_DIR, exist_ok=True)
        self.lock = threading.RLock()
        self.window: deque = deque(maxlen=PPLNS_POINTS)   # [(worker_addr)]
        self.balances: dict[str, int] = {}                 # addr -> sats credited
        self.total_blocks = 0
        self.total_shares = 0
        self.workers: dict[str, dict] = {}                 # addr -> vardiff state
        self.blocks_history: deque = deque(maxlen=50)      # found-block log
        self.saved_at: float = 0.0
        self._primary_valid = False   # can we safely rotate primary -> .bak?
        self._saves_done = 0
        self._load()

    @staticmethod
    def _read_snapshot(path: str) -> dict:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            raise ValueError("ledger root is not an object")
        return d

    def _apply(self, d: dict):
        self.window = deque(d.get("window", []), maxlen=PPLNS_POINTS)
        self.balances = {k: int(v) for k, v in d.get("balances", {}).items()}
        self.total_blocks = int(d.get("total_blocks", 0))
        self.total_shares = int(d.get("total_shares", 0))
        self.blocks_history = deque(d.get("blocks_history", []), maxlen=50)
        for addr, w in d.get("workers", {}).items():
            self.workers[addr] = {
                "shift": int(w.get("shift", POOL_DIFF_SHIFT)),
                "shares": int(w.get("shares", 0)),
                "last": float(w.get("last", time.time())),
                "recent": deque(maxlen=600),
            }

    def _load(self):
        import shutil

        candidates = [LEDGER_PATH, LEDGER_PATH + ".bak"]
        for path in candidates:
            try:
                d = self._read_snapshot(path)
                self._apply(d)
                self.saved_at = time.time()
                # Only rotate primary->bak on save if the CURRENT primary was
                # readable; never overwrite a good backup with corrupt data.
                self._primary_valid = path == LEDGER_PATH
                print(f"[pool] ledger restored from {path}: "
                      f"balances={len(self.balances)} "
                      f"window={len(self.window)} blocks={self.total_blocks} "
                      f"shares={self.total_shares}", flush=True)
                return
            except FileNotFoundError:
                continue
            except Exception as exc:
                print(f"[pool] LEDGER {path} unreadable ({exc}) — "
                      f"trying backup...", flush=True)
        self._primary_valid = False
        print("[pool] starting with EMPTY ledger (no valid snapshot found)",
              flush=True)

    def save(self):
        """Atomic durable write: fsync tmp -> rotate old to .bak -> replace."""
        import shutil

        snapshot = {
            "version": 2,
            "saved_at": int(time.time()),
            "window": list(self.window),
            "balances": self.balances,
            "total_blocks": self.total_blocks,
            "total_shares": self.total_shares,
            "blocks_history": list(self.blocks_history),
            "workers": {
                k: {"shift": w.get("shift", POOL_DIFF_SHIFT),
                    "shares": w.get("shares", 0),
                    "last": w.get("last", 0)}
                for k, w in self.workers.items()
            },
        }
        tmp = LEDGER_PATH + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(snapshot, f)
                f.flush()
                os.fsync(f.fileno())
            if self._primary_valid and os.path.exists(LEDGER_PATH):
                shutil.copyfile(LEDGER_PATH, LEDGER_PATH + ".bak")
            os.replace(tmp, LEDGER_PATH)
            self._primary_valid = True
            self.saved_at = time.time()
            self._saves_done += 1
            # Periodic visible proof-of-persistence in deploy logs.
            if self._saves_done % 10 == 1 or self._saves_done == 1:
                print(f"[pool] ledger SAVED #{self._saves_done}: "
                      f"path={LEDGER_PATH} "
                      f"bytes={os.path.getsize(LEDGER_PATH)} "
                      f"balances={len(self.balances)} "
                      f"window={len(self.window)} "
                      f"blocks={self.total_blocks} "
                      f"shares={self.total_shares}", flush=True)
        except Exception as exc:
            # LOUD failure — silent data loss is unacceptable
            print(f"[pool] !!! LEDGER SAVE FAILED: {exc}", flush=True)

    def add_share(self, worker: str):
        with self.lock:
            self.window.append(worker)
            self.total_shares += 1
            w = self.workers.setdefault(worker, {
                "shift": POOL_DIFF_SHIFT, "last": time.time(),
                "shares": 0, "recent": deque(maxlen=600),
            })
            now = time.time()
            dt = now - w["last"]
            w["last"] = now
            w["shares"] += 1
            w.setdefault("recent", deque(maxlen=600)).append(now)
            if dt < SHARE_FAST_SEC:
                w["shift"] = max(MIN_SHIFT, w["shift"] - 1)
            elif dt > SHARE_SLOW_SEC:
                w["shift"] = min(MAX_SHIFT, w["shift"] + 1)
            new_shift = w["shift"]
            # flush EVERY share — redeploy/crash must never eat work
            self.save()
        return new_shift

    def credit_block(self, reward_sats: int, height: int) -> dict:
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
            self.blocks_history.append({
                "height": height,
                "found_at": int(time.time()),
                "reward_sats": reward_sats,
                "window_points": total_pts,
                "n_workers": len(payout),
            })
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


def _expected_merkle(job: dict) -> bytes:
    """Recompute the merkle root a miner must produce for `job`."""
    cb = coinbase_tx(int(job["height"]), int(job["reward_sats"]), POOL_ADDRESS)
    txids = [cb.txid()]
    for hx in job.get("txs", []):
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
_jobs_lock = threading.Lock()
_recent_jobs: dict[str, dict] = {}             # job_id -> issued snapshot
_seen_headers: deque = deque(maxlen=100_000)   # duplicate-share protection
_JOB_SEQ_COUNTER = 0


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
    global _JOB_SEQ_COUNTER
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

    # Unique job per request. The reference miner rebuilds its candidate
    # deterministically from (height, reward, coinbase, txs, timestamp), so a
    # repeated job_id made it rediscover the SAME nonce and resubmit a
    # duplicate share. Issuing fresh seq+timestamp breaks that loop.
    with _jobs_lock:
        _JOB_SEQ_COUNTER += 1
        seq = _JOB_SEQ_COUNTER
        job_id = f'{tpl["height"]}-{seq}'
        ts = max(int(time.time()), int(tpl["timestamp"]))
        _recent_jobs[job_id] = {
            "height": int(tpl["height"]),
            "reward_sats": int(tpl["reward_sats"]),
            "bits": int(tpl["bits"]),
            "timestamp": ts,
            "prev_hash": tpl["prev_hash"],
            "txs": list(tpl.get("txs", [])),
        }
        while len(_recent_jobs) > 240:
            _recent_jobs.pop(next(iter(_recent_jobs)))

    return {
        "job_id": job_id,
        "height": int(tpl["height"]),
        "reward_sats": int(tpl["reward_sats"]),
        "bits": int(tpl["bits"]),
        "timestamp": ts,
        "prev_hash": tpl["prev_hash"],
        "coinbase_address": POOL_ADDRESS,
        "pool_target": _target_hex(pool_target),
        "node_target": _target_hex(node_target),
        "pplns_points": PPLNS_POINTS,
        "txs": list(tpl.get("txs", [])),
    }


@app.post("/pool/submit")
def pool_submit(body: SubmitReq):
    if not validate_address(body.worker_addr):
        raise HTTPException(status_code=400, detail="invalid worker address")

    with _jobs_lock:
        job = _recent_jobs.get(body.job_id)
    if job is None:
        raise HTTPException(status_code=400, detail="stale job — request a new one")

    try:
        version, prev_hash, merkle, ts, bits, nonce = _parse_header(body.header_hex)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"bad header: {exc}")

    if hexstr_bytes(prev_hash) != job["prev_hash"]:
        raise HTTPException(status_code=400, detail="prev_hash mismatch")
    if bits != int(job["bits"]):
        raise HTTPException(status_code=400, detail="bits mismatch")
    if abs(ts - time.time()) > 120 or ts < int(job["timestamp"]) - 5:
        raise HTTPException(status_code=400, detail="timestamp out of range")
    if merkle != _expected_merkle(job):
        raise HTTPException(status_code=400, detail="merkle root mismatch")

    header80 = bytes.fromhex(body.header_hex)
    h = _sha256d(header80)

    # duplicate protection: identical header (same PoW solution) must never
    # be credited twice — deterministic miners resubmitting the same nonce
    with _jobs_lock:
        dup = h.hex() in _seen_headers
        if not dup:
            _seen_headers.append(h.hex())
    if dup:
        raise HTTPException(status_code=400, detail="duplicate share")

    node_target = target_from_bits(int(job["bits"]))
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
        wt = target_from_bits(int(job["bits"])) << new_shift
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
        cur_tpl = TPL.get(max_age=3600)
        if cur_tpl is None or int(cur_tpl["height"]) != int(job["height"]):
            raise HTTPException(status_code=400, detail="chain moved on — stale block")
        from block import Block, BlockHeader
        from tx import Transaction

        cb = coinbase_tx(int(job["height"]), int(job["reward_sats"]), POOL_ADDRESS)
        txs = [cb] + [Transaction.from_hex(hx) for hx in job.get("txs", [])]
        hdr = BlockHeader(version=version, prev_hash=prev_hash, merkle_root=merkle,
                          timestamp=ts, bits=bits, nonce=nonce)
        blk = Block(hdr, txs)
        try:
            _req("POST", "/mining/submit", {"block": blk.to_hex()})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:200]
            raise HTTPException(status_code=502, detail=f"node rejected block: {detail}")
        payout = LEDGER.credit_block(int(job["reward_sats"]), int(job["height"]))
        TPL.refresh()
        return {
            "accepted": True,
            "is_block": True,
            "height": int(job["height"]),
            "reward_sats": int(job["reward_sats"]),
            "payout": payout,
            "window_points": len(LEDGER.window),
            "balance_sats": LEDGER.balances.get(body.worker_addr, 0),
            "pool_target": _target_hex(node_target << shift),
        }


def _fmt_hashrate(hps: float) -> str:
    for unit, div in (("PH/s", 1e15), ("TH/s", 1e12), ("GH/s", 1e9), ("MH/s", 1e6)):
        if hps >= div:
            return f"{hps/div:.2f} {unit}"
    return f"{hps/1e3:.2f} kH/s" if hps >= 1e3 else f"{hps:.0f} H/s"


def _stats_snapshot() -> dict:
    """Everything the JSON API and the HTML pages need, in one snapshot."""
    now = time.time()
    tpl = TPL.get()
    node_target = target_from_bits(int(tpl["bits"])) if tpl else None
    with LEDGER.lock:
        win_counts: dict[str, int] = {}
        for w in LEDGER.window:
            win_counts[w] = win_counts.get(w, 0) + 1
        miners = []
        total_hr = 0.0
        for addr in sorted(LEDGER.workers.keys()):
            w = LEDGER.workers[addr]
            recent = [t for t in w.get("recent", ()) if now - t < 900]
            shift = int(w.get("shift", POOL_DIFF_SHIFT))
            hr = 0.0
            if node_target and recent:
                expected = (1 << 256) // (node_target << shift)
                hr = len(recent) / 900.0 * expected
            total_hr += hr
            miners.append({
                "worker": addr,
                "window_shares": win_counts.get(addr, 0),
                "total_shares": int(w.get("shares", 0)),
                "balance_sats": LEDGER.balances.get(addr, 0),
                "shift": shift,
                "hashrate_hps": round(hr, 2),
                "last_share_age": int(now - w.get("last", now)),
            })
        return {
            "blocks_found": LEDGER.total_blocks,
            "shares_accepted": LEDGER.total_shares,
            "workers_count": len(LEDGER.workers),
            "window_points": len(LEDGER.window),
            "pplns_points_max": PPLNS_POINTS,
            "fee_pct": POOL_FEE_PCT,
            "estimated_hashrate_hps": round(total_hr, 2),
            "estimated_hashrate": _fmt_hashrate(total_hr),
            "node_tip_height": (tpl or {}).get("height"),
            "node_reachable": TPL.last_error == "",
            "leaderboard": sorted(miners, key=lambda m: -m["window_shares"]),
            "latest_blocks": list(sorted(
                LEDGER.blocks_history, key=lambda b: -b.get("height", 0)))[:15],
        }


@app.get("/pool/ledger")
def pool_ledger_info():
    """Physical proof of persistence: file, size, hash, backup state."""
    import hashlib

    def finfo(path):
        if not os.path.exists(path):
            return None
        raw = open(path, "rb").read()
        return {
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()[:16],
            "modified_age_s": int(time.time() - os.path.getmtime(path)),
        }

    return {
        "path": LEDGER_PATH,
        "on_volume": LEDGER_PATH.startswith("/data"),
        "primary": finfo(LEDGER_PATH),
        "backup": finfo(LEDGER_PATH + ".bak"),
        "saved_at_iso": (time.strftime(
            "%Y-%m-%d %H:%M:%S UTC", time.gmtime(LEDGER.saved_at))
            if LEDGER.saved_at else "never"),
        "saves_done": LEDGER._saves_done,
        "totals": {
            "blocks_found": LEDGER.total_blocks,
            "shares_accepted": LEDGER.total_shares,
            "window_points": len(LEDGER.window),
            "balances_sats": LEDGER.balances,
        },
    }


@app.get("/pool/stats")
def pool_stats(request: Request, json: int = Query(0)):
    snap = _stats_snapshot()
    # Browser → human page; programmatic clients (Accept: application/json
    # or ?json=1) keep getting the raw JSON feed.
    if json or "text/html" not in (request.headers.get("accept") or ""):
        return {
            "blocks_found": snap["blocks_found"],
            "shares_accepted": snap["shares_accepted"],
            "workers": [m["worker"] for m in snap["leaderboard"]],
            "window_points": snap["window_points"],
            "pplns_points_max": snap["pplns_points_max"],
            "fee_pct": snap["fee_pct"],
            "estimated_hashrate": snap["estimated_hashrate"],
            "estimated_hashrate_hps": snap["estimated_hashrate_hps"],
            "node_tip_height": snap["node_tip_height"],
            "node_reachable": snap["node_reachable"],
            "leaderboard": [
                {"worker": m["worker"], "window_shares": m["window_shares"],
                 "balance_sats": m["balance_sats"]}
                for m in snap["leaderboard"]
            ],
            "balances": {m["worker"]: m["balance_sats"] for m in snap["leaderboard"]},
            "latest_blocks": snap["latest_blocks"],
        }
    return HTMLResponse(_render_page(snap, detail=True))


_PAGE_CSS = """
:root{--bg:#0b0e14;--panel:#12161f;--panel2:#171c26;--line:#232a36;--fg:#e6edf3;
 --dim:#8b98a9;--acc:#f7b32b;--acc2:#4da3ff;--ok:#3fb950;--red:#f85149}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;font-size:14px}
.wrap{max-width:1080px;margin:0 auto;padding:24px 18px}
header{background:linear-gradient(120deg,#141a26,#1d2433 60%,#23304a);border-bottom:2px solid var(--acc);padding:18px;border-radius:0 0 14px 14px}
.logo{font-size:22px;font-weight:800;letter-spacing:.5px}.logo span{color:var(--acc)}
.tag{color:var(--dim);font-size:12px;margin-top:6px;word-break:break-all}
.pill{display:inline-block;background:var(--line);border-radius:20px;padding:3px 12px;font-size:11px;margin-right:8px;margin-top:10px;color:var(--dim)}
.pill.ok{color:var(--ok)}.pill.bad{color:var(--red)}
h2{font-size:13px;text-transform:uppercase;letter-spacing:1.2px;color:var(--acc);margin:26px 0 12px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}
.card .k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.8px}
.card .v{font-size:24px;font-weight:800;margin-top:6px}
.card .s{color:var(--dim);font-size:11px;margin-top:4px}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:10px}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--acc),#ffd166)}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th{background:var(--panel2);text-align:left;color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.8px;padding:10px 14px}
td{padding:10px 14px;border-top:1px solid var(--line);font-size:13px}
tr:hover td{background:#161d29}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
.dim{color:var(--dim)}.ok{color:var(--ok)}.num{text-align:right;font-family:ui-monospace,Consolas,monospace}
.hbar{height:5px;background:var(--line);border-radius:3px;overflow:hidden;min-width:80px}
.hbar i{display:block;height:100%;background:var(--acc2)}
footer{color:var(--dim);font-size:11px;text-align:center;padding:26px;line-height:1.8}
footer a{color:var(--acc)}
"""


def _render_page(snap: dict, detail: bool) -> str:
    lb_rows = "".join(
        f"<tr><td>{i+1}</td>"
        f"<td class='mono'>{m['worker'][:22]}…</td>"
        f"<td>{_fmt_hashrate(m['hashrate_hps'])}</td>"
        f"<td class='num'>{m['window_shares']:,}</td>"
        f"<td class='num'>{m['total_shares']:,}</td>"
        f"<td class='num'>{m['balance_sats']/1e8:,.8f} ORI</td>"
        f"<td>{'online' if m['last_share_age'] < 300 else 'idle ' + str(m['last_share_age']) + 's'}</td></tr>"
        for i, m in enumerate(snap["leaderboard"]))
    max_ws = max([m["window_shares"] for m in snap["leaderboard"]] or [1])
    lb_bars = "".join(
        f"<tr><td class='mono'>{m['worker'][:22]}…</td>"
        f"<td><div class='hbar'><i style='width:{100*m['window_shares']/max_ws:.0f}%'></i></div></td></tr>"
        for m in snap["leaderboard"])
    block_rows = "".join(
        f"<tr><td><a href='/explorer#/block/{b['height']}' style='color:var(--acc2)'>#{b['height']:,}</a></td>"
        f"<td class='mono'>{time.strftime('%Y-%m-%d %H:%M', time.gmtime(b['found_at']))} UTC</td>"
        f"<td class='num'>{b['reward_sats']/1e8:,.8f} ORI</td>"
        f"<td class='num'>{b['window_points']:,}</td>"
        f"<td class='num'>{b['n_workers']}</td></tr>"
        for b in snap["latest_blocks"])
    reach = ("<span class='pill ok'>● node online</span>" if snap["node_reachable"]
             else "<span class='pill bad'>● node offline — " +
                  (TPL.last_error[:60] or "") + "</span>")
    window_pct = 100 * snap["window_points"] / max(1, snap["pplns_points_max"])
    detail_extra = (
        f"<h2>Window share distribution</h2>"
        f"<table><tr><th>Worker</th><th>Share of PPLNS window</th></tr>{lb_bars}</table>"
        if detail and snap["leaderboard"] else "")
    blocks_section = (
        "<h2>Latest found blocks</h2>"
        "<table><tr><th>Height</th><th>Found at (UTC)</th><th class='num'>Reward</th>"
        "<th class='num'>Window pts</th><th class='num'>Workers paid</th></tr>"
        + (block_rows or "<tr><td colspan='5' class='dim'>No blocks yet — the "
           "first one is always the hardest. Keep mining!</td></tr>")
        + "</table>")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>ORI PPLNS Pool</title><style>{_PAGE_CSS}</style></head><body>
<header><div class="wrap" style="padding:0">
 <div class="logo">ORI <span>PPLNS Pool</span></div>
 <div class="tag">payout address <b>{POOL_ADDRESS}</b> · fee {POOL_FEE_PCT}% ·
   node {POOL_NODE_URL}</div>
 {reach}<span class="pill">network height {snap.get('node_tip_height') or '—'}</span>
 <span class="pill">PPLNS N = {snap['pplns_points_max']:,} shares</span>
</div></header>
<div class="wrap">
 <h2>Pool statistics</h2>
 <div class="cards">
  <div class="card"><div class="k">Estimated hashrate</div><div class="v">{snap['estimated_hashrate']}</div><div class="s">from last 15 min of shares</div></div>
  <div class="card"><div class="k">Blocks found</div><div class="v">{snap['blocks_found']:,}</div><div class="s">since pool start</div></div>
  <div class="card"><div class="k">Miners</div><div class="v">{snap['workers_count']:,}</div><div class="s">registered workers</div></div>
  <div class="card"><div class="k">Shares accepted</div><div class="v">{snap['shares_accepted']:,}</div><div class="s">all time</div></div>
 </div>
 <h2>PPLNS window</h2>
 <div class="card">
  <div class="k">Window fill</div>
  <div class="v">{snap['window_points']:,} / {snap['pplns_points_max']:,} pts</div>
  <div class="bar"><i style="width:{min(100, window_pct):.1f}%"></i></div>
  <div class="s" style="margin-top:8px">Rewards are split proportionally over the
  newest {snap['pplns_points_max']:,} shares whenever a block is found (fee {POOL_FEE_PCT}%).
  Oldest points slide out as new ones arrive.</div>
 </div>
 <h2>Leaderboard</h2>
 <table><tr><th>#</th><th>Worker</th><th>Hashrate (est.)</th><th class='num'>Window shares</th>
 <th class='num'>Total shares</th><th class='num'>Balance</th><th>Status</th></tr>
 {lb_rows or "<tr><td colspan='7' class='dim'>No miners yet.</td></tr>"}</table>
 {detail_extra}
 {blocks_section}
 <footer>Served by your self-hosted ORI pool · <a href="/explorer#/blocks">block explorer</a>
 · <a href="/pool/stats?json=1">JSON API</a> · rewards mature after 100 blocks ·
 auto-refresh 30s</footer>
</div></body></html>"""


@app.get("/pool", response_class=HTMLResponse, include_in_schema=False)
def pool_dashboard():
    return _render_page(_stats_snapshot(), detail=False)


def _dashboard_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ORI PPLNS Pool</title>
<style>
 :root {{ --bg:#0d1117; --card:#161b22; --line:#21262d; --fg:#e6edf3;
          --dim:#8b949e; --acc:#f5a623; --ok:#3fb950; }}
 * {{ box-sizing:border-box; margin:0; padding:0; }}
 body {{ background:var(--bg); color:var(--fg);
        font-family:ui-monospace,Consolas,monospace; padding:24px; }}
 h1 {{ font-size:20px; }} h1 span{{color:var(--acc)}}
 .sub {{ color:var(--dim); font-size:12px; margin:6px 0 18px; word-break:break-all; }}
 .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
          gap:12px; margin-bottom:20px; }}
 .card {{ background:var(--card); border:1px solid var(--line); border-radius:10px;
         padding:14px; }}
 .card .v {{ font-size:22px; font-weight:700; margin-top:4px; }}
 .card .k {{ color:var(--dim); font-size:11px; text-transform:uppercase;
           letter-spacing:.08em; }}
 table {{ width:100%; border-collapse:collapse; background:var(--card);
         border:1px solid var(--line); border-radius:10px; overflow:hidden; }}
 th {{ text-align:left; color:var(--dim); font-size:11px; text-transform:uppercase;
     letter-spacing:.08em; padding:10px 14px; border-bottom:1px solid var(--line); }}
 td {{ padding:10px 14px; border-bottom:1px solid var(--line); font-size:13px; }}
 tr:last-child td {{ border-bottom:none; }}
 .bar {{ height:6px; background:var(--line); border-radius:3px; overflow:hidden;
       margin-top:8px; }}
 .bar i {{ display:block; height:100%; background:var(--acc); }}
 .muted {{ color:var(--dim); font-size:12px; margin-top:10px; }}
 .refresh {{ color:var(--dim); font-size:11px; margin-top:14px; }}
</style></head><body>
<h1>ORI <span>PPLNS Pool</span></h1>
<div class="sub">payout <b>{POOL_ADDRESS}</b> &nbsp;·&nbsp; fee {POOL_FEE_PCT}% &nbsp;·&nbsp; node {POOL_NODE_URL}</div>
<div class="cards">
 <div class="card"><div class="k">Blocks found</div><div class="v" id="blocks">–</div></div>
 <div class="card"><div class="k">Total shares</div><div class="v" id="shares">–</div></div>
 <div class="card"><div class="k">Workers</div><div class="v" id="workers">–</div></div>
 <div class="card"><div class="k">PPLNS window</div><div class="v" id="window">–</div></div>
</div>
<div class="bar"><i id="winbar" style="width:0%"></i></div>
<table>
 <thead><tr><th>#</th><th>Worker</th><th>Window shares</th><th>Balance</th></tr></thead>
 <tbody id="rows"><tr><td colspan="4" style="color:var(--dim)">loading…</td></tr></tbody>
</table>
<div class="muted">Rewards are credited per PPLNS window when the pool finds a block
(minus {POOL_FEE_PCT}% fee). Coins mature after 100 blocks.</div>
<div class="refresh">auto-refresh 15s · last update <span id="ts">–</span></div>
<script>
const fmt=sats=>(sats/1e8).toLocaleString(undefined,{{minimumFractionDigits:2,maximumFractionDigits:8}})+' ORI';
async function load(){{
 try {{
  const s=await (await fetch('/pool/stats')).json();
  document.getElementById('blocks').textContent=s.blocks_found.toLocaleString();
  document.getElementById('shares').textContent=s.shares_accepted.toLocaleString();
  document.getElementById('workers').textContent=(s.workers||[]).length;
  document.getElementById('window').textContent=
     s.window_points.toLocaleString()+' / '+s.pplns_points_max.toLocaleString();
  document.getElementById('winbar').style.width=
     Math.min(100,100*s.window_points/s.pplns_points_max)+'%';
  const rows=(s.leaderboard||[]).map((e,i)=>`<tr><td>${{i+1}}</td>`+
    `<td>${{e.worker.slice(0,18)}}…</td><td>${{e.window_shares.toLocaleString()}}</td>`+
    `<td>${{fmt(e.balance_sats)}}</td></tr>`);
  document.getElementById('rows').innerHTML=
     rows.length?rows.join(''):'<tr><td colspan="4" style="color:#8b949e">'+
     'no miners yet — be the first: miner-ori.exe --address ori1... --pool</td></tr>';
  document.getElementById('ts').textContent=new Date().toLocaleTimeString();
 }} catch(e){{}}
}}
load(); setInterval(load,15000);
</script></body></html>"""


@app.get("/pool", response_class=HTMLResponse, include_in_schema=False)
def pool_dashboard():
    return _dashboard_html()
