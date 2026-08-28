import argparse
import hashlib
import json
import multiprocessing as mp
import os
import queue
import struct
import sys
import time
import urllib.error
import urllib.request
from ctypes import c_ulonglong

from block import Block, BlockHeader
from merkle import merkle_root
from tx import Transaction, coinbase_tx
from utils import unhexstr

POLL_SECONDS = 3.69  # Match config.py block_time_seconds for accurate difficulty adjustment
PROGRESS_SECONDS = 0.5
DEFAULT_BATCH_NONCES = 65_536
NONCE_SPACE = 1 << 32

_RED = "\033[1;31m"
_GREEN = "\033[1;32m"
_YELLOW = "\033[33m"
_BLUE = "\033[1;34m"
_CYAN = "\033[36m"
_DIM = "\033[2m"
_RESET = "\033[0m"


def _auth_headers(api_token: str = "") -> dict:
    headers = {"Content-Type": "application/json"}
    if api_token:
        headers["X-API-Key"] = api_token
    return headers


def http_get(url: str, api_token: str = ""):
    req = urllib.request.Request(url, headers=_auth_headers(api_token), method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def http_post(url: str, body: dict, api_token: str = ""):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers=_auth_headers(api_token),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _target_bytes_from_template(template: dict) -> bytes:
    target_hex = str(template.get("target", "0x0"))
    target_int = int(target_hex, 16)
    return target_int.to_bytes(32, "big")


def _header_static76(header: BlockHeader) -> bytes:
    return struct.pack(
        "<i32s32sII",
        header.version,
        header.prev_hash,
        header.merkle_root,
        header.timestamp,
        header.bits,
    )


def _kernel_full_header(static76: bytes, target_bytes: bytes, start: int, end: int):
    sha256 = hashlib.sha256
    pack_into = struct.pack_into
    header = bytearray(80)
    header[:76] = static76
    nonce = start
    while nonce < end:
        pack_into("<I", header, 76, nonce)
        digest = sha256(sha256(header).digest()).digest()
        if digest <= target_bytes:
            return nonce
        nonce += 1
    return None


def _kernel_midstate_copy(static76: bytes, target_bytes: bytes, start: int, end: int):
    sha256 = hashlib.sha256
    pack_into = struct.pack_into
    nonce_buf = bytearray(4)
    
    # Pre-calculate midstate for the first 64 bytes (one SHA256 block)
    base = sha256()
    base.update(static76[:64])
    midstate = base.copy()
    
    # Remaining 12 bytes from static header + 4 bytes nonce
    tail = static76[64:76] + b"\x00\x00\x00\x00"
    tail_ba = bytearray(tail)
    
    nonce = start
    while nonce < end:
        pack_into("<I", tail_ba, 12, nonce)
        # Second half of first SHA256
        h1 = midstate.copy()
        h1.update(tail_ba)
        # Second SHA256
        if sha256(h1.digest()).digest() <= target_bytes:
            return nonce
        nonce += 1
    return None


def _benchmark_kernel(static76: bytes, target_bytes: bytes, loops: int = 4096) -> str:
    target_never = b"\x00" * 32
    t0 = time.perf_counter()
    _kernel_full_header(static76, target_never, 0, loops)
    full_dt = time.perf_counter() - t0
    t0 = time.perf_counter()
    _kernel_midstate_copy(static76, target_never, 0, loops)
    mid_dt = time.perf_counter() - t0
    return "midstate" if mid_dt <= full_dt else "full"


def _pow_worker(
    static76: bytes,
    target_bytes: bytes,
    worker_id: int,
    worker_count: int,
    batch_nonces: int,
    stop_event,
    result_queue,
    counters,
    kernel_name: str,
):
    if kernel_name == "full":
        kernel = _kernel_full_header
    else:
        kernel = _kernel_midstate_copy

    chunk_start = worker_id * batch_nonces
    chunk_stride = worker_count * batch_nonces
    tries = 0
    counters[worker_id] = 0

    while chunk_start < NONCE_SPACE and not stop_event.is_set():
        chunk_end = min(chunk_start + batch_nonces, NONCE_SPACE)
        nonce = kernel(static76, target_bytes, chunk_start, chunk_end)
        tries += chunk_end - chunk_start
        counters[worker_id] = tries
        if nonce is not None:
            if not stop_event.is_set():
                stop_event.set()
                result_queue.put((nonce, worker_id, tries))
            return
        chunk_start += chunk_stride


def _build_candidate(template: dict, address: str) -> tuple[BlockHeader, list]:
    height = int(template["height"])
    reward = int(template["reward_sats"])
    coinbase = coinbase_tx(height, reward, address)
    txs = [coinbase]
    for tx_hex in template.get("txs", []):
        txs.append(Transaction.from_hex(tx_hex))
    merkle = merkle_root([tx.txid() for tx in txs])
    header = BlockHeader(
        version=1,
        prev_hash=unhexstr(template["prev_hash"]),
        merkle_root=merkle,
        timestamp=max(int(template["timestamp"]), int(time.time())),
        bits=int(template["bits"]),
        nonce=0,
    )
    return header, txs


def mine_one(
    template: dict,
    address: str,
    worker_count: int,
    found_event=None,
    progress=None,
    batch_nonces: int = DEFAULT_BATCH_NONCES,
    kernel_name: str = "auto",
    refresh_seconds: float = 0,
):
    worker_count = max(1, int(worker_count))
    batch_nonces = max(256, min(int(batch_nonces), NONCE_SPACE))
    if found_event is not None and found_event.is_set():
        return None, {
            "tries": 0,
            "elapsed": 0.0,
            "rate": 0,
            "kernel": kernel_name,
            "batch_nonces": batch_nonces,
            "cancelled": True,
        }
    header, txs = _build_candidate(template, address)
    static76 = _header_static76(header)
    target_bytes = _target_bytes_from_template(template)
    if kernel_name == "auto":
        kernel_name = _benchmark_kernel(static76, target_bytes)

    ctx = mp.get_context()
    stop_event = ctx.Event()
    result_queue = ctx.Queue(maxsize=1)
    counters = ctx.RawArray(c_ulonglong, worker_count)
    processes = []
    t0 = time.monotonic()
    deadline = t0 + refresh_seconds if refresh_seconds and refresh_seconds > 0 else None

    for worker_id in range(worker_count):
        proc = ctx.Process(
            target=_pow_worker,
            args=(
                static76,
                target_bytes,
                worker_id,
                worker_count,
                batch_nonces,
                stop_event,
                result_queue,
                counters,
                kernel_name,
            ),
            daemon=True,
        )
        processes.append(proc)
        proc.start()

    winning_nonce = None
    last_progress = 0.0
    try:
        while winning_nonce is None:
            try:
                winning_nonce, winner_id, winner_tries = result_queue.get(timeout=0.1)
                break
            except queue.Empty:
                pass

            elapsed = time.monotonic() - t0
            tries = sum(counters)
            if progress is not None and elapsed - last_progress >= PROGRESS_SECONDS:
                last_progress = elapsed
                rate = tries / elapsed if elapsed > 0 else 0
                progress(
                    {
                        "tries": tries,
                        "elapsed": elapsed,
                        "rate": rate,
                        "kernel": kernel_name,
                        "batch_nonces": batch_nonces,
                    }
                )

            if found_event is not None and found_event.is_set():
                stop_event.set()
                break
            if deadline is not None and time.monotonic() >= deadline:
                stop_event.set()
                break
            if all(not proc.is_alive() for proc in processes):
                break
    finally:
        stop_event.set()
        for proc in processes:
            proc.join(timeout=1.0)
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=1.0)

    elapsed = time.monotonic() - t0
    tries = sum(counters)
    if winning_nonce is None:
        return None, {
            "tries": tries,
            "elapsed": elapsed,
            "rate": tries / elapsed if elapsed > 0 else 0,
            "kernel": kernel_name,
            "batch_nonces": batch_nonces,
            "exhausted": tries >= NONCE_SPACE,
        }

    header.nonce = int(winning_nonce)
    block = Block(header, txs)
    block.mine_stats = {
        "tries": tries,
        "elapsed": elapsed,
        "rate": tries / elapsed if elapsed > 0 else 0,
        "kernel": kernel_name,
        "batch_nonces": batch_nonces,
    }
    return block, block.mine_stats


