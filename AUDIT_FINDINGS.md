# ORI Blockchain Critical Audit Findings

**Date**: 2026-08-27  
**Auditor**: opencode (AI-assisted)  
**Status**: CRITICAL - Multiple consensus-breaking bugs found

---

## Executive Summary

The ORI blockchain codebase has **fundamental consensus parameter mismatches** that cause:
- P2P handshake failures ("chain mismatch (genesis)")
- QT wallet unable to connect to nodes
- Pool servers operating on different consensus rules
- Miners submitting blocks that get rejected
- Explorer displaying wrong network parameters

**Root Cause**: Configuration parameters that affect genesis hash are scattered, have hardcoded fallbacks, and differ between code paths (main node, QT wallet, pool-pplns, pool_server.py, static explorer JS).

---

## CRITICAL BUGS (Consensus-Breaking)

### 1. Config.from_env() Ignores Dataclass Defaults (FIXED - config.py:131-135, 140)
**File**: `config.py`  
**Severity**: CRITICAL  
**Impact**: All nodes run with wrong consensus params unless env vars explicitly set

The `Config.from_env()` method uses **hardcoded fallback strings** instead of dataclass defaults:
- `block_time_seconds`: fallback `"60"` vs dataclass `3.69` → 60s blocks instead of 3.69s
- `retarget_interval`: fallback `"60"` vs dataclass `23414` → retargets every 60 blocks
- `block_reward_sats`: fallback `"4628000000"` (46.28 ORI) vs dataclass `612073980` (6.12 ORI)
- `coinbase_maturity`: fallback `"100"` vs dataclass `2000`

**Fix Applied**: Changed fallbacks to `str(cls.field_name)` so they track dataclass defaults.

---

### 2. Genesis Hash Depends on Multiple Config Parameters
**File**: `chain.py:_make_genesis()` (lines 59-74)  
**Severity**: CRITICAL  
**Impact**: Any parameter difference = different genesis = P2P rejection

Genesis block hash depends on:
| Parameter | Source | Must Match Across ALL Nodes |
|-----------|--------|----------------------------|
| `block_reward_sats` | `cfg.block_reward_sats` | YES |
| `halving_interval` | `cfg.halving_interval` | YES |
| `coinbase_note` | `cfg.coinbase_note` | YES |
| `initial_zeros` | `cfg.initial_zeros` | YES |
| `GENESIS_TIMESTAMP` | Hardcoded `1784610000` | YES |
| `network_hrp` | `cfg.network_hrp` | YES |

**Current Mismatch**: Railway node (`nozomi.proxy.rlwy.net`) runs old config (46.28 ORI reward) while local code now has 6.12 ORI reward → **different genesis** → P2P handshake fails.

---

### 3. P2P Handshake Validates Genesis Hash (p2p.py:322-325)
**File**: `p2p.py` Peer._dispatch()  
**Severity**: CRITICAL  
**Code**:
```python
peer_genesis = data.get("genesis")
if peer_genesis and peer_genesis != node.chain.genesis_hash():
    self.disconnect_reason = "chain mismatch (genesis)"
    raise ValueError("chain mismatch (genesis)")
```
**Impact**: Any config mismatch = immediate disconnect. QT wallet and pool servers cannot sync.

---

### 4. QT Wallet Connects to Wrong Port (API vs P2P)
**File**: `qt/controller.py` user input handling  
**Severity**: HIGH  
**User Error**: Connecting to `sakura.proxy.rlwy.net:24044` — this is likely an **HTTP/API port**, not P2P port (8033).  
**Evidence**: p2p.py:254-258 warns: *"Endpoint answered with non-P2P data — this looks like an HTTP/API port added as a peer."*

**Fix Needed**: QT wallet must connect to P2P port (8033), not API port.

---

### 5. Static Explorer Has Hardcoded Wrong Network Defaults
**File**: `static/index.html` line 203  
**Severity**: HIGH  
**Code**:
```javascript
const state = {tip:-1, reward:4628000000, halving:2102400, timer:null};
```
**Wrong Values**: 
- `reward: 4628000000` = 46.28 ORI (should be 612073980 = 6.12 ORI)
- `halving: 2102400` = ~2.1M blocks (should be 30143415 = 30.1M blocks)

**Impact**: Explorer displays wrong block reward, halving schedule, miner reward calculations.

---

### 6. Static Explorer Contains "2026" Branding
**File**: `static/index.html` lines 166, 182  
**Severity**: LOW (cosmetic)  
**Lines**:
- Line 166: `<span>2026</span>` in logo
- Line 182: `&copy; 2026` in footer

**User Request**: Remove 2026 references.

---

