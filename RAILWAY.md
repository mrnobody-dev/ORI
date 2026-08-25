# 🚂 Deploy ORI Node & PPLNS Pool on Railway

Guide to host a **public ORI node** and a **PPLNS mining pool** on
[Railway](https://railway.app) — so other people can sync, transact, and mine
against your deployment with `miner-ori.exe --pool`.

---

## 0. Prerequisites

- GitHub repo forked/cloned (this repo) & pushed to your GitHub.
- Railway account (free trial credits work).
- Your payout address `ori1...` (from `ORICore.exe` → Receive tab).

> ⚠️ **Use the repo's Dockerfile.** In Railway → your service → **Settings →
> Builder = Dockerfile**. Do NOT use Nixpacks: the error
> `ModuleNotFoundError: No module named 'tx'` comes from builders that launch
> uvicorn without the project directory on `sys.path`. (The Dockerfile +
> patched `main.py` handle this correctly.)

---

## 1. Deploy the PUBLIC NODE

This node serves the blockchain: sync for wallets + templates/submits for miners.

### 1.1 Create service
1. Railway → **New Project → Deploy from GitHub repo** → pick this repo.
2. Settings → **Builder: Dockerfile**.
3. Settings → Networking → **Generate Domain** (HTTP, port `8000`) — e.g.
   `https://ori-node.up.railway.app`.
4. Settings → Networking → **TCP Proxy** for P2P: port `26000` → gives you a
   host like `sakura.proxy.rlwy.net:24044`. Wallets add this in
   **Window → Peers → Add node** (`host:port`, no http://).

### 1.2 Variables (Railway → Variables)

| Variable | Value | Why |
|---|---|---|
| `BTPY_API_HOST` | `0.0.0.0` | bind public |
| `BTPY_API_TOKEN` | a long random string | **required** for public API (protects submit/mine endpoints) |
| `BTPY_P2P_HOST` | `0.0.0.0` | accept peers |
| `BTPY_P2P_PORT` | `26000` | must match TCP Proxy target |
| `BTPY_SEED_PEERS` | existing seeds, comma separated | network bootstrap |
| `BTPY_DATA_DIR` | `/data` | attach a Railway **Volume** here so the chain survives deploys |

> 🔐 Generate a token: `python -c "import secrets;print(secrets.token_hex(24))"`

### 1.3 Verify
```bash
curl https://ori-node.up.railway.app/stats
curl -H "X-API-Key: YOUR_TOKEN" "https://ori-node.up.railway.app/mining/template?address=ori1..."
```

---

## 2. Mine SOLO against your node

From any PC (Windows exe or Python miner):

```bat
miner-ori.exe --address ori1YOUR_ADDRESS ^
              --host ori-node.up.railway.app --port 443 ^
              --token YOUR_TOKEN --threads 8
```

Python variant:
```bash
python miner.py --node https://ori-node.up.railway.app \
                --address ori1YOUR_ADDRESS \
                --api-token YOUR_TOKEN --threads 6
```

Solo = you only earn when YOU find a full block. For steady payouts → run the pool below.

---

## 3. Deploy the PPLNS POOL (`pool_server.py`)

The pool gives miners frequent share-credits; when anyone in the pool finds a
block, the reward is split proportionally over the last N shares (PPLNS,
default fee 1%).

Add a **second** Railway service from the same repo:

### 3.1 Settings
- Builder: **Dockerfile**
- Start command override:
  ```
  python -m uvicorn pool_server:app --host 0.0.0.0 --port $PORT
  ```
- Networking → Generate Domain (e.g. `https://ori-pool.up.railway.app`)

### 3.2 Variables

| Variable | Example | Meaning |
|---|---|---|
| `POOL_NODE_URL` | `https://ori-node.up.railway.app` | the node from step 1 |
| `BTPY_API_TOKEN` | same token as the node | lets the pool submit blocks |
| `POOL_ADDRESS` | `ori1...` | **pool wallet** — coinbase pays here first |
| `POOL_FEE_PCT` | `1.0` | pool fee |
| `PPLNS_POINTS` | `10000` | window size (shares) |
| `POOL_DIFF_SHIFT` | `12` | start difficulty multiplier vs network (vardiff auto-adjusts per miner) |
| `POOL_DATA_DIR` | `/data` | attach a Volume → ledger survives restarts |

### 3.3 Point miners at the pool

```bat
miner-ori.exe --address ori1MINER_ADDRESS --pool ^
              --host ori-pool.up.railway.app --port 443
```

That's it. Miners can check their stats at:
```
https://ori-pool.up.railway.app/pool/stats
```

### 3.4 How payouts work
1. Every accepted share = 1 point into a rolling window (`PPLNS_POINTS`).
2. When a share also hits the real network target, the pool submits the block
   to your node; coinbase pays `POOL_ADDRESS`.
3. The block reward minus fee is credited proportionally to each worker's
   points in that window → visible as balances in `/pool/stats`.
4. Distributing matured coins to miners is an off-chain transfer from the pool
   wallet (`ORICore.exe` or `wallet.py send`) — do it periodically.

---

## 4. Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'tx'` | Builder is Nixpacks → switch to **Dockerfile**, or ensure Start Command is `python -m uvicorn main:app ...` (module form adds CWD to sys.path). `main.py` also self-bootstraps sys.path now. |
| Miner: HTTP 403 on template/submit | Public node requires `--token` / env `BTPY_API_TOKEN`. |
| Wallet won't sync via proxy peer | Add as `host:port` (no `http://`), wait ~15 s retry backoff, or restart the wallet. |
| Pool job returns 503 | `POOL_NODE_URL` wrong or node still starting / syncing. |
| Shares rejected "stale job" | Round took too long — vardiff will make it easier automatically. |

## 5. Local test before deploying

```bash
# terminal 1 — node
python main.py            # API :8000

# terminal 2 — pool
set POOL_NODE_URL=http://127.0.0.1:8000
set POOL_ADDRESS=ori1...
python -m uvicorn pool_server:app --port 9000

# terminal 3 — mine into the pool
miner-ori.exe --address ori1YOU --pool --host 127.0.0.1 --port 9000

curl http://127.0.0.1:9000/pool/stats
```
