# ORI — build.md

Catatan proses pembangunan blockchain ORI (berbasis "bitpy") di Python 3. Dokumen ini
adalah sumber kebenaran: keputusan arsitektur, peta file, protokol, cara menjalankan,
hasil verifikasi, dan pekerjaan yang masih terbuka.

## 0. Spesifikasi ORI (v0.2, migrasi dari bitpy)

| Parameter | Nilai | Keterangan |
|---|---|---|
| Coin | ORI | Bech32 native SegWit, HRP `ori` (contoh `ori1q...`) |
| Total supply | 194.600.000 ORI (19.460.000.000.000.000 sat) | MAX_MONEY |
| Block time | 60 detik | |
| Block reward era 0 | 46,28 ORI = 4.628.000.000 sat | |
| Halving | setiap 2.102.400 blok (~4 tahun), floor integer | tabel era 0..32 di bawah |
| PoW | Py-ORI: SHA-256d header 80 byte, CPU-friendly | node tidak mining |
| Difficulty | Retarget **per 60 blok** (Bitcoin-style window dengan Digishield dampening): `ori_retarget_next_bits()` — window 60 baris lineage parent, timespan = median 5 timestamp pertama vs median 5 terakhir, clamp [1/4, 4], cap max_target; height lain memakai `parent.bits` persis; retarget terjadi di height kelipatan 60 (60, 120, …) | `retarget_interval=60` / `BTPY_RETARGET_INTERVAL`. **Perubahan konsensus 2026-08-20** (sebelumnya ORI-Shield retarget tiap blok) → chain lama perlu reset |
| Premine | Tidak ada: coinbase genesis ke program 20-byte zero (unspendable) | |
| Toleransi waktu | FTL 60 detik ke depan; **anti time-warp**: ts blok harus > median 11 blok terakhir (MTP); cek "terlalu tua" dihapus agar sync catch-up tidak macet | audit 2026-08-17 |
| Header indexing | SHA-256d | |
| Fee tier | 5 tier target konfirmasi (1-5 blok): 1.4 / 0.7 / 0.46 / 0.35 / 0.28 sat/vB, fee = rate × ukuran tx (sats), hanya `--tier 1..5` (tanpa custom fee); min relay 0.28 sat/vB wajib, mempool urut by fee rate | `wallet.py send --tier <1..5>` |
| Low-S (BIP-146) | WAJIB sejak height 53 (soft-fork: chain lama di-grandfather) | `low_s_activation_height` |
| Maturity coinbase | 100 blok (Bitcoin-like). Soft-fork: berlaku untuk coinbase height >= 100 (`coinbase_maturity_activation_height`), chain lama tetap valid | `BTPY_COINBASE_MATURITY` |

Subsidy tiap era (sat): era n = `4_628_000_000 >> n` untuk n=0..32; total seluruh era
= 19.459.814.370.566.400 sat ~ 194.598.143,71 ORI (delta 1.856,29 ORI dari
MAX_MONEY karena floor integer — sesuai hitungan spesifikasi).

Migrasi kode: `genesis` deterministik baru (address no-premine), address base58check
diganti Bech32 (`bech32.py` baru; script_pubkey tetap menyimpan string address),
difficulty `ori_retarget_next_bits()` per 60 blok (pengganti `ori_shield_next_bits` per blok),
fungsi base58 dihapus total dari
`utils.py` (audit), config menambah `fee_tiers_per_vb` (5 tier 0.28-1.4 sat/vB + `min_relay_fee_per_vb`),
`max_money_sats`, `shield_window`, `retarget_interval`, `low_s_activation_height`, seed DNS (`seed_dns_*`).

---

## 1. Konsep Dasar (sesuai permintaan)

| Permintaan | Implementasi |
|---|---|
| Blockchain sungguhan ala Bitcoin | UTXO model, proof-of-work, target/bits compact, difficulty retarget, block reward + halving, coinbase tx, merkle root |
| Node tidak bisa mining | Node hanya: validasi, simpan, relay, sinkronisasi. Kode PoW TIDAK ada di node — hanya ada di `miner.py` |
| Mining terpisah dari node | Miner = proses Python terpisah (`miner.py`), dapat template dari node via API, melakukan PoW, submit blok |
| Connect antar nodes | P2P TCP dengan framing ala Bitcoin (magic + command + length), handshake version/verack, inv/getdata/getblocks/getheaders, relay tx & blok, headers-first sync fase 1, fork + reorg |

