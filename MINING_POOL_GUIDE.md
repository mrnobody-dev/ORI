# ORI Mining Pool Connection Guide

## Panduan Koneksi ke Mining Pool altaria.proxy.rlwy.net:20878

### Overview
Dokumen ini menjelaskan cara menghubungkan miner ORI Anda ke mining pool yang berjalan di `altaria.proxy.rlwy.net:20878`.

### Prerequisites
1. **Miner ORI** - Pastikan Anda memiliki miner ORI (miner.py atau miner-ori.exe)
2. **Wallet Address** - Address ORI yang valid untuk menerima reward (format: ori1...)
3. **Koneksi Internet** - Akses ke altaria.proxy.rlwy.net

---

## Metode Koneksi

### 1. Menggunakan Python Miner (miner.py)

#### A. Solo Mining ke Pool Server
```bash
python miner.py --node http://altaria.proxy.rlwy.net:20878 --address ori1your_address_here --threads 4
```

#### B. Pool Mining (Jika pool server support pool protocol)
```bash
python miner.py --pool http://altaria.proxy.rlwy.net:20878 --address ori1your_address_here --threads 4
```

### 2. Menggunakan C++ Miner (miner-ori.exe)

#### A. Solo Mining
```bash
./miner-ori.exe --node http://altaria.proxy.rlwy.net:20878 --address ori1your_address_here --threads 4
```

#### B. Pool Mining
```bash
./miner-ori.exe --pool http://altaria.proxy.rlwy.net:20878 --address ori1your_address_here --threads 4
```

### 3. Parameter Penting

| Parameter | Deskripsi | Default |
|-----------|-----------|---------|
| `--node` atau `--pool` | URL pool server | http://127.0.0.1:8000 |
| `--address` | Address ORI untuk reward (WAJIB) | - |
| `--threads` | Jumlah worker threads | CPU cores - 1 |
| `--batch` | Nonce batch size per worker | 65,536 |
| `--kernel` | Hashing algorithm (auto/midstate/full) | auto |
| `--refresh` | Template refresh interval (seconds) | 30.0 |
| `--api-token` | API token jika diperlukan | - |

---

## Konfigurasi Optimal

### 1. Untuk CPU Mining
```bash
# Untuk CPU 4 cores
python miner.py --node http://altaria.proxy.rlwy.net:20878 --address ori1your_address_here --threads 3 --batch 32768

# Untuk CPU 8+ cores
python miner.py --node http://altaria.proxy.rlwy.net:20878 --address ori1your_address_here --threads 7 --batch 65536
```

### 2. Untuk Testing/Development
```bash
# Mining dengan refresh cepat untuk testing
python miner.py --node http://altaria.proxy.rlwy.net:20878 --address ori1your_address_here --threads 1 --refresh 10 --quiet
```

### 3. Dengan API Token (jika diperlukan)
```bash
export BTPY_API_TOKEN="your_api_token_here"
python miner.py --node http://altaria.proxy.rlwy.net:20878 --address ori1your_address_here --threads 4 --api-token $BTPY_API_TOKEN
```

---

## Monitoring & Troubleshooting

### 1. Cek Status Pool
```bash
curl http://altaria.proxy.rlwy.net:20878/info/
```

### 2. Cek Mining Template
```bash
curl "http://altaria.proxy.rlwy.net:20878/mining/template?address=ori1your_address_here"
```

### 3. Monitor Mining Stats
```bash
# Jika pool mendukung stats endpoint
curl http://altaria.proxy.rlwy.net:20878/pool/stats
```

### 4. Common Issues & Solutions

#### Error: "Connection refused"
```bash
# Cek apakah server online
ping altaria.proxy.rlwy.net
curl -I http://altaria.proxy.rlwy.net:20878/
```

#### Error: "Invalid address"
```bash
# Pastikan format address benar (ori1...)
# Generate address baru jika perlu
python -c "from wallet import Wallet; w = Wallet(); print('Address:', w.address)"
```

#### Error: "API token required"
```bash
# Gunakan API token
python miner.py --node http://altaria.proxy.rlwy.net:20878 --address ori1... --api-token YOUR_TOKEN
```

