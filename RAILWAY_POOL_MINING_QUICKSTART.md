# Railway Pool Setup & Mining dengan miner-ori.exe
## Panduan Lengkap untuk Pool Operator & Miner

---

## 📋 Table of Contents

1. [Setup Pool Server di Railway](#setup-pool-server-di-railway)
2. [Konfigurasi Environment Variables](#konfigurasi-environment-variables)
3. [Setup Persistent Volume (PENTING!)](#setup-persistent-volume)
4. [Deploy & Verify](#deploy--verify)
5. [Mining dengan miner-ori.exe](#mining-dengan-miner-oriexe)
6. [Monitoring & Maintenance](#monitoring--maintenance)
7. [Troubleshooting](#troubleshooting)

---

## 🚀 Setup Pool Server di Railway

### Step 1: Persiapan

**Yang Dibutuhkan**:
- ✅ Akun Railway (https://railway.app)
- ✅ GitHub account (untuk Gist backup)
- ✅ ORI node yang sudah running (bisa di Railway atau local)
- ✅ Pool wallet address (ori1q...)

**Install Railway CLI** (opsional, bisa pakai web dashboard):
```bash
npm install -g @railway/cli
railway login
```

---

### Step 2: Create New Railway Project

**Via Web Dashboard**:
1. Login ke https://railway.app
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Pilih repository: `mrnobody-dev/ORI`
4. Railway akan auto-detect dan deploy

**Via CLI**:
```bash
cd D:\coding\BlockchainPython\blockchain-fastapi
railway init
railway up
```

---

### Step 3: Konfigurasi Environment Variables

Masuk ke **Railway Dashboard** → Project → **Variables** tab.

#### 🔴 CRITICAL Variables (WAJIB)

```bash
# Node Connection
POOL_NODE_URL=http://your-node-domain:8000
# Contoh: http://sakura.proxy.rlwy.net:24044
# atau: http://127.0.0.1:8000 (jika node di project yang sama)

BTPY_API_TOKEN=GniTTY_J_Pe8lKpgCkT-HXJZBpPn4xMG00ui_ht2T6k
# Token dari node ORI (sama dengan BTPY_API_TOKEN di node)

# Pool Identity
POOL_ADDRESS=ori1q2lpx737545zshpqfcyn35gyex03fljwxfucmt6
# Address yang akan menerima mining rewards

POOL_FEE_PCT=1.2
# Fee pool dalam persen (1.2 = 1.2% dari reward)

# Data Persistence (CRITICAL!)
POOL_DATA_DIR=/data
# HARUS /data jika pakai Railway persistent volume

# Cloud Backup
POOL_GIST_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# GitHub Personal Access Token untuk backup cloud
# Buat di: https://github.com/settings/tokens
# Scope: gist (read/write)
```

#### ⚙️ PPLNS Configuration

```bash
PPLNS_POINTS=10000
# Window size: last 10,000 shares
# Lebih besar = lebih fair tapi payout lebih lama

POOL_DIFF_SHIFT=12
# Starting difficulty: node_target << 12
# Lebih tinggi = lebih mudah (untuk miner lemah)
# Lebih rendah = lebih susah (untuk miner kuat)

POOL_MIN_SHIFT=4
# Minimum difficulty (paling susah)

POOL_MAX_SHIFT=24
# Maximum difficulty (paling mudah)

SHARE_FAST_SEC=5
# Jika share datang < 5 detik → difficulty naik (harder)

SHARE_SLOW_SEC=45
# Jika share datang > 45 detik → difficulty turun (easier)

SHARE_RATE_LIMIT_SEC=0.5
# Anti-spam: max 1 share per 0.5 detik per worker
```

#### 📊 Logging

```bash
ORI_LOG_LEVEL=INFO
# DEBUG untuk troubleshooting, INFO untuk production

ORI_LOG_CONSOLE=1
# Log ke console (Railway logs)

ORI_LOG_FILE=0
# Jangan write file log (pakai Railway logs saja)
```

#### 🔧 Advanced (Opsional)

```bash
# Fee Address (jika mau fee dikirim ke address terpisah)
POOL_FEE_ADDRESS=
# Kosongkan = fee masuk ke POOL_ADDRESS

# Payout Frequency
PAYOUT_FREQUENCY_BLOCKS=1000
# Payout setiap 1000 blocks (default)

# Minimum Payout
MIN_PAYOUT_SATS=100000000
# Minimum 1 ORI untuk payout (100 juta satoshis)
```

---

### Step 4: Setup Persistent Volume (🚨 SANGAT PENTING!)

**Tanpa persistent volume, semua data miner akan HILANG saat redeploy!**

#### Via Railway Web Dashboard:

1. Masuk ke **Railway Dashboard** → Your Project
2. Click service pool → **Settings** tab
3. Scroll ke **Volumes** section
4. Click **"+ New Volume"**
5. Configure:
   ```
   Mount Path: /data
   Size: 1 GB (cukup untuk ledger)
   ```
6. Click **"Add"**
7. **IMPORTANT**: Railway akan restart service otomatis

#### Verify Volume Mounted:

Check di Railway **Logs** tab, cari:
```
[pool] ledger restored from /data/ledger.json: balances=X window=Y blocks=Z
```

Jika muncul `/data/ledger.json` berarti volume sudah mounted ✅

---

### Step 5: Configure Start Command

Railway biasanya auto-detect, tapi kalau perlu manual:

**Railway Dashboard** → Service → **Settings** → **Deploy** section:

```bash
# Start Command:
uvicorn pool_server:app --host 0.0.0.0 --port $PORT

# Build Command (jika perlu):
pip install -r requirements.txt
```

---

### Step 6: Deploy & Verify

#### Auto Deploy:
- Railway akan auto-deploy setiap git push ke `main` branch
- Check **Deployments** tab untuk status

#### Manual Deploy (via CLI):
```bash
railway up
```

#### Verify Deployment:

**1. Check Logs:**
```bash
railway logs
```

Cari output:
```
[pool] ledger restored from /data/ledger.json
[pool] node template OK height=5042 reward=612073980
INFO:     Uvicorn running on http://0.0.0.0:XXXX
```

**2. Get Public URL:**
```bash
railway domain
```

Output contoh:
```
ori-pool-production.up.railway.app
```

**3. Test Pool API:**
```bash
curl https://ori-pool-production.up.railway.app/

# Response:
{
  "name": "ORI PPLNS Pool (pool_server.py)",
  "node": "http://sakura.proxy.rlwy.net:24044",
  "node_reachable": true,
  "node_tip_height": 5042,
  "pool_address": "ori1q2lpx...",
  "fee_pct": 1.2,
  "pplns_points": 10000,
  "blocks_found": 0,
  "shares_accepted": 0,
  "workers": 0
}
```

✅ Jika `"node_reachable": true` berarti pool siap!

---

## ⛏️ Mining dengan miner-ori.exe

### Cara 1: Mining via HTTP API (Current)

Pool sekarang pakai HTTP API (bukan stratum), jadi miner harus support format ini.

#### Download miner-ori.exe

Jika belum ada, compile dari source:
```bash
cd D:\coding\BlockchainPython\blockchain-fastapi
# Sudah ada miner-ori.exe di folder
```

#### Run Miner (Windows):

**Command Line:**
```cmd
miner-ori.exe --pool https://ori-pool-production.up.railway.app --worker ori1qYOUR_MINER_ADDRESS --threads 4

REM Atau dengan config lengkap:
miner-ori.exe ^
  --pool https://ori-pool-production.up.railway.app ^
  --worker ori1qagntpw3px43nd309thfxds8f8tyrdhunepk2zj ^
  --threads 8 ^
  --log-level INFO
```

**Batch File** (CPU miner launcher.bat):
```batch
@echo off
echo ================================================
echo    ORI Mining Pool - CPU Miner Launcher
echo ================================================
echo.

REM Configuration
set POOL_URL=https://ori-pool-production.up.railway.app
set WORKER_ADDRESS=ori1qagntpw3px43nd309thfxds8f8tyrdhunepk2zj
set THREADS=8
set LOG_LEVEL=INFO

echo Pool URL: %POOL_URL%
echo Worker: %WORKER_ADDRESS%
echo Threads: %THREADS%
echo.
echo Starting miner...
echo Press Ctrl+C to stop
echo.

miner-ori.exe --pool %POOL_URL% --worker %WORKER_ADDRESS% --threads %THREADS% --log-level %LOG_LEVEL%

pause
```

Save sebagai `mine_pool.bat` dan double-click untuk start mining.

---

### Cara 2: Mining via TCP Proxy (untuk Stratum Miners)

Jika mau pakai miner standar (cgminer, bfgminer), perlu setup TCP proxy.

#### Enable TCP Proxy di Railway:

1. **Railway Dashboard** → Service → **Settings**
2. Scroll ke **Networking** section
3. Enable **TCP Proxy**
4. Note the proxy address, contoh:
   ```
   tokaido.proxy.rlwy.net:49718
   ```

5. Pool internal port: `33333` (untuk stratum)

#### Miner Connect String:

```bash
# Format: proxy_host:proxy_port:internal_port
stratum+tcp://tokaido.proxy.rlwy.net:49718:33333
```

⚠️ **NOTE**: Current `pool_server.py` hanya support HTTP API, bukan stratum protocol. Untuk stratum, perlu implement `stratum_proxy.py` (belum ada).

---

### Monitoring Mining Activity

#### Check Miner Logs:
Miner akan print:
```
[miner] Connecting to pool: https://ori-pool-production.up.railway.app
[miner] Worker: ori1qagnt...
[miner] Job received: height=5042 difficulty=0.000244 target=0x0000000000001fff...
[miner] Mining... hashrate=2.5 MH/s
[miner] Share found! Submitting...
[miner] Share accepted: shift=12 balance=0.00000000 ORI
```

#### Check Pool Stats:

**Via Browser:**
```
https://ori-pool-production.up.railway.app/pool/stats
```

Akan muncul HTML dashboard dengan:
- Leaderboard (top miners)
- Estimated hashrate
- Blocks found
- Latest payouts

**Via API:**
```bash
curl https://ori-pool-production.up.railway.app/pool/stats

# Response JSON:
{
  "blocks_found": 3,
  "shares_accepted": 1523,
  "workers_count": 5,
  "window_points": 10000,
  "estimated_hashrate": "125.3 MH/s",
  "leaderboard": [
    {
      "worker": "ori1qagnt...",
      "window_shares": 542,
      "total_shares": 1205,
      "balance_sats": 12500000,
      "hashrate_hps": 45230000
    },
    ...
  ]
}
```

---

## 📊 Monitoring & Maintenance

### Health Check Endpoints

#### 1. Pool Info:
```bash
curl https://your-pool.railway.app/
```

Verify:
- ✅ `"node_reachable": true`
- ✅ `"node_tip_height"` is recent
- ✅ `"workers"` > 0 (after miners connect)

#### 2. Ledger Persistence:
```bash
curl https://your-pool.railway.app/pool/ledger
```

Verify:
- ✅ `"on_volume": true` (MUST be true!)
- ✅ `"primary"` file exists
- ✅ `"primary.modified_age_s"` < 3600 (updated recently)
- ✅ `"saves_done"` is increasing

**Red Flags**:
- ❌ `"on_volume": false` → Volume not mounted!
- ❌ `"primary": null` → File missing (check permissions)
- ❌ `modified_age_s > 3600` → Not saving (check logs)

#### 3. Worker Activity:
```bash
curl https://your-pool.railway.app/pool/stats | jq '.leaderboard[0]'
```

Verify:
- ✅ `"last_share_age"` < 60 (miner aktif)
- ✅ `"hashrate_hps"` > 0

---

### Railway Logs Monitoring

```bash
# Via CLI:
railway logs --tail 100

# Via Dashboard:
# Railway → Your Service → Logs tab
```

**Logs yang bagus:**
```
✅ [pool] ledger SAVED #142: path=/data/ledger.json bytes=12345
✅ [GIST] Cloud Sync Successful (Length: 12345 bytes)
✅ [share] worker=ori1qagnt... hash=0x597c45... is_block=false
✅ INFO: 127.0.0.1:XXXXX - "GET /pool/job?worker=ori1q..." 200
```

**Logs yang bahaya:**
```
❌ [pool] !!! LEDGER SAVE FAILED: [Errno 28] No space left on device
❌ [pool] NODE UNREACHABLE (http://...): Connection refused
❌ [GIST] Upload error: 403 Forbidden (rate limit exceeded)
```

---

### Backup & Recovery

#### Manual Backup (Recommended Daily):

```bash
# Download ledger dari Railway:
railway run cat /data/ledger.json > backup-$(date +%Y%m%d).json

# Atau via API:
curl https://your-pool.railway.app/pool/ledger | jq '.totals' > backup.json
```

#### Restore dari Backup:

**Jika Gist masih available:**
1. Railway akan auto-restore dari Gist on startup
2. Check logs: `[pool] LEDGER RECOVERED FROM GIST CLOUD`

**Jika perlu restore manual:**
```bash
# Upload backup ke Railway volume:
railway run "cat > /data/ledger.json" < backup-20260828.json

# Restart service:
railway restart
```

#### Emergency Recovery (Data Loss):

Jika semua backup hilang, gunakan `POOL_LEDGER_SEED`:

```bash
# Set variable ONE TIME ONLY:
railway variables set POOL_LEDGER_SEED='{"ori1qaddr1": 50000000, "ori1qaddr2": 30000000}'

# Railway restart akan seed balances
railway restart

# ⚠️ IMMEDIATELY REMOVE variable setelah recovery:
railway variables delete POOL_LEDGER_SEED
```

---

## 🔧 Troubleshooting

### Problem 1: Pool Tidak Bisa Connect ke Node

**Symptoms:**
```json
{
  "node_reachable": false,
  "node_last_error": "Connection refused"
}
```

**Solutions:**

1. **Check Node URL**:
   ```bash
   # Test node dari local:
   curl http://sakura.proxy.rlwy.net:24044/stats
   
   # Jika timeout, node mungkin down atau firewall block
   ```

2. **Check API Token**:
   ```bash
   curl -H "X-API-Key: YOUR_TOKEN" http://your-node:8000/stats
   
   # Jika 401 Unauthorized, token salah
   ```

3. **Node & Pool di Railway yang sama**:
   Jika node dan pool di same Railway project, gunakan internal URL:
   ```bash
   POOL_NODE_URL=http://node-service:8000
   # Ganti "node-service" dengan service name di Railway
   ```

---

### Problem 2: Ledger Tidak Persist (Data Loss)

**Symptoms:**
- Setelah redeploy, `blocks_found: 0`, balances kosong
- Logs: `[pool] starting with EMPTY ledger`

**Diagnosis:**
```bash
curl https://your-pool.railway.app/pool/ledger | jq '{on_volume, primary, backup}'
```

**Solutions:**

1. **Volume Not Mounted**:
   ```json
   {"on_volume": false}
   ```
   → Go to Railway Dashboard → Settings → Volumes → Add volume `/data`

2. **Permission Denied**:
   Logs: `[Errno 13] Permission denied: '/data/ledger.json'`
   → Railway should auto-fix permissions, try redeploy

3. **Disk Full**:
   Logs: `[Errno 28] No space left on device`
   → Increase volume size in Railway settings

---

### Problem 3: Miner Tidak Dapat Job

**Symptoms:**
```
[miner] ERROR: Failed to get job: 503 Service Unavailable
```

**Solutions:**

1. **Pool belum siap**:
   Check: `curl https://your-pool.railway.app/`
   
   Tunggu sampai `"node_reachable": true`

2. **Worker address invalid**:
   Verify address format: `ori1q...` (bech32)
   
   Test:
   ```bash
   curl "https://your-pool.railway.app/pool/job?worker=ori1qYOUR_ADDRESS"
   ```

3. **Rate limit hit**:
   Miner terlalu cepat polling (< 3.69s)
   
   Check miner `POLL_SECONDS` setting

---

### Problem 4: Share Rejected

**Symptoms:**
```
[miner] Share rejected: above pool target (low difficulty share)
```

**Solutions:**

1. **Difficulty terlalu tinggi**:
   Pool assigned difficulty terlalu susah untuk hashrate miner
   
   Tunggu beberapa submit, vardiff akan auto-adjust

2. **Stale share**:
   ```
   [miner] Share rejected: stale job — request a new one
   ```
   
   → Miner harus poll lebih sering (< 30s)

3. **Duplicate share**:
   ```
   [miner] Share rejected: duplicate share
   ```
   
   → Miner bug, check nonce randomization

---

### Problem 5: Gist Backup Failing

**Symptoms:**
```
[GIST] Upload error: 403 Forbidden
[GIST] Upload error: Rate limit exceeded
```

**Solutions:**

1. **Token expired/invalid**:
   Generate new token di https://github.com/settings/tokens
   
   Update Railway variable:
   ```bash
   railway variables set POOL_GIST_TOKEN=ghp_NEW_TOKEN
   ```

2. **Rate limit**:
   Gist API limit: 5000 requests/hour
   
   Pool sync every 5 saves, jadi max ~1000 saves/hour OK
   
   Jika kena limit, wait 1 jam atau increase sync interval

3. **Network issue**:
   Temporary, akan retry otomatis
   
   Check Railway logs untuk confirm next sync success

---

## 🎯 Performance Tuning

### Optimize Difficulty Settings

**Untuk miner GPU yang kuat:**
```bash
POOL_MIN_SHIFT=2  # Harder minimum
SHARE_FAST_SEC=3  # Adjust faster
```

**Untuk banyak miner CPU lemah:**
```bash
POOL_MAX_SHIFT=28  # Easier maximum
SHARE_SLOW_SEC=60  # Wait longer before easing
```

### Optimize PPLNS Window

**Pool kecil (< 10 workers):**
```bash
PPLNS_POINTS=5000  # Smaller window = faster payout
```

**Pool besar (> 50 workers):**
```bash
PPLNS_POINTS=20000  # Larger window = more fair
```

### Optimize Logging

**Production (reduce log noise):**
```bash
ORI_LOG_LEVEL=WARNING  # Only warnings and errors
```

**Debugging (verbose):**
```bash
ORI_LOG_LEVEL=DEBUG  # Everything
```

---

## 📱 Quick Reference Commands

### Railway CLI Shortcuts:

```bash
# View logs (live tail):
railway logs

# Check environment variables:
railway variables

# Restart service:
railway restart

# Open dashboard:
railway open

# Check status:
railway status

# Run command in container:
railway run <command>
```

### Pool API Shortcuts:

```bash
POOL_URL="https://your-pool.railway.app"

# Pool info:
curl $POOL_URL/

# Get job (as miner):
curl "$POOL_URL/pool/job?worker=ori1qYOUR_ADDRESS"

# Stats dashboard:
curl $POOL_URL/pool/stats

# Ledger health:
curl $POOL_URL/pool/ledger | jq '{on_volume, saves_done, balances}'

# Worker leaderboard:
curl $POOL_URL/pool/stats | jq '.leaderboard[] | {worker, shares: .window_shares, balance_sats}'
```

---

## ✅ Final Checklist

Before going live, verify:

**Pool Configuration:**
- [ ] ✅ `POOL_NODE_URL` correct and reachable
- [ ] ✅ `BTPY_API_TOKEN` valid
- [ ] ✅ `POOL_ADDRESS` correct (you own this address!)
- [ ] ✅ `POOL_GIST_TOKEN` valid and working
- [ ] ✅ `POOL_DATA_DIR=/data`

**Railway Setup:**
- [ ] ✅ Persistent volume mounted to `/data`
- [ ] ✅ Volume size ≥ 1 GB
- [ ] ✅ Service deployed and running
- [ ] ✅ Public domain assigned

**Verification:**
- [ ] ✅ `/` endpoint returns `"node_reachable": true`
- [ ] ✅ `/pool/ledger` returns `"on_volume": true`
- [ ] ✅ Test miner can get job
- [ ] ✅ Test miner can submit share (even if rejected for difficulty)
- [ ] ✅ Gist backup visible di GitHub

**Monitoring:**
- [ ] ✅ Railway logs clean (no errors)
- [ ] ✅ Ledger saving successfully
- [ ] ✅ Gist syncing (check every 5 saves)

---

## 🚀 Ready to Launch!

Jika semua checklist ✅, pool siap untuk production:

1. **Announce Pool**:
   ```
   🎉 ORI Mining Pool LIVE!
   
   Pool URL: https://your-pool.railway.app
   Fee: 1.2%
   PPLNS Window: 10,000 shares
   Min Payout: 1 ORI
   
   Connect your miner:
   miner-ori.exe --pool https://your-pool.railway.app --worker YOUR_ADDRESS
   ```

2. **Monitor First 24 Hours**:
   - Check logs every hour
   - Verify shares being accepted
   - Watch for any errors
   - Confirm Gist backups working

3. **First Payout Test**:
   - Wait for 1000 blocks (or your PAYOUT_FREQUENCY)
   - Run `python pool_payout.py --dry-run` first
   - Verify output correct
   - Run real payout
   - Confirm miners receive ORI

4. **Celebrate!** 🎊

---

## 📞 Support & Resources

- **Full Deployment Guide**: `MINING_POOL_DEPLOYMENT_COMPLETE_GUIDE.md` (150 pages)
- **Security Audit**: `SECURITY_AUDIT_MAINNET_READINESS.md`
- **Payout System**: `pool_payout.py` usage examples
- **Pool Server Code**: `pool_server.py`

**Questions?**
- Check Railway logs first
- Review `/pool/ledger` health endpoint
- Test with dry-run commands
- Join ORI community for support

---

**Guide Version**: 1.0  
**Last Updated**: August 28, 2026  
**Compatibility**: ORI v0.2.4+, Railway Platform

**Happy Mining!** ⛏️💎
