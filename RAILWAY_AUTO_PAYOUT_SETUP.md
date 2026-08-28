# 🤖 Railway Auto-Payout Setup Guide

## Problem
Pool tidak payout otomatis ke miners, harus trigger `pool_payout.py` manual.

## Solution
**Payout Daemon** - Background thread yang run `pool_payout.py` otomatis di Railway!

---

## ✅ Setup (2 Minutes)

### Step 1: Add Railway Variable

**Railway Dashboard → Pool Service → Variables → Add:**

```bash
ENABLE_PAYOUT_DAEMON=true
```

**That's it!** Daemon akan start automatically!

### Step 2: (Optional) Configure Frequency

**Default:** Payout setiap **100 blocks** atau **60 minutes** (mana yang lebih dulu).

**To change:**
```bash
PAYOUT_FREQUENCY_BLOCKS=10     # Payout every 10 blocks (~37 seconds)
PAYOUT_INTERVAL_MINUTES=30     # OR every 30 minutes (fallback)
```

### Step 3: Redeploy

Railway akan auto-pull latest commit and restart (~2 minutes).

**OR manually:**
- Railway → Pool Service → **"Redeploy"**

---

## 🔍 Verify It's Working

### Check Startup Logs

**Railway → Deployments → Latest → Logs:**

```
[pool] ========== AUTO-PAYOUT CONFIG ==========
[pool] ENABLE_AUTO_PAYOUT: True
[pool] POOL_PRIVATE_KEY: SET (64 chars)
[pool] ==========================================
[payout-daemon] 🤖 Started!
[payout-daemon] Interval: 10 blocks OR 60 minutes
[payout-daemon] ✅ Background thread started
```

### Wait for Payout Trigger

**After 10 blocks (~37 seconds):**

```
[payout-daemon] 🔔 Payout trigger: 10 blocks since last
[payout-daemon] ⚡ Executing payout...
[payout-daemon] ✅ Payout SUCCESS!
[payout-daemon] Output: Payout transaction created...
  TxID: abc123def456...
  Paid: ori1qkm2w5... → 567.92 ORI
```

---

## 🎯 How It Works

```
┌──────────────────────────────────────────────────────┐
│ Railway Container                                    │
│                                                      │
│  ┌────────────────┐         ┌─────────────────┐    │
│  │ pool_server.py │ spawns  │ Payout Daemon   │    │
│  │ (main process) │────────→│ (thread)        │    │
│  └────────────────┘         └─────────────────┘    │
│                                      │               │
│                                      │ every N       │
│                                      │ blocks        │
│                                      ↓               │
│                             ┌─────────────────┐     │
│                             │ pool_payout.py  │     │
│                             │ (subprocess)    │     │
│                             └─────────────────┘     │
│                                      │               │
│                                      ↓               │
│                             ┌─────────────────┐     │
│                             │ Blockchain      │     │
│                             │ (broadcasts TX) │     │
│                             └─────────────────┘     │
└──────────────────────────────────────────────────────┘
```

### Components:

1. **pool_server.py** (main process)
   - Runs Uvicorn web server
   - Accepts mining shares
   - Starts payout daemon on startup

2. **pool_payout_daemon.py** (background thread)
   - Checks height every 30 seconds
   - Triggers payout when interval reached
   - Calls pool_payout.py subprocess

3. **pool_payout.py** (subprocess)
   - Reads ledger
   - Builds payout transaction
   - Signs & broadcasts
   - Updates ledger

---

## 📊 Configuration Options

### Required Variables:
```bash
ENABLE_PAYOUT_DAEMON=true      # Enable daemon
POOL_PRIVATE_KEY=xxx...        # Pool wallet private key
POOL_ADDRESS=ori1q...          # Pool address
```

### Optional Variables:
```bash
PAYOUT_FREQUENCY_BLOCKS=10     # Blocks between payouts (default: 100)
PAYOUT_INTERVAL_MINUTES=60     # Time-based fallback (default: 60)
MIN_PAYOUT_SATS=50000000       # Min balance to payout (default: 1 ORI)
```

### Example Config (Fast Payouts):
```bash
ENABLE_PAYOUT_DAEMON=true
PAYOUT_FREQUENCY_BLOCKS=10     # ~37 seconds
MIN_PAYOUT_SATS=10000000       # 0.1 ORI minimum
```

### Example Config (Batched Payouts):
```bash
ENABLE_PAYOUT_DAEMON=true
PAYOUT_FREQUENCY_BLOCKS=1000   # ~1 hour
MIN_PAYOUT_SATS=100000000      # 1 ORI minimum
```

