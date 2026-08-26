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

## ⛏️ Mining

You can mine ORI either solo or via a PPLNS mining pool. Start your node (`python qt_app.py`), then use the bundled miner.

**Solo Mining:**
```bash
python miner.py --address ori1YOUR_ADDRESS --threads 4
```

**Pool Mining (PPLNS):**
```bash
python miner.py --address ori1YOUR_ADDRESS --host pool.ori-network.com --port 443 --https --pool --threads 4
```

---

## 🌐 Network & Infrastructure

To run a headless node on a VPS, Railway, or Docker, or to host your own PPLNS Mining Pool, please read the deployment guide:

👉 **[Deployment & Hosting Guide (RAILWAY.md)](RAILWAY.md)**

For deeper technical documentation and architecture, read the [Build Guide](build.md).

## License

Released under the [MIT License](LICENSE) © 2026 ORI.
