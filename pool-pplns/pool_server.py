"""ORI PPLNS Mining Pool Server.

Workers connect here instead of directly to the ORI node.
Use miner-ori.exe with the --pool flag:

    miner-ori.exe --address ori1YOUR_ADDR \
                  --host POOL_HOST --port POOL_PORT --pool
"""
import hashlib
import json
import os
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── ORI modules from parent directory ────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tx import Transaction, coinbase_tx
from merkle import merkle_root as _ori_merkle
from pool_db import PoolDB
from pool_payer import run_payer

# ── Configuration (env vars) ─────────────────────────────────────────────────
def _e(k, d=""): return os.environ.get(k, d)

NODE_URL          = _e("ORI_NODE_URL",   "http://127.0.0.1:8000").rstrip("/")
NODE_TOKEN        = _e("ORI_NODE_TOKEN", "")
POOL_ADDRESS      = _e("POOL_ADDRESS",   "")
POOL_PRIV_HEX     = _e("POOL_PRIV_HEX", "")
POOL_PUB_HEX      = _e("POOL_PUB_HEX",  "")
POOL_NAME         = _e("POOL_NAME",      "ORI Pool")
POOL_FEE_PCT      = float(_e("POOL_FEE_PCT",      "1.0"))
POOL_FEE_ADDRESS  = _e("POOL_FEE_ADDRESS", "")
POOL_DIFF         = float(_e("POOL_DIFF",          "0.01"))
PPLNS_N           = int(_e("PPLNS_N",              "1000000"))
MIN_PAYOUT_SATS   = int(_e("MIN_PAYOUT_SATS",      "100000000"))   # 1 ORI
COINBASE_MATURITY = int(_e("COINBASE_MATURITY",     "100"))
DB_PATH           = _e("POOL_DB_PATH",   "pool.db")

VARDIFF_TARGET_S  = float(_e("VARDIFF_TARGET_SECS", "10"))
VARDIFF_MIN       = float(_e("VARDIFF_MIN",         "0.001"))
VARDIFF_MAX       = float(_e("VARDIFF_MAX",         "10000.0"))

# ── PoW math ─────────────────────────────────────────────────────────────────
_MAX_INT = (1 << 256) - 1


