# ORI (We build but to tear down)

ORI is an experimental, decentralized, peer-to-peer Proof-of-Work cryptocurrency designed for high-speed transactions and long-term sustainability. It features a native GUI wallet, REST API, UTXO model, and both solo and PPLNS pool mining capabilities.

*"We build but to tear down. Most of our work and resource is squandered - 2030"*

---

## 📖 Specifications

| Parameter | Value |
|-----------|-------|
| **Consensus** | Proof-of-Work (PoW) |
| **Algorithm** | SHA-256d (CPU Mining Friendly) |
| **Block Time** | ~3.69 Seconds |
| **Block Reward** | 6.12073980 ORI |
| **Halving Interval**| 30,143,415 Blocks |
| **Max Supply** | 368,999,999.79 ORI |
| **Retargeting** | Every 23,414 Blocks (~1 Day) |
| **Block Size** | 100 KB Max |
| **Maturity** | 2,000 Blocks |
| **Addresses** | Bech32 `ori1...` |

---

## 🚀 Quick Start — Windows (GUI)

1. Download the latest release from the [Releases Page](https://github.com/mrnobody-dev/ORI/releases).
2. Extract the `.zip` file.
3. Run `ORICore.exe` to launch the GUI wallet. It automatically starts a full node and syncs with the network.

---

## 🖥️ Running a Headless Node (CLI)

For VPS or server environments where no GUI is needed, run `ORICore.exe` directly from Command Prompt with flags:

**Basic node (connect to official seed):**
```cmd
ORICore.exe -datadir=data\node1 -port=8000 -p2pport=8033
```

**Custom peers (manual P2P connection):**
```cmd
ORICore.exe -datadir=data\node1 -port=8000 -p2pport=8034 -seed-peers=sakura.proxy.rlwy.net:24044
```

**Linux/Mac (from source, using the `orid` daemon script):**
```bash
./orid -datadir=data/node1 -port=8000 -p2pport=8033 -seed-peers=sakura.proxy.rlwy.net:24044
```

**Available flags:**

| Flag | Description |
|------|-------------|
| `-datadir` | Directory to store blockchain data |
| `-port` | REST API port (default: `8000`) |
| `-p2pport` | P2P network port (default: `8033`) |
| `-api-host` | API bind address (default: `127.0.0.1`) |
| `-seed-peers` | Comma-separated list of peers `host:port` |
| `-initial-zeros` | PoW difficulty initial zeros |

---

## ⛏️ Mining

ORI is CPU-friendly (SHA-256d). You can mine solo or join the PPLNS pool.

### Option 1 — Batch Launcher (Windows, Easiest)
1. Extract the `.zip` release.
2. Double-click `CPU miner launcher.bat`.
3. Enter your `ori1...` wallet address and number of threads.
4. The miner connects to the official pool automatically.

### Option 2 — `miner-ori.exe` (Windows CLI)

**Solo mining** (requires `ORICore.exe` running locally on port 8000):
```cmd
miner-ori.exe --address ori1YOUR_ADDRESS --threads 4
```

**Pool mining (PPLNS):**
```cmd
miner-ori.exe --address ori1YOUR_ADDRESS --host ori-production-8364.up.railway.app --port 443 --https --pool --threads 4
```

### Option 3 — Python source (Linux/Mac/Windows)

**Solo mining:**
```bash
python miner.py --address ori1YOUR_ADDRESS --threads 4
```

**Pool mining:**
```bash
python miner.py --address ori1YOUR_ADDRESS --host ori-production-8364.up.railway.app --port 443 --https --pool --threads 4
```

---

## 🔗 Connecting Nodes (P2P)

To connect manually to a specific node, either pass `-seed-peers` on launch or create a `config.json` in the same directory as the executable:

```json
{
  "seed_peers": [
    "sakura.proxy.rlwy.net:24044",
    "192.168.1.100"
  ]
}
```

Restart the node after editing. It will connect to the listed peers on startup.

---

## 🌐 Hosting & Deployment

To run a node on Railway, VPS, or Docker, or to host your own PPLNS Mining Pool, see the deployment guide:

👉 **[Deployment & Hosting Guide (RAILWAY.md)](RAILWAY.md)**

For architecture and technical documentation, see the [Build Guide](build.md).

## License

Released under the [MIT License](LICENSE) © 2026 ORI.