---

## 🎊 Benefits

### vs Manual Execution:
- ❌ **Before:** Must SSH to Railway and run `python pool_payout.py` manually
- ✅ **After:** Fully automatic, runs in background

### vs Inline Auto-Payout:
- ❌ **Inline:** Auto-payout in pool_server.py (complex, may have bugs)
- ✅ **Daemon:** Uses proven `pool_payout.py` script (already tested)

### vs Railway Cron:
- ❌ **Cron:** Not available in Railway free tier
- ✅ **Daemon:** Built-in, no external services needed

---

## 🔧 Troubleshooting

### Problem: Daemon not starting

**Check logs for:**
```
[payout-daemon] DISABLED (set ENABLE_PAYOUT_DAEMON=true to enable)
```

**Solution:** Add `ENABLE_PAYOUT_DAEMON=true` to Railway variables.

---

### Problem: No private key

**Check logs for:**
```
[payout-daemon] ⚠️  Cannot start: POOL_PRIVATE_KEY not set!
```

**Solution:** Add `POOL_PRIVATE_KEY=xxx` to Railway variables.

---

### Problem: Payout fails

**Check logs for:**
```
[payout-daemon] ❌ Payout FAILED (exit 1)
[payout-daemon] Error: No mature coinbase UTXOs
```

**Possible causes:**
1. No mature coinbase (need 2000+ blocks old)
2. Insufficient balance
3. Node unreachable

**Solution:** Check pool address balance and UTXO maturity.

---

### Problem: Payout timeout

**Check logs for:**
```
[payout-daemon] ⏱️ Payout TIMEOUT (>120s)
```

**Solution:** Node might be slow. Daemon will retry on next interval.

---

## 🚀 Current Status

### Your Setup:
```bash
ENABLE_PAYOUT_DAEMON=true           # ✅ Ready to enable!
PAYOUT_FREQUENCY_BLOCKS=10          # ✅ Already set (37s intervals)
POOL_PRIVATE_KEY=15b6dc46...        # ✅ Already set
POOL_ADDRESS=ori1qrhdg9a0...        # ✅ Already set
```

### Pending Balance:
```
5,679.25083540 ORI
567,925,083,540 sats
942 blocks found
```

### Action Required:
1. **Add `ENABLE_PAYOUT_DAEMON=true` to Railway**
2. **Redeploy** (Railway will auto-pull latest code)
3. **Check logs** for `[payout-daemon] 🤖 Started!`
4. **Wait ~37 seconds** (10 blocks)
5. **CHECK YOUR WALLET** → 5,679 ORI incoming! 💰

---

## ✅ Complete Variable List (With Daemon)

```bash
# Node Connection
POOL_NODE_URL=http://hopper.proxy.rlwy.net:34657
BTPY_API_TOKEN=c30e7e82ac51

# Pool Identity
POOL_ADDRESS=ori1qrhdg9a0vswnlqn4p5mdl9cq0wrt5k47wettr60
POOL_PRIVATE_KEY=15b6dc462156b8520d5ba77238cf5d0f675a42c8518a03e2364fc45b31e5130d

# Data & Backup
POOL_DATA_DIR=/data
POOL_GIST_TOKEN=ghp_xxxYourGistTokenHerexxx

# Pool Economics
POOL_FEE_PCT=1.5
PPLNS_POINTS=5000
MIN_PAYOUT_SATS=50000000

# Difficulty
POOL_DIFF_SHIFT=0
POOL_MIN_SHIFT=0
POOL_MAX_SHIFT=1
SHARE_FAST_SEC=1
SHARE_SLOW_SEC=2
SHARE_RATE_LIMIT_SEC=0.1

# Logging
ORI_LOG_LEVEL=INFO
ORI_LOG_CONSOLE=1
ORI_LOG_FILE=0

# Auto-Payout (Inline - Backup)
ENABLE_AUTO_PAYOUT=true
PAYOUT_FREQUENCY_BLOCKS=10
AUTO_PAYOUT_DRY_RUN=false

# Payout Daemon (NEW!)
ENABLE_PAYOUT_DAEMON=true          # ← ADD THIS!
PAYOUT_INTERVAL_MINUTES=60
```

**Total: 24 variables**

---

## 🎉 Ready to Deploy!

1. **Add `ENABLE_PAYOUT_DAEMON=true`** to Railway
2. Railway auto-redeploy
3. **PROFIT!** 💰

No more manual payouts! Fully automatic! 🤖✅
