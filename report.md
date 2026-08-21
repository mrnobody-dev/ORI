# Laporan Audit Keamanan & Bug — ORI Blockchain Node

**Tanggal:** 2026-08-19 (audit) · **Perbaikan:** 2026-08-20  
**Cakupan:** Seluruh modul inti (`chain`, `tx`, `crypto`, `block`, `utxo`, `mempool`, `node`, `p2p`, `api`, `storage`, `wallet`, `miner`, `seeder`, `pow`, `dns`, `qt`)  
**Metode:** Static code review (analisis alur konsensus, validasi transaksi, jaringan P2P, API REST, wallet)

---

## Status perbaikan (2026-08-20 / 2026-08-21)

Sesi 1: perbaikan tanpa mengubah rumus reward/merkle/PoW/format tx — penegakan aturan yang sudah ada di miner/template, plus proteksi relay/jaringan.
**Sesi 2 (sore):** Satu perubahan **konsensus** — retarget difficulty dari tiap-blok (ORI-Shield) menjadi **per 60 blok** (lihat C-08). Chain lama hasil mining dengan rule lama **tidak lagi valid**; data node perlu di-reset.

| ID | Status | Catatan |
|----|--------|---------|
| C-01 | Diperbaiki | `add_block` / reorg / `validate_full` menolak `bits` yang tidak sama dengan `expected_bits()` (window dihitung dari lineage parent). |
| C-07 | Diperbaiki | **H-01 dikerjakan:** `Mempool.overlay_utxo()` — view UTXO = chain + output mempool − input yang sudah dipakai mempool. `_accept_mempool_tx` & `_revalidate_mempool` memakai view ini → kirim beruntun (child spends change parent di mempool) tidak lagi ditolak "spends nonexistent utxo". |
| C-08 | Diperbaiki (**konsensus**) | Difficulty **retarget per 60 blok**: `ori_retarget_next_bits()` (window 60 baris, median-of-5 Digishield, clamp [1/4, 4], cap max_target); `expected_bits()` → `parent.bits` di semua height selain kelipatan 60. Param `retarget_interval` / env `BTPY_RETARGET_INTERVAL`. |
| C-02 | Diperbaiki | `locktime` ditegakkan di `validate_tx` (height vs unix, threshold 500000000). Tx lama dengan `locktime=0` tetap valid. |
| C-03 | Diperbaiki | `add_block` + mutasi mempool di bawah `node._lock` yang sama dengan submit tx. |
| C-04 | Diperbaiki | Token `BTPY_API_TOKEN` / `api_token`. Kosong tetap boleh untuk localhost; jika API bind public, endpoint mutasi fail-closed `403` sampai token diset (`require_api_token_when_public=1`). |
| C-05 | Diperbaiki fase 1 | Token bucket per-peer, byte/minute guard, subnet throttle, outbound subnet diversity, anchor sederhana, ban score persisten, addr relay filtering, dan log reject path. Masih perlu addrman/asmap/feeler/eviction matang sebelum mainnet besar. |
| C-06 | Diperbaiki | `POST /register` ditutup kecuali `ORI_SEEDER_TOKEN` di-set. Discovery tetap via P2P scan. |
| H-01 | Diperbaiki | Lihat C-07. |
| H-02 | Diperbaiki | Duplicate `txid` dalam satu block ditolak. |
| H-03 | Relay only | Alamat Bech32 invalid ditolak di mempool, **bukan** hard-fork konsensus. |
| H-04 | Diperbaiki | `Mempool(max_txs=cfg.max_mempool_txs)`. |
| H-05 | Diperbaiki | Path P2P memakai batas ukuran tx yang sama. |
| H-06 | Diperbaiki | RBF peer memakai path `_accept_mempool_tx` yang sama. |
| H-08 | Diperbaiki | `validate_full` cek `bits` vs retarget rule. |
| H-09 | Diperbaiki fase 1 | Ancestor/descendant mempool benar-benar dihitung dari input kandidat; chain >25 tx ditolak; RBF tx dengan descendant ditolak konservatif untuk mengurangi replacement cycling. |
| M-03 | Diperbaiki | Cache `invalid_blocks` di-cap 10_000. |
| M-08 | Diperbaiki | Tx tanpa input ditolak. |
| M-10 | Diperbaiki fase 1 | Headers-first runtime path diperbaiki: `BlockHeader.to_hex/from_hex`, `Block.from_bytes`, hash header pakai `hexstr()` konsisten dengan storage; header batch wajib connect ke requested locator dan `bits == expected_bits()`. |
| M-11 | Diperbaiki aman | AssumeValid default off; hanya skip script jika hash hardcode terbukti pada main-chain/header chain dengan burial depth. Ini bukan checkpoint konsensus. |
| D-01 | Baru | **wallet.dat** — container binary (`ORIWLT01`, crc32 payload, tulis atomik temp+`os.replace`+fsync) pengganti `wallet.json`; deteksi korupsi 0-byte; migrasi otomatis dari `wallet.json` (file lama dipertahankan sebagai cadangan); enkripsi AES-256-GCM dengan flag di header; `wallet_is_encrypted()` untuk deteksi tanpa dekripsi. |
| D-02 | Baru | **GUI API fallback port** — `NodeController._start_api` memindai port kosong kalau `api_port` sibuk, `cfg.api_port` di-update, URL `/docs` tampil di halaman Overview (`api_url` di snapshot). |

