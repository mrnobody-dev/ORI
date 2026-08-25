# ORI Mining Pool — Rancangan PPLNS

> **Status:** Desain / Rancangan Arsitektur  
> **Konsensus:** Sesuai dengan chain ORI (SHA256d, Digishield, Bech32 `ori1`, fee tiers 1–5)  
> **Pendekatan:** Pool sebagai "Solo Miner raksasa" di mata Node; PPLNS distribusi reward ke workers

---

## 1. Mengapa Perlu Pool?

| | Solo Mining | Pool Mining (PPLNS) |
|--|--|--|
| **Cara kerja** | Miner menang sendiri → dapat 100% reward | Pool gabungkan hashrate semua miner → reward dibagi proporsional |
| **Variance** | Sangat tinggi — bisa mining berminggu-minggu tanpa blok | Rendah — income lebih stabil dan rutin |
| **Minimum hashrate** | Tidak ada — tapi makin kecil hashrate, makin lama nunggu | Tidak ada — sekecil apapun tetap dapat bagian |
| **Transparansi** | 100% transparan | Bergantung pool operator — desain ini terbuka |

---

## 2. Konsensus ORI yang Relevan

Sebelum merancang pool, kita perlu pahami karakteristik chain ORI yang mempengaruhi desain:

### 2.1 Block Reward & Coinbase
Dari `tx.py → coinbase_tx()`:
```python
def coinbase_tx(height, reward_sats, address, note=""):
    txin  = TxIn(NULL_HASH, 0xFFFFFFFF, script)   # coinbase input
    txout = TxOut(reward_sats, address.encode())   # 1 output = 1 penerima
    return Transaction(1, [txin], [txout], 0)
```
- Coinbase **hanya 1 output** → Pool harus distribusikan sendiri setelah coinbase mature

### 2.2 Coinbase Maturity
Dari `config.py` / API: `coinbase_maturity = 100 blok`  
Artinya reward blok yang ditemukan pool baru bisa dibelanjakan setelah **100 blok berikutnya**.

### 2.3 Proof-of-Work
- Algoritma: **SHA-256d** (double SHA-256) — identik dengan Bitcoin
- Target: `hash(header) ≤ target`, di mana `target = target_from_bits(bits)`
- Nonce: 4 bytes (uint32), 0..2^32
- Jika nonce space habis → timestamp +1 detik, minta template baru

### 2.4 Fee Tiers
ORI menggunakan 5 tier fee (bukan dynamic fee seperti Bitcoin):

| Tier | sat/vB | Target konfirmasi |
|--|--|--|
| 5 | 0.28 | ~5 blok |
| 4 | 0.35 | ~4 blok |
| 3 | 0.46 | ~3 blok |
| 2 | 0.70 | ~2 blok |
| 1 | 1.40 | ~1 blok |

Pool harus menggunakan **tier 2 atau 1** untuk payout transaction agar cepat dikonfirmasi.

### 2.5 Mining API (sudah ada di node)
```
GET  /mining/template?address=<pool_addr>
     → { height, prev_hash, bits, target, timestamp, reward_sats,
         txs[], tx_count, fees_sats, difficulty }

POST /mining/submit { block: "<hex>" }
     → { height, hash } on success
     → 400/422 + detail on failure
```

---

## 3. Arsitektur Keseluruhan

```
                         ┌──────────────────────────────────────┐
  Workers (miners)       │         POOL SERVER                  │
                         │                                      │
 miner-ori.exe           │  ┌─────────────┐  ┌──────────────┐  │
 --host pool.domain.com  │  │ Pool API    │  │  Job Manager │  │
 --address ori1<KAMU>    │  │             │  │              │  │
 --port 3333    ────────►│  │ POST /share │  │ - Poll node  │  │
                         │  │ GET  /stats │  │   /template  │  │
 (miner-ori.exe HARUS    │  │ GET  /miner │  │ - Broadcast  │  │
  dimodifikasi untuk     │  │ GET  /blocks│  │   job ke     │  │
  kirim ke pool,         │  └──────┬──────┘  │   workers    │  │
  bukan langsung ke node)│         │         └──────┬───────┘  │
                         │         ▼                 ▼          │
                         │  ┌─────────────────────────────────┐ │
                         │  │       POOL DATABASE (SQLite)    │ │
                         │  │  shares | blocks | payouts      │ │
                         │  └─────────────────┬───────────────┘ │
                         │                    │                  │
                         │  ┌─────────────────▼───────────────┐ │
                         │  │      PPLNS PAYOUT ENGINE        │ │
                         │  │  - Hitung proporsi N shares     │ │
                         │  │  - Potong pool fee              │ │
                         │  │  - Kirim TX ke workers          │ │
                         │  └─────────────────┬───────────────┘ │
                         └────────────────────┼─────────────────┘
                                              │
                                              ▼
                                  ┌───────────────────┐
                                  │  ORI NODE         │
                                  │  (tidak diubah)   │
                                  │                   │
                                  │ GET /template     │
                                  │ POST /submit      │
                                  │ POST /tx/         │
                                  └───────────────────┘
```

