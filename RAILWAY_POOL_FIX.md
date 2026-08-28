# 🔧 Fix: Railway Pool "Application failed to respond"

## Problem
URL: `https://ori-production-8364.up.railway.app/pool/stats`  
Error: **"Application failed to respond"**

## Root Cause
Railway is deploying `main.py` (node server) instead of `pool_server.py` (pool server) because the default `Dockerfile` is configured for the node, not the pool.

---

## ✅ Solution: Deploy Pool Server

### Option 1: Create New Railway Service (RECOMMENDED)

**Deploy pool as a SEPARATE service from node:**

#### Step 1: Create New Service in Railway

1. Go to Railway Dashboard: https://railway.app
2. Open your existing project (or create new)
3. Click **"+ New"** → **"Empty Service"**
4. Name it: **"ORI-Pool"**

#### Step 2: Configure Dockerfile Path

**Railway Dashboard → ORI-Pool Service → Settings:**

Scroll to **"Build"** section:
```
Root Directory: .
Dockerfile Path: Dockerfile.pool
```

Save changes.

#### Step 3: Set Environment Variables

**Railway Dashboard → ORI-Pool Service → Variables:**

Add these variables:
```bash
# Node connection (REQUIRED)
POOL_NODE_URL=http://sakura.proxy.rlwy.net:24044
BTPY_API_TOKEN=GniTTY_J_Pe8lKpgCkT-HXJZBpPn4xMG00ui_ht2T6k

# Pool identity (REQUIRED)
POOL_ADDRESS=ori1q2lpx737545zshpqfcyn35gyex03fljwxfucmt6
POOL_FEE_PCT=1.2

# Data persistence (REQUIRED)
POOL_DATA_DIR=/data

# Cloud backup (REQUIRED)
POOL_GIST_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# PPLNS config (OPTIONAL)
PPLNS_POINTS=10000
POOL_DIFF_SHIFT=12
SHARE_RATE_LIMIT_SEC=0.5

# Logging (OPTIONAL)
ORI_LOG_LEVEL=INFO
ORI_LOG_CONSOLE=1
ORI_LOG_FILE=0
```

**IMPORTANT**: Get GitHub token from https://github.com/settings/tokens (scope: gist)

#### Step 4: Add Persistent Volume

**Railway Dashboard → ORI-Pool Service → Settings → Volumes:**

Click **"+ New Volume"**:
```
Mount Path: /data
Size: 1 GB
```

Click **"Add"**.

Railway will restart service automatically.

#### Step 5: Deploy from GitHub

**Railway Dashboard → ORI-Pool Service → Settings → Deploy:**

Connect GitHub repository:
```
Repository: mrnobody-dev/ORI
Branch: main
```

Enable **"Auto-deploy on push"**.

Click **"Deploy Now"**.

#### Step 6: Generate Public URL

**Railway Dashboard → ORI-Pool Service → Settings → Networking:**

Click **"Generate Domain"**.

You'll get a URL like:
```
https://ori-pool-production-abcd.up.railway.app
```

#### Step 7: Verify Deployment

**Check Logs:**
```bash
railway logs --service ori-pool
```

Should see:
```
INFO:     Started server process
INFO:     Waiting for application startup.
[pool] ledger restored from /data/ledger.json: balances=0 window=0 blocks=0
[pool] node template OK height=5042 reward=612073980
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:XXXX
```

**Test Pool:**
```bash
curl https://ori-pool-production-abcd.up.railway.app/

# Expected response:
{
  "name": "ORI PPLNS Pool (pool_server.py)",
  "node": "http://sakura.proxy.rlwy.net:24044",
  "node_reachable": true,
  "node_tip_height": 5042,
  "pool_address": "ori1q2lpx...",
  "blocks_found": 0,
  "workers": 0
}
```

✅ If `"node_reachable": true`, pool is ready!

---

### Option 2: Modify Existing Service

**If you want to convert existing service to pool:**

#### Step 1: Change Dockerfile Path

**Railway Dashboard → Service Settings → Build:**
```
Dockerfile Path: Dockerfile.pool
```

#### Step 2: Update Environment Variables

Add all pool variables from Option 1 Step 3.

#### Step 3: Add Persistent Volume

Follow Option 1 Step 4.

#### Step 4: Redeploy

Railway will automatically redeploy with new Dockerfile.

**⚠️ WARNING**: This will STOP running the node server. Use Option 1 (separate service) if you want both node AND pool running.

---

### Option 3: Manual Deploy via Railway CLI

#### Install Railway CLI:
```bash
npm install -g @railway/cli
railway login
```

#### Deploy Pool:
```bash
cd D:\coding\BlockchainPython\blockchain-fastapi

# Link to Railway project:
railway link

# Set environment variables:
railway variables set POOL_NODE_URL=http://sakura.proxy.rlwy.net:24044
railway variables set BTPY_API_TOKEN=GniTTY_J_Pe8lKpgCkT-HXJZBpPn4xMG00ui_ht2T6k
railway variables set POOL_ADDRESS=ori1q2lpx737545zshpqfcyn35gyex03fljwxfucmt6
railway variables set POOL_FEE_PCT=1.2
railway variables set POOL_DATA_DIR=/data
railway variables set POOL_GIST_TOKEN=ghp_xxxx

# Deploy with custom Dockerfile:
railway up --dockerfile Dockerfile.pool

# Check logs:
railway logs

# Get domain:
railway domain
```

---

## 📊 Verify Pool is Working