## 2. Arsitektur & Peta File

```
blockchain-fastapi/
├── main.py          entry FastAPI (uvicorn main:app)
├── config.py        Config dataclass + env BTPY_*
├── utils.py         varint, sha256d, hexstr (display order ala Bitcoin)
├── crypto.py        ECDSA secp256k1 (kompresi pubkey, RFC6979 deterministic sign, low-S)
├── tx.py            Transaction, TxIn, TxOut, coinbase, sighash
├── merkle.py        Merkle root (double-SHA256, duplikasi node ganjil)
├── block.py         BlockHeader + Block, serialisasi/parse
├── pow.py           compact bits -> target, retarget, block_work, hash_meets_target
├── utxo.py          UTXO set (txid+vout -> address, value, height)
├── storage.py       SQLite: blok main chain + side branch (fork), meta tip
├── mempool.py       tx pool, ordering by fee, conflict check, cap 100k tx
├── chain.py         Blockchain: validasi penuh, reorg, template mining
├── p2p.py           Jaringan P2P (listener, peer thread, message framing, rate-limit/ban, headers-first)
├── node.py          Node: orchestrator antara chain/mempool/network/API + seed DNS
├── api.py           FastAPI routes (wallet, explorer, mining RPC)
├── miner.py         MINING terpisah: ambil template, PoW, submit
├── wallet.py        CLI wallet: new/list/balance/send
├── dns.py           wire codec DNS minimal (query A + parse/answer) — untuk seeder
├── seeder.py        DNS seeder: UDP DNS server + node P2P scanner + HTTP register
├── test_smoke.py    test unit end-to-end dalam proses
├── test_p2p.py      test sinkronisasi 2 node via P2P
├── test_reorg.py    test fork + reorg 2 node via P2P
└── data/            data runtime (SQLite) — di .gitignore
```

Alur data: `wallet.py send` -> POST /tx/ -> `node.submit_raw_tx` -> validasi ->
mempool -> broadcast inv ke peer. `miner.py` -> GET /mining/template -> PoW ->
POST /mining/submit -> `node.submit_raw_block` -> validasi -> store -> broadcast.
Node lain menerima inv -> getdata -> block/tx -> validasi ulang lokal -> relay.

## 3. Keputusan Desain (sengaja)

- **Byte order hash**: serialisasi memakai byte order digest (bukan little-endian
  seperti Bitcoin asli) — secara fungsi identik, hanya beda representasi. API
  menampilkan `hexstr()` (reverse) ala block explorer Bitcoin.
- **Script pubkey disederhanakan**: output script = string address ASCII (bukan
  opcode P2PKH penuh). Unlock script = `sig(64B) + pubkey_compressed(33B)`.
- **Address**: Bech32 native SegWit v0: `ori1` + witness program 20-byte
  (hash160 pubkey) — lihat `bech32.py`; bukan lagi base58check.
- **Genesis**: deterministik (timestamp tetap dari `chain.GENESIS_TIMESTAMP`,
  pencarian nonce dari 0) — semua node menghasilkan genesis yang identik.
- **Coinbase**: script berisi height (ala BIP34) + bebas catatan; nilai = reward
  (halving) + total fee. Genesis coinbase ke address program 20-byte zero (tidak
  bisa dibelanjakan = no premine). Reward 4.628.000.000 sats.
- **Maturity coinbase**: 100 blok, diaktifkan soft-fork di height 100 (coinbase
  yang ditambang sebelum height 100 di-grandfather) — ala Bitcoin; reward yang
  belum mature tampil sebagai `immature_sats` dan tidak bisa dipakai `wallet.py
  send` (utxo `mature: false` dilewati).
- **Difficulty**: ORI-Shield (Digishield) sejak genesis — retarget SETIAP blok,
  window 11 blok, median 5 selisih waktu, clamp [1/3, 3x], dampening 1/4.