---

## 4. Komponen yang Perlu Dibuat

### File Struktur
```
pool-pplns/
├── DESIGN.md            ← file ini
├── pool_server.py       ← FastAPI server (Pool API + Job Manager)
├── pool_db.py           ← SQLite schema & query helpers
├── pool_pplns.py        ← PPLNS calculation engine
├── pool_payer.py        ← Automated payout worker
├── pool_miner_client.py ← Modifikasi miner.py khusus untuk submit ke pool
├── requirements.txt     ← fastapi, uvicorn, (aiosqlite)
└── README.md            ← cara setup & jalankan pool
```

---

## 5. Database Schema

```sql
-- ============================================================
-- SHARES: setiap "bukti kerja" yang dikirim worker ke pool
-- ============================================================
CREATE TABLE shares (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_addr     TEXT    NOT NULL,           -- ori1... payout address worker
    worker_name     TEXT    DEFAULT '',         -- label opsional (misal: "rig-01")
    job_id          TEXT    NOT NULL,           -- ID job dari pool (prev_hash+height)
    header_hex      TEXT    NOT NULL,           -- 80-byte header hex yang disubmit
    block_hash      TEXT    NOT NULL,           -- hash header (little-endian hex)
    share_diff      REAL    NOT NULL,           -- difficulty aktual share ini
    pool_diff       REAL    NOT NULL,           -- difficulty target pool saat ini
    is_block        INTEGER DEFAULT 0,          -- 1 = share ini juga valid block!
    block_height    INTEGER,                    -- diisi jika is_block=1
    timestamp       INTEGER NOT NULL,           -- unix timestamp
    ip_addr         TEXT    DEFAULT ''
);

CREATE INDEX idx_shares_worker ON shares(worker_addr);
CREATE INDEX idx_shares_timestamp ON shares(timestamp DESC);
CREATE INDEX idx_shares_block ON shares(is_block, block_height);

-- ============================================================
-- BLOCKS: blok yang berhasil ditemukan pool
-- ============================================================
CREATE TABLE blocks_found (
    height          INTEGER PRIMARY KEY,
    block_hash      TEXT    NOT NULL,
    reward_sats     INTEGER NOT NULL,           -- block reward + tx fees
    fees_sats       INTEGER NOT NULL DEFAULT 0,
    finder_addr     TEXT    NOT NULL,           -- worker yang temukan blok
    timestamp       INTEGER NOT NULL,
    mature_at_height INTEGER NOT NULL,          -- height+100 (baru bisa bayar)
    paid            INTEGER DEFAULT 0,          -- 0=pending, 1=paid
    payout_txid     TEXT                        -- txid payout (jika sudah bayar)
);

-- ============================================================
-- PAYOUTS: record payout per worker per block
-- ============================================================
CREATE TABLE payouts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    block_height    INTEGER NOT NULL REFERENCES blocks_found(height),
    worker_addr     TEXT    NOT NULL,
    shares_in_window INTEGER NOT NULL,          -- share worker dalam N window
    total_shares    INTEGER NOT NULL,           -- total share semua worker
    gross_sats      INTEGER NOT NULL,           -- reward sebelum pool fee
    pool_fee_sats   INTEGER NOT NULL,
    net_sats        INTEGER NOT NULL,           -- yang diterima worker
    paid            INTEGER DEFAULT 0,
    txid            TEXT,
    paid_at         INTEGER
);

CREATE INDEX idx_payouts_worker ON payouts(worker_addr);
CREATE INDEX idx_payouts_block ON payouts(block_height);

-- ============================================================
-- CONFIG: parameter pool
-- ============================================================
CREATE TABLE pool_config (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
-- Contoh rows:
-- ('pool_fee_pct', '1.0')
-- ('pplns_window', '1000000')    -- N shares terakhir
-- ('pool_address', 'ori1...')
-- ('pool_diff', '0.001')         -- starting pool difficulty
-- ('min_payout_sats', '100000000') -- 1 ORI minimum payout
```

