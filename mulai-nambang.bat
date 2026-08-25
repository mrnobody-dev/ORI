@echo off
color 0A
title ORI PPLNS Pool Miner

echo =======================================================
echo               ORI PPLNS POOL MINING
echo =======================================================
echo.
echo Selamat datang di Pool Mining ORI!
echo Pool Dashboard: https://ori-production-8364.up.railway.app/
echo.
echo Pastikan file miner-ori.exe ada di folder yang sama.
echo Download dari: https://github.com/mrnobody-dev/ORI/releases
echo.

set /p WALLET="Masukkan Alamat Wallet ORI Anda (contoh: ori1q...): "
set /p THREADS="Berapa banyak core CPU yang ingin digunakan? (contoh: 2): "

echo.
echo Memulai proses mining untuk dompet: %WALLET%
echo Menggunakan %THREADS% core CPU...
echo.
echo Silakan pantau statistik Anda di: https://ori-production-8364.up.railway.app/
echo Tekan CTRL+C kapan saja untuk berhenti menambang.
echo =======================================================
echo.

miner-ori.exe --address %WALLET% --host ori-production-8364.up.railway.app --port 443 --https --pool --threads %THREADS%

echo.
echo Mining berhenti. Tekan tombol apa saja untuk keluar.
pause