- **Fork/reorg**: blok di sisi yang kalah disimpan sebagai *side branch* (kolom
  `main=0`). Reorg hanya terjadi bila kandidat punya *total work* lebih besar
  (cumulative chain work, bukan panjang saja).
- **P2P protocol v1**: framing `magic(4) + command(12) + length(4) + payload`,
  payload JSON (blok/tx sebagai hex) — realistis secara framing, pragmatis isinya.
  Version dikirim DUA ARAH (inbound maupun outbound) — seperti Bitcoin, supaya
  kedua sisi tahu height/tip lawan.
- **Tidak ada checkpoint, tidak ada OP_RETURN, tidak ada SegWit** (TODO).
- Node **tidak pernah** mencari nonce — satu-satunya PoW di node adalah pencarian
  nonce genesis sekali saat inisialisasi (deterministik, untuk semua node sama).

## 4. Protokol P2P (pesan)

| Pesan | Arah | Isi |
|---|---|---|
| version | dua arah (inbound + outbound) | protocol version, port, height, best_hash, ua |
| verack | dua arah | kosong |
| ping/pong | dua arah | nonce |
| addr | dua arah | daftar peer dikenal |
| getblocks | permintaan | `from` (hash tip/prev kita), `stop` |
| getheaders | permintaan | `from`, `stop`, `count` (dibatasi 2000) |
| headers | balasan | list header 80-byte hex, PoW-valid dan chain-contiguous |
| inv | balasan | daftar hash blok (max 500, atau 3 hash terakhir bila `from` tak dikenal) |
| getdata | permintaan | daftar item {type: block/tx, hash} |
| block | kirim | blok mentah hex |
| tx | kirim | tx mentah hex |

Alur sinkronisasi: version -> kalau height peer > height kita ATAU tip berbeda ->
getblocks -> inv -> getdata -> block -> validasi -> kalau masih di belakang,
getblocks lagi. Blok dengan parent tak dikenal (datang duluan / gap): node meminta
**parent-nya langsung via getdata** (`pending_children`) sehingga rantai tersambung
mundur; bila parent sudah ada, anak di-request ulang otomatis. Fork yang kalah tetap
disimpan sebagai side branch dan pesan hasilnya diperlakukan sukses (bukan error).

Headers-first fase 1: jika peer jauh lebih tinggi, node meminta `getheaders`,
memvalidasi PoW header dan kontinuitas `prev_hash`, lalu meminta block penuh dari
peer yang tersedia. Hash header selalu dinormalisasi ke format display `hexstr()`
yang sama dengan storage/API agar `getdata` tidak meminta hash byte-order yang salah.

Proteksi P2P fase 1: token bucket per-peer, byte/minute guard, inbound subnet
throttle, outbound subnet diversity, anchor peer sederhana, ban score persisten
(`banned_peers.json`), filtering addr relay non-routable/CGNAT/reserved, dan log
terstruktur untuk reject/ban/framing error. Ini belum setara Bitcoin Core addrman
lengkap/asmap/feeler/eviction scoring, tetapi sudah menutup silent flood paling
kasar untuk pre-mainnet.

Parameter tuning P2P bisa diubah lewat config/env: `BTPY_P2P_MSG_TOKEN_REFILL_RATE`,
`BTPY_P2P_MSG_TOKEN_BUCKET`, `BTPY_P2P_MSG_TOKEN_COST_PER_KB`,
`BTPY_P2P_MAX_BYTES_PER_MINUTE`, `BTPY_P2P_BAN_SCORE_THRESHOLD`,
`BTPY_P2P_BAN_DURATION_HOURS`, `BTPY_P2P_MAX_INBOUND_PER_SUBNET`,
`BTPY_P2P_MAX_OUTBOUND_PER_SUBNET`, `BTPY_P2P_CONNECTION_RATE_LIMIT`, dan
`BTPY_P2P_INBOUND_RATE_LIMIT_PER_SUBNET`.

## 5. API Node (FastAPI)