def sha256d(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def diff_to_target_int(diff: float) -> int:
    return min(int(_MAX_INT / max(diff, 1e-15)), _MAX_INT)


def diff_to_target_hex(diff: float) -> str:
    return f"0x{diff_to_target_int(diff):064x}"


def hash_to_diff(h: bytes) -> float:
    n = int.from_bytes(h, "big")
    return _MAX_INT / n if n else float("inf")


def target_hex_to_int(s: str) -> int:
    return int(s.removeprefix("0x").removeprefix("0X").lstrip("0") or "0", 16)


def _varint(n: int) -> bytes:
    if n < 0xFD:       return bytes([n])
    if n <= 0xFFFF:    return b"\xfd" + n.to_bytes(2, "little")
    if n <= 0xFFFFFFFF:return b"\xfe" + n.to_bytes(4, "little")
    return b"\xff" + n.to_bytes(8, "little")

# ── Node HTTP helpers ─────────────────────────────────────────────────────────
def _node_get(path: str) -> dict:
    url  = f"{NODE_URL}{path}"
    hdrs = {"Content-Type": "application/json"}
    if NODE_TOKEN: hdrs["X-API-Key"] = NODE_TOKEN
    req  = urllib.request.Request(url, headers=hdrs, method="GET")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _node_post(path: str, body: dict) -> dict:
    url  = f"{NODE_URL}{path}"
    hdrs = {"Content-Type": "application/json"}
    if NODE_TOKEN: hdrs["X-API-Key"] = NODE_TOKEN
    req  = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

# ── Global mutable pool state ─────────────────────────────────────────────────
_jobs: dict         = {}   # job_id -> job dict (keeps last 5)
_current_job_id: str = ""
_stop               = threading.Event()
_db: Optional[PoolDB] = None
_worker_diff: dict  = {}   # addr -> float
_worker_times: dict = {}   # addr -> list[timestamp]  (last 30 share times)

# ── Job management ────────────────────────────────────────────────────────────
def _job_id(height: int, prev_hash: str) -> str:
    return f"{height}:{prev_hash[:16]}"


def _expected_merkle(height: int, reward_sats: int, txs: list) -> bytes | None:
    """Pre-compute the merkle root the miner MUST produce for a valid share."""
    try:
        cb = coinbase_tx(height, reward_sats, POOL_ADDRESS, fee_address=POOL_FEE_ADDRESS, fee_pct=POOL_FEE_PCT)
        txid_list = [cb.txid()]
        for tx_hex in txs:
            txid_list.append(Transaction.from_hex(tx_hex).txid())
        return _ori_merkle(txid_list)
    except Exception:
        return None


def _fetch_job():
    global _current_job_id
    try:
        tpl = _node_get(f"/mining/template?address={POOL_ADDRESS}")
    except Exception:
        return

    height   = int(tpl["height"])
    prev     = str(tpl["prev_hash"])
    bits     = int(tpl["bits"])
    ts       = int(tpl["timestamp"])
    reward   = int(tpl["reward_sats"])
    fees     = int(tpl.get("fees_sats", 0))
    txs      = tpl.get("txs", [])
    node_tgt = str(tpl["target"])
    jid      = _job_id(height, prev)

    if jid == _current_job_id:
        return  # no change

    node_int = target_hex_to_int(node_tgt)
    merkle   = _expected_merkle(height, reward, txs)

    _jobs[jid] = {
        "job_id":          jid,
        "height":          height,
        "prev_hash":       prev,
        "bits":            bits,
        "timestamp":       ts,
        "reward_sats":     reward,
        "fees_sats":       fees,
        "txs":             txs,
        "node_target_hex": node_tgt,
        "node_target_int": node_int,
        "pool_diff":       POOL_DIFF,
        "pool_target_hex": diff_to_target_hex(POOL_DIFF),
        "pool_target_int": diff_to_target_int(POOL_DIFF),
        "coinbase_address": POOL_ADDRESS,
        "expected_merkle": merkle,
        "created_at":      int(time.time()),
    }
    _current_job_id = jid
    # Evict oldest jobs, keep last 5
    while len(_jobs) > 5:
        oldest = min(_jobs, key=lambda k: _jobs[k]["created_at"])
        del _jobs[oldest]


def _fetcher_loop():
    while not _stop.is_set():
        try:
            _fetch_job()
        except Exception:
            pass
        _stop.wait(2)

# ── Vardiff ───────────────────────────────────────────────────────────────────
def _worker_diff_get(addr: str) -> float:
    if addr not in _worker_diff:
        w = _db.get_worker(addr) if _db else None
        d = w.get("current_diff") if w else None
        _worker_diff[addr] = d if (d and d > 0) else POOL_DIFF
    return _worker_diff[addr]


def _vardiff_update(addr: str) -> float:
    now    = int(time.time())
    window = 60
    times  = _worker_times.get(addr, [])
    recent = [t for t in times if t >= now - window]
    recent.append(now)
    _worker_times[addr] = recent[-30:]  # keep last 30

    current = _worker_diff_get(addr)
    if len(recent) >= 4:
        avg_interval = window / len(recent)
        if avg_interval < VARDIFF_TARGET_S * 0.4:
            new = min(current * 2, VARDIFF_MAX)
        elif avg_interval > VARDIFF_TARGET_S * 3:
            new = max(current / 2, VARDIFF_MIN)
        else:
            new = current
    else:
        new = current

    if new != current:
        _worker_diff[addr] = new
        if _db:
            _db.set_worker_diff(addr, new)
    return new

# ── Share validation ──────────────────────────────────────────────────────────
def _validate(job: dict, header_hex: str, worker: str):
    """Returns (ok, reason, block_hash_bytes, is_block)."""
    try:
        hdr = bytes.fromhex(header_hex)
    except ValueError:
        return False, "invalid hex", b"", False

    if len(hdr) != 80:
        return False, f"header must be 80 bytes, got {len(hdr)}", b"", False

    bits_in_hdr = struct.unpack_from("<I", hdr, 72)[0]
    if bits_in_hdr != job["bits"]:
        return False, f"bits mismatch: {bits_in_hdr} != {job['bits']}", b"", False

    # prev_hash: display order in job → reverse for header bytes
    exp_prev = bytes(reversed(bytes.fromhex(job["prev_hash"])))
    if hdr[4:36] != exp_prev:
        return False, "prev_hash mismatch (stale job)", b"", False

    # Merkle root (security: ensures worker used pool coinbase address)
    exp_merkle = job.get("expected_merkle")
    if exp_merkle is not None and hdr[36:68] != exp_merkle:
        return False, "merkle_root mismatch — use --pool flag, not --address", b"", False

    block_hash = sha256d(hdr)
    hash_int   = int.from_bytes(block_hash, "big")

    # Check against worker's pool target
    worker_diff = _worker_diff_get(worker)
    pool_tgt    = diff_to_target_int(worker_diff)
    if hash_int > pool_tgt:
        actual = hash_to_diff(block_hash)
        return False, f"diff too low: got {actual:.5f} need {worker_diff:.5f}", block_hash, False

    is_block = hash_int <= job["node_target_int"]
    return True, "ok", block_hash, is_block

# ── Block submission to node ──────────────────────────────────────────────────
def _submit_block(job: dict, header_hex: str) -> tuple[bool, int, str]:
    """Build full block bytes from job + header and submit to node."""
    hdr = bytes.fromhex(header_hex)
    cb  = coinbase_tx(job["height"], job["reward_sats"], POOL_ADDRESS, fee_address=POOL_FEE_ADDRESS, fee_pct=POOL_FEE_PCT)
    all_txs = [cb]
    for tx_hex in job.get("txs", []):
        try:
            all_txs.append(Transaction.from_hex(tx_hex))
        except Exception:
            pass

    block_bin = hdr + _varint(len(all_txs))
    for tx in all_txs:
        block_bin += tx.serialize()

    try:
        res = _node_post("/mining/submit", {"block": block_bin.hex()})
        h   = res.get("height")
        if h is not None:
            disp_hash = bytes(reversed(sha256d(hdr))).hex()
            return True, int(h), disp_hash
        return False, 0, str(res)
    except urllib.error.HTTPError as exc:
        return False, 0, exc.read().decode()
    except Exception as exc:
        return False, 0, str(exc)

# ── Pydantic models ───────────────────────────────────────────────────────────
class SubmitRequest(BaseModel):
    worker_addr: str
    job_id:      str
    header_hex:  str

# ── App lifecycle ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _db
    if not POOL_ADDRESS:
        raise RuntimeError("POOL_ADDRESS env var is required")

    _db = PoolDB(DB_PATH)

    t_fetch = threading.Thread(target=_fetcher_loop, daemon=True, name="fetcher")
    t_fetch.start()

    if POOL_PRIV_HEX and POOL_PUB_HEX:
        cfg = {
            "node_url":        NODE_URL,
            "node_token":      NODE_TOKEN,
            "pool_wallet":     {"address": POOL_ADDRESS,
                                "priv_hex": POOL_PRIV_HEX,
                                "pub_hex":  POOL_PUB_HEX},
            "pool_fee_pct":    POOL_FEE_PCT,
            "pplns_n":         PPLNS_N,
            "min_payout_sats": MIN_PAYOUT_SATS,
        }
        t_pay = threading.Thread(target=run_payer, args=(_db, cfg, _stop),
                                 daemon=True, name="payer")
        t_pay.start()

    yield
    _stop.set()

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title=f"{POOL_NAME} — ORI Mining Pool",
    description=(
        "PPLNS mining pool for the ORI blockchain.  "
        "Workers connect with `miner-ori.exe --pool`."
    ),
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

# ── Endpoints ─────────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")

@app.get("/", response_class=HTMLResponse, summary="Pool Dashboard")
def dashboard():
    with open(os.path.join(os.path.dirname(__file__), "static", "index.html"), "r") as f:
        return f.read()

@app.get("/api/stats", summary="Pool info & statistics")
def api_stats():
    stats = _db.get_pool_stats_24h() if _db else {}
    job   = _jobs.get(_current_job_id, {})
    return {
        "pool":             POOL_NAME,
        "version":          "1.0.0",
        "fee_pct":          POOL_FEE_PCT,
        "pool_address":     POOL_ADDRESS,
        "pplns_n":          PPLNS_N,
        "current_height":   job.get("height", 0),
        "pool_diff":        POOL_DIFF,
        "vardiff_min":      VARDIFF_MIN,
        "vardiff_max":      VARDIFF_MAX,
        "min_payout_sats":  MIN_PAYOUT_SATS,
        **stats,
    }


@app.get("/pool/job", summary="Get current mining job (pool mode)")
def get_job(
    worker: str = Query(..., description="Worker payout address (ori1...)"),
):
    if not _current_job_id or _current_job_id not in _jobs:
        raise HTTPException(503, detail="pool not ready — no job yet (node unreachable?)")

    job          = _jobs[_current_job_id]
    worker_diff  = _worker_diff_get(worker)

    return {
        "job_id":            job["job_id"],
        "height":            job["height"],
        "prev_hash":         job["prev_hash"],
        "bits":              job["bits"],
        "timestamp":         max(job["timestamp"], int(time.time())),
        "pool_target":       diff_to_target_hex(worker_diff),
        "node_target":       job["node_target_hex"],
        "coinbase_address":  POOL_ADDRESS,
        "reward_sats":       job["reward_sats"],
        "txs":               job["txs"],
        "difficulty":        round(worker_diff, 6),
    }


@app.post("/pool/submit", summary="Submit a mining share")
def submit(body: SubmitRequest, request: Request):
    if not _db:
        raise HTTPException(503, detail="pool not initialized")

    job = _jobs.get(body.job_id)
    if job is None:
        raise HTTPException(400, detail="unknown or expired job_id")

    ok, reason, block_hash, is_block = _validate(
        job, body.header_hex, body.worker_addr)
    if not ok:
        raise HTTPException(400, detail=f"rejected: {reason}")

    # Display hash = reversed
    bh_hex   = bytes(reversed(block_hash)).hex()
    diff_val = hash_to_diff(block_hash)
    w_diff   = _worker_diff_get(body.worker_addr)
    ip       = request.client.host if request.client else ""

    inserted = _db.insert_share(
        worker_addr=body.worker_addr,
        job_id=body.job_id,
        header_hex=body.header_hex,
        block_hash=bh_hex,
        share_diff=diff_val,
        pool_diff=w_diff,
        is_block=is_block,
        block_height=job["height"] if is_block else None,
        ip_addr=ip,
    )
    if not inserted:
        raise HTTPException(400, detail="duplicate share")

    new_diff = _vardiff_update(body.worker_addr)
    out = {
        "status":    "accepted",
        "share_diff": round(diff_val, 6),
        "pool_diff":  round(new_diff, 6),
        "is_block":   is_block,
    }

    if is_block:
        ok2, found_h, detail = _submit_block(job, body.header_hex)
        out["block_found"]  = ok2
        out["block_height"] = found_h if ok2 else None
        out["block_hash"]   = bh_hex
        if ok2:
            _db.insert_block(
                height=found_h,
                block_hash=bh_hex,
                reward_sats=job["reward_sats"],
                fees_sats=job.get("fees_sats", 0),
                finder_addr=body.worker_addr,
                mature_height=found_h + COINBASE_MATURITY,
            )
            print(f"[pool] *** BLOCK FOUND height={found_h} by {body.worker_addr} ***", flush=True)
        else:
            out["node_response"] = detail
            print(f"[pool] block submission rejected: {detail}", flush=True)

    return out


@app.get("/stats", summary="Pool statistics")
def stats():
    db_stats = _db.get_pool_stats_24h() if _db else {}
    active   = _db.get_active_workers(int(time.time()) - 600) if _db else []
    job      = _jobs.get(_current_job_id, {})
    return {
        "pool_name":      POOL_NAME,
        "fee_pct":        POOL_FEE_PCT,
        "pool_address":   POOL_ADDRESS,
        "pplns_n":        PPLNS_N,
        "current_height": job.get("height", 0),
        "pool_diff":      POOL_DIFF,
        "active_workers": len(active),
        **db_stats,
    }


@app.get("/worker/{addr}", summary="Worker stats and pending payout")
def worker(addr: str):
    if not _db:
        raise HTTPException(503, "pool not initialized")
    w = _db.get_worker(addr)
    if not w:
        raise HTTPException(404, "worker not found")
    pays    = _db.get_worker_payouts(addr, 20)
    pending = sum(p["net_sats"] for p in pays if not p["paid"])
    earned  = sum(p["net_sats"] for p in pays if p["paid"])
    return {
        "addr":         addr,
        "first_seen":   w["first_seen"],
        "last_seen":    w["last_seen"],
        "shares_total": w["shares_total"],
        "blocks_found": w["blocks_found"],
        "current_diff": round(_worker_diff_get(addr), 6),
        "pending_sats": pending,
        "earned_sats":  earned,
        "payouts":      pays[:10],
    }


@app.get("/blocks", summary="Recent blocks found by pool")
def blocks(limit: int = Query(20, ge=1, le=100)):
    if not _db:
        raise HTTPException(503, "pool not initialized")
    return {"blocks": _db.get_recent_blocks(limit)}


@app.get("/payouts/{addr}", summary="Full payout history for worker")
def payouts(addr: str, limit: int = Query(50, ge=1, le=200)):
    if not _db:
        raise HTTPException(503, "pool not initialized")
    return {"payouts": _db.get_worker_payouts(addr, limit)}
