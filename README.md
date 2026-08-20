# ORI

ORI blockchain: a Bitcoin-style full node with a UTXO model, ECDSA secp256k1,
proof-of-work Py-ORI (SHA-256d, CPU-friendly), **ORI-Shield** difficulty
(Digishield, retarget every block), halving every 2,102,400 blocks (46.28 ORI/block,
max supply 194.6 million ORI), **Bech32 `ori1...` addresses**, **fee-tier
transactions** (Eco/Swift/Turbo/Blast/Flash), side branches + reorg, and P2P
networking between nodes.
**Nodes only verify** — mining runs as a separate process.

Full build documentation is in [build.md](build.md).

## Structure

```
main.py        FastAPI entry         chain.py       validation, reorg, template
config.py      config + env          p2p.py         P2P network (TCP)
crypto.py      ECDSA secp256k1       node.py        node orchestrator
tx.py          transactions, coinbase api.py        REST API
block.py       block + header        miner.py       STANDALONE miner
pow.py         PoW / bits / ORI-Shield wallet.py    CLI wallet
merkle.py      merkle root           bech32.py      ori1 address (witness v0)
utxo.py        UTXO set              test_*.py      smoke / p2p / reorg
dns.py         DNS wire codec        seeder.py      DNS seeder + scanner
qt_app.py      ORI Core GUI (PySide6) qt/           GUI package (11 modules)
```