| Method | Path | Fungsi |
|---|---|---|
| GET | / | info + stats |
| GET | /blockchain/ | seluruh chain + valid flag |
| GET | /block/{height}, /block/hash/{hash} | detail blok |
| GET | /tx/{txid} | tx confirmed / mempool |
| GET | /address/{addr} | balance + utxos |
| GET | /mempool/ | tx pending |
| GET | /peers/ | daftar peer |
| GET | /stats | height, difficulty, work, supply |
| GET | /validate/ | validasi ulang full chain |
| POST | /tx/ {tx: hex} | broadcast transaksi |
| GET | /mining/template?address=... | template untuk miner |
| POST | /mining/submit {block: hex} | submit blok hasil mining |
| POST | /network/addpeer {host, port} | connect ke node lain |

## 6. Cara Menjalankan

```bash
python3 -m venv .venv && source .venv/bin/activate   # di Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Node 1 (default: API 8000, P2P 8033)
uvicorn main:app --reload

# Node 2 (terminal lain, port beda)
BTPY_API_PORT=8001 BTPY_P2P_PORT=8034 uvicorn main:app --port 8001 --reload

# Hubungkan node 2 ke node 1
curl -X POST http://127.0.0.1:8001/network/addpeer -H "Content-Type: application/json" \
  -d '{"host":"127.0.0.1","port":8033}'

# Wallet
python wallet.py new
python wallet.py list
python wallet.py balance ADDRESS --node http://127.0.0.1:8000

# Miner terpisah (proses sendiri, tidak di node)
python miner.py --node http://127.0.0.1:8000 --address ADDRESS --threads 4
# Jika API node dibuka public dan protected endpoint butuh token:
BTPY_API_TOKEN=... python miner.py --node http://HOST:8000 --address ADDRESS --threads 4

# Tuning mining CPU:
python miner.py --node http://127.0.0.1:8000 --address ADDRESS \
  --threads 4 --batch 65536 --kernel auto --refresh 30

# Kirim (dalam sats)
python wallet.py send --node http://127.0.0.1:8000 --from NAMA_WALLET --to ADDRESS --amount 1000000

# DNS SEEDER (bootstrapping tanpa addpeer manual)
# 1) Jalankan seeder di komputer yang akan jadi "DNS seeder" (IP LAN, mis. 192.168.1.14).
#    --bootstrap = node pertama yang dikenalnya, --announce = IP yang diiklankan.
python seeder.py --bootstrap 192.168.1.14:8033 --announce 192.168.1.14 --dns-port 5353

# 2) Node lain (di mesin mana pun) cukup menyebut DNS seeder — TANPA addpeer:
BTPY_SEED_DNS_HOST=192.168.1.14 BTPY_SEED_DNS_NAME=seed.ori BTPY_SEED_DNS_PORT=5353 \
BTPY_SEED_DNS_P2P_PORT=8033 uvicorn main:app --port 8001
#    Node resolve 'seed.ori' via UDP :5353 -> dapat IP node lain -> connect otomatis.

# 3) Cek seeder: query DNS manual / HTTP status
python -c "from dns import resolve_a; print(resolve_a('seed.ori', '127.0.0.1', 5353))"
curl http://127.0.0.1:8123/          # daftar peer yang dikenal seeder
curl -X POST http://127.0.0.1:8123/register -d '{"host":"192.168.1.20","port":8033}'
```

Catatan: port DNS default 5353 (port 53 butuh root). `_pick_ips` mengiklankan
3 IP acak dari daftar peer yang dikenal (TTL 60s) sehingga node yang mati cepat
tergantikan. Seeder sendiri adalah full node p2p (port default 8353) sehingga ia
juga belajar peer dari handshake `addr` setiap siklus scan.

Demo cepat dengan difficulty rendah: `BTPY_INITIAL_ZEROS=2` sebelum menjalankan node
dan miner (genesis juga ikut deterministik).

## 7. Verifikasi (sudah dieksekusi, WSL Python 3.12.3 + venv)

- `python test_smoke.py` — PASS: genesis, mining blok, tx broadcast + confirm,
  double-spend ditolak, validasi penuh.
