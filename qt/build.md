# ORI Core Qt — build.md (GUI PySide6)

Catatan pembangunan GUI **ORI Core** gaya Bitcoin-Qt (node penuh + wallet, **tanpa
mining**). Bagian dari proyek ORI — lihat `build.md` di root untuk spesifikasi chain,
P2P, dan wallet CLI. Dokumen ini sumber kebenaran untuk GUI.

## 0. Ringkasan

| Item | Nilai |
|---|---|
| Framework | PySide6 (Qt 6) 6.11.1, widget-based (bukan QML) |
| Entry | `qt_app.py` (import `qt` package) |
| Gaya | Bitcoin Core GUI: splash, sidebar nav (Overview / Send / Receive / Transactions), toolbar, console, peers, address book, options |
| Node | In-process: `NodeController` menjalankan node ORI yang sama dari `main.py` (API uvicorn di localhost) |
| Wallet | Default mengikuti wallet CLI: **`wallet.json`**; loader tetap bisa membaca legacy `wallet.dat` container |
| Mining | TIDAK ada — GUI murni full-node + wallet (sesuai spesifikasi ORI) |
| Theme | "Bitcoin Orange": `#F7931A` aksen, `#F2F1F0` latar, panel putih, teks `#2B2B2B` |
| Test headless | `QT_QPA_PLATFORM=offscreen` dibuktikan berjalan di WSL tanpa X server |
| Belum ada | Logo asli, QR code untuk alamat (catatan di bawah) |

## 1. Struktur

```
blockchain-fastapi/
├── qt_app.py                  entry: QApplication + boot splash + main window
└── qt/                        package GUI (FastAPI & node masuk proses yang sama)
    ├── __init__.py
    ├── controller.py          NodeController — satu-satunya "otak" GUI
    ├── mainwindow.py          MainWindow + menu + toolbar + sidebar + status bar
    ├── overview_page.py       ringkasan: balance, recent tx, network status
    ├── send_page.py           form kirim: jumlah, fee tier 1..5, subtract fee, label
    ├── receive_page.py        alamat baru + daftar permintaan terima
    ├── tx_page.py             daftar transaksi; double-click → TxDetailDialog
    ├── dialogs.py             TxDetailDialog, About, Information, Console, Peers,
    │                          AddressBook, Options
    ├── splash.py              BootThread (QThread) + Splash (QSplashScreen)
    ├── theme.py               palet warna + QSS lengkap
    └── icons.py               ikon (app_icon, nav icons, tx_arrow, dst.)
```

## 2. Requirements

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt          # fastapi, uvicorn, ecdsa, ...
pip install PySide6                      # GUI
```

Note WSL/Linux headless: tidak perlu X server untuk test otomatis (lihat §7).
Untuk pemakaian nyata di Windows, PySide6 jalan langsung (platform qwindows).

## 3. Cara menjalankan

```bash
# wajib: node dijalankan IN-PROCESS oleh GUI (bukan uvicorn terpisah)
.venv/bin/python qt_app.py                      # wallet default wallet.json
.venv/bin/python qt_app.py --datadir data/node1 # override BTPY_DATA_DIR
.venv/bin/python qt_app.py --wallet mywallet.json
```

Alur boot (`qt_app.py`): parse args → QApplication → `Config.from_env()` →
`NodeController(cfg, wallet_path)` → `Splash` → `MainWindow(controller)` →
`BootThread` (start node di QThread agar UI tidak beku) → `on_boot_finished`
(load wallet + timer → `window.show()`).

Server API: `NodeController._start_api` menjalankan uvicorn FastAPI
(`api.create_app` + `main` lifespan) di localhost port `BTPY_API_PORT`
(default 8000). Dengan begitu GUI, CLI wallet, dan browser `/docs` memakai API
yang sama. **Fallback port**: kalau port sibuk (proses lama/uviicorn lain),
`_start_api` memindai port kosong berikutnya dan `cfg.api_port` di-update;
URL `http://127.0.0.1:<port>/docs` ditampilkan di halaman Overview saat bind
public seperti `0.0.0.0`, walaupun server tetap listen sesuai `cfg.api_host`
(key `api_url` di snapshot), sehingga `/docs` selalu bisa diakses dari GUI.

## 4. Arsitektur controller

`NodeController(QObject)` di `qt/controller.py` adalah satu-satunya pihak yang
memegang `Chain/P2P/Mempool/Wallet` — halaman UI TIDAK menyentuh node langsung,
semua lewat controller (alo Bitcoin Core: GUI → validation interface).

Metode utama:

| Method | Fungsi |
|---|---|
| `start_node` / `shutdown` | start & stop node penuh (chain, p2p, api) |
| `load_wallet_and_timers` | buka wallet default/terpilih (deteksi enkripsi via `wallet_is_encrypted`) + aktifkan `QTimer` refresh berkala |
| `_build_snapshot` / `refresh` | snapshot state (balance, mempool, blocks, peers) untuk UI |
| `_scan_history_step` | scan riwayat tx bertahap (stepped, tidak memblokir UI) |
| `_classify_tx` | klasifikasi tx: received/sent/self, confirmed/pending/immature |
| `tx_detail(txid)` | JSON detail tx (dipakai TxDetailDialog) |
| `estimate_send(to, amount_ori, tier, subtract_fee)` | simulasi kirim + fee (memakai wallet.plan_send) |
| `send_coins(...)` | tanda tangan + broadcast via API node |
| `new_receiving_address` / `set_label` | manajemen alamat & label |
| `connected_peers` / `add_peer` | status & koneksi P2P (PeersDialog) |
| `debug_command` | console gaya Bitcoin (`getblockchaininfo`, `getbalance`, …) |

UI ↔ data: refresh memakai sinyal Qt (`refresh_needed`, dsb.) + `QTimer`
berkala, bukan polling di tiap widget. Scan riwayat bertahap (batch kecil per tick)
agar window tetap responsif di chain besar — sama prinsipnya dengan
`ThreadedScanner` di Bitcoin Core.

## 5. Halaman & fitur

- **OverviewPage** — saldo total (mature/immature/pending), tx terbaru
  (double-click → detail), peer count, height terakhir, waktu blok.
- **SendPage** — alamat tujuan (validasi bech32 `ori1`), jumlah ORI,
  tier fee 1–5 (lihat tabel fee di README), *subtract fee from amount*,
  label opsional; menampilkan estimasi fee + ukuran tx (vB) sebelum kirim.
- **ReceivePage** — generate alamat baru per permintaan, daftar request terima.
- **TransactionsPage** — tabel tx (tanggal, tipe, label, jumlah, confirmations);
  double-click membuka **TxDetailDialog**: status (confirmed/pending/immature),
  tanggal, kepada/dari, jumlah, fee, size, version, locktime, block (height/hash),
  transaction id (copy), dan raw transaction hex.
- **ConsoleDialog** — REPL perintah debug berbasis `NodeController.debug_command`.
- **PeersDialog** — daftar peer + `addpeer` manual.
- **OptionsDialog / AddressBookDialog / AboutDialog** — pengaturan, buku alamat, info.

## 6. Tema

`qt/theme.py`: palet + `QSS` (Fusion style).

```
BITCOIN_ORANGE  #F7931A   aksen: tombol primary, selection, underline nav
WINDOW_BG       #F2F1F0   latar window
PANEL_BG        #FFFFFF   panel/kartu
BORDER          #C6C6C6   border umum
TEXT            #2B2B2B   teks utama
MUTED           #6D6D6D   teks sekunder
GREEN           #2E7D32   balance positif / received
RED             #C0392B   negative / outgoing
PENDING         #7A7A7A   transaksi pending
```

QtWidgets yang di-cover QSS: QMenuBar, QMenu, QToolBar, QToolButton (nav),
QTableWidget (selection orange), QLineEdit/QComboBox focus ring orange,
QPushButton (primary/disabled), QSplitter, QTabWidget, QProgressBar, QLabel
(selector `#balanceValue`, `#positive`, `#negative`, `#pending`, `#muted`).

Ikon (`qt/icons.py`) digambar programatik (painter) — tidak ada file aset PNG.

## 7. Test headless / offscreen

Tanpa X server (WSL, CI):

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -c "import qt_app; qt_app.main([])"
```

Hook test otomatis: env `ORI_TEST_AUTOQUIT_MS=<ms>` membuat aplikasi quit
otomatis setelah N ms — dipakai skrip test offscreen-nya:

```bash
QT_QPA_PLATFORM=offscreen ORI_TEST_AUTOQUIT_MS=15000 \
  .venv/bin/python qt_app.py --datadir data/node1