Tidak diubah (agar tidak mengubah kemurnian / hard-fork): dust limit dan validasi alamat di dalam block historis.

---

## Ringkasan Eksekutif

| Severity | Jumlah |
|----------|--------|
| 🔴 Kritis | 6 |
| 🟠 Tinggi | 8 |
| 🟡 Sedang | 9 |
| 🔵 Rendah / Info | 7 |

Node ORI memiliki fondasi PoW + UTXO + Bech32 yang cukup lengkap, namun ditemukan **celah konsensus** (validasi difficulty & locktime), **race condition** pada mempool, dan **permukaan serangan jaringan** (API/P2P/seeder tanpa autentikasi) yang harus segera ditangani sebelum mainnet atau exposure publik.

---

## 🔴 Bug & Celah Kritis

### C-01 — Difficulty (`bits`) tidak divalidasi saat menerima block

**Lokasi:** `chain.py` → `add_block()`, `validate_full()`  
**Deskripsi:** Block hanya dicek `hash_meets_target(block.hash(), block.header.bits)`, tetapi **tidak pernah** dibandingkan dengan `next_bits()` yang dihitung dari riwayat parent chain. Penyerang dapat men-submit block dengan `bits` yang lebih mudah (target lebih besar) selama hash memenuhi target tersebut.

**Dampak:** Pelanggaran aturan konsensus ORI-Shield; miner jahat dapat mempercepat produksi block di fork, merusak total-work comparison, dan berpotensi reorg chain yang sah.

**Reproduksi konseptual:**
1. Ambil template block dengan `bits` yang benar.
2. Ubah header `bits` ke nilai lebih mudah, re-mine nonce.
3. Submit via P2P atau `POST /mining/submit` → block diterima jika parent valid.

**Perbaikan yang disarankan:**
```python
expected_bits = self.next_bits_for_height(height, parent)
if block.header.bits != expected_bits:
    return False, "incorrect difficulty bits", None
```

---

### C-02 — `locktime` transaksi tidak pernah divalidasi

**Lokasi:** `chain.py` → `validate_tx()`, `_apply_block_to_utxo()`  
**Deskripsi:** Field `locktime` di-parse dan diserialisasi (`tx.py`) tetapi **tidak pernah** dicek terhadap block height atau waktu saat validasi maupun saat block diterapkan.

**Dampak:** Transaksi yang seharusnya terkunci (timelock) dapat dimasukkan mempool dan di-mine segera — pelanggaran semantik transaksi dan potensi bypass skenario escrow/time-delayed payment.

**Perbaikan yang disarankan:** Tolak transaksi jika `locktime > height` (BIP-113 style) atau `locktime > block.timestamp` (jika locktime ≥ 500000000), baik di mempool maupun saat apply block.

---

### C-03 — Race condition: mempool menerima double-spend UTXO yang sudah ter-confirmed

**Lokasi:** `node.py` → `on_peer_block_hex()`, `submit_raw_block()` vs `submit_raw_tx()`

**Deskripsi:**
- `submit_raw_tx()` memegang `node._lock` saat validate + mempool add.
- `on_peer_block_hex()` dan `submit_raw_block()` **tidak** memegang `node._lock` saat `chain.add_block()` mengubah UTXO set.
- `validate_tx()` tidak memegang `chain._lock` dan menerima referensi UTXO snapshot di awal panggilan.

**Alur race:**
1. Thread A: `submit_raw_tx()` mulai validasi terhadap UTXO lama (UTXO X masih unspent).
2. Thread B: block baru datang, spends UTXO X, UTXO set diperbarui.
3. Thread A: validasi sudah lulus, `mempool.add()` sukses → **double-spend** UTXO X ada di mempool padahal sudah ter-confirmed.

**Dampak:** Mempool berisi transaksi invalid; miner dapat membangun block invalid; kebingungan wallet/explorer; potensi split jika miner tidak re-validate ketat.