- `python test_p2p.py` — PASS: 2 node connect, sinkronisasi blok, relay tx,
  mempool bersih setelah blok di kedua node.
- `python test_reorg.py` — PASS: fork kompetitif, side branch disimpan, reorg ke
  chain dengan total work lebih besar, balance konsisten setelah reorg.

Bug yang ditemukan & diperbaiki saat membangun (catatan agar tidak kambuh):
1. `pip` Debian: pakai venv (`python3 -m venv .venv`), jangan system pip.
2. `orjson 3.6.2` (requirements lama) gagal build di Python 3.12 — requirements
   diganti minimal: fastapi, uvicorn, ecdsa.
3. `Transaction.serialize` awalnya memakai `sign_input=-1` sebagai default sehingga
   script_sig ikut dikosongkan saat serialisasi penuh — dipecah: `sign_input=None`
   (penuh) vs `-1` (semua kosong untuk sighash).
4. `VerifyingKey.from_string` tidak bisa menerima pubkey terkompresi (33B) — perlu
   decompress point secp256k1 (`y = (x³+7)^((p+1)/4)`) sebelum from_string.
5. `connect()` menolak peer karena key sudah masuk set `known` dari `learn_peers` —
   dipisah pelacakan outbound aktif.
6. Fork lemah tadinya tidak disimpan, sehingga blok lanjutan fork tidak bisa
   diterima (orphan) — side branch disimpan dengan flag `main`.
7. `peer.height` basi dari handshake menyebabkan sinkronisasi berhenti — direfresh
   saat blok diterima; gap ("unknown parent") selalu memicu getblocks.
8. Jalur "extend tip" hanya mengecek `height == tip+1`, tidak mengecek parent == tip
   — blok di atas side branch bisa "meneruskan" main chain secara tidak konsisten.
   Sekarang wajib `parent["hash"] == tip_hash`, selain itu masuk jalur reorg.
9. Clamp retarget memakai `MAX_TARGET` ala Bitcoin (target 4 nol) padahal difficulty
   awal chain bisa lebih mudah (initial_zeros=2) — retarget meledakkan difficulty
   65.000x. `adjust_bits` sekarang menerima `max_target` dari config chain.
10. Bug kembar #9: `max_target` dan `base` difficulty diisi nilai BITS (compact)
    padahal harus TARGET (int 256-bit) — `target_from_bits(bits_for_zeros(...))`.
11. Race di miner multi-thread: header dibagi antar thread, thread pemenang bisa
    kehilangan nonce-nya sebelum blok dibangun → blok ditolak "proof of work failed".
    Sekarang tiap thread punya header sendiri + hasil disalin via lock.
12. Mempool tidak dipersistensikan — hilang saat node restart (lihat TODO).
13. Genesis disimpan tapi coinbase-nya tidak pernah di-apply ke UTXO set
    (`_bootstrap` lupa `_apply_unchecked`) — node baru kekurangan 1 coinbase
    (50M sats) selamanya, kecuali reorg memicu `_rebuild_state` dari storage
    (menyembuhkan). Gejala: dua node dengan chain & DB identik tapi
    `utxo_count`/`supply_sats` berbeda 1 (node1=59 vs node2=60 di height 59).

Bug audit menyeluruh 2026-08-17 (masalah yang PERNAH dihadapi Bitcoin + temuan lokal):
14. **Time-warp (Bitcoin 2015)**: cek "timestamp too old" (±70s) menolak blok lama
    saat sync ketertinggalan/side branch → diganti aturan MTP: ts blok harus lebih
    besar dari median 11 blok terakhir (height >= 11). FTL 60s tetap.
15. **P2P: inv counter `pending` tidak pernah dikurangi** — setelah 100 item inv,
    node berhenti meminta blok/tx selamanya. Diganti set `peer.requested`
    (max 100) yang di-discard saat item diterima.
16. **P2P: peer inbound tidak pernah kirim version** — lawan bicara yang jadi
    inbound tidak diketahui height/tip-nya → sinkronisasi bisa deadlock (terutama
    bila node lebih tinggi yang inbound). Version sekarang dikirim dua arah.
