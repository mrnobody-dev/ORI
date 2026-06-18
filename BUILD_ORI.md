# ORI Blockchain - Build Guide

## Prerequisites by Platform

### Linux (Ubuntu 22.04+ / Debian 12+)
```bash
# Install build dependencies
sudo apt update && sudo apt install -y \
  build-essential cmake libboost-all-dev \
  libssl-dev libzmq3-dev libunbound-dev \
  libsodium-dev libpgm-dev libnorm-dev \
  libgtest-dev ccache doxygen graphviz \
  git pkg-config

# Install newer CMake (3.16+ required)
sudo apt install -y cmake
cmake --version  # should be >= 3.16
```

### macOS (Apple Silicon / Intel, macOS 13+)
```bash
# Install Homebrew if not present
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install dependencies
brew install cmake boost openssl zmq libsodium \
  pkg-config unbound ccache doxygen graphviz
```

### Windows (Visual Studio 2022)
1. Install **Visual Studio 2022 Community** (free):
   - https://visualstudio.microsoft.com/vs/community/
   - Select workloads: "Desktop development with C++"
   - Select individual components: "C++ CMake tools for Windows"
   - Ensure MSVC v143 and Windows 10/11 SDK are included

2. Install **Git for Windows**: https://git-scm.com/download/win

3. Install **vcpkg** for dependency management:
   ```powershell
   cd C:\
   git clone https://github.com/Microsoft/vcpkg.git
   cd vcpkg
   .\bootstrap-vcpkg.bat
   # Add vcpkg to PATH (System Environment Variables)
   # Path: C:\vcpkg
   ```

4. Install dependencies via vcpkg:
   ```powershell
   cd C:\vcpkg
   .\vcpkg install boost-filesystem boost-program-options boost-date-time boost-thread boost-regex boost-serialization boost-chrono boost-locale boost-range boost-format boost-optional boost-multi-array boost-uuid boost-multiprecision boost-concept-check unbound libsodium zeromq
   ```

## Build Steps

### Linux & macOS
```bash
# From the ORI source directory
cd D:\coding\ORI  # or wherever you cloned

# Create build directory
mkdir build && cd build

# Configure (Release build)
cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_TESTS=OFF \
  -DBUILD_GUI_DEPS=OFF \
  -DUSE_DEVICE_TREZOR=OFF \
  -DSTACK_TRACE=OFF \
  -DCMAKE_INSTALL_PREFIX=/opt/ori

# Build (use all CPU cores)
# Linux:
make -j$(nproc)

# macOS (Apple Silicon):
make -j$(sysctl -n hw.logicalcpu)

# Install (optional)
sudo make install
```

### Windows (Visual Studio 2022 + vcpkg)
```powershell
# From the ORI source directory
cd D:\coding\ORI

# Create build directory
mkdir build
cd build

# Configure with vcpkg toolchain
cmake .. ^
  -DCMAKE_BUILD_TYPE=Release ^
  -DBUILD_TESTS=OFF ^
  -DBUILD_GUI_DEPS=OFF ^
  -DUSE_DEVICE_TREZOR=OFF ^
  -DSTACK_TRACE=OFF ^
  -DCMAKE_TOOLCHAIN_FILE=C:\vcpkg\scripts\buildsystems\vcpkg.cmake ^
  -DCMAKE_INSTALL_PREFIX=C:\ORI

# Build (parallel, 8 cores)
cmake --build . --config Release --parallel 8

# Alternative: Open the generated .sln in Visual Studio
#   build\ORI.sln  ->  Build Solution (Ctrl+Shift+B)
```

### macOS Apple Silicon Specific Notes
```bash
# Force arm64 architecture (M1/M2/M3/M4)
cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DBUILD_TESTS=OFF \
  -DBUILD_GUI_DEPS=OFF \
  -DUSE_DEVICE_TREZOR=OFF \
  -DSTACK_TRACE=OFF

# If you get OpenSSL errors:
export OPENSSL_ROOT_DIR=$(brew --prefix openssl)
```