**Perbaikan:** Pegang satu lock global (`node._lock`) di semua path yang mengubah UTXO **atau** mempool; validasi mempool harus cek `_inputs` **dan** confirmed UTXO atomically.

---

### C-04 — REST API tanpa autentikasi (block/tx submission, mining, peer control)

**Lokasi:** `api.py` — endpoint `POST /tx/`, `POST /mining/submit`, `GET /mining/template`, `POST /network/addpeer`

**Deskripsi:** Tidak ada API key, JWT, mTLS, atau IP allowlist. Default bind `127.0.0.1` (`config.json`), tetapi `Config`/`orid` mendukung `0.0.0.0` (lihat `tests/test_server_bind.py`).

**Dampak jika API exposed:**
- Submit transaksi / block arbitrer
- Ambil mining template & submit block (mining remote abuse)
- Paksa node connect ke peer attacker (`/network/addpeer`) → eclipse attack
- DoS via mempool flooding (`POST /tx/`)

**Perbaikan:** Minimal: bind localhost-only by default + dokumentasi; production: auth token, rate limiting, firewall.

---

### C-05 — P2P protocol tanpa autentikasi & proteksi DoS terbatas

**Lokasi:** `p2p.py` → `Peer`, `Network`

**Deskripsi:**
- Siapa saja dapat connect ke port P2P (`0.0.0.0:8033`).
- Tidak ada ban score, rate limit per peer, atau batas inv/getdata.
- `learn_peers()` auto-connect ke peer yang dilaporkan — vektor **peer poisoning**.
- `reply_blocks()` dapat mengirim hingga 500 block hash per request tanpa throttling.

**Dampak:** Eclipse attack, Sybil flooding, resource exhaustion (CPU/mem/bandwidth), propagasi peer list palsu.

---

### C-06 — Seeder HTTP `/register` tanpa autentikasi

**Lokasi:** `seeder.py` → `do_POST /register`

**Deskripsi:** Endpoint publik di `0.0.0.0` menerima `{host, port}` dan menambahkan ke daftar DNS seed tanpa verifikasi.

**Dampak:** Penyerang mendaftarkan IP/port jahat → node baru diarahkan ke attacker via DNS seed → **eclipse / partition attack** pada jaringan.

---

## 🟠 Bug Tinggi

### H-01 — Mempool hanya validasi terhadap UTXO confirmed, bukan mempool UTXO

**Lokasi:** `node.py` → `submit_raw_tx()`, `on_peer_tx_hex()`  
**Deskripsi:** `validate_tx(tx, self.chain.utxo, ...)` tidak memperhitungkan output dari transaksi unconfirmed di mempool.

**Dampak:** Transaksi child (CPFP) yang spend change dari parent mempool **selalu ditolak** (`spends nonexistent utxo`). Bukan exploit, tapi fungsionalitas RBF/CPFP rusak; wallet bump-fee gagal untuk chain mempool.

---

### H-02 — Tidak ada pengecekan duplicate `txid` dalam satu block

**Lokasi:** `chain.py` → `_apply_block_to_utxo()`

**Deskripsi:** Block dengan dua transaksi identik (txid sama) tidak ditolak eksplisit. Transaksi kedua akan gagal saat spend UTXO, tetapi block seharusnya ditolak lebih awal.

**Dampak:** Block invalid dapat lolos ke tahap apply; edge case merkle/validation inconsistency.

---

### H-03 — Output address (`script_pubkey`) tidak divalidasi

**Lokasi:** `chain.py` → `validate_tx()`, `utxo.py` → `add_tx()`

**Deskripsi:** Output disimpan sebagai `txout.script_pubkey.decode()` tanpa `validate_address()`. Transaksi dapat mengirim ke string arbitrer/non-Bech32.

**Dampak:** Coin **permanently unspendable** (burned) jika script bukan alamat ori1 valid; tidak ada di level protocol tapi berisiko kehilangan dana user/wallet bug.

---

### H-04 — `max_mempool_txs` di config tidak digunakan

**Lokasi:** `config.py` (defined) vs `node.py` → `Mempool()` (hardcoded default 100_000)

**Deskripsi:** `Config.max_mempool_txs` tidak pernah diteruskan ke `Mempool(max_txs=...)`.

**Dampak:** Operator tidak dapat membatasi mempool via config; DoS memory lebih mudah.

---

### H-05 — `on_peer_tx_hex()` tidak cek ukuran transaksi eksplisit

**Lokasi:** `node.py` → `on_peer_tx_hex()`

