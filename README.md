# ORI Blockchain

Bitcoin-style blockchain: UTXO + Proof-of-Work (CPU SHA-256d), Bech32 `ori1...` addresses,
GUI wallet, REST API, solo **and pool** mining.

## 🚀 Try It in 2 Minutes (Windows, no Python)

1. Download & extract → [ORICore-v0.2.2-windows-x64.zip](https://github.com/mrnobody-dev/ORI/releases/download/v0.2.2/ORICore-v0.2.2-windows-x64.zip)
   👉 [All releases](https://github.com/mrnobody-dev/ORI/releases)
2. Run `ORICore.exe` — full node + GUI wallet starts and syncs automatically.
3. Mine with the bundled miner:

```bat
:: solo mining (to your own address, needs your local ORICore running)
miner-ori.exe --address ori1YOUR_ADDRESS --threads 8

:: pool mining (PPLNS) — connect to the public ORI pool
miner-ori.exe --address ori1YOUR_ADDRESS --host ori-production-8364.up.railway.app --port 443 --https --pool --threads 2
```

Your first address is shown on first launch of `ORICore.exe` (also under
**Receive** tab). Coins from mining mature after 100 blocks.

## 🐍 Run From Source (Windows / Linux / Mac)

```bash
git clone https://github.com/mrnobody-dev/ORI.git
cd ORI
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

pip install -r requirements.txt

# GUI wallet + node (recommended)
python qt_app.py
```

That's it — the GUI embeds a full node, wallet, and REST API (`http://127.0.0.1:8000/docs`).

## ⛏️ Mining From Source

Start the node first (`python qt_app.py` or headless below), then:

```bash
# get an address
python wallet.py new --name miner1
python wallet.py list

# solo mining against your local node
python miner.py --address ori1YOUR_ADDRESS --threads 6

# remote mining against any public node (token required if set by operator)
python miner.py --node https://your-node.example.com --address ori1YOUR_ADDRESS --api-token TOKEN
```

## 🌐 Host a Node or PPLNS Pool (Railway / VPS / Docker)

Deploy a public node or a mining pool server in minutes:

👉 **[RAILWAY.md — step-by-step deployment guide](RAILWAY.md)**

Includes: node hosting, PPLNS pool hosting (`pool_server.py`), TCP proxy setup,
env vars, and how to point miners at your deployment.

## 💸 Sending Transactions

```bash
python wallet.py send --from miner1 --to ori1RECIPIENT --amount 1.5 --tier 3
```

| Tier | Confirms in | Fee |
|------|-------------|-----|
| 5 | ~5 blocks | 0.28 sat/vB |
| 3 | ~3 blocks | 0.46 sat/vB |
| 1 | ~1 block | 1.4 sat/vB |

## 📖 Specs

| | |
|---|---|
| Consensus | PoW, SHA-256d (CPU friendly) |
| Difficulty | ORI-Shield retarget every 60 blocks |
| Block time | ~60 s |
| Reward | 46.28 ORI, halving every 2,102,400 blocks |
| Max supply | 194.6 million ORI |
| Addresses | Bech32 `ori1...` |
| Maturity | Coinbase spends after 100 blocks |

More technical detail: [build.md](build.md) · Security audit: [AUDIT_FINDINGS.md](AUDIT_FINDINGS.md)

## License

[MIT](LICENSE) © 2026 ORI