### 7. pool-pplns/pool_server.py Has Duplicated Consensus Logic
**File**: `pool-pplns/pool_server.py`  
**Severity**: HIGH  
**Issues**:
- Imports `coinbase_tx` from parent `tx.py` but uses own env vars for `POOL_ADDRESS`, `POOL_FEE_PCT`
- `_expected_merkle()` recomputes merkle root — must EXACTLY match node's merkle computation
- Uses `coinbase_tx(height, reward_sats, POOL_ADDRESS, fee_address=POOL_FEE_ADDRESS, fee_pct=POOL_FEE_PCT)` — fee splitting logic must match node's coinbase validation
- **No halving_interval awareness** — pool doesn't know about halving, just uses `reward_sats` from node template
- **Vardiff logic** (`POOL_DIFF_SHIFT`, `MIN_SHIFT`, `MAX_SHIFT`) is pool-specific but affects share validation

**Risk**: If pool's coinbase construction differs from node's (message field, fee split, script format), submitted blocks will fail merkle check.

---

### 8. pool_server.py (Non-PPLNS) Has Different Vardiff Logic
**File**: `pool_server.py`  
**Severity**: MEDIUM  
- Uses `POOL_DIFF_SHIFT` (default 12), `MIN_SHIFT` (4), `MAX_SHIFT` (24)
- Shift-based: `pool_target = node_target << shift`
- Different from pool-pplns which uses `POOL_DIFF` float and vardiff per worker
- Both pools must produce blocks that pass node validation

---

### 9. Miner Merkle Root Bug (FIXED - miner_standalone.cpp)
**File**: `miner_standalone.cpp` `build_coinbase()`  
**Severity**: CRITICAL (was causing "merkle root mismatch")  
**Root Cause**: Node's `Transaction.serialize()` appends empty message varint (`0x00`) but C++ miner omitted it.  
**Fix Applied**: Added `tx.push_back(0x00)` after locktime in `build_coinbase()`.

---

### 10. Railway Deployment Config Drift
**Severity**: CRITICAL (Operational)  
**Issue**: User changed local config.py but Railway deployment still runs old code with:
- Old fallback values (46.28 ORI reward)
- Old chain DB (blocks mined with 46.28 ORI reward)
- No env vars set for new params

**Required**: 
1. Redeploy Railway with fixed config.py
2. Set env vars: `BTPY_BLOCK_REWARD=612073980`, `BTPY_HALVING_INTERVAL=30143415`, `BTPY_BLOCK_TIME=3.69`, `BTPY_RETARGET_INTERVAL=23414`, `BTPY_COINBASE_MATURITY=2000`
3. **WIPE Railway volume** (chain DB) — genesis changed

---

## HIGH SEVERITY BUGS

### 11. Block Time Config Not Used in Retarget Until Height 23414
**File**: `chain.py:expected_bits()` line 208  
**Issue**: `retarget_interval` default 23414, but old fallback was 60. First retarget at block 23414. Until then, difficulty stays at genesis minimum (`initial_zeros=2`).  
**Result**: Blocks found in ~1s (as user observed) instead of ~3.69s target. This is **expected behavior** until retarget activates.

---

### 12. Coinbase Maturity Mismatch
**File**: `chain.py:357` validation uses `cfg.coinbase_maturity`  
**Old fallback**: 100 blocks  
**New dataclass**: 2000 blocks  
**Impact**: If nodes disagree on maturity, coinbase spends will be accepted on one node, rejected on another → chain split.

---

### 13. Network Magic Hardcoded
**File**: `config.py:58` `network_magic: bytes = b"\x4f\x52\x49\x31"` (ORI1)  
**File**: `p2p.py:1316` `magic: bytes` param in `read_msg()`  
**Risk**: If changed, all P2P breaks. Should be in config and consistent.

---

### 14. No Genesis Hash Verification at Startup
**Missing**: No check that local genesis matches expected genesis for the configured parameters. Nodes silently accept wrong chain if DB corrupted.

---

### 15. AssumeValid Config Not in from_env()
**File**: `config.py` — `assume_valid_block`, `assume_valid_height`, `assume_valid_min_depth` are in dataclass but NOT loaded from env/file in `from_env()`.  
**Impact**: These security features can't be configured via env vars.

---

## MEDIUM SEVERITY BUGS

### 16. Transaction Message Field Parsing Ambiguity
**File**: `tx.py:98-123` `Transaction.parse()`  
**Issue**: After parsing locktime, if `pos < len(data)`, reads message varint. For multi-tx blocks, this consumes next tx's version bytes as "message".  
**Mitigation**: Only matters for blocks with >1 tx. Current mining mostly produces 1-tx blocks.

---

### 17. Shield Window Uses Wrong Config in Retarget
**File**: `chain.py:221-225` `ori_retarget_next_bits()` called with `cfg.block_time_seconds`  
**But**: `expected_bits()` at line 208 checks `height % cfg.retarget_interval != 0` — if retarget_interval wrong, retarget never triggers or triggers at wrong height.

---

### 18. Fee Tiers Per vB Not in from_env()
**File**: `config.py:64-72` `fee_tiers_per_vb` dict not loadable from env. Hardcoded defaults only.

---

