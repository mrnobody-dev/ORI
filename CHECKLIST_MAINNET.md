# ORI Mainnet Launch Checklist

## Phase 1: Code Freeze & Review (Current)

- [x] **Config parameters finalized**
  - Block time: 30s
  - Max supply: 210,000,000 ORI (hard cap, enforced)
  - Initial reward: 35 ORI
  - Halving: every 3,000,000 blocks
  - Tail emission: 0.3 ORI/block after reward < 0.3
  - Address prefix: 120 (mainnet), 130 (testnet), 140 (stagenet)
  - Ports: 28080 P2P / 28081 RPC (mainnet)

- [x] **Genesis transaction**
  - Indonesia government message embedded in coinbase tx_extra
  - Deterministic output key derived from SHA256(message)
  - Cross-platform reproducible

- [ ] **Genesis block hash captured**
  *How: Build on ONE platform, run `./bin/orid --offline`, save hash from log:
   `Genesis block added: height=0, hash=<64-char-hex>`

- [ ] **Genesis block hash verified**
  *Must verify hash is IDENTICAL on:*
  - [ ] Windows (MSVC)
  - [ ] Linux (GCC)
  - [ ] macOS (Apple Clang)

- [x] **Yespower PoW — ref-only**
  - yespower-opt.c excluded (determinism)
  - yespower-ref.c + sha256.c only
  - Parameters: N=2048, r=32, pers="ORI_PoW:0"

- [x] **LWMA-3 difficulty**
  - Window: 60 blocks
  - Target: 30s
  - Clamp: 10× target for outlier prevention

- [x] **Emission logic**
  - Halving every 3,000,000 blocks
  - Tail emission at 0.3 ORI after reward < 0.3
  - Hard cap at 210,000,000 ORI (block reward = 0 once reached)

- [x] **Blockchain DB**
  - Supply cap enforced in main chain and alt-chains
  - `already_generated_coins` capped at `ORI_MAX_SUPPLY_ATOMIC`

## Phase 2: Build & Test

- [ ] **Build on all platforms**
  - [ ] Linux (Ubuntu 22.04+)
  - [ ] macOS (Intel + Apple Silicon)
  - [ ] Windows (Visual Studio 2022)

- [ ] **Unit tests pass**
  ```bash
  cmake .. -DBUILD_TESTS=ON
  make -j$(nproc)   # or cmake --build . --config Release
  cd build && ctest --output-on-failure
  ```

- [ ] **Testnet launch (3+ nodes)**
  - [ ] Node 1 (seed) syncing
  - [ ] Node 2 connected to Node 1
  - [ ] Node 3 connected to Node 2
  - [ ] All nodes reach same block height
  - [ ] Transactions relay between nodes

- [ ] **Wallet test**
  - [ ] `ori-wallet-cli` generates addresses with prefix "M..."
  - [ ] Wallet syncs on testnet
  - [ ] Can send/receive transactions
  - [ ] Balance displays correctly (8 decimal places)

- [ ] **Mining test**
  - [ ] Testnet mining produces blocks (~30s each)
  - [ ] Block reward = 35 ORI (testnet initial)
  - [ ] Difficulty adjusts via LWMA-3

## Phase 3: Security & Hardening

- [ ] **Checkpoint hardcoding**
  - Add genesis block hash + first 10 block hashes to `src/blocks/checkpoints.dat`
  - Regenerate checkpoints.dat binary
  - Verify checkpoints file compiles into the binary

- [ ] **DNS checkpoint setup**
  - Configure DNS seed nodes
  - [ ] Mainnet DNS checkpoints configured in `src/checkpoints/checkpoints.cpp`

- [ ] **Dandelion++ stem/fluff**
  - Dandelion++ enabled (transaction privacy)
  - Test transaction propagation delay

- [ ] **Network ID uniqueness**
  - Mainnet: `orianonymous.or` (already set in config)
  - Testnet: `ori.testnet` (already set)
  - Stagenet: `ori.stage` (already set)

- [ ] **Fee structure review**
  - Minimum fee: 0.0002 ORI/kB (20,000 atomic units with 8 decimals)
  - Dynamic fee enabled
  - Dust threshold: 0.0002 ORI

## Phase 4: Genesis Block Finalization

- [ ] **Genesis hash added to `src/blocks/checkpoints.dat`**
  - [ ] Mainnet genesis hash
  - [ ] Testnet genesis hash
  - [ ] Stagenet genesis hash

- [ ] **Genesis hash added to `src/checkpoints/checkpoints.cpp`**
  - Add `ADD_CHECKPOINT2(0, "<genesis-hash>", "0x1")` for each network

- [ ] **Final build with checkpoints**
  - Rebuild ORI with embedded genesis checkpoint
  - Verify first run accepts checkpoint

- [ ] **Genesis block hash double-checked**
  ```bash
  # After rebuilding with checkpoint:
  ./bin/orid --offline
  # Confirm: "Genesis block added: height=0, hash=<previously-saved-hash>"
  # The checkpoint should match automatically
  ```

## Phase 5: Launch Preparation

- [ ] **Release build** (fully optimized)
  ```bash
  cmake .. -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTS=OFF
  ```

- [ ] **Seed node operators recruited**
  - Minimum 3 geographically distributed seed nodes
  - Each with static IP / DNS name
  - Node operators have build instructions + configuration

- [ ] **Bootstrap blockchain file generated**
  ```bash
  # After testnet has been running for some blocks:
  ./bin/ori-blockchain-export --output-file ori-bootstrap.raw
  ```
  - Compress and host for download

- [ ] **Launch announcement prepared**
  - Genesis block hash published
  - Seed node addresses published
  - Build instructions for all platforms
  - Wallet download links
  - Block explorer URL

- [ ] **Community wallet/explorer**
  - [ ] Block explorer (e.g., ORI-based or generic cryptonote explorer)
  - [ ] Web wallet or mobile wallet (optional for v1)

## Phase 6: Mainnet Launch

- [ ] **Coordinate launch time** (all seed nodes start simultaneously)
- [ ] **All seed nodes start orid**
- [ ] **Verify all nodes sync to same genesis block**
- [ ] **First block mined** (height 1)
- [ ] **Monitor for 1 hour** (check orphan rate, block time, difficulty)
- [ ] **Open mining pools / solo mining**
- [ ] **Public announcement**

## Notes & Risks

### Known Risks

1. **30s block time** → Higher orphan rate (~2-3%) compared to Monero's 120s.
   Mitigation: LWMA-3 difficulty responds faster to hashrate changes.

2. **8 decimal places** → Not compatible with Monero's 12-decimal ecosystem.
   Mitigation: All display code uses `CRYPTONOTE_DISPLAY_DECIMAL_POINT`;
   wallets will show correct amounts.

3. **Yespower PoW** → CPU-only, no ASIC resistance beyond memory-hardness.
   Mitigation: yespower-ref is memory-hard (N=2048, r=32, ~2MB/hash).
   Regular N parameter updates may be needed as hardware improves.

4. **No hardfork mechanism** → All features enabled at block 1.
   Risk: If a bug is found in Clsag/Bulletproofs, a hardfork would require
   a new network. Mitigation: extensive testing on testnet.

5. **Genesis determinism** → yespower-opt.c uses SIMD which produces
   different hashes on different CPUs. We use yespower-ref.c only,
   which is purely deterministic. Verify on all platforms.