### 1. Test Root Endpoint:
```bash
curl https://your-pool-url.railway.app/

# Must return:
{
  "node_reachable": true  ← IMPORTANT!
}
```

### 2. Test Job Endpoint:
```bash
curl "https://your-pool-url.railway.app/pool/job?worker=ori1q2lpx737545zshpqfcyn35gyex03fljwxfucmt6"

# Must return job with:
{
  "job_id": "5042-1",
  "height": 5042,
  "reward_sats": 612073980,
  "bits": 503382015,
  "pool_target": "0x0000000000001fff...",
  ...
}
```

### 3. Test Stats Endpoint:
```bash
curl https://your-pool-url.railway.app/pool/stats

# Must return HTML dashboard
```

### 4. Check Ledger Persistence:
```bash
curl https://your-pool-url.railway.app/pool/ledger | jq '{on_volume, primary, saves_done}'

# Must return:
{
  "on_volume": true,  ← CRITICAL!
  "primary": "/data/ledger.json",
  "saves_done": 1
}
```

---

## 🔍 Troubleshooting

### Error: "node_reachable": false

**Check logs:**
```bash
railway logs --service ori-pool | grep "NODE UNREACHABLE"
```

**Possible causes:**
1. **Wrong POOL_NODE_URL**
   - Test node directly: `curl http://sakura.proxy.rlwy.net:24044/stats`
   - If timeout, node is down or URL wrong

2. **Wrong BTPY_API_TOKEN**
   - Test with token: `curl -H "X-API-Key: YOUR_TOKEN" http://node-url/stats`
   - If 401 Unauthorized, token is wrong

3. **Node & Pool in same Railway project**
   - Use internal URL: `POOL_NODE_URL=http://node-service-name:8000`
   - Replace `node-service-name` with actual service name

**Solution:**
```bash
# Get correct node URL from node service:
railway logs --service node | grep "Uvicorn running"

# Update pool variable:
railway variables set POOL_NODE_URL=http://correct-url:8000 --service ori-pool
```

---

### Error: "on_volume": false

**Ledger will NOT persist on redeploy!**

**Check volume:**
```bash
railway volumes --service ori-pool
```

**If no volume, add it:**

Railway Dashboard → ORI-Pool Service → Settings → Volumes → **+ New Volume**
```
Mount Path: /data
Size: 1 GB
```

Railway will restart service automatically.

**Verify after restart:**
```bash
curl https://your-pool-url.railway.app/pool/ledger | jq .on_volume
# Must return: true
```

---

### Error: Port Already in Use

**Logs show:**
```
OSError: [Errno 98] Address already in use
```

**Cause:** Railway's `$PORT` variable conflict.

**Solution:** Railway automatically sets `$PORT`, pool server will use it.

Check start command uses `$PORT`:
```bash
python -m uvicorn pool_server:app --host 0.0.0.0 --port $PORT
```

---

### Error: Module Not Found (merkle, pow, etc.)

**Logs show:**
```
ModuleNotFoundError: No module named 'merkle'
```

**Cause:** `Dockerfile.pool` not copying source files correctly.

**Verify Dockerfile.pool:**
```dockerfile
# MUST have this line:
COPY . .
```

This copies ALL Python files (merkle.py, pow.py, tx.py, etc.) to container.

**Redeploy:**
```bash
railway up --dockerfile Dockerfile.pool
```

---

### Error: POOL_ADDRESS Required

**Logs show:**
```
RuntimeError: POOL_ADDRESS env is required
```

**Solution:**
```bash
railway variables set POOL_ADDRESS=ori1q2lpx737545zshpqfcyn35gyex03fljwxfucmt6 --service ori-pool
```

Railway will auto-redeploy.

---

## 📦 Files Created for Pool Deployment

I've created these files to help you deploy:

1. **`Dockerfile.pool`** - Docker config for pool server
2. **`railway.pool.toml`** - Railway config (optional)
3. **`RAILWAY_POOL_FIX.md`** - This troubleshooting guide
4. **`RAILWAY_POOL_MINING_QUICKSTART.md`** - Complete setup guide

All files pushed to GitHub: https://github.com/mrnobody-dev/ORI

---

## ✅ Quick Fix Checklist

Before contacting support, verify:

- [ ] ✅ Dockerfile path set to `Dockerfile.pool`
- [ ] ✅ All required environment variables set:
  - `POOL_NODE_URL`
  - `BTPY_API_TOKEN`
  - `POOL_ADDRESS`
  - `POOL_DATA_DIR=/data`
  - `POOL_GIST_TOKEN`
- [ ] ✅ Persistent volume mounted to `/data`
- [ ] ✅ Node URL is correct and reachable
- [ ] ✅ API token is valid
- [ ] ✅ GitHub Gist token is valid
- [ ] ✅ Railway logs show "Application startup complete"
- [ ] ✅ Root endpoint `/` returns `"node_reachable": true`

---

## 🚀 After Fix: Start Mining

Once pool responds successfully, miners can connect:

```cmd
miner-ori.exe --pool https://your-pool-url.railway.app --worker ori1qYOUR_ADDRESS --threads 8
```

See full mining guide: **`RAILWAY_POOL_MINING_QUICKSTART.md`**

---

**Need more help?**
- Check Railway logs: `railway logs --service ori-pool`
- Test endpoints manually with curl
- Join ORI community for support

---

**Guide Version**: 1.0  
**Date**: August 28, 2026  
**Compatibility**: Railway Platform, ORI v0.2.4+