---

## 6. Cara Kerja PPLNS — Detail

### 6.1 Pool Difficulty vs Node Difficulty

Node difficulty bisa sangat tinggi (target `0000000FFFF...`).  
Pool memberikan target yang **jauh lebih mudah** kepada worker, sehingga worker bisa mengirim banyak share per menit (bukan menunggu berjam-jam untuk 1 blok).

```
Node target:  0000000FFFFF...  (misal difficulty 1000)
Pool target:  000FFFFFFFFF...  (misal difficulty 0.01 → 100,000x lebih mudah)
```

Setiap kali worker menemukan hash ≤ pool target, itu disebut **share**.  
Jika kebetulan hash ≤ node target juga, share tersebut juga valid sebagai **full block**.

### 6.2 Hitung Difficulty Share

```python
MAX_TARGET = (1 << 256) - 1

def share_difficulty(block_hash_hex: str) -> float:
    """Hitung difficulty aktual dari sebuah hash."""
    hash_int = int(block_hash_hex, 16)
    if hash_int == 0:
        return float('inf')
    return MAX_TARGET / hash_int
```

### 6.3 PPLNS Window

Ketika pool berhasil menemukan satu blok, pool melihat ke belakang **N share terakhir**  
(bukan N share per worker, tapi N share total dari semua worker):

```python
N = 1_000_000  # parameter PPLNS window (bisa dikonfigurasi)

def calculate_pplns(db, block_height: int, reward_sats: int, pool_fee_pct: float) -> list:
    """Hitung payout PPLNS untuk satu blok."""
    # Ambil N share terakhir sebelum/saat block ditemukan
    rows = db.execute("""
        SELECT worker_addr, COUNT(*) as count
        FROM shares
        WHERE id <= (SELECT MAX(id) FROM shares WHERE block_height = ?)
        ORDER BY id DESC
        LIMIT ?
    """, (block_height, N)).fetchall()

    # Hitung total shares dalam window
    total_shares = sum(r['count'] for r in rows)
    if total_shares == 0:
        return []

    # Hitung distribusi
    pool_fee_sats = int(reward_sats * pool_fee_pct / 100)
    distributable = reward_sats - pool_fee_sats

    payouts = []
    for row in rows:
        worker_share = int(distributable * row['count'] / total_shares)
        payouts.append({
            'worker_addr': row['worker_addr'],
            'shares': row['count'],
            'net_sats': worker_share,
            'pool_fee_sats': int(pool_fee_sats * row['count'] / total_shares),
        })
    return payouts
```

### 6.4 Alur Lengkap

```
1. Pool Server mulai:
   - Load pool wallet (pool_address = ori1POOL...)
   - Mulai polling /mining/template setiap 1 detik
   - Simpan "job aktif" (job_id = prev_hash[:8] + str(height))

2. Worker connect ke pool:
   POST /pool/register { address: "ori1ALICE...", name: "rig-01" }
   GET  /pool/job → { job_id, prev_hash, bits, height, pool_target, ... }

3. Worker mining dengan pool_target (bukan node target):
   - Temukan hash ≤ pool_target → kirim share ke pool
   POST /pool/share { job_id, header_hex, worker_addr }

4. Pool validasi share:
   a. Parse 80-byte header
   b. Hitung SHA256d(header)
   c. Cek hash ≤ pool_target → valid share → simpan ke DB
   d. Cek hash ≤ node_target → FULL BLOCK!
      - POST /mining/submit ke node
      - Tandai di DB: blocks_found

5. Setelah 100 blok (coinbase mature):
   - Pool cek balance di pool_address
   - Hitung PPLNS dari N share terakhir
   - Buat TX multi-output ke semua worker
   - Broadcast TX ke node: POST /tx/
   - Update DB: payouts.paid = 1

6. Worker bisa cek status kapan saja:
   GET /pool/miner/ori1ALICE... → { shares_24h, pending_sats, paid_total }
```

---

## 7. Modifikasi yang Dibutuhkan

### 7.1 `miner-ori.exe` / `miner.py` — Tambah Mode Pool

