# ORI Mining Pool - Complete Deployment & Operation Guide

## Table of Contents
1. [Prerequisites](#prerequisites)
2. [Pool Server Setup](#pool-server-setup)
3. [Railway Deployment](#railway-deployment)
4. [Database Persistence](#database-persistence)
5. [Pool Operation](#pool-operation)
6. [Miner Connection](#miner-connection)
7. [Payout System](#payout-system)
8. [Monitoring & Maintenance](#monitoring--maintenance)
9. [Security Checklist](#security-checklist)
10. [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required Services
- ✅ ORI Node (full node synced to latest height)
- ✅ Railway Account (or any cloud provider with persistent volumes)
- ✅ GitHub Account (for Gist cloud backup)
- ✅ Domain/DNS (optional, for custom pool URL)

### Required Tokens
```bash
# Node API Token (from your ORI node)
BTPY_API_TOKEN="your_node_api_token"

# Pool address (receives mining rewards)
POOL_ADDRESS="ori1q2lpx737545zshpqfcyn35gyex03fljwxfucmt6"

# GitHub Personal Access Token (for cloud backup)
# Create at: https://github.com/settings/tokens
# Scopes needed: gist (create/read/write)
POOL_GIST_TOKEN="ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

---

## Pool Server Setup

### 1. Configuration Variables

Create a `.env` file or set environment variables:

```bash
# ═══════════════════════════════════════════════════════════
# CRITICAL CONFIGURATION
# ═══════════════════════════════════════════════════════════

# Node Connection
POOL_NODE_URL="http://your-node-domain:8000"
BTPY_API_TOKEN="your_node_api_token_here"

# Pool Identity
POOL_ADDRESS="ori1qxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"  # YOUR POOL ADDRESS
POOL_FEE_PCT="1.2"                                      # 1.2% pool fee

# Fee Split (optional - if you want separate fee address)
POOL_FEE_ADDRESS=""  # Leave empty to credit fees to POOL_ADDRESS

# ═══════════════════════════════════════════════════════════
# PPLNS CONFIGURATION
# ═══════════════════════════════════════════════════════════

PPLNS_POINTS="10000"     # Window size: last 10,000 shares
                          # Larger = more fair but slower payouts
                          # Smaller = faster variance but less fair

# ═══════════════════════════════════════════════════════════
# DIFFICULTY ADJUSTMENT (VarDiff)
# ═══════════════════════════════════════════════════════════

POOL_DIFF_SHIFT="12"     # Starting difficulty: node_target << 12
                          # Higher = easier (for slow miners)
                          # Lower = harder (for fast miners)

POOL_MIN_SHIFT="4"       # Hardest difficulty: node_target << 4
POOL_MAX_SHIFT="24"      # Easiest difficulty: node_target << 24

SHARE_FAST_SEC="5"       # If share < 5s apart → increase difficulty
SHARE_SLOW_SEC="45"      # If share > 45s apart → decrease difficulty
SHARE_RATE_LIMIT_SEC="0.5"  # Anti-spam: max 1 share per 0.5s per worker

# ═══════════════════════════════════════════════════════════
# DATABASE PERSISTENCE (CRITICAL!)
# ═══════════════════════════════════════════════════════════

POOL_DATA_DIR="/data"    # MUST be persistent volume (not ephemeral!)
                          # Railway: mount volume to /data
                          # Docker: -v pool_data:/data

# Cloud Backup (Gist) - HIGHLY RECOMMENDED
POOL_GIST_TOKEN="ghp_xxxxxxxxxxxxxxxxxxxxx"  # GitHub Personal Access Token

# Emergency Recovery (ONE-TIME USE ONLY)
# If ledger lost and Gist unavailable, seed initial balances:
# POOL_LEDGER_SEED='{"ori1qaddr1": 50000000, "ori1qaddr2": 30000000}'
# ⚠️ REMOVE THIS VARIABLE AFTER FIRST SUCCESSFUL START!

# ═══════════════════════════════════════════════════════════
# LOGGING & MONITORING
# ═══════════════════════════════════════════════════════════

ORI_LOG_LEVEL="INFO"     # DEBUG for troubleshooting
ORI_LOG_CONSOLE="1"      # Log to console (Railway/Docker logs)
ORI_LOG_FILE="0"         # Don't write log files (use centralized logging)
```

### 2. File Structure

```
blockchain-fastapi/
├── pool_server.py           # Main pool server (PPLNS)
├── pool_payout.py           # Payout transaction builder (NEW - see below)
├── pool_data/               # Local database directory
│   ├── ledger.json         # PPLNS state (balances, shares, blocks)
│   ├── ledger.json.bak     # Automatic backup
│   └── ledger.json.tmp     # Atomic write temp file
├── requirements.txt         # Python dependencies
└── .env                     # Configuration (DO NOT COMMIT!)
```

---

## Railway Deployment

### Step 1: Create Railway Project

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Create new project
railway init
```

### Step 2: Configure Persistent Volume ⚠️ CRITICAL

**Without persistent volume, all miner balances will be LOST on redeploy!**

1. Go to Railway Dashboard → Your Project → Settings
2. Click "Volumes" → "New Volume"
3. Configure:
   - **Mount Path**: `/data`
   - **Size**: 1 GB (sufficient for ledger)
4. Save and redeploy

**Verify Volume Mounted**:
```bash
# Check Railway logs after deploy
railway logs

# Should see:
# [pool] ledger restored from /data/ledger.json: balances=X window=Y blocks=Z
```

### Step 3: Set Environment Variables

```bash
# Set all variables from Configuration section above
railway variables set POOL_NODE_URL="http://your-node:8000"
railway variables set BTPY_API_TOKEN="your_token"
railway variables set POOL_ADDRESS="ori1q..."
railway variables set POOL_FEE_PCT="1.2"
railway variables set PPLNS_POINTS="10000"
railway variables set POOL_DATA_DIR="/data"
railway variables set POOL_GIST_TOKEN="ghp_..."
# ... etc
```

### Step 4: Deploy

```bash
# Deploy to Railway
railway up

# Get public URL
railway domain

# Example output: ori-pool-production.up.railway.app
```

### Step 5: Configure TCP Proxy (for Stratum)

**Pool HTTP API**: `https://ori-pool-production.up.railway.app`

**TCP Proxy** (for miners):
1. Railway Dashboard → Your Service → Settings → Networking
2. Enable TCP Proxy
3. Note the proxy address: `tokaido.proxy.rlwy.net:49718`
4. Miners connect to: `tokaido.proxy.rlwy.net:49718:33333`
   - Format: `proxy_host:proxy_port:internal_port`
   - Internal port 33333 is the pool stratum port (not HTTP 8000)

**⚠️ NOTE**: Current `pool_server.py` serves HTTP API only. For stratum protocol support, see "Advanced: Stratum Proxy" section.

---

## Database Persistence

### Three-Layer Backup Strategy

#### Layer 1: Local File (Atomic Writes)
```python
# Automatic on every share submission
# pool_server.py Ledger.save()

1. Write to /data/ledger.json.tmp
2. fsync() to ensure disk persistence
3. Copy /data/ledger.json → /data/ledger.json.bak
4. os.replace(tmp, ledger.json)  # Atomic on POSIX
```

**Protection**: Crash-safe, prevents corruption

#### Layer 2: GitHub Gist Cloud Sync
```python
# Automatic every 5 local saves
# pool_server.py GistLedgerSync.upload()

1. JSON serialization of ledger state
2. Upload to private GitHub Gist
3. On startup: if local files corrupt, restore from Gist
```

**Protection**: Redeploy/volume-loss recovery

#### Layer 3: Manual Backup (Recommended)
```bash
# Daily backup script (run as cron job)
#!/bin/bash
DATE=$(date +%Y%m%d)
scp user@pool-server:/data/ledger.json "./backups/ledger-$DATE.json"
```

**Protection**: Catastrophic failure (GitHub/Railway both down)

### Verify Persistence Health

```bash
# Check ledger endpoint
curl https://your-pool.railway.app/pool/ledger

# Should return:
{
  "path": "/data/ledger.json",
  "on_volume": true,              # ✅ MUST BE TRUE
  "primary": {
    "bytes": 12345,
    "sha256": "abc123...",
    "modified_age_s": 30
  },
  "backup": {
    "bytes": 12340,
    "sha256": "def456...",
    "modified_age_s": 600
  },
  "saved_at_iso": "2026-08-28 10:30:45 UTC",
  "saves_done": 142,
  "totals": {
    "blocks_found": 5,
    "shares_accepted": 1420,
    "window_points": 10000,
    "balances_sats": {"ori1q...": 50000000}
  }
}
```

**Red Flags**:
- ❌ `"on_volume": false` → Using ephemeral filesystem!
- ❌ `modified_age_s > 3600` → Ledger not saving (check logs)
- ❌ `primary: null` → File doesn't exist (check permissions)

---

## Pool Operation

### Start Pool Server

```bash
# Local development
uvicorn pool_server:app --host 0.0.0.0 --port 9000

# Production (Railway auto-starts with Procfile)
# Or manually:
uvicorn pool_server:app --host 0.0.0.0 --port $PORT --workers 2
```

### API Endpoints

#### GET `/` - Pool Info
```bash
curl https://your-pool.railway.app/

# Response:
{
  "name": "ORI PPLNS Pool (pool_server.py)",
  "node": "http://node-url:8000",
  "node_reachable": true,
  "node_tip_height": 5042,
  "pool_address": "ori1q2lpx...",
  "fee_pct": 1.2,
  "pplns_points": 10000,
  "blocks_found": 3,
  "shares_accepted": 1523,
  "workers": 5
}
```

#### GET `/pool/job?worker=ori1q...` - Get Mining Job
```bash
curl "https://your-pool.railway.app/pool/job?worker=ori1qYOUR_ADDRESS"

# Response:
{
  "job_id": "5042-123",
  "height": 5042,
  "reward_sats": 612073980,
  "bits": 503382015,
  "timestamp": 1724850000,
  "prev_hash": "882050a81eba6a...",
  "coinbase_address": "ori1q2lpx...",  # Pool address
  "pool_target": "0000000000001fff...",  # Easier difficulty
  "node_target": "0000000000000001...",  # Network difficulty
  "txs": ["tx_hex_1", "tx_hex_2"]
}
```

#### POST `/pool/submit` - Submit Share
```bash
curl -X POST https://your-pool.railway.app/pool/submit \
  -H "Content-Type: application/json" \
  -d '{
    "worker_addr": "ori1qYOUR_ADDRESS",
    "job_id": "5042-123",
    "header_hex": "010000002050a81e...80bytes..."
  }'

# Response (valid share):
{
  "accepted": true,
  "is_block": false,
  "pool_target": "0000000000001fff...",
  "window_points": 9523,
  "balance_sats": 1250000,
  "shift": 12,
  "worker_shares": 142
}

# Response (found block):
{
  "accepted": true,
  "is_block": true,
  "height": 5042,
  "reward_sats": 612073980,
  "payout": {
    "ori1qaddr1": 245000000,
    "ori1qaddr2": 367000000
  },
  "window_points": 10000,
  "balance_sats": 1250000
}
```

#### GET `/pool/stats` - Leaderboard & Stats
```bash
curl https://your-pool.railway.app/pool/stats

# Or visit in browser for HTML dashboard:
# https://your-pool.railway.app/pool/stats
```

#### GET `/pool/ledger` - Persistence Health
```bash
curl https://your-pool.railway.app/pool/ledger
# See "Verify Persistence Health" section above
```

---

## Miner Connection

### Option 1: Direct HTTP Pool (Current Implementation)

**Miner Configuration**:
```bash
# Using Python miner
python miner.py \
  --pool https://your-pool.railway.app \
  --worker ori1qYOUR_MINER_ADDRESS \
  --threads 4

# Using C++ miner (miner-ori.exe)
miner-ori.exe \
  --pool https://your-pool.railway.app \
  --worker ori1qYOUR_MINER_ADDRESS \
  --threads 8
```

**How It Works**:
1. Miner polls `/pool/job?worker=...` every 3.69 seconds
2. Miner hashes block header with different nonces
3. When valid share found: POST to `/pool/submit`
4. Pool validates share, credits PPLNS points
5. If share also meets network difficulty: pool submits block to node

### Option 2: Stratum Protocol (Advanced - Not Implemented Yet)

For standard mining software (cgminer, bfgminer, etc.), implement stratum protocol:

**Create `stratum_proxy.py`** (separate from `pool_server.py`):
```python
# Listens on TCP port 33333
# Converts stratum protocol → HTTP pool API
# Handles: mining.subscribe, mining.authorize, mining.submit
```

**Deployment**:
```bash
# Railway: Add second service for stratum
railway service add stratum-proxy

# Expose TCP port 33333
railway up
```

**Miner Configuration**:
```bash
# Standard stratum miners
cgminer -o stratum+tcp://tokaido.proxy.rlwy.net:49718 \
        -u ori1qYOUR_ADDRESS \
        -p x

# Or use Railway TCP proxy format:
# stratum+tcp://proxy.rlwy.net:PORT:33333
```

---

## Payout System

### Understanding Coinbase Maturity

**Problem**: Mining reward (coinbase) cannot be spent until 2000 blocks mature.

**Pool receives reward at height H**:
- Balance credited immediately to miners (tracked in ledger)
- But coinbase UTXO is LOCKED until height H + 2000
- If pool tries to spend before maturity → TX REJECTED by network

**Solution**: Custom payout schedule using MATURE coinbase only.

### Payout Implementation

Create `pool_payout.py`:

```python
#!/usr/bin/env python3
"""ORI Pool Payout Transaction Builder

Sends accumulated miner balances from pool's mature coinbase rewards.
Can run on custom schedule (every 1000 blocks) instead of waiting for
individual coinbase maturity.
"""

import json
import os
import sys
import urllib.request
from tx import Transaction, TxIn, TxOut, make_transfer
from crypto import sign, pub_to_address
from utils import sha256d

# Configuration
POOL_NODE_URL = os.environ.get("POOL_NODE_URL", "http://127.0.0.1:8000")
POOL_API_TOKEN = os.environ.get("BTPY_API_TOKEN", "")
POOL_PRIVATE_KEY = os.environ.get("POOL_PRIVATE_KEY", "")  # HEX format
POOL_PUBLIC_KEY = os.environ.get("POOL_PUBLIC_KEY", "")    # HEX format
MIN_PAYOUT_SATS = int(os.environ.get("MIN_PAYOUT_SATS", "100000000"))  # 1 ORI
PAYOUT_FREQUENCY_BLOCKS = int(os.environ.get("PAYOUT_FREQUENCY_BLOCKS", "1000"))

def http_get(path: str) -> dict:
    url = POOL_NODE_URL + path
    req = urllib.request.Request(url, headers={
        "X-API-Key": POOL_API_TOKEN
    }, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def http_post(path: str, body: dict) -> dict:
    url = POOL_NODE_URL + path
    req = urllib.request.Request(url, 
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "X-API-Key": POOL_API_TOKEN
        }, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def get_mature_utxos(pool_address: str, current_height: int, maturity: int = 2000) -> list:
    """Fetch pool's UTXOs that are mature enough to spend"""
    data = http_get(f"/address/{pool_address}")
    utxos = data.get("utxos", [])
    
    mature = [u for u in utxos 
              if not u.get("coinbase") or u["height"] + maturity <= current_height]
    
    print(f"[payout] Total UTXOs: {len(utxos)}, Mature: {len(mature)}")
    return mature

def load_ledger_balances(ledger_path: str = "pool_data/ledger.json") -> dict:
    """Load miner balances from pool ledger"""
    with open(ledger_path, "r") as f:
        ledger = json.load(f)
    return ledger.get("balances", {})

def create_payout_transaction(
    utxos: list, 
    payouts: dict, 
    pool_address: str,
    priv_key: bytes,
    pub_key: bytes,
    fee_per_vb: float = 1.0
) -> Transaction:
    """Build and sign payout transaction"""
    
    # Filter payouts above minimum threshold
    eligible = {addr: sats for addr, sats in payouts.items() 
                if sats >= MIN_PAYOUT_SATS and addr != pool_address}
    
    if not eligible:
        raise ValueError("No payouts above minimum threshold")
    
    total_payout = sum(eligible.values())
    
    # Select UTXOs (simple: use all mature ones)
    inputs = [(bytes.fromhex(u["txid"]), u["vout"]) for u in utxos]
    total_input = sum(u["value"] for u in utxos)
    
    if total_input < total_payout:
        raise ValueError(f"Insufficient funds: {total_input} < {total_payout}")
    
    # Build outputs
    outputs = [(sats, addr) for addr, sats in eligible.items()]
    
    # Calculate fee (estimate: 10 bytes per input + 34 per output + 10 overhead)
    estimated_size = len(inputs) * 180 + len(outputs) * 34 + 10
    fee_sats = int(estimated_size * fee_per_vb)
    
    # Change output (back to pool)
    change = total_input - total_payout - fee_sats
    if change > 1000:  # Dust threshold
        outputs.append((change, pool_address))
    
    # Build transaction
    tx = make_transfer(inputs, outputs, locktime=0, rbf=False, 
                       message=f"Pool payout to {len(eligible)} miners")
    
    # Sign all inputs
    for i, txin in enumerate(tx.inputs):
        sig = sign(priv_key, tx.sighash())
        txin.script_sig = sig + pub_key
    
    return tx

def main():
    if not POOL_PRIVATE_KEY or not POOL_PUBLIC_KEY:
        print("Error: POOL_PRIVATE_KEY and POOL_PUBLIC_KEY required")
        sys.exit(1)
    
    priv_key = bytes.fromhex(POOL_PRIVATE_KEY)
    pub_key = bytes.fromhex(POOL_PUBLIC_KEY)
    pool_address = pub_to_address(pub_key)
    
    # Get current blockchain height
    stats = http_get("/stats")
    current_height = stats["height"]
    print(f"[payout] Current height: {current_height}")
    
    # Check if it's payout time
    if current_height % PAYOUT_FREQUENCY_BLOCKS != 0:
        print(f"[payout] Not payout height (every {PAYOUT_FREQUENCY_BLOCKS} blocks)")
        sys.exit(0)
    
    # Load balances from ledger
    balances = load_ledger_balances()
    print(f"[payout] Loaded {len(balances)} miner balances")
    
    # Get mature UTXOs
    utxos = get_mature_utxos(pool_address, current_height)
    if not utxos:
        print("[payout] No mature UTXOs available")
        sys.exit(1)
    
    # Create payout transaction
    try:
        tx = create_payout_transaction(utxos, balances, pool_address, 
                                        priv_key, pub_key)
        print(f"[payout] Transaction built: {len(tx.inputs)} inputs, {len(tx.outputs)} outputs")
        
        # Submit to node
        result = http_post("/transactions/submit", {"tx": tx.to_hex()})
        print(f"[payout] Transaction submitted: {result}")
        
        # Update ledger (deduct paid balances)
        # TODO: Implement ledger update logic
        
    except Exception as e:
        print(f"[payout] Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
```

### Payout Automation

**Option 1: Cron Job** (Traditional)
```bash
# Every 1000 blocks ≈ every 3.69 * 1000 ≈ 3690 seconds ≈ 1 hour
# Run every hour, script checks if it's payout height
0 * * * * cd /app && python pool_payout.py >> /data/payout.log 2>&1
```

**Option 2: Railway Cron** (Cloud Native)
```yaml
# railway.toml
[deploy]
  startCommand = "uvicorn pool_server:app --host 0.0.0.0 --port $PORT"
  
[deploy.cron]
  schedule = "0 * * * *"  # Every hour
  command = "python pool_payout.py"
```

**Option 3: Webhook Trigger** (Event-Driven)
```python
# Add to pool_server.py credit_block()
def credit_block(self, reward_sats: int, height: int) -> dict:
    # ... existing code ...
    self.total_blocks += 1
    
    # Trigger payout if reached frequency
    if height % PAYOUT_FREQUENCY_BLOCKS == 0:
        threading.Thread(target=run_payout, daemon=True).start()
    
    # ... rest of code ...
```

### Security: Pool Private Key Management

**⚠️ CRITICAL**: Pool private key has access to ALL mined funds!

**Best Practices**:
1. **Generate Dedicated Pool Key**:
   ```bash
   python -c "from crypto import new_keypair; import sys; 
   priv, pub = new_keypair(); 
   print(f'PRIV: {priv.hex()}\nPUB: {pub.hex()}')"
   ```

2. **Store Securely**:
   - Railway: Use environment variables (encrypted at rest)
   - Never commit to Git
   - Use separate key from operator's personal wallet

3. **Limit Exposure**:
   - Keep only minimum balance in pool address
   - Transfer excess to cold storage weekly
   - Use multisig for large pools (future enhancement)

4. **Monitor Transactions**:
   - Alert on any transaction NOT from payout script
   - Log all payout transactions to separate audit log

---

## Monitoring & Maintenance

### Key Metrics to Monitor

**Pool Health**:
```bash
# Check every 5 minutes
curl https://your-pool.railway.app/ | jq '{
  node_reachable,
  node_tip_height,
  blocks_found,
  shares_accepted,
  workers
}'
```

**Persistence Health**:
```bash
# Check hourly
curl https://your-pool.railway.app/pool/ledger | jq '{
  on_volume,
  primary_modified_age_s: .primary.modified_age_s,
  saves_done
}'

# Alert if:
# - on_volume == false
# - primary_modified_age_s > 3600
```

**Worker Activity**:
```bash
# Check for dead/stuck miners
curl https://your-pool.railway.app/pool/stats | jq '.leaderboard[] | 
  select(.last_share_age > 3600) | {worker, last_share_age}'
```

### Alerts to Configure

**Critical**:
- 🚨 Node unreachable for >5 minutes
- 🚨 Ledger save failed
- 🚨 Volume not mounted (`on_volume: false`)
- 🚨 Payout transaction failed

**Warning**:
- ⚠️ No blocks found in 24 hours (network hashrate too high)
- ⚠️ Gist sync failed 3 times in a row
- ⚠️ Worker submitted shares above pool target (indicates difficulty issue)

**Info**:
- ℹ️ Block found (celebrate! 🎉)
- ℹ️ New worker joined
- ℹ️ Payout transaction sent

### Log Analysis

```bash
# Railway logs
railway logs --tail 100

# Common log patterns:
# ✅ [pool] ledger SAVED #142: path=/data/ledger.json bytes=12345
# ✅ [GIST] Cloud Sync Successful (Length: 12345 bytes)
# ✅ [share] worker=ori1qagnt... hash=0x597c45... is_block=true
# ❌ [pool] !!! LEDGER SAVE FAILED: [Errno 28] No space left on device
# ❌ [pool] NODE UNREACHABLE (http://...): Connection refused
```

### Backup & Recovery Procedures

**Daily Backup**:
```bash
#!/bin/bash
# backup-pool-ledger.sh
DATE=$(date +%Y%m%d-%H%M%S)
curl https://your-pool.railway.app/pool/ledger > "backups/ledger-$DATE.json"
```

**Disaster Recovery**:
1. **Scenario**: Railway volume corrupted, Gist unavailable
2. **Recovery**:
   ```bash
   # Restore from manual backup
   scp backups/ledger-20260828.json pool-server:/data/ledger.json
   
   # Or set emergency seed variable
   railway variables set POOL_LEDGER_SEED='{"ori1q...": 50000000}'
   railway up
   
   # ⚠️ REMOVE seed variable after recovery!
   railway variables delete POOL_LEDGER_SEED
   ```

---

## Security Checklist

### Before Launch

- [ ] ✅ Persistent volume configured (`/data` mounted)
- [ ] ✅ Gist token configured and tested
- [ ] ✅ Node API token secured (not in public repo)
- [ ] ✅ Pool private key generated and backed up securely
- [ ] ✅ Minimum payout threshold set (>= 1 ORI)
- [ ] ✅ Share rate limiting enabled (`SHARE_RATE_LIMIT_SEC=0.5`)
- [ ] ✅ Fee percentage reasonable (1-3%)
- [ ] ✅ PPLNS window size appropriate (5000-20000 shares)

### Operational Security

- [ ] ✅ Monitor ledger saves (no failures in logs)
- [ ] ✅ Verify Gist backups syncing (check Gist page)
- [ ] ✅ Test payout transaction on testnet first
- [ ] ✅ Review payout destinations before broadcasting
- [ ] ✅ Keep pool hot wallet balance minimal (transfer excess to cold storage)
- [ ] ✅ Alert configured for critical failures
- [ ] ✅ Access logs reviewed weekly for suspicious activity

### Network Security

- [ ] ✅ Rate limiting enabled on pool endpoints
- [ ] ✅ DDoS protection (Railway provides some, consider Cloudflare)
- [ ] ✅ No debug endpoints exposed in production
- [ ] ✅ HTTPS enforced (Railway provides free SSL)
- [ ] ✅ API tokens rotated quarterly

---

## Troubleshooting

### Pool Not Finding Blocks

**Symptoms**: Shares accepted, but `blocks_found: 0` for days

**Diagnosis**:
```bash
# Check current network difficulty
curl https://your-pool.railway.app/ | jq '.node_tip_height'
curl http://your-node:8000/stats | jq '.difficulty'

# Compare pool hashrate to network hashrate
curl https://your-pool.railway.app/pool/stats | jq '.estimated_hashrate'
```

**Possible Causes**:
1. Network hashrate too high (pool hashrate < 1% of network)
   - **Solution**: Attract more miners, or join larger pool
2. Miners submitting shares but not checking node target
   - **Solution**: Verify miner checks `node_target` not just `pool_target`
3. Block submission failing (node rejects)
   - **Solution**: Check node logs for rejection reason

### Ledger Not Persisting

**Symptoms**: After redeploy, `blocks_found: 0`, balances empty

**Diagnosis**:
```bash
curl https://your-pool.railway.app/pool/ledger | jq '{
  on_volume,
  primary,
  backup,
  saves_done
}'
```

**Possible Causes**:
1. Volume not mounted (`on_volume: false`)
   - **Solution**: Configure Railway volume, redeploy
2. Permission denied (`EACCES`)
   - **Solution**: Check POOL_DATA_DIR permissions
3. Disk full (`ENOSPC`)
   - **Solution**: Increase volume size or clean old logs

**Emergency Recovery**:
```bash
# Check Gist for backup
curl -H "Authorization: Bearer $POOL_GIST_TOKEN" \
  https://api.github.com/gists | jq '.[] | 
  select(.description == "ORI Pool Ledger State")'

# Download latest
curl https://gist.githubusercontent.com/.../ledger.json > recovery.json

# Restore to pool
railway run "cat > /data/ledger.json" < recovery.json
railway restart
```

### Miners Not Getting Vardiff Adjusted

**Symptoms**: All miners stuck at same difficulty (shift=12)

**Diagnosis**:
```bash
curl https://your-pool.railway.app/pool/stats | jq '.leaderboard[] | 
  {worker, shift, shares: .total_shares, last_share_age}'
```

**Possible Causes**:
1. Miners not polling frequently enough
   - **Solution**: Verify miner polls every <45 seconds
2. Rate limiting too aggressive
   - **Solution**: Increase `SHARE_RATE_LIMIT_SEC` or `SHARE_SLOW_SEC`
3. Difficulty adjustment logic broken
   - **Solution**: Check pool logs for errors in `add_share()`

### Payout Transaction Rejected

**Symptoms**: Payout script runs but transaction fails

**Diagnosis**:
```bash
# Check payout log
railway logs | grep "[payout]"

# Common errors:
# - "spends nonexistent utxo" → Used immature coinbase
# - "coinbase output not mature" → Maturity check bypassed
# - "outputs exceed inputs" → Fee calculation wrong
# - "invalid signature" → Wrong private key
```

**Solutions**:
1. Verify maturity check: `utxo["height"] + 2000 <= current_height`
2. Test on private testnet with reduced maturity (100 blocks)
3. Dry-run mode: build TX but don't broadcast

---

## Advanced Configuration

### Multi-Pool Proxy (Load Balancing)

Use `pool_proxy.py` to distribute miners across multiple pool backends:

```bash
# Start proxy on port 9999
python pool_proxy.py \
  --pools "http://pool1:9000,http://pool2:9000,http://pool3:9000" \
  --port 9999

# Miners connect to proxy
miner-ori.exe --pool http://proxy:9999 --worker ori1q...
```

### Geographic Distribution

Deploy pools in multiple regions:
- **Americas**: Railway US West
- **Europe**: Railway EU West
- **Asia**: Railway Asia Pacific

Use GeoDNS to route miners to nearest pool.

### Monitoring Dashboard

Integrate with Grafana:
```python
# Add to pool_server.py
@app.get("/metrics")
def prometheus_metrics():
    return f"""
# HELP ori_pool_blocks_found Total blocks found
# TYPE ori_pool_blocks_found counter
ori_pool_blocks_found {LEDGER.total_blocks}

# HELP ori_pool_shares_accepted Total shares accepted
# TYPE ori_pool_shares_accepted counter
ori_pool_shares_accepted {LEDGER.total_shares}

# HELP ori_pool_workers Active worker count
# TYPE ori_pool_workers gauge
ori_pool_workers {len(LEDGER.workers)}
"""
```

---

## Conclusion

This guide covers everything needed to deploy and operate a secure ORI mining pool. Key takeaways:

✅ **Always use persistent volumes** - Ledger data is irreplaceable  
✅ **Enable Gist cloud backup** - Railway volumes can fail  
✅ **Test payouts on testnet first** - One wrong TX can lose all funds  
✅ **Monitor continuously** - Set up alerts before going live  
✅ **Keep pool key secure** - Use dedicated key, not personal wallet  

**Ready to Launch?**
1. Complete Security Checklist
2. Deploy to Railway with persistent volume
3. Test with 1-2 miners for 24 hours
4. Verify ledger persistence through simulated redeploy
5. Announce pool to community 🚀

**Need Help?**
- GitHub Issues: https://github.com/mrnobody-dev/ORI/issues
- Community Discord: [your-discord-link]
- Pool Operator Forum: [your-forum-link]

---

**Document Version**: 1.0  
**Last Updated**: August 28, 2026  
**Author**: Security Audit Team