### 19. Max Block Bytes Not in from_env()
**File**: `config.py:36` `max_block_bytes: int = 100_000` not in from_env() — can't be overridden.

---

### 20. QT Wallet P2P Peer Management Uses Node's Network Directly
**File**: `qt/controller.py` `connected_peers()` accesses `node.network.peers`  
**Issue**: QT wallet runs in-process node. If user adds peer via GUI, it calls `node.add_peer()` which uses `node.network.connect()`. This works but the QT wallet's embedded node must have P2P enabled and correct port.

---

## LOW SEVERITY / COSMETIC

### 21. Explorer "Next Block" Timer Hardcoded in JS
**File**: `static/index.html` line 509 shows `info.block_time_seconds` from API — this works if node returns correct value.

---

### 22. Duplicate pool_server.py Files
Two pool implementations:
- `pool_server.py` (root) — shift-based vardiff, Gist cloud sync
- `pool-pplns/pool_server.py` — float difficulty, PPLNS points, SQLite DB  
**Confusion**: Which is canonical? Both have duplicated consensus logic.

---

### 23. No Integration Tests for Cross-Component Consensus
No test verifies: miner → pool → node → P2P → QT wallet all agree on genesis/consensus.

---

## FIX PLAN (Priority Order)

### Phase 1: Consensus Parameter Unification (CRITICAL)
1. ✅ Fix `config.py` fallbacks to use dataclass defaults (DONE)
2. Add `halving_interval` to `from_env()` (missing!)
3. Add `assume_valid_*` to `from_env()`
4. Add `fee_tiers_per_vb`, `max_block_bytes` to `from_env()`
5. Make `GENESIS_TIMESTAMP` configurable via env (optional, but useful for testnets)
6. Make `network_magic` loadable from env

### Phase 2: Genesis Verification & P2P Hardening
7. Add `genesis_hash()` verification at startup — log warning if mismatch with expected
8. Add `expected_genesis_hash()` method that computes what genesis SHOULD be for current config
9. In P2P handshake, include more chain params in version message (reward, halving, block_time) for early mismatch detection

### Phase 3: Pool Synchronization
10. Ensure `pool-pplns/pool_server.py` and `pool_server.py` use EXACT same `coinbase_tx` construction as node
11. Remove duplicated merkle/coinbase logic in pools — import from core modules
12. Pool must validate submitted shares against node's current template (already done)

### Phase 4: QT Wallet Fixes
13. Fix QT wallet peer connection UI to enforce P2P port (8033) not API port
14. Add genesis mismatch detection in QT wallet with clear error message

### Phase 5: Explorer Fixes
15. Remove "2026" from logo and footer
16. Fix hardcoded `state.reward` and `state.halving` in explorer JS — fetch from `/info/` API on load

### Phase 6: Testing & Deployment
17. Write integration test: fresh node + miner + pool + QT wallet all sync
18. Document required env vars for Railway deployment
19. Rebuild `miner-ori.exe` with merkle fix
20. Push all fixes to GitHub

---

## Required Railway Environment Variables

```bash
# Consensus parameters (MUST match local config.py dataclass defaults)
BTPY_BLOCK_REWARD=612073980
BTPY_HALVING_INTERVAL=30143415
BTPY_BLOCK_TIME=3.69
BTPY_RETARGET_INTERVAL=23414
BTPY_COINBASE_MATURITY=2000
BTPY_INITIAL_ZEROS=2
BTPY_SHIELD_WINDOW=11

# Network
BTPY_P2P_PORT=8033
BTPY_API_PORT=8000
BTPY_API_HOST=0.0.0.0
BTPY_P2P_HOST=0.0.0.0
BTPY_NETWORK_HRP=ori
BTPY_COIN_NAME=ORI

# P2P Seeds (optional)
BTPY_SEED_PEERS=seed1.example.com:8033,seed2.example.com:8033

# API Security
BTPY_API_TOKEN=your-secure-token
BTPY_REQUIRE_API_TOKEN_WHEN_PUBLIC=1

# Pool (if running pool on Railway)
POOL_ADDRESS=ori1q...
POOL_FEE_PCT=1.0
POOL_FEE_ADDRESS=ori1q...
ORI_NODE_URL=https://your-node.railway.app
ORI_NODE_TOKEN=your-api-token
```

---

## Verification Checklist After Fixes

- [ ] Local node starts, genesis hash matches expected for config
- [ ] QT wallet connects to local node P2P port (8033) → handshake completes
- [ ] Miner (miner-ori.exe) submits block → accepted (not "merkle root mismatch")
- [ ] Pool (pool-pplns) submits block via node → accepted
- [ ] Two nodes with same config + wiped DB → P2P handshake completes, sync works
- [ ] Explorer shows correct block reward (6.12 ORI), halving (30.1M), block time (3.69s)
- [ ] Railway node redeployed with env vars + wiped volume → same genesis as local
- [ ] Miner against Railway → accepted blocks
- [ ] QT wallet against Railway → connects, syncs, shows correct balances