# Blockchain Security Audit Report - ORI Core
**Auditor:** Senior Blockchain Architect (Satoshi Level)
**Date:** 2026-08-23

## 1. High Severity: Coinbase Height Validation (tx.py)
Current implementation of `coinbase_height` only checks `script[0] > 75`. It doesn't verify the length of the script against the claimed size. A malicious miner could craft a script that causes out-of-bounds reads or bypasses height checks.

## 2. Medium Severity: Retargeting Window (pow.py)
`ori_retarget_next_bits` uses a fixed 60-block window. Lack of per-block smoothing (like Digishield) makes it susceptible to "hashrate jumping" where miners join for low difficulty and leave, stalling the chain.

## 3. Low Severity: P2P Message Pacing (p2p.py)
The `_consume_msg_tokens` uses `time.sleep`, which can block the event loop in a threaded environment if too many peers are throttled, potentially slowing down the entire node.

## 4. Wallet Exposure (wallet.py)
`wallet.dat` is stored in the data directory without mandatory encryption by default.

---
**Next Actions:**
- [ ] Patch `coinbase_height` in `tx.py`.
- [ ] Implement strict BIP-34 style height checks.
- [ ] Refactor QT UI to match Bitcoin Core.
- [ ] Redesign Explorer HTML.