def _format_eta(seconds: float) -> str:
    if seconds == float("inf"):
        return "inf"
    if seconds < 3600:
        return f"{seconds:.0f}s"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def main():
    parser = argparse.ArgumentParser(description="ORI standalone miner")
    parser.add_argument("--node", default="http://127.0.0.1:8000")
    parser.add_argument("--address", required=True, help="payout address")
    parser.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    parser.add_argument("--limit", type=int, default=0, help="stop after N accepted blocks (0 = unlimited)")
    parser.add_argument("--quiet", action="store_true", help="only print block results")
    parser.add_argument("--api-token", default=os.environ.get("BTPY_API_TOKEN", ""), help="X-API-Key for protected mining endpoints")
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH_NONCES, help="contiguous nonce chunk per worker")
    parser.add_argument("--kernel", choices=("auto", "midstate", "full"), default="auto", help="hashing kernel")
    parser.add_argument("--refresh", type=float, default=30.0, help="refresh template after N seconds without a solution (0 = scan full nonce space)")
    args = parser.parse_args()

    node_url = args.node.rstrip("/")
    mined = 0
    print(
        f"miner started -> {_BLUE}node {node_url}{_RESET}  "
        f"payout {_CYAN}{args.address}{_RESET}  workers {_CYAN}{args.threads}{_RESET} "
        f"batch {_CYAN}{args.batch:,}{_RESET} kernel {_CYAN}{args.kernel}{_RESET}"
        + (f"  limit {_YELLOW}{args.limit}{_RESET} blocks" if args.limit else "")
    )
    while True:
        try:
            template = http_get(f"{node_url}/mining/template?address={args.address}", args.api_token)
            height = int(template["height"])
            difficulty = template.get("difficulty", 0)
            target_int = int(template["target"], 16)
            p_find = target_int / (1 << 256)
            if not args.quiet:
                print(
                    f"{_BLUE}[round]{_RESET} height {_CYAN}{height}{_RESET} "
                    f"| difficulty {_YELLOW}{difficulty:.2f}{_RESET} "
                    f"| bits {_DIM}{template['bits']}{_RESET} "
                    f"| target 0x{_DIM}{target_int:064x}{_RESET}"
                )
                if template.get("tx_count", 0):
                    print(
                        f"{_BLUE}[round]{_RESET} txs {_CYAN}{template['tx_count']}{_RESET} "
                        f"(fees {_GREEN}{template.get('fees_sats', 0):,}{_RESET} sats) "
                        f"| reward {_GREEN}{template['reward_sats']:,}{_RESET} sats"
                    )
                else:
                    print(
                        f"{_BLUE}[round]{_RESET} no txs in block "
                        f"| reward {_GREEN}{template['reward_sats']:,}{_RESET} sats"
                    )

            def progress(st):
                rate = st["rate"]
                eta = (1.0 / (p_find * rate)) if (p_find > 0 and rate > 0) else float("inf")
                print(
                    f"\r{_YELLOW}[work ]{_RESET} h{_CYAN}{height}{_RESET} "
                    f"| {_YELLOW}{rate / 1e6:.2f} Mhash/s{_RESET} "
                    f"| {_DIM}{st['tries'] / 1e6:.2f}M nonces{_RESET} "
                    f"| {_DIM}{st['elapsed']:.0f}s{_RESET} "
                    f"| kernel {_CYAN}{st['kernel']}{_RESET} "
                    f"| eta ~{_CYAN}{_format_eta(eta)}{_RESET}"
                    + " " * 8,
                    end="",
                    flush=True,
                )

            t0 = time.monotonic()
            block, stats = mine_one(
                template,
                args.address,
                args.threads,
                None,
                None if args.quiet else progress,
                batch_nonces=args.batch,
                kernel_name=args.kernel,
                refresh_seconds=args.refresh,
            )
            dt = time.monotonic() - t0
            if block is None:
                if not args.quiet:
                    print(
                        f"\r{_DIM}[round]{_RESET} refreshed after {dt:.1f}s "
                        f"| {stats['rate'] / 1e6:.2f} Mhash/s"
                        + " " * 16
                    )
                time.sleep(POLL_SECONDS)
                continue

            print()
            print(
                f"{_CYAN}[found]{_RESET} nonce {_CYAN}{block.header.nonce}{_RESET} "
                f"| {stats['tries']:,} nonces in {_DIM}{dt:.1f}s{_RESET} "
                f"({_YELLOW}{stats['rate'] / 1e6:.2f} Mhash/s{_RESET}, kernel {stats['kernel']})"
            )
            result = http_post(f"{node_url}/mining/submit", {"block": block.to_hex()}, args.api_token)
            if result.get("height") is not None:
                mined += 1
                print(
                    f"{_GREEN}[block ] ACCEPTED{_RESET} height {_CYAN}{result['height']}{_RESET} "
                    f"| hash {_DIM}{block.block_hash_hex()}{_RESET} "
                    f"| txs {len(block.transactions) - 1}"
                )
                if args.limit and mined >= args.limit:
                    print(f"{_GREEN}limit reached{_RESET} ({mined} blocks mined) - stopping")
                    sys.exit(0)
            else:
                print(f"{_RED}[block ] REJECTED{_RESET} by node: {result.get('detail', 'unknown')}")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode()).get("detail", str(exc))
            except Exception:
                detail = str(exc)
            print(f"{_RED}[block ] REJECTED{_RESET} by node: {detail}")
            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            print("\nminer stopped")
            sys.exit(0)
        except Exception as exc:
            print(f"{_RED}mining round failed:{_RESET} {exc}")
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