Saat ini miner langsung submit ke node.  
Perlu tambah flag `--pool` agar submit share ke pool server, bukan ke node:

```
# Solo (sekarang):
miner-ori.exe --address ori1ALICE --host 127.0.0.1 --port 8000

# Pool mode (baru):
miner-ori.exe --address ori1ALICE --host pool.domain.com --port 3333 --pool
```

Perbedaan utama:
- Job datang dari `GET /pool/job` (bukan `/mining/template`)
- Target yang dipakai: **pool target** (lebih mudah dari node target)
- Submit: `POST /pool/share` (bukan `/mining/submit`)

### 7.2 Node ORI — Tidak Perlu Diubah ✅

Pool berinteraksi dengan node menggunakan endpoint yang sudah ada:
- `GET /mining/template?address=ori1POOL` → dapat job
- `POST /mining/submit` → submit full block
- `POST /tx/` → kirim payout ke workers

---

## 8. Keamanan & Anti-Cheat

| Ancaman | Solusi |
|--|--|
| Worker submit share palsu (hash tidak valid) | Pool selalu verifikasi SHA256d(header) ≤ pool_target |
| Worker submit share untuk job lama (stale) | Tolak share yang job_id-nya sudah > 2 job lalu |
| Duplicate share | Index unik pada (job_id, header_hex) |
| Worker klaim difficulty lebih tinggi | Pool yang hitung difficulty dari hash aktual |
| Pool operator curang | Pool bersifat open-source, block explorer bisa diverifikasi siapapun |
| DDoS share spam | Rate limit per IP: max 100 shares/detik per IP |

---

## 9. Parameter PPLNS yang Perlu Diatur

| Parameter | Default | Penjelasan |
|--|--|--|
| `pplns_window` | `1,000,000` | Jumlah N share terakhir yang dihitung |
| `pool_fee_pct` | `1.0` | Pool fee dalam persen (1% = wajar) |
| `pool_diff` | Auto | Pool difficulty awal (auto-adjust per worker) |
| `diff_target_time` | `10` detik | Target: 1 share per worker per 10 detik |
| `min_payout_sats` | `100,000,000` | Minimum 1 ORI untuk payout |
| `payout_interval` | `100` blok | Bayar setiap 100 blok (saat coinbase mature) |
| `mature_confirms` | `100` | Sesuai `coinbase_maturity` di konsensus ORI |

---

## 10. Estimasi Ukuran N (PPLNS Window)

Untuk menentukan N yang tepat:

```
Target: Window mencakup sekitar 2x waktu rata-rata menemukan 1 blok

Jika pool hashrate = 1 MH/s, difficulty node = 100:
  Expected time per block = difficulty * 2^32 / hashrate
                          = 100 * 4,294,967,296 / 1,000,000
                          = 429,497 detik ≈ 5 hari

Jika pool difficulty = 0.001 (pool target):
  Shares per second = hashrate / (difficulty * 2^32)
                    = 1,000,000 / (0.001 * 4,294,967,296)
                    = ~0.23 shares/detik

Untuk window = 2 blok:
  N = 0.23 * (2 * 429,497) = ~197,769 shares

→ N = 1,000,000 adalah konservatif yang baik untuk pool ukuran kecil-menengah
```

---

## 11. Langkah Implementasi (Roadmap)

- [ ] **Fase 1** — Database & Core Logic
  - [ ] `pool_db.py` — SQLite schema, insert/query helpers
  - [ ] `pool_pplns.py` — calculate_pplns(), share_difficulty()

- [ ] **Fase 2** — Pool Server API
  - [ ] `pool_server.py` — FastAPI: /job, /share, /stats, /miner/:addr, /blocks
  - [ ] Job Manager — poll node template, broadcast ke workers
  - [ ] Share Validator — verify SHA256d, simpan ke DB

- [ ] **Fase 3** — Payout Engine
  - [ ] `pool_payer.py` — cek coinbase mature, hitung PPLNS, kirim TX
  - [ ] Wallet pool — manage pool_address private key untuk sign TX

- [ ] **Fase 4** — Worker Client
  - [ ] `pool_miner_client.py` — modifikasi miner.py dengan flag `--pool`
  - [ ] Update `miner-ori.exe` dengan mode pool

- [ ] **Fase 5** — Dashboard (Opsional)
  - [ ] Halaman web stats pool (hashrate, workers online, blok terakhir, payout history)