17. **P2P: fork lemah mengembalikan False padahal blok TERSIMPAN** — `add_block`
    mengembalikan `"weak fork stored as side branch"` dan dianggap error, sehingga
    `pending_children` tidak pernah diproses dan blok lanjutan fork tidak pernah
    di-request ulang → reorg tidak pernah terjadi. Sekarang reason tsb dianggap
    sukses. (Ini juga sebab test_reorg sebelumnya tidak stabil.)
18. **P2P: blok dengan parent tak dikenal tidak pernah di-request parent-nya**
    (looping getblocks tip-tak-dikenal) — sekarang `getdata` parent langsung
    (`pending_children` per peer); saat parent tiba, anak di-request ulang otomatis.
    getblocks request dari hash prev bila perlu.
19. **Malleability (BIP-62/BIP-146)**: wallet lama bisa menghasilkan signature
    high-S (txid bisa berubah di relay) — `crypto.sign` selalu normalisasi low-S;
    `chain.validate_tx` MENOLAK high-S sejak height 53 (soft-fork, chain lama
    di-grandfather via `low_s_activation_height`). Tanpa ini chain live height 37
    yang berisi tx high-S jadi invalid — terdeteksi oleh validate_full baru.
20. **DoS/robustness**: HTTP body blok/tx tidak dibatasi sebelum parse (blok raksasa
    bisa menguras memori) — guard `max_block_bytes` sebelum `from_hex`; mempool di-cap
    100.000 tx; `max_msg_bytes` sudah ada di framing P2P.
21. **Mempool tidak direvalidasi setelah reorg** — tx yang dependen pada blok
    orphan tetap di pool → referensi UTXO basi. Sekarang `_revalidate_mempool()`
    dijalankan setiap reorg.
