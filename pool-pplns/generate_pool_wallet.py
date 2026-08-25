#!/usr/bin/env python3
"""Generate a new ORI pool wallet keypair.

Run once, then set the printed values as Railway environment variables:
    POOL_ADDRESS   → the pool's payout/coinbase address
    POOL_PRIV_HEX  → private key (keep SECRET, never share)
    POOL_PUB_HEX   → compressed public key

Usage:
    cd blockchain-fastapi
    python pool-pplns/generate_pool_wallet.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crypto import new_keypair, pub_to_address

def main():
    priv_bytes, pub_bytes = new_keypair()
    address = pub_to_address(pub_bytes)

    print("\n" + "=" * 60)
    print("  ORI Pool Wallet — KEEP PRIVATE KEY SECRET!")
    print("=" * 60)
    print(f"\n  POOL_ADDRESS  = {address}")
    print(f"  POOL_PRIV_HEX = {priv_bytes.hex()}")
    print(f"  POOL_PUB_HEX  = {pub_bytes.hex()}")
    print("\n  Copy these three values into your Railway environment")
    print("  variables for the pool service.")
    print("\n  ⚠️  BACK UP your private key somewhere safe!")
    print("=" * 60 + "\n")

    # Also save to pool_wallet.json for local use
    out = {
        "address":  address,
        "priv_hex": priv_bytes.hex(),
        "pub_hex":  pub_bytes.hex(),
    }
    out_path = os.path.join(os.path.dirname(__file__), "pool_wallet_BACKUP.json")
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"  Backup saved to: {out_path}")
    print("  (DELETE this file after copying keys to Railway env)\n")

if __name__ == "__main__":
    main()
