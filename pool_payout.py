#!/usr/bin/env python3
"""ORI Pool Payout Transaction Builder

Sends accumulated miner balances from pool's mature coinbase rewards.
Can run on custom schedule (every 1000 blocks) instead of waiting for
individual coinbase maturity (2000 blocks).

Usage:
    python pool_payout.py --dry-run  # Test without broadcasting
    python pool_payout.py             # Execute real payout

Environment Variables:
    POOL_NODE_URL - ORI node API endpoint
    BTPY_API_TOKEN - Node authentication token
    POOL_PRIVATE_KEY - Pool wallet private key (HEX)
    POOL_PUBLIC_KEY - Pool wallet public key (HEX)
    MIN_PAYOUT_SATS - Minimum payout threshold (default: 100000000 = 1 ORI)
    PAYOUT_FREQUENCY_BLOCKS - Blocks between payouts (default: 1000)
    POOL_DATA_DIR - Pool ledger directory (default: pool_data)

Security:
    - Private key NEVER logged or stored in files
    - All transactions logged to audit trail
    - Dry-run mode for testing without broadcasting
    - Automatic fee calculation with safety margins
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from typing import Dict, List, Tuple

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tx import Transaction, TxIn, TxOut, make_transfer
from crypto import sign, pub_to_address
from utils import sha256d
from config import Config

# ═══════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════

POOL_NODE_URL = os.environ.get("POOL_NODE_URL", "http://127.0.0.1:8000").rstrip("/")
POOL_API_TOKEN = os.environ.get("BTPY_API_TOKEN", "")
POOL_PRIVATE_KEY = os.environ.get("POOL_PRIVATE_KEY", "")  # HEX format
POOL_PUBLIC_KEY = os.environ.get("POOL_PUBLIC_KEY", "")    # HEX format
MIN_PAYOUT_SATS = int(os.environ.get("MIN_PAYOUT_SATS", "100000000"))  # 1 ORI
PAYOUT_FREQUENCY_BLOCKS = int(os.environ.get("PAYOUT_FREQUENCY_BLOCKS", "1000"))
POOL_DATA_DIR = os.environ.get("POOL_DATA_DIR", "pool_data")
LEDGER_PATH = os.path.join(POOL_DATA_DIR, "ledger.json")
PAYOUT_LOG_PATH = os.path.join(POOL_DATA_DIR, "payout_audit.log")

# Fee configuration (sats per virtual byte)
FEE_PER_VB = 1.0  # Conservative: 1 sat/vB
DUST_THRESHOLD = 1000  # Don't create outputs < 1000 sats

# ═══════════════════════════════════════════════════════════════════════════
# HTTP CLIENT
# ═══════════════════════════════════════════════════════════════════════════

def http_get(path: str, timeout: int = 30) -> dict:
    """GET request to ORI node"""
    url = POOL_NODE_URL + path
    headers = {"Accept": "application/json"}
    if POOL_API_TOKEN:
        headers["X-API-Key"] = POOL_API_TOKEN
    
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise Exception(f"HTTP {e.code}: {e.read().decode()}")
    except Exception as e:
        raise Exception(f"Request failed: {e}")

def http_post(path: str, body: dict, timeout: int = 30) -> dict:
    """POST request to ORI node"""
    url = POOL_NODE_URL + path
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    if POOL_API_TOKEN:
        headers["X-API-Key"] = POOL_API_TOKEN
    
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise Exception(f"HTTP {e.code}: {e.read().decode()}")
    except Exception as e:
        raise Exception(f"Request failed: {e}")

# ═══════════════════════════════════════════════════════════════════════════
# BLOCKCHAIN QUERIES
# ═══════════════════════════════════════════════════════════════════════════

def get_current_height() -> int:
    """Get current blockchain height"""
    stats = http_get("/stats")
    return stats["height"]

def get_mature_utxos(pool_address: str, current_height: int, 
                     maturity: int = 2000) -> List[dict]:
    """Fetch pool's UTXOs that are mature enough to spend
    
    Coinbase outputs require 2000 block confirmations before spendable.
    Non-coinbase UTXOs are immediately spendable.
    """
    data = http_get(f"/address/{pool_address}")
    utxos = data.get("utxos", [])
    
    # Filter for mature UTXOs
    mature = []
    for u in utxos:
        if u.get("coinbase"):
            # Coinbase: must wait for maturity
            if u["height"] + maturity <= current_height:
                mature.append(u)
        else:
            # Regular UTXO: immediately spendable
            mature.append(u)
    
    total_value = sum(u["value"] for u in mature)
    print(f"[payout] UTXOs: {len(utxos)} total, {len(mature)} mature, "
          f"{total_value / 1e8:.8f} ORI available", flush=True)
    
    return mature

# ═══════════════════════════════════════════════════════════════════════════
# LEDGER OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════

def load_ledger() -> dict:
    """Load pool ledger state"""
    if not os.path.exists(LEDGER_PATH):
        raise FileNotFoundError(f"Ledger not found: {LEDGER_PATH}")
    
    with open(LEDGER_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_balances() -> Dict[str, int]:
    """Load miner balances from ledger"""
    ledger = load_ledger()
    balances = ledger.get("balances", {})
    
    # Convert string values to int (in case of JSON serialization issues)
    return {addr: int(sats) for addr, sats in balances.items()}

def update_ledger_after_payout(paid_addresses: Dict[str, int]):
    """Deduct paid balances from ledger
    
    NOTE: This modifies ledger.json directly. Make backup first!
    """
    ledger = load_ledger()
    
    for addr, paid_sats in paid_addresses.items():
        current = ledger["balances"].get(addr, 0)
        new_balance = max(0, current - paid_sats)
        ledger["balances"][addr] = new_balance
    
    # Write atomically
    tmp = LEDGER_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    
    # Backup old ledger
    if os.path.exists(LEDGER_PATH):
        import shutil
        shutil.copyfile(LEDGER_PATH, LEDGER_PATH + ".payout_backup")
    
    os.replace(tmp, LEDGER_PATH)
    print(f"[payout] Ledger updated: {len(paid_addresses)} balances deducted", flush=True)

# ═══════════════════════════════════════════════════════════════════════════
# TRANSACTION BUILDER
# ═══════════════════════════════════════════════════════════════════════════

def build_payout_transaction(
    utxos: List[dict],
    payouts: Dict[str, int],
    pool_address: str,
    priv_key: bytes,
    pub_key: bytes,
    fee_per_vb: float = FEE_PER_VB
) -> Tuple[Transaction, dict]:
    """Build and sign payout transaction
    
    Returns:
        (tx, metadata) where metadata contains:
        - total_input: sum of input values
        - total_output: sum of payout outputs
        - change: amount returned to pool
        - fee: transaction fee
        - recipients: number of payouts
    """
    
    # Filter payouts above minimum threshold (exclude pool address)
    eligible = {addr: sats for addr, sats in payouts.items() 
                if sats >= MIN_PAYOUT_SATS and addr != pool_address}
    
    if not eligible:
        raise ValueError(f"No payouts above minimum threshold ({MIN_PAYOUT_SATS} sats)")
    
    total_payout = sum(eligible.values())
    print(f"[payout] Eligible recipients: {len(eligible)}, "
          f"total: {total_payout / 1e8:.8f} ORI", flush=True)
    
    # Select UTXOs (use all available for simplicity - can optimize with coin selection)
    total_input = sum(u["value"] for u in utxos)
    
    if total_input < total_payout:
        raise ValueError(f"Insufficient funds: have {total_input / 1e8:.8f} ORI, "
                         f"need {total_payout / 1e8:.8f} ORI")
    
    # Estimate transaction size
    # Formula: 10 (version+locktime) + inputs*(180) + outputs*(34) + 10 (overhead)
    num_outputs = len(eligible) + 1  # payouts + change
    estimated_vsize = 10 + len(utxos) * 180 + num_outputs * 34 + 10
    fee_sats = int(estimated_vsize * fee_per_vb)
    
    # Add 20% safety margin to fee (better to overpay than get rejected)
    fee_sats = int(fee_sats * 1.2)
    
    print(f"[payout] Estimated size: {estimated_vsize} vB, "
          f"fee: {fee_sats / 1e8:.8f} ORI", flush=True)
    
    # Calculate change
    change = total_input - total_payout - fee_sats
    
    if change < 0:
        raise ValueError(f"Fee ({fee_sats}) + payouts ({total_payout}) "
                         f"exceeds inputs ({total_input})")
    
    # Build inputs
    inputs = [(bytes.fromhex(u["txid"]), u["vout"]) for u in utxos]
    
    # Build outputs (payouts first, then change)
    outputs = [(sats, addr) for addr, sats in eligible.items()]
    
    if change > DUST_THRESHOLD:
        outputs.append((change, pool_address))
        print(f"[payout] Change: {change / 1e8:.8f} ORI back to pool", flush=True)
    else:
        # Donate dust to fee
        fee_sats += change
        change = 0
        print(f"[payout] Change {change} < dust threshold, added to fee", flush=True)
    
    # Build transaction
    tx = make_transfer(
        inputs=inputs,
        outputs=outputs,
        locktime=0,
        rbf=False,  # Disable Replace-By-Fee for payout finality
        message=f"Pool payout to {len(eligible)} miners"
    )
    
    # Sign all inputs
    for i, txin in enumerate(tx.inputs):
        # Sign with SIGHASH_ALL
        sighash = tx.sighash()
        sig = sign(priv_key, sighash)
        txin.script_sig = sig + pub_key
    
    metadata = {
        "total_input": total_input,
        "total_output": total_payout,
        "change": change,
        "fee": fee_sats,
        "recipients": len(eligible),
        "tx_size": len(tx.serialize()),
        "txid": tx.txid().hex()
    }
    
    return tx, metadata

# ═══════════════════════════════════════════════════════════════════════════
# AUDIT LOGGING
# ═══════════════════════════════════════════════════════════════════════════

def log_payout(metadata: dict, payouts: Dict[str, int], success: bool, error: str = None):
    """Write payout to audit log"""
    os.makedirs(os.path.dirname(PAYOUT_LOG_PATH), exist_ok=True)
    
    log_entry = {
        "timestamp": int(time.time()),
        "timestamp_iso": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "success": success,
        "error": error,
        "metadata": metadata,
        "payouts": payouts
    }
    
    with open(PAYOUT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")
        f.flush()
        os.fsync(f.fileno())
    
    print(f"[payout] Audit log written to {PAYOUT_LOG_PATH}", flush=True)

# ═══════════════════════════════════════════════════════════════════════════
# MAIN PAYOUT LOGIC
# ═══════════════════════════════════════════════════════════════════════════

def execute_payout(dry_run: bool = False, force: bool = False):
    """Execute payout process
    
    Args:
        dry_run: Build transaction but don't broadcast
        force: Skip payout frequency check (for manual payouts)
    """
    
    # Validate configuration
    if not POOL_PRIVATE_KEY or not POOL_PUBLIC_KEY:
        raise ValueError("POOL_PRIVATE_KEY and POOL_PUBLIC_KEY environment variables required")
    
    if not POOL_NODE_URL:
        raise ValueError("POOL_NODE_URL environment variable required")
    
    # Load keys
    try:
        priv_key = bytes.fromhex(POOL_PRIVATE_KEY)
        pub_key = bytes.fromhex(POOL_PUBLIC_KEY)
    except ValueError as e:
        raise ValueError(f"Invalid key format (must be HEX): {e}")
    
    # Derive pool address
    pool_address = pub_to_address(pub_key, Config())
    print(f"[payout] Pool address: {pool_address}", flush=True)
    
    # Get current height
    current_height = get_current_height()
    print(f"[payout] Current blockchain height: {current_height}", flush=True)
    
    # Check if it's payout time
    if not force and current_height % PAYOUT_FREQUENCY_BLOCKS != 0:
        print(f"[payout] Not payout height (frequency: every {PAYOUT_FREQUENCY_BLOCKS} blocks)", 
              flush=True)
        print(f"[payout] Next payout at height: {(current_height // PAYOUT_FREQUENCY_BLOCKS + 1) * PAYOUT_FREQUENCY_BLOCKS}", 
              flush=True)
        return
    
    # Load miner balances
    balances = load_balances()
    total_owed = sum(balances.values())
    print(f"[payout] Loaded {len(balances)} miner balances, "
          f"total owed: {total_owed / 1e8:.8f} ORI", flush=True)
    
    if not balances:
        print("[payout] No balances to pay out", flush=True)
        return
    
    # Get mature UTXOs
    utxos = get_mature_utxos(pool_address, current_height, maturity=2000)
    
    if not utxos:
        print("[payout] ERROR: No mature UTXOs available (wait for coinbase maturity)", 
              flush=True)
        return
    
    # Build transaction
    print("[payout] Building payout transaction...", flush=True)
    try:
        tx, metadata = build_payout_transaction(
            utxos, balances, pool_address, priv_key, pub_key
        )
    except Exception as e:
        print(f"[payout] ERROR building transaction: {e}", flush=True)
        log_payout({}, balances, success=False, error=str(e))
        raise
    
    # Display transaction details
    print("\n" + "="*70)
    print("PAYOUT TRANSACTION SUMMARY")
    print("="*70)
    print(f"TXID: {metadata['txid']}")
    print(f"Inputs: {len(tx.inputs)}")
    print(f"Outputs: {len(tx.outputs)}")
    print(f"Size: {metadata['tx_size']} bytes")
    print(f"Total Input: {metadata['total_input'] / 1e8:.8f} ORI")
    print(f"Total Payout: {metadata['total_output'] / 1e8:.8f} ORI")
    print(f"Change: {metadata['change'] / 1e8:.8f} ORI")
    print(f"Fee: {metadata['fee'] / 1e8:.8f} ORI ({metadata['fee'] / metadata['tx_size']:.2f} sat/vB)")
    print(f"Recipients: {metadata['recipients']}")
    print("="*70 + "\n")
    
    # Dry run: don't broadcast
    if dry_run:
        print("[payout] DRY RUN: Transaction built successfully but NOT broadcasted", 
              flush=True)
        print(f"[payout] Transaction hex: {tx.to_hex()[:100]}...", flush=True)
        log_payout(metadata, balances, success=True, error="dry_run")
        return
    
    # Broadcast transaction
    print("[payout] Broadcasting transaction to network...", flush=True)
    try:
        result = http_post("/transactions/submit", {"tx": tx.to_hex()})
        print(f"[payout] ✅ SUCCESS: Transaction accepted by node", flush=True)
        print(f"[payout] Response: {result}", flush=True)
    except Exception as e:
        print(f"[payout] ❌ ERROR: Transaction rejected: {e}", flush=True)
        log_payout(metadata, balances, success=False, error=str(e))
        raise
    
    # Update ledger (deduct paid balances)
    print("[payout] Updating ledger to deduct paid balances...", flush=True)
    paid_addresses = {addr: sats for addr, sats in balances.items() 
                     if sats >= MIN_PAYOUT_SATS and addr != pool_address}
    update_ledger_after_payout(paid_addresses)
    
    # Log success
    log_payout(metadata, balances, success=True)
    
    print("[payout] ✅ PAYOUT COMPLETE", flush=True)

# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="ORI Pool Payout Transaction Builder")
    parser.add_argument("--dry-run", action="store_true", 
                       help="Build transaction but don't broadcast")
    parser.add_argument("--force", action="store_true",
                       help="Skip payout frequency check (manual payout)")
    args = parser.parse_args()
    
    try:
        execute_payout(dry_run=args.dry_run, force=args.force)
    except KeyboardInterrupt:
        print("\n[payout] Interrupted by user", flush=True)
        sys.exit(130)
    except Exception as e:
        print(f"\n[payout] FATAL ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