Security/protocol: **low-S** signatures (BIP-146, mandatory since height 53),
anti **time-warp** MTP (block timestamp > median of last 11 blocks), **coinbase
maturity of 100 blocks** (Bitcoin-like, soft-forked at height 100 — immature
rewards appear as `immature_sats` and cannot be spent), `GET /validate/` = full
chain replay + UTXO audit, block/tx bodies limited before parsing, capped
mempool, mempool re-validated on every reorg. Full bug list:
[build.md](build.md#7).

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt

BTPY_DATA_DIR=data/node1 uvicorn main:app --port 8000   # node 1 (API 8000, P2P 8033)
```

Node 2 (another terminal) — **bootstrap via DNS seeder, no manual addpeer**:

```bash
# 1) Run the DNS seeder once on the machine it will run on (LAN IP, e.g. 192.168.1.14):
python seeder.py --bootstrap 192.168.1.14:8033 --announce 192.168.1.14 --dns-port 5353

# 2) Any node only needs to point at the DNS seeder:
BTPY_DATA_DIR=data/node2 BTPY_P2P_PORT=8034 \
BTPY_SEED_DNS_HOST=192.168.1.14 BTPY_SEED_DNS_NAME=seed.ori \
BTPY_SEED_DNS_PORT=5353 BTPY_SEED_DNS_P2P_PORT=8033 \
uvicorn main:app --port 8001
```

Or without a seeder, connect manually:
`curl -X POST http://127.0.0.1:8001/network/addpeer -H "Content-Type: application/json" -d '{"host":"127.0.0.1","port":8033}'`

### Deploying a node on Railway (nodes only, no GUI)

Railway supports non-HTTP TCP ports via **TCP Proxy** (1 TCP port per service)
and **persistent volumes** to store the chain. ORI P2P is pure TCP (no UDP needed).

1. Push the repo to GitHub and deploy the repo as a service (using the `Dockerfile`).
2. Set a **TCP Proxy** → internal port `26000`; Railway provides a `domain:port`
   (e.g. `shuttle.proxy.rlwy.net:15140`).
3. Mount a **Volume** → `/data` (otherwise the chain is wiped on redeploy).
4. Service variables (Environment):
   - `BTPY_DATA_DIR=/data`
   - `BTPY_P2P_PORT=26000`
   - `BTPY_P2P_HOST=0.0.0.0`
   - `BTPY_ENABLE_P2P=1`
   - `BTPY_SEED_PEERS=<another node's proxy, e.g. shuttle.proxy.rlwy.net:15140>`
   - `BTPY_SEED_DNS_HOST` leave empty (DNS seed A-records cannot point to a Railway proxy domain)
   - `BTPY_API_TOKEN=<optional>`
5. Nodes in the same project can connect privately via **private networking**
   (`RAILWAY_PRIVATE_DOMAIN`) or through their own TCP proxies.

Note: the HTTP port is used by uvicorn (`$PORT`/8000) for the API; only 1 public
TCP port per service — that's the P2P port. You need ≥1 always-on bootstrap node
so new nodes can find the network.

Miner (separate process) and wallet:

```bash
python wallet.py new --name alice
python miner.py --node http://127.0.0.1:8000 --address <ADDRESS_ORI1> --threads 4
python wallet.py balance alice --node http://127.0.0.1:8000
python wallet.py send --node http://127.0.0.1:8000 --from alice \
  --to <ADDRESS_ORI1> --amount 1000000 --tier 3
```

Ecosystem fees: 5 tiers — `--tier 1..5` is required, no custom fees. Fee =
rate × tx size (vB), paid in sats:

| Tier | Target confirmation | Rate (sat/vB) | Use case |
|------|---------------------|---------------|----------|
| 5    | 5 blocks            | 0.28          | everyday transactions, local trading, asset transfers |
| 4    | 4 blocks            | 0.35          | retail with moderate traffic |
| 3    | 3 blocks            | 0.46          | time-sensitive payments |
| 2    | 2 blocks            | 0.7           | critical payments & digital services |
| 1    | 1 block             | 1.4           | high urgency |

Nodes reject transactions below the 0.28 sat/vB minimum relay (anti 0-fee spam)
and the mempool prioritizes mining by fee rate (sat/vB), like Bitcoin.

> **About confirmations:** tier = *estimated* target confirmation for fee
> determination, **not a lock**. Non-coinbase tx outputs (including change still
> unconfirmed in the mempool) can be spent immediately; only *coinbase* outputs
> must wait for the **100-block maturity** before they are spendable.

Quick demo (low difficulty): set `BTPY_INITIAL_ZEROS=2` before running the node
and miner.

## API

- `GET /blockchain/`, `/block/{height}`, `/block/hash/{hash}`, `/tx/{txid}`, `/address/{addr}`
- `GET /mempool/`, `/peers/`, `/stats`, `/validate/`
- `POST /tx/` — broadcast transaction `{"tx": "<hex>"}`
- `GET /mining/template?address=...` — template for the miner
- `POST /mining/submit` — submit block `{"block": "<hex>"}`
- `POST /network/addpeer` — `{"host": "...", "port": 8033}`

Interactive docs at `http://127.0.0.1:8000/docs`.

### `/tx/{txid}` and `/mempool/` endpoint details

- `/tx/{txid}` — Bitcoin Core-style JSON: for each input there is
  `prev_txid/prev_vout/sequence/sigscript` plus prevout `pkscript/value/address`
  (coinbase: `coinbase: true, message: "new generated coin"`); outputs have
  `value/address/pkscript/spent` (spent computed from the UTXO set, always
  `False` for mempool txs); plus `version/locktime/size/raw_hex`,
  `deleted`, and `block {height, hash, position, mempool}`. Invalid txid
  → clean 404.
- `/mempool/` — each entry: `txid, fee, size, fee_rate, version, locktime,
  raw_hex, inputs[], outputs[]`; sorted by fee rate (highest first).

## GUI (ORI Core, PySide6)

Bitcoin-Qt-style GUI: full node + in-process wallet (**no mining**), with
Overview / Send / Receive / Transactions pages, transaction details
(double-click), console, peers, address book, options; "Bitcoin Orange" theme
(QSS), splash + boot thread. Build documentation is in
[qt/build.md](qt/build.md).

Wallet file: `wallet.json` (JSON, atomic writes via temp+`os.replace` — safe
from 0-byte corruption). Optional AES-256-GCM encryption (JSON envelope). Old
`wallet.dat` files (binary container format) are still readable.

If the API port (default 8000) is already taken by another process, the GUI
automatically picks the next free port; the `http://127.0.0.1:<port>/docs` URL
is shown on the Overview page (clickable/copyable).

```bash
pip install PySide6
.venv/bin/python qt_app.py --datadir data/node1 [--wallet wallet.json]
```

Headless testing (WSL/CI without X): `QT_QPA_PLATFORM=offscreen` +
`ORI_TEST_AUTOQUIT_MS=<ms>` for a controlled auto-quit.

## Test

```bash
.venv/bin/python test_smoke.py   # end-to-end unit (genesis, mining, tx, double-spend)
.venv/bin/python test_p2p.py     # 2-node sync + relay
.venv/bin/python test_reorg.py   # fork + reorg between nodes
```

## Configuration (env)

| Env | Default | Purpose |
|---|---|---|
| BTPY_DATA_DIR | data | data folder (SQLite) |
| BTPY_API_PORT / BTPY_P2P_PORT | 8000 / 8033 | API / P2P port |
| BTPY_P2P_HOST | 0.0.0.0 | P2P bind address |
| BTPY_SEED_PEERS | (empty) | seed nodes, format `host:port,host:port` |
| BTPY_ENABLE_P2P | 1 | set 0 for a solo node (P2P off) |
| BTPY_INITIAL_ZEROS | 2 | initial difficulty (number of leading zeros) |
| BTPY_BLOCK_TIME | 60 | target block time for ORI-Shield (seconds) |
| BTPY_SHIELD_WINDOW | 11 | ORI-Shield window (blocks) |
| BTPY_BLOCK_REWARD | 4628000000 | era-0 block reward (sats = 46.28 ORI) |
| BTPY_SEED_DNS_HOST | (empty) | DNS seeder IP for automatic bootstrap |
| BTPY_SEED_DNS_PORT | 5353 | UDP DNS seeder port |
| BTPY_SEED_DNS_NAME | seed.ori | DNS name to resolve (A record) |
| BTPY_SEED_DNS_P2P_PORT | 8033 | P2P port advertised in resolved records |
| BTPY_MAX_MEMPOOL_TXS | 100000 | mempool tx count limit |
| BTPY_API_TOKEN | (empty) | if set, write/mining endpoints require `X-API-Key` header |

Seeder env (for the `seeder.py` process): `ORI_SEEDER_BOOTSTRAP` (`host:port`
of the initial node), `ORI_SEEDER_ANNOUNCE` (advertised IPs, comma-separated),
`ORI_SEEDER_DNS_PORT` (5353), `ORI_SEEDER_HTTP_PORT` (8123 — `GET /` lists
peers, `POST /register` requires `ORI_SEEDER_TOKEN`),
`ORI_SEEDER_P2P_PORT` (8353 — the seeder's own P2P port),
`ORI_SEEDER_DNS_P2P_PORT` (8033 — P2P port advertised to nodes),
`ORI_SEEDER_TOKEN` (required for `/register`).

License: [MIT](LICENSE) © 2026 ORI.
