# ORI Blockchain

Welcome to the ORI blockchain! ORI is a Bitcoin-style full node with a UTXO model, Proof-of-Work mining, and a user-friendly GUI. It features Bech32 addresses (`ori1...`), fast transactions with fee tiers, and an easy-to-use wallet.

Whether you just want to run the wallet, start a node, or mine some ORI, this guide will get you started quickly!

## ⬇️ Download Pre-compiled Binaries (Windows)

> **No Python required** — just download, extract, and run!

| File | Description |
|------|-------------|
| [**ORICore-v0.2.2-windows-x64.zip**](https://github.com/mrnobody-dev/ORI/releases/download/v0.2.2/ORICore-v0.2.2-windows-x64.zip) | Full node + GUI wallet + miner v1.1.0 (pool + solo) |

**How to use:**
1. Download and extract the ZIP
2. Run `ORICore.exe` → full node + wallet GUI starts automatically
3. Run `miner-ori.exe --address ori1<YOUR_ADDRESS>` for **solo mining**, or
4. Run `miner-ori.exe --address ori1<YOUR_ADDRESS> --host POOL_HOST --port POOL_PORT --pool` for **pool mining**

👉 **[All releases →](https://github.com/mrnobody-dev/ORI/releases)**

## 🚀 Quick Start (Easiest Way)

The easiest way to try ORI is by using **ORI Core**, our graphical interface (GUI). It includes a full node and a wallet built right in.

### 1. Install Requirements
Make sure you have Python 3 installed. Then, set up a virtual environment and install the dependencies:

```bash
# Create a virtual environment
python -m venv .venv

# Activate it (Windows)
.venv\Scripts\activate
# Activate it (Mac/Linux)
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Run the ORI Core GUI
Start the application:
```bash
python qt_app.py
```
*That's it! The GUI will start syncing with the network automatically. You can view your balance, send/receive ORI, and manage your wallet directly from the interface.*

---

## 🛠️ Command Line Usage (Advanced)

If you prefer the terminal or want to run a headless server, you can use the command-line tools.

### Running a Node
Run a full node with the REST API (port 8000) and P2P network (port 8033):
```bash
BTPY_DATA_DIR=data/node1 uvicorn main:app --port 8000
```
*Interactive API documentation is available at `http://127.0.0.1:8000/docs` while the node is running.*

### Wallet CLI
Create a new wallet and manage your funds:
```bash
# Create a new wallet
python wallet.py new --name alice

# Check balance
python wallet.py balance alice --node http://127.0.0.1:8000

# Send ORI to another address (requires a fee tier 1-5)
python wallet.py send --node http://127.0.0.1:8000 --from alice --to <ADDRESS_ORI1> --amount 1000000 --tier 3
```

### ⛏️ Mining

ORI provides two ways to mine: a **pre-compiled `.exe`** (Windows, fastest) and a **Python script** (cross-platform).

#### Option A — Pre-compiled Miner (Windows, Recommended)

Download and run `miner-ori.exe` directly — no Python or installation needed:

```bash
# Basic usage — connect to a local node
miner-ori.exe --address ori1<YOUR_ADDRESS> --host 127.0.0.1 --port 8000

# Multi-threaded (recommended: use all CPU cores)
miner-ori.exe --address ori1<YOUR_ADDRESS> --host 127.0.0.1 --port 8000 --threads 8

# Connect to a remote/public node
miner-ori.exe --address ori1<YOUR_ADDRESS> --host your-node.example.com --port 8000 --threads 4

# With API token (if node has BTPY_API_TOKEN set)
miner-ori.exe --address ori1<YOUR_ADDRESS> --host 127.0.0.1 --port 8000 --threads 4 --token YOUR_TOKEN
```

**Arguments:**

| Argument | Required | Default | Description |
|---|---|---|---|
| `--address` | ✅ Yes | — | Your ORI payout address (`ori1…`) |
| `--host` | No | `127.0.0.1` | Node API host |
| `--port` | No | `8000` | Node API port |
| `--threads` | No | `1` | Number of CPU threads to use for mining |
| `--token` | No | *(empty)* | API token (only needed if node requires `X-API-Key`) |

> 💡 **Tip:** For maximum hashrate, set `--threads` to the number of physical CPU cores on your machine.

#### Option B — Python Miner (Cross-platform)

Requires the Python environment set up (see Quick Start):

```bash
python miner.py --node http://127.0.0.1:8000 --address ori1<YOUR_ADDRESS> --threads 4
```

*(Optional) Quick Demo:* To test mining instantly on a fresh local network, lower the difficulty by setting `BTPY_INITIAL_ZEROS=2` before starting the node and miner.


---

## 📚 Technical Details & Features

- **Proof-of-Work (PoW):** SHA-256d, CPU-friendly (Py-ORI).
- **Difficulty Adjustment:** ORI-Shield (Digishield, retargets every block).
- **Halving:** Every 2,102,400 blocks (starts at 46.28 ORI/block).
- **Max Supply:** 194.6 million ORI.
- **Addresses:** Bech32 `ori1...` (witness v0).
- **Maturity:** Mined coins (coinbase) require 100 blocks to mature before they can be spent.
- **Ecosystem Fees:** 5 tiers (1-5) determining target confirmation speed and fee rate (sat/vB).

### Fee Tiers

| Tier | Target confirmation | Rate (sat/vB) | Use case |
|------|---------------------|---------------|----------|
| 5    | 5 blocks            | 0.28          | everyday transactions, local trading |
| 4    | 4 blocks            | 0.35          | retail with moderate traffic |
| 3    | 3 blocks            | 0.46          | time-sensitive payments |
| 2    | 2 blocks            | 0.7           | critical payments & digital services |
| 1    | 1 block             | 1.4           | high urgency |

### Code Structure

- `qt_app.py` / `qt/` - ORI Core GUI
- `main.py` - FastAPI entry point
- `miner.py` - Standalone miner
- `wallet.py` - CLI wallet tool
- `chain.py`, `p2p.py`, `node.py`, `api.py` - Core blockchain, networking, and API

For full build documentation, advanced deployment (e.g., Railway), and architecture details, see [build.md](build.md).

## ⚙️ Configuration (Environment Variables)

| Env | Default | Purpose |
|---|---|---|
| BTPY_DATA_DIR | data | data folder (SQLite) |
| BTPY_API_PORT / BTPY_P2P_PORT | 8000 / 8033 | API / P2P port |
| BTPY_P2P_HOST | 0.0.0.0 | P2P bind address |
| BTPY_SEED_PEERS | (empty) | seed nodes (e.g. `host:port`) |
| BTPY_ENABLE_P2P | 1 | set 0 for a solo node (P2P off) |
| BTPY_INITIAL_ZEROS | 2 | initial difficulty (leading zeros) |

<br>
License: [MIT](LICENSE) © 2026 ORI.