22. **validate_full lemah** — hanya cek merkle/pow/link. Sekarang replay penuh dari
    genesis: semua tx divalidasi ulang (signature, fee, coinbase, UTXO) dan
    count/supply hasil replay dibandingkan dengan state live. (Ini yang menangkap
    bug #19 di chain live.)

Hardening tambahan 2026-08-21:
23. **Patch logging/P2P hardening**: logger lama diperluas menjadi
    `StructuredLogger` kategori (`p2p`, `sync`, `consensus`, `mempool`,
    `security`, dll.), terminal default human-readable, file `logs/orid.log`
    default JSON + rotasi. P2P reject path sekarang mencatat framing error,
    ban score, inbound throttle, addr filtering, request/reply header, dan block/tx
    peer rejection.
24. **Headers-first runtime bug**: `p2p.py` memakai `BlockHeader.from_hex()`,
    `BlockHeader.to_hex()`, dan `Block.from_bytes()` yang belum ada; selain itu
    hash header dipakai dalam raw digest `.hex()` bukan `hexstr()`. Sekarang method
    tersebut ada di `block.py`, parser header menolak truncation, dan sync header
    memakai hash display yang sama dengan storage. Header batch juga wajib connect
    ke locator yang diminta dan `bits` harus sama dengan `expected_bits()` lokal.
25. **API public bind fail-open**: token kosong tetap diizinkan untuk localhost,
    tetapi jika `api_host` public (`0.0.0.0`, `::`, atau IP non-loopback) maka
    endpoint mutasi yang protected (`/tx/`, `/mining/*`, `/network/addpeer`) return
    `403 api token required for public bind` sampai `BTPY_API_TOKEN` diset.
26. **Mempool ancestor limit semu**: implementasi ancestor/descendant awal menghitung
    ancestor dari txid kandidat sebelum tx masuk `_txs`, sehingga chain unconfirmed
    panjang dapat lolos. Sekarang ancestor dihitung dari input kandidat, limit
    25 ancestor/descendant dan 101 kB family size ditegakkan; RBF konservatif
    menolak replace tx yang sudah punya descendant untuk mengurangi replacement
    cycling/orphan descendant sebelum package relay matang.
27. **AssumeValid fail-safe**: default `assume_valid_block=""` dan height `0`
    (off). Script skip hanya aktif bila hash hardcode ada di main-chain pada height
    yang dikonfigurasi dan buried minimal `assume_valid_min_depth` (default 1440
    blok = sekitar 24 jam), atau header chain PoW-contiguous memverifikasi burial
    tersebut. Jika hash tidak cocok, node validasi penuh; ini bukan checkpoint
    konsensus dan tidak memaksa chain.
28. **Miner CPU optimization 2026-08-21**: `miner.py` diubah ke multiprocessing
    worker dengan pembagian nonce chunk contiguous (`--batch`, default 65.536),
    shared counter `RawArray` tanpa stats queue, result queue hanya untuk nonce
    pemenang, target compare langsung `digest <= target_bytes`, dan dua kernel
    hashing portable: `full` (mutasi bytearray header 80 byte + `pack_into`) dan
    `midstate` (`hashlib.sha256().copy()` setelah static 76 byte). Mode `auto`
    benchmark singkat per template dan memilih kernel tercepat di mesin lokal.
    Miner juga mendukung `--api-token` / `BTPY_API_TOKEN` untuk endpoint mining
    yang protected saat API bind public.

Verifikasi tambahan (live, HTTP sungguhan via uvicorn di WSL):
- `miner.py` terhadap node live: 46 blok berturut, difficulty naik 16x oleh retarget
  (bukti retarget bekerja), lalu konfirmasi.
- `wallet.py send` -> mempool -> blok `txs 1` -> balance penerima bertambah,
  mempool kosong, tx lookup menampilkan block_hash. Semua PASS.
- **Audit 2026-08-17**: semua test PASS berulang (smoke, p2p, reorg x3 — reorg kini
  stabil), `GET /validate/` True di dua node live (replay penuh cocok dengan state
  UTXO: 54 utxo / 245.284.000.000 sat di height 52), kirim tx tier 5 berhasil dan
  relay P2P ke node2.
- **DNS seeder live**: `resolve_a('seed.ori')` → 3 IP; nama tak dikenal dapat
  NOERROR kosong (tidak timeout); node2 dibersihkan (tanpa `--seed-peers` dan tanpa
  `addpeer`) dengan `BTPY_SEED_DNS_HOST=127.0.0.1` langsung terhubung ke node1 dan
  sinkron (52/52, best_hash sama).
- **Maturity + coinbase display live**: spend coinbase immature ditolak
  (`"coinbase output not mature"`), `immature_balance`/`spendable` terpisah benar,
  `/tx/` coinbase menampilkan `coinbase: true, message: "new generated coin",
  pkscript/value/address: null`, utxo berflag `coinbase`/`mature`.
- **Hardening 2026-08-21**: `python -m py_compile utils.py block.py tx.py p2p.py
  node.py chain.py mempool.py api.py config.py` PASS; `python -m pytest -q` PASS
  (9 tests). Test baru mencakup public API mutation fail-closed, header hex
  round-trip 80 byte, rejection chain mempool yang melewati `MAX_ANCESTORS`, dan
  rejection header batch yang tidak connect ke requested locator.
- **Miner optimization 2026-08-21**: `python -m py_compile miner.py` PASS; smoke
  multiprocessing lokal menemukan block valid pada target mudah; test regresi
  miner mencakup block valid, API token header, dan external cancel.

## 8. TODO / Pekerjaan Berikutnya

- [ ] Persistensi mempool (sekarang hilang saat restart node)
- [ ] Peers.json persistent + reconnect berkala (saat ini setelah restart node,
      koneksi harus di-ulang via addpeer atau seed DNS)
- [x] Rate limiting / ban peer fase 1 (token bucket, subnet throttle, ban score,
      banlist disk, logging reject path). Lanjutan: addrman/asmap/feeler/eviction.
- [x] Headers-first sync fase 1 (`getheaders`/`headers`, header PoW chain check,
      block request by normalized hash). Lanjutan: persistent header index,
      chainwork headers, stall timeout, parallel download window.
- [ ] UTXO index query cepat untuk explorer (saat ini scan seluruh set)
- [ ] Test deterministik regresi CI (pytest)
- [ ] DNS seeder: dukungan TCP DNS (RFC 1035) untuk response >512B; drain node mati
      dengan probing berkala sebelum mengiklankan
