@echo off
color 0A
title ORI PPLNS Pool Miner Launcher

echo ===============================================================================
echo                            ORI PPLNS MINING POOL
echo                                (SHA-256d PoW)
echo ===============================================================================
echo Make sure 'miner-ori.exe' is located in this directory.
echo If you don't have it, download from: https://github.com/mrnobody-dev/ORI/releases
echo.

set /p WALLET="Enter your ORI Wallet Address (e.g. ori1q...): "
set /p THREADS="How many CPU threads do you want to allocate? (e.g. 4): "

echo.
echo ===============================================================================
echo [INFO] Initializing mining protocol for wallet: %WALLET%
echo [INFO] Allocating %THREADS% CPU threads...
echo [INFO] Connecting to stratums...
echo ===============================================================================
echo.
echo Check your balance and worker statistics at: https://ori-production-8364.up.railway.app/
echo Press CTRL+C at any time to gracefully stop the miner.
echo.

miner-ori.exe --address %WALLET% --host ori-production-8364.up.railway.app --port 443 --https --pool --threads %THREADS%

echo.
echo [!] Mining operation terminated. Press any key to exit.
pause >nul