```

Yang sudah DIVERIFIKASI offscreen (WSL, PySide6 6.11.1):

- Boot penuh: splash → node start → wallet load → main window muncul, tanpa crash.
- Deep test in-process: mine 1 blok via template API → balance bertambah
  (4.628B → 4.628,117… dst, immaturity tampil), `tx_detail` coinbase
  (1 confirm, type immature, posisi 0, size 112, raw hex 224 char),
  `estimate_send` tier 3 (fee 116 sats @ 252 vB), `send_coins`
  tier 5 → mempool 1 & tag pending muncul, `tx_detail` pending
  (confirmations 0, mempool true), TxDetailDialog menampilkan fields
  dan raw tx (copy ke clipboard).
- Autoquit hook terbukti berhenti bersih tanpa hang.

Patch hardening QT 2026-08-21:

- `ORICore.spec` sekarang mencantumkan hidden import eksplisit untuk semua modul
  core root (`utils`, `tx`, `api`, `chain`, `p2p`, dll.) dan seluruh paket `qt`.
  Ini memperbaiki crash frozen app `dist/ORICore/ORICore.exe` seperti
  `ModuleNotFoundError: No module named 'utils'` saat PyInstaller gagal
  menemukan import root dari dependency chain `qt.controller -> api -> tx`.
- Startup lock data-dir sekarang memeriksa PID pemilik `.lock`. Jika PID masih
  hidup, GUI menolak start untuk mencegah dua node menulis chain DB yang sama;
  jika PID sudah mati/stale, lock otomatis dibersihkan sehingga crash lama tidak
  membuat QT wallet "tidak bisa dibuka".
- `qt_app.py` memanggil `controller.shutdown()` di `finally`, supaya P2P/API
  in-process ditutup saat window keluar normal.
- `TxDetailDialog` untuk transaksi mempool/RBF tidak lagi crash karena `QIcon`
  tidak ter-import dan handler `_bump_fee` tidak ada; tombol sekarang memakai
  icon internal dan memanggil `NodeController.bump_fee`.
- Console debug disinkronkan dengan API storage/chain saat ini: `getblock`
  menerima height/hash, `getblockhash` memakai `block_by_height`, `getrawmempool`
  menampilkan hex txid, `getsupply` memakai UTXO live supply, `getdifficulty`
  memakai `chain.tip()`/`next_bits()`, dan `decoderawtransaction` tidak bergantung
  pada helper FastAPI internal yang sudah tidak ada.
- Test regresi baru: stale lock auto-recover dan konstruksi dialog detail tx
  pending/RBF di mode offscreen.

Patch P2P/QT connectivity 2026-08-21:

- Manual Add Node di QT sekarang menerima `host`, `host:port`, atau URL P2P
  non-HTTP. URL `http://...` / `https://...` ditolak eksplisit karena itu REST
  API endpoint, bukan endpoint P2P; memakai port API di dialog peers memang akan
  terlihat connect sebentar lalu drop karena protokol framing berbeda.
- Manual peer dengan hostname DNS seperti `nozomi.proxy.rlwy.net` sekarang
  disimpan ke known peers dan ikut reconnect. Filtering addr-relay tetap ketat:
  peer yang dipelajari dari jaringan masih wajib routable IP, supaya perbaikan
  UX manual tidak membuka surface eclipse via relay hostname arbitrary.
- Peer list QT menampilkan state `Connecting` vs `Ready`; `Ready` hanya setelah
  `verack`, jadi UI tidak lagi menyamakan socket TCP mentah dengan koneksi P2P
  valid.
- Network menyimpan recent peer failures dan menampilkannya di Peers dialog,
  misalnya `connect failed`, `bad magic`, `socket closed`, `ping timeout`, atau
  `chain mismatch (genesis)`.
- Sync download dipindah ke fase setelah handshake selesai dan `Node.on_peer_ready`
  memakai headers-first untuk gap >10 blok. Smoke lokal dua node memverifikasi
  node QT/GUI-compatible bisa sync dari height 0 ke height 12 lewat P2P.

Skrip test ad-hoc dipakai: `qt_deep_test*.py` (temp di luar repo).

## 8. Tolok ukur (benchmark) ala Bitcoin Core

Parameter yang diukur untuk memastikan GUI tidak mengganggu node:

| Ukuran | Alat | Target |
|---|---|---|
| Boot sampai window tampil | stopwatch di `qt_app` (splash → show) | < 2 dtk chain kecil |
| Refresh snapshot (balance+wallet) | `controller._build_snapshot` | < 30 ms |
| Satu langkah scan history | `_scan_history_step` | < 10 ms/step |
| `tx_detail` / `estimate_send` | panggil langsung + stopwatch | < 5 ms |
| Rendering tabel 100 baris | `TransactionsPage` populate | < 50 ms |
| Max fps selama refresh berkala | `QT_LOGGING_RULES=qt.*=true` frame delta | tetap responsif |

Hasil pengukuran resmi dicatat di README/build saat benchmark dijalankan ulang
dengan chain >10.000 blok (data mainnet lokal). Belum dilaksanakan karena chain
uji masih pendek; metodologi di atas sudah siap dipakai.

## 9. Catatan: logo & QR (menyusul)

- **Logo**: saat ini `app_icon` dari `icons.py` (painter, oranye) — logo resmi
  (mark ORI + tulisan) belum dibuat; rencana menggambar SVG dua-aware atau
  menyertakan aset PNG/XPM bila user menyerahkan desainnya.
- **QR code**: ReceivePage belum menampilkan QR — rencana memakai
  `qrencode` (PySide6.QtQrCode tersedia di Qt 6.11) saat modul di-wire;
  kebutuhan: libqrencode hadir saat build PySide6 (check `QtQrCode` saat
  runtime, fallback: generate dengan library murni Python).

## 10. Tanpa mining — penegasan desain

GUI TIDAK memuat kode PoW sama sekali (sesuai spesifikasi ORI: node = verifikasi
saja). `qt/controller.py` tidak mengimpor `miner.py`/`pow.py`; satu-satunya
jalur "membuat blok" yang diverifikasi di test offscreen adalah melalui
`/mining/template` + `submit` API — itu pun hanya dipakai test, bukan UI.
Block reward mengalir dari miner eksternal (proses `miner.py` terpisah) ke
alamat yang tercantum di template/address mereka.