#### Low Hashrate
```bash
# Eksperimen dengan batch size dan kernel
python miner.py --node http://altaria.proxy.rlwy.net:20878 --address ori1... --batch 16384 --kernel midstate
python miner.py --node http://altaria.proxy.rlwy.net:20878 --address ori1... --batch 131072 --kernel full
```

---

## Pool Mining vs Solo Mining

### Pool Mining Benefits:
- ✅ Reward yang lebih stabil dan sering
- ✅ Cocok untuk miner dengan hashrate rendah
- ✅ Shared difficulty/variance
- ✅ Statistics dan monitoring yang lebih baik

### Solo Mining Benefits:
- ✅ 100% reward jika menemukan block
- ✅ Tidak ada pool fee
- ✅ Full control atas mining

### Rekomendasi:
- **Hashrate < 1 MH/s**: Gunakan Pool Mining
- **Hashrate > 10 MH/s**: Bisa pilih Pool atau Solo
- **Testing/Development**: Gunakan Solo Mining

---

## Advanced Configuration

### 1. Multiple Workers dengan Pool
```bash
# Worker 1
python miner.py --pool http://altaria.proxy.rlwy.net:20878 --address ori1worker1_address --threads 2 &

# Worker 2  
python miner.py --pool http://altaria.proxy.rlwy.net:20878 --address ori1worker2_address --threads 2 &
```

### 2. Batch Script untuk Windows
```batch
@echo off
set POOL_URL=http://altaria.proxy.rlwy.net:20878
set YOUR_ADDRESS=ori1your_address_here
set THREADS=4

echo Starting ORI Miner...
python miner.py --node %POOL_URL% --address %YOUR_ADDRESS% --threads %THREADS%
pause
```

### 3. Systemd Service untuk Linux
```ini
[Unit]
Description=ORI Pool Miner
After=network.target

[Service]
Type=simple
User=miner
WorkingDirectory=/home/miner/ori-miner
ExecStart=/usr/bin/python3 miner.py --node http://altaria.proxy.rlwy.net:20878 --address ori1your_address_here --threads 4
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## API Endpoints Pool

### Mining Endpoints:
- `GET /mining/template?address=ori1...` - Get mining template
- `POST /mining/submit` - Submit mined block

### Pool-specific Endpoints (jika ada):
- `GET /pool/job?worker=ori1...` - Get pool job
- `POST /pool/submit` - Submit pool share
- `GET /pool/stats` - Pool statistics

### Info Endpoints:
- `GET /info/` - Node information
- `GET /stats` - Node statistics
- `GET /peers/` - Connected peers

---

## Example Output

### Successful Connection:
```
miner started -> node http://altaria.proxy.rlwy.net:20878  payout ori1qw...  workers 4 batch 65,536 kernel auto
[round] height 12345 | difficulty 1234.56 | bits 0x1a0fffff | target 0x0000ffff00000000...
[work ] h12345 | 1.23 Mhash/s | 45.2M nonces | 37s | kernel midstate | eta ~12m
[found] nonce 0x12345678 | 45,234,567 nonces in 37.2s (1.22 Mhash/s, kernel midstate)
[block] ACCEPTED height 12345 | hash abc123... | txs 5
```

### Connection Error:
```
[block] REJECTED by node: HTTP 503: {"detail":"connection refused"}
mining round failed: HTTPError: HTTP Error 503: Service Unavailable
```

---

## Security Notes

1. **Jangan Share Private Keys**: Hanya gunakan public address untuk mining
2. **Secure API Tokens**: Jika menggunakan API token, jaga kerahasiaan
3. **Monitor Pool**: Pastikan pool terpercaya dan tidak scam
4. **Backup Wallet**: Selalu backup wallet file Anda

---

## Contact & Support

Jika mengalami masalah koneksi ke `altaria.proxy.rlwy.net:20878`:

1. Check server status di explorer: http://altaria.proxy.rlwy.net:20878/explorer
2. Verify API endpoints: http://altaria.proxy.rlwy.net:20878/docs
3. Contact pool administrator
4. Join komunitas ORI untuk support

---

**Happy Mining! 🚀⛏️**