**Deskripsi:** `submit_raw_tx()` cek `len(tx_hex) > max_block_bytes * 2`, tetapi path P2P langsung parse hex tanpa cek ukuran awal.

**Dampak:** Bergantung sepenuhnya pada `max_msg_bytes` (4 MB); transaksi sangat besar dapat stress parsing/memory.

---

### H-06 — RBF hanya di path API lokal, tidak di path P2P

**Lokasi:** `node.py` → `submit_raw_tx()` vs `on_peer_tx_hex()`

**Deskripsi:** RBF/replace logic hanya ada di `submit_raw_tx()`. Transaksi replacement dari peer silently dropped oleh `mempool.add()`.

**Dampak:** RBF tidak interoperable antar node; fee bump dari peer tidak propagate.

---

### H-07 — Private key wallet disimpan plaintext (default)

**Lokasi:** `wallet.py` → `create_account()`, `save_wallet()`

**Deskripsi:** Enkripsi AES-256-GCM tersedia, tetapi wallet baru disimpan plaintext kecuali user set passphrase. CLI `new` menampilkan private key ke stdout.

**Dampak:** Kompromi file system = kehilangan total dana; shoulder surfing saat `wallet.py new`.

---

### H-08 — `validate_full()` tidak cek aturan difficulty retarget

**Lokasi:** `node.py` → `validate_full()`

**Deskripsi:** Full chain validation cek PoW vs `block.header.bits` masing-masing block, tapi tidak verifikasi `bits` sesuai `ori_shield_next_bits()`.

**Dampak:** Endpoint `/validate/` dapat return `true` untuk chain dengan difficulty history invalid — false sense of security.

---

## 🟡 Bug Sedang

### M-01 — `ripemd160` fallback ke `sha256[:20]`

**Lokasi:** `utils.py` → `ripemd160()`

Jika OpenSSL/Python tidak support RIPEMD160, address derivation fallback ke SHA256 truncated. Address tidak compatible dengan Bitcoin/ORI standard.

---

### M-02 — Tidak ada dust limit / minimum output value

**Lokasi:** `chain.py` → `validate_tx()`

Output 1 sat dapat dibuat unlimited → UTXO set bloat (storage DoS jangka panjang).

---

### M-03 — Cache `invalid_blocks` tumbuh tanpa batas

**Lokasi:** `chain.py` → `self.invalid_blocks = set()`

Hash block invalid di-cache selamanya di memori. Attacker dapat flood invalid block hash → memory leak jangka panjang.

---

### M-04 — Coinbase height opsional

**Lokasi:** `chain.py` → `_apply_block_to_utxo()`, `tx.py` → `coinbase_height()`

Jika coinbase script tidak encode height dengan benar, `coinbase_height()` return `None` dan check dilewati. Block tanpa height commitment diterima.

---

### M-05 — Tidak ada batas jumlah transaksi per block (selain byte limit)

**Lokasi:** `block.py` → `parse()`, `chain.py` → `add_block()`

Varint `n_tx` dapat sangat besar → parsing loop panjang sebelum gagal di size check (DoS parsing).

---

### M-06 — DNS seed resolution tanpa verifikasi

**Lokasi:** `dns.py`, `node.py` → `_seed_from_dns()`

Response DNS UDP diterima tanpa DNSSEC/autentikasi. MITM pada jaringan lokal dapat redirect ke node jahat.

---

### M-07 — Side-chain blocks dihapus permanen saat reorg

**Lokasi:** `storage.py` → `delete_from_height()`

Semua block (main + side) di height ≥ fork point di-delete. Tidak ada archival — sulit forensik/debug fork history.

---

### M-08 — Transaksi nol-input non-coinbase tidak ditolak eksplisit

**Lokasi:** `chain.py` → `validate_tx()`

Tx dengan `inputs=[]` dan outputs semua 0 lolos validasi (fee 0). Aneh tapi mostly harmless; should reject explicitly.

---

### M-09 — Qt API server di-start tanpa lifespan/shutdown graceful

**Lokasi:** `qt/controller.py` → `_start_api()`

Uvicorn di daemon thread; shutdown node tidak selalu stop API server dengan bersih.

---

## 🔵 Rendah / Informational

