# ORI (We build but to tear down)

ORI is an experimental, decentralized, peer-to-peer Proof-of-Work cryptocurrency designed for high-speed transactions and long-term sustainability. Built purely in Python, it features a native GUI wallet, REST API, UTXO model, and both solo and PPLNS pool mining capabilities.

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

## 🚀 Quick Start (Windows)

The easiest way to run the node and wallet without installing Python:

1. Download the latest release from the [Releases Page](https://github.com/mrnobody-dev/ORI/releases).
2. Extract the `.zip` file.
3. Run `ORICore.exe` to start the GUI wallet. It will automatically start a full node in the background and sync with the network.

## 🐍 Build from Source (Linux / Mac / Windows)

Requires Python 3.10+.

```bash
git clone https://github.com/mrnobody-dev/ORI.git
cd ORI

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install requirements
pip install -r requirements.txt

# Run the GUI Wallet & Full Node
python qt_app.py
```

*The GUI embeds a full node, wallet, and REST API (`http://127.0.0.1:8000/docs`).*

---

## ⛏️ Mining Tutorial

ORI is highly optimized for CPU mining (SHA-256d). You can mine **Solo** (directly to your own node) or connect to a **PPLNS Pool** (mining with others to share rewards).

### Option 1: Mining on Windows (Easy Mode GUI/Batch)
If you downloaded the Windows `.zip` release, you already have everything you need!
1. Open the folder where you extracted ORI.
2. Double-click the file named `CPU miner launcher.bat`.
3. Paste your ORI wallet address and choose how many CPU threads to use.
4. The miner will automatically connect to the Official Pool and start hashing!

### Option 2: CLI Mining with `miner-ori.exe` (Windows)
If you prefer using the Command Prompt (cmd) or PowerShell directly using the compiled executable, use the following commands:

**Solo Mining (Requires `ORICore.exe` running locally):**
```cmd
miner-ori.exe --address ori1YOUR_ADDRESS --threads 4
```

**Pool Mining (PPLNS):**
```cmd
miner-ori.exe --address ori1YOUR_ADDRESS --host ori-production-8364.up.railway.app --port 443 --https --pool --threads 4
```

### Option 3: Mining from Source Code (Python)
If you are running from source code on Linux/Mac/Windows, start your local node first (`python qt_app.py`), then open a new terminal:

**Solo Mining:**
```bash
python miner.py --address ori1YOUR_ADDRESS --threads 4
```

**Pool Mining:**
```bash
python miner.py --address ori1YOUR_ADDRESS --host ori-production-8364.up.railway.app --port 443 --https --pool --threads 4
```

---

## 🔗 Connecting Nodes (P2P Network)

By default, an ORI node attempts to connect to the hardcoded DNS seed to find other peers. However, if you are setting up a private network or want to manually connect to a specific node, you can do so using the `config.json` file.

1. In the same directory as your `ORICore.exe` or source code, create a file named `config.json`.
2. Add the `seed_peers` array pointing to the IP addresses or domains of the target nodes.

**Example `config.json`:**
```json
{
  "seed_peers": [
    "ori-production-8364.up.railway.app",
    "192.168.1.100"
  ]
}
```
3. Restart your node. It will now force a P2P connection to the specified peers on port `8033`.

---

## 🌐 Network & Infrastructure

To run a headless node on a VPS, Railway, or Docker, or to host your own PPLNS Mining Pool, please read the deployment guide:

👉 **[Deployment & Hosting Guide (RAILWAY.md)](RAILWAY.md)**

For deeper technical documentation and architecture, read the [Build Guide](build.md).

## License

Released under the [MIT License](LICENSE) © 2026 ORI.