## Output Binaries

After build, binaries are in:
- Linux/macOS: `build/bin/`
- Windows: `build/bin/Release/`

| Binary | Purpose |
|--------|---------|
| `orid` | ORI daemon (node) |
| `ori-wallet-rpc` | Wallet RPC server |
| `ori-wallet-cli` | Command-line wallet |
| `ori-blockchain-export` | Blockchain export utility |

### Quick Test — Generate Genesis Block Hash
```bash
# Run orid in offline mode to generate + display the genesis hash
./bin/orid --offline
# Look for: "Genesis block added: height=0, hash=<64-char-hex>"
# This hash must be identical on Windows, macOS, and Linux
```

## Running Testnet

### Node 1 (First seed node)
```bash
# Linux/macOS
./bin/orid --testnet --data-dir ~/.ori/testnet_node1 \
  --p2p-bind-port 38080 --rpc-bind-port 38081 \
  --add-exclusive-node 127.0.0.1:48080 \
  --confirm-external-bind --log-level 1

# Windows PowerShell
.\bin\Release\orid.exe --testnet --data-dir C:\ori\testnet_node1 `
  --p2p-bind-port 38080 --rpc-bind-port 38081 `
  --add-exclusive-node 127.0.0.1:48080 `
  --confirm-external-bind --log-level 1
```

### Node 2 (Second node, connects to Node 1)
```bash
# Linux/macOS
./bin/orid --testnet --data-dir ~/.ori/testnet_node2 \
  --p2p-bind-port 48080 --rpc-bind-port 48081 \
  --add-exclusive-node 127.0.0.1:38080 \
  --confirm-external-bind --log-level 1
```

### Wallet (connects to Node 1)
```bash
./bin/ori-wallet-cli --testnet --daemon-address 127.0.0.1:38081
```

### Mining on Testnet
```bash
# In ori-wallet-cli, once connected:
# start_mining <threads>
start_mining 2
```

### Verify Nodes Are Connected
```bash
curl http://127.0.0.1:38081/get_info
# Look for: "incoming_connections_count", "outgoing_connections_count"
```

## Cross-Platform Genesis Verification

To verify the genesis block hash is identical across platforms:

1. Build `orid` on each platform
2. Run `orid --offline` once (starts with empty blockchain)
3. Compare the genesis block hash from the logs

The hash MUST be identical on Windows, macOS, and Linux if:
- yespower-ref.c is used (yespower-opt.c is excluded — CMakeLists.txt is ref-only)
- Same GENESIS_TX hex (hardcoded in config.h)
- Same nonce (10000)
- Same Yespower parameters (N=2048, r=32, pers="ORI_PoW:0")

If the hashes differ, the genesis tool (`origen`) provides a standalone
cross-platform implementation for debugging.

## Troubleshooting

### Build fails: "cannot find -lboost_..."
- Linux: `sudo apt install libboost-all-dev`
- macOS: `brew install boost`
- Windows: Verify vcpkg installed boost correctly

### Build fails: "randomx/... not found"
- This is EXPECTED — RandomX is removed from ORI (replaced by Yespower).
- Make sure git submodules are cleaned: remove `external/randomx/` directory.
- The build system was updated to use Yespower only.

### Build fails: "yespower-opt.c not found"
- This is EXPECTED — yespower-opt.c is intentionally excluded for
  cross-platform determinism. Only yespower-ref.c is used.
- Verify `external/yespower/CMakeLists.txt` lists only ref sources.

### Daemon exits immediately on testnet
- Check ports are not in use: `netstat -an | findstr 38080` (Windows)
  or `ss -tlnp | grep 38080` (Linux)
- Try different data-dir for each node

### "Failed to add genesis block to blockchain"
- The GENESIS_TX hex is corrupted or mis-formatted.
- Regenerate using: `python3 tools/genesis_tx.py`
- Verify the hex string matches exactly between mainnet/testnet/stagenet
  config (only nonce should differ between networks).