| ID | Temuan | Lokasi |
|----|--------|--------|
| L-01 | CLI `bump-fee` tidak diimplementasi (exit dengan pesan error) | `wallet.py` |
| L-02 | `Mempool()` default 100k, config field ignored | `node.py`, `config.py` |
| L-03 | P2P `reply_blocks` kirim dummy `tx hash "0"` jika empty — aneh tapi harmless | `p2p.py:336` |
| L-04 | `GENESIS_TIMESTAMP` hardcoded future (1784610000 ≈ 2026+) — intentional? | `chain.py` |
| L-05 | Test regresi masih tipis, tetapi sudah ada hardening tests untuk API public-bind, header serialization, dan mempool ancestor limit | `tests/` |
| L-06 | `wallet.json` / `chain.db-wal` tracked in git status — risk commit secrets/data. **D-01**: wallet sekarang `wallet.dat` (.gitignore diperbarui: `wallet.dat`, `wallet.dat.tmp`, `wallet.json.bak`) | repo root |
| L-07 | High-S signature masih valid sebelum `low_s_activation_height` (53) — known tradeoff | `chain.py` |

---

## Matriks Komponen vs Temuan

| Komponen | Kritis | Tinggi | Sedang |
|----------|--------|--------|--------|
| Konsensus (`chain`, `pow`, `block`) | C-01, C-02 | H-02, H-03, H-08 | M-04, M-05 |
| Node/Mempool (`node`, `mempool`) | C-03 | H-01, H-04, H-05, H-06 | M-08 |
| Jaringan (`p2p`, `dns`, `seeder`) | C-05, C-06 | — | M-06 |
| API (`api`) | C-04 | — | — |
| Wallet/Crypto (`wallet`, `crypto`) | — | H-07 | M-01 |
| Storage (`storage`, `utxo`) | — | — | M-03, M-07 |

---

## Prioritas Perbaikan (Rekomendasi)

### Sprint 1 — Konsensus (blocker mainnet)
1. **C-01** Validasi `bits` == expected pada `add_block()` dan `validate_full()`
2. **C-02** Enforce `locktime` di `validate_tx()` dan block apply
3. **C-03** Unified locking: semua mutasi UTXO + mempool under satu mutex
4. **H-02** Reject duplicate txid dalam block

### Sprint 2 — Keamanan Jaringan
5. **C-04** API auth + rate limit; default deny non-localhost
6. **C-05** P2P ban score, rate limits, peer verification
7. **C-06** Seeder register auth atau proof-of-uptime

### Sprint 3 — Robustness
8. **H-01** Mempool UTXO view untuk CPFP/RBF
9. **H-03** Validasi Bech32 pada output
10. **H-04** Wire `max_mempool_txs` dari config
11. **M-02** Dust limit
12. **M-03** LRU cap untuk `invalid_blocks`

---

## Checklist Verifikasi Pasca-Fix

Verifikasi runtime (soak test, 2026-08-20):
- [x] Chain 300+ blok hasil mining ulang divalidasi penuh: `GET /validate/` → `valid: true` (rule difficulty per 60 OK)
- [x] Kirim beruntun alice→bob: 1 ORI tier2 → 1 ORI tier2 → 2 ORI tier1, semuanya diterima (H-01 CPFP fix); bob menerima tepat 4 ORI setelah konfirmasi
- [x] RBF: submit tier5 lalu replace tier1 → status `replaced`, tx lama hilang dari mempool; double-spend tanpa sinyal RBF → 400
- [x] `GET /tx/{malformed}` → 404; raw tx malformed → 400
- [x] Reorg: fork 2 blok di node2 vs 3 blok di node1 → node2 reorg ke chain node1, kedua node `valid: true`
- [x] Relay P2P: 3 tx mempool identik di kedua node; addpeer dua arah
- [x] GUI (offscreen): boot dengan `wallet.dat`, API fallback ke port kosong (8002), `GET /docs` → 200
- [x] wallet.dat: round-trip, deteksi korupsi crc32, migrasi `wallet.json` → `wallet.dat` (backup dipertahankan)
- [ ] Block dengan `bits` salah ditolak meskipun PoW valid (unit test)
- [x] `/validate/` mendeteksi chain dengan difficulty history invalid
- [x] API mutasi public-bind tanpa token ditolak `403` (`api_host=0.0.0.0`)
- [x] Header hex round-trip 80 byte (`BlockHeader.to_hex/from_hex`)
- [x] Mempool menolak chain ancestor yang melewati `MAX_ANCESTORS`
- [x] `python -m py_compile utils.py block.py tx.py p2p.py node.py chain.py mempool.py api.py config.py`
- [x] `python -m pytest -q` -> 6 passed

---

## Catatan Metodologi

Audit ini berbasis **static analysis** (code review). Temuan C-03 (race condition) dan C-01 (difficulty bypass) direkomendasikan untuk verifikasi runtime dengan test konkuren dan block submission test sebelum fix di-deploy.

**File yang paling perlu perhatian segera:** `chain.py`, `node.py`, `api.py`, `p2p.py`, `seeder.py`

---

*Generated by security audit — ORI Blockchain FastAPI codebase.*
