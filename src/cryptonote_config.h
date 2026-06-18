// Copyright (c) 2014-2024, The ORI Project
// Copyright (c) 2024, Mr. Nobody
// 
// All rights reserved.
// 
// Redistribution and use in source and binary forms, with or without modification, are
// permitted provided that the following conditions are met:
// 
// 1. Redistributions of source code must retain the above copyright notice, this list of
//    conditions and the following disclaimer.
// 
// 2. Redistributions in binary form must reproduce the above copyright notice, this list
//    of conditions and the following disclaimer in the documentation and/or other
//    materials provided with the distribution.
// 
// 3. Neither the name of the copyright holder nor the names of its contributors may be
//    used to endorse or promote products derived from this software without specific
//    prior written permission.
// 
// THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY
// EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
// MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL
// THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
// SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
// PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
// INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
// STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF
// THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
// 
// Parts of this file are originally copyright (c) 2012-2013 The Cryptonote developers

#pragma once

#include <cstdint>
#include <stdexcept>
#include <string>
#include <boost/uuid/uuid.hpp>

#define CRYPTONOTE_DNS_TIMEOUT_MS                       20000

#define CRYPTONOTE_MAX_BLOCK_NUMBER                     500000000
#define CRYPTONOTE_MAX_TX_SIZE                          1000000
#define CRYPTONOTE_MAX_TX_PER_BLOCK                     0x10000000
#define CRYPTONOTE_PUBLIC_ADDRESS_TEXTBLOB_VER          0
#define CRYPTONOTE_MINED_MONEY_UNLOCK_WINDOW            60
#define CURRENT_TRANSACTION_VERSION                     2
#define CURRENT_BLOCK_MAJOR_VERSION                     1
#define CURRENT_BLOCK_MINOR_VERSION                     0
#define CRYPTONOTE_BLOCK_FUTURE_TIME_LIMIT              60*60*2
#define CRYPTONOTE_DEFAULT_TX_SPENDABLE_AGE             10

#define BLOCKCHAIN_TIMESTAMP_CHECK_WINDOW               60

// ORI emission: Bitcoin-style halving + tail emission
// Block time: 30s, Initial reward: 35 ORI
// Halving every 3,000,000 blocks (~2.85 years)
// Tail emission: 0.3 ORI/block after reward drops below 0.3
#define MONEY_SUPPLY                                    ((uint64_t)(-1))
#define EMISSION_SPEED_FACTOR_PER_MINUTE                (2)
#define FINAL_SUBSIDY_PER_MINUTE                        ((uint64_t)0)

#define CRYPTONOTE_REWARD_BLOCKS_WINDOW                 100
#define CRYPTONOTE_BLOCK_GRANTED_FULL_REWARD_ZONE_V2    60000
#define CRYPTONOTE_BLOCK_GRANTED_FULL_REWARD_ZONE_V1    20000
#define CRYPTONOTE_BLOCK_GRANTED_FULL_REWARD_ZONE_V5    300000
#define CRYPTONOTE_LONG_TERM_BLOCK_WEIGHT_WINDOW_SIZE   100000
#define CRYPTONOTE_SHORT_TERM_BLOCK_WEIGHT_SURGE_FACTOR 50
#define CRYPTONOTE_COINBASE_BLOB_RESERVED_SIZE          600
#define CRYPTONOTE_DISPLAY_DECIMAL_POINT                8
#define COIN                                            ((uint64_t)100000000)

// ORI emission constants
#define ORI_INITIAL_REWARD_IN_COIN                      35
#define ORI_HALVING_INTERVAL_BLOCKS                     3000000
#define ORI_TAIL_EMISSION_ATOMIC                        ((uint64_t)30000000) // 0.3 ORI
#define ORI_MAX_SUPPLY_ATOMIC                           ((uint64_t)21000000000000000ULL) // 210M * 10^8

#define FEE_PER_KB_OLD                                  ((uint64_t)100000)
#define FEE_PER_KB                                      ((uint64_t)20000)
#define FEE_PER_BYTE                                    ((uint64_t)3)
#define DYNAMIC_FEE_PER_KB_BASE_FEE                     ((uint64_t)20000)
#define DYNAMIC_FEE_PER_KB_BASE_BLOCK_REWARD            ((uint64_t)100000000)
#define DYNAMIC_FEE_PER_KB_BASE_FEE_V5                  ((uint64_t)20000 * (uint64_t)CRYPTONOTE_BLOCK_GRANTED_FULL_REWARD_ZONE_V2 / CRYPTONOTE_BLOCK_GRANTED_FULL_REWARD_ZONE_V5)
#define DYNAMIC_FEE_REFERENCE_TRANSACTION_WEIGHT         ((uint64_t)3000)

#define ORPHANED_BLOCKS_MAX_COUNT                       100

// ORI: 30 second block time, LWMA-3 window
#define DIFFICULTY_TARGET_V2                            30
#define DIFFICULTY_TARGET_V1                            30
#define DIFFICULTY_WINDOW                               60
#define DIFFICULTY_LAG                                  0
#define DIFFICULTY_CUT                                  0
#define DIFFICULTY_BLOCKS_COUNT                         DIFFICULTY_WINDOW + DIFFICULTY_LAG

#define CRYPTONOTE_LOCKED_TX_ALLOWED_DELTA_SECONDS_V1   DIFFICULTY_TARGET_V1 * CRYPTONOTE_LOCKED_TX_ALLOWED_DELTA_BLOCKS
#define CRYPTONOTE_LOCKED_TX_ALLOWED_DELTA_SECONDS_V2   DIFFICULTY_TARGET_V2 * CRYPTONOTE_LOCKED_TX_ALLOWED_DELTA_BLOCKS
#define CRYPTONOTE_LOCKED_TX_ALLOWED_DELTA_BLOCKS       1

#define DIFFICULTY_BLOCKS_ESTIMATE_TIMESPAN             DIFFICULTY_TARGET_V1

#define BLOCKS_IDS_SYNCHRONIZING_DEFAULT_COUNT          10000
#define BLOCKS_IDS_SYNCHRONIZING_MAX_COUNT              25000
#define BLOCKS_SYNCHRONIZING_DEFAULT_COUNT_PRE_V4       100
#define BLOCKS_SYNCHRONIZING_DEFAULT_COUNT              20
#define BLOCKS_SYNCHRONIZING_MAX_COUNT                  2048
#define BATCH_MAX_WEIGHT                                10
#define BATCH_MAX_ALLOWED_WEIGHT                        50
#define BLOCKS_MAX_WINDOW                               CRYPTONOTE_REWARD_BLOCKS_WINDOW

#define CRYPTONOTE_MEMPOOL_TX_LIVETIME                    (86400*3)
#define CRYPTONOTE_MEMPOOL_TX_FROM_ALT_BLOCK_LIVETIME     604800

#define CRYPTONOTE_DANDELIONPP_STEMS              2
#define CRYPTONOTE_DANDELIONPP_FLUFF_PROBABILITY 20
#define CRYPTONOTE_DANDELIONPP_MIN_EPOCH         10
#define CRYPTONOTE_DANDELIONPP_EPOCH_RANGE       30
#define CRYPTONOTE_DANDELIONPP_FLUSH_AVERAGE      5
#define CRYPTONOTE_DANDELIONPP_EMBARGO_AVERAGE   39

#define CRYPTONOTE_NOISE_MIN_EPOCH                      5
#define CRYPTONOTE_NOISE_EPOCH_RANGE                    30
#define CRYPTONOTE_NOISE_MIN_DELAY                      10
#define CRYPTONOTE_NOISE_DELAY_RANGE                    5
#define CRYPTONOTE_NOISE_BYTES                          3*1024
#define CRYPTONOTE_NOISE_CHANNELS                       2

#define CRYPTONOTE_FORWARD_DELAY_BASE (CRYPTONOTE_NOISE_MIN_DELAY + CRYPTONOTE_NOISE_DELAY_RANGE)
#define CRYPTONOTE_FORWARD_DELAY_AVERAGE (CRYPTONOTE_FORWARD_DELAY_BASE + (CRYPTONOTE_FORWARD_DELAY_BASE / 2))

#define CRYPTONOTE_MAX_FRAGMENTS                        20

#define COMMAND_RPC_GET_BLOCKS_FAST_MAX_BLOCK_COUNT     1000
#define COMMAND_RPC_GET_BLOCKS_FAST_MAX_TX_COUNT        20000
#define DEFAULT_RPC_MAX_CONNECTIONS_PER_PUBLIC_IP       3
#define DEFAULT_RPC_MAX_CONNECTIONS_PER_PRIVATE_IP      25
#define DEFAULT_RPC_MAX_CONNECTIONS                     100
#define DEFAULT_RPC_SOFT_LIMIT_SIZE                     25 * 1024 * 1024
#define MAX_RPC_CONTENT_LENGTH                          1048576

#define P2P_LOCAL_WHITE_PEERLIST_LIMIT                  1000
#define P2P_LOCAL_GRAY_PEERLIST_LIMIT                   5000

#define P2P_DEFAULT_CONNECTIONS_COUNT                   12
#define P2P_DEFAULT_HANDSHAKE_INTERVAL                  60
#define P2P_DEFAULT_PACKET_MAX_SIZE                     50000000
#define P2P_DEFAULT_PEERS_IN_HANDSHAKE                  250
#define P2P_MAX_PEERS_IN_HANDSHAKE                      250
#define P2P_DEFAULT_CONNECTION_TIMEOUT                  5000
#define P2P_DEFAULT_SOCKS_CONNECT_TIMEOUT               45
#define P2P_DEFAULT_PING_CONNECTION_TIMEOUT             2000
#define P2P_DEFAULT_INVOKE_TIMEOUT                      60*2*1000
#define P2P_DEFAULT_HANDSHAKE_INVOKE_TIMEOUT            5000
#define P2P_DEFAULT_WHITELIST_CONNECTIONS_PERCENT       70
#define P2P_DEFAULT_ANCHOR_CONNECTIONS_COUNT            2
#define P2P_DEFAULT_SYNC_SEARCH_CONNECTIONS_COUNT       2
#define P2P_DEFAULT_LIMIT_RATE_UP                       8192
#define P2P_DEFAULT_LIMIT_RATE_DOWN                     32768

#define P2P_FAILED_ADDR_FORGET_SECONDS                  (60*60)
#define P2P_IP_BLOCKTIME                                (60*60*24)
#define P2P_IP_FAILS_BEFORE_BLOCK                       10
#define P2P_IDLE_CONNECTION_KILL_INTERVAL               (5*60)

#define P2P_SUPPORT_FLAG_FLUFFY_BLOCKS                  0x01
#define P2P_SUPPORT_FLAGS                               P2P_SUPPORT_FLAG_FLUFFY_BLOCKS

#define RPC_IP_FAILS_BEFORE_BLOCK                       3

#define CRYPTONOTE_NAME                         "ori"
#define CRYPTONOTE_BLOCKCHAINDATA_FILENAME      "data.mdb"
#define CRYPTONOTE_BLOCKCHAINDATA_LOCK_FILENAME "lock.mdb"
#define P2P_NET_DATA_FILENAME                   "p2pstate.bin"
#define MINER_CONFIG_FILE_NAME                  "miner_conf.json"

#define THREAD_STACK_SIZE                       5 * 1024 * 1024

// ORI: All hardfork versions set to 1 (single-version chain)
#define HF_VERSION_DYNAMIC_FEE                  1
#define HF_VERSION_MIN_MIXIN_4                  1
#define HF_VERSION_MIN_MIXIN_6                  1
#define HF_VERSION_CRYPTONIGHT_VARIANT_1        1
#define HF_VERSION_MIN_MIXIN_10                 1
#define HF_VERSION_MIN_MIXIN_15                 1
#define HF_VERSION_ENFORCE_RCT                  1
#define HF_VERSION_PER_BYTE_FEE                 1
#define HF_VERSION_SMALLER_BP                   1
#define HF_VERSION_LONG_TERM_BLOCK_WEIGHT       1
#define HF_VERSION_MIN_2_OUTPUTS                1
#define HF_VERSION_MIN_V2_COINBASE_TX           1
#define HF_VERSION_SAME_MIXIN                   1
#define HF_VERSION_REJECT_SIGS_IN_COINBASE      1
#define HF_VERSION_ENFORCE_MIN_AGE              1
#define HF_VERSION_EFFECTIVE_SHORT_TERM_MEDIAN_IN_PENALTY 1
#define HF_VERSION_EXACT_COINBASE               1
#define HF_VERSION_CLSAG                        1
#define HF_VERSION_DETERMINISTIC_UNLOCK_TIME    1
#define HF_VERSION_BULLETPROOF_PLUS             1
#define HF_VERSION_VIEW_TAGS                    1
#define HF_VERSION_2021_SCALING                 1

#define PER_KB_FEE_QUANTIZATION_DECIMALS        8
#define CRYPTONOTE_SCALING_2021_FEE_ROUNDING_PLACES 2

#define HASH_OF_HASHES_STEP                     512

#define DEFAULT_TXPOOL_MAX_WEIGHT               648000000ull

#define BULLETPROOF_MAX_OUTPUTS                 16
#define BULLETPROOF_PLUS_MAX_OUTPUTS            16

#define CRYPTONOTE_PRUNING_STRIPE_SIZE          4096
#define CRYPTONOTE_PRUNING_LOG_STRIPES          3
#define CRYPTONOTE_PRUNING_TIP_BLOCKS           5500

#define RPC_CREDITS_PER_HASH_SCALE ((float)(1<<24))

#define DNS_BLOCKLIST_LIFETIME (86400 * 8)

#define MAX_TX_EXTRA_SIZE                       1060

namespace config
{
  uint64_t const DEFAULT_FEE_ATOMIC_XMR_PER_KB = 20000; // 0.0002 ORI/kB
  uint8_t const FEE_CALCULATION_MAX_RETRIES = 10;
  uint64_t const DEFAULT_DUST_THRESHOLD = ((uint64_t)20000); // 0.0002 ORI
  uint64_t const BASE_REWARD_CLAMP_THRESHOLD = ((uint64_t)100000000);

  // NOTE: "ori" at START of address is mathematically impossible.
  // Base58 encodes 8 bytes of data; max value 2^64-1 ≈ 1.84e19.
  // First base58 char 'o' (index 46) requires ≥ 46 × 58^10 ≈ 1.98e19 → impossible.
  // Best feasible first char is 'j' (index 42) with a 2-byte varint prefix.
  // For simplicity, using prefix 120 → first char ≈ 'M' (unique to ORI).
  uint64_t const CRYPTONOTE_PUBLIC_ADDRESS_BASE58_PREFIX = 120;
  uint64_t const CRYPTONOTE_PUBLIC_INTEGRATED_ADDRESS_BASE58_PREFIX = 121;
  uint64_t const CRYPTONOTE_PUBLIC_SUBADDRESS_BASE58_PREFIX = 122;
  uint16_t const P2P_DEFAULT_PORT = 28080;
  uint16_t const RPC_DEFAULT_PORT = 28081;
  uint16_t const ZMQ_RPC_DEFAULT_PORT = 28082;
  boost::uuids::uuid const NETWORK_ID = { {
      0x6f, 0x72, 0x69, 0x00, 0x61, 0x6e, 0x6f, 0x6e,
      0x79, 0x6d, 0x6f, 0x75, 0x73, 0x2e, 0x6f, 0x72
    } }; // "orianonymous.or"
  // Genesis coinbase tx with embedded Indonesia government message
  // "The Indonesian government declined a $25-30 billion loan offer from the IMF and World Bank during meetings in Washington DC, held from April 13 to 17, 2026, citing a strong fiscal position."
  std::string const GENESIS_TX = "023c01ff00018086f7840d021c19ef6c3f26587a7ec00b3ac1bf72e91de8309789f73723f92391abeba808d5e1010171c0c0ed676caf278b5046a3f878e191056edb0a7ccce04ed9facc50a7daf58902bd0154686520496e646f6e657369616e20676f7665726e6d656e74206465636c696e65642061202432352d33302062696c6c696f6e206c6f616e206f666665722066726f6d2074686520494d4620616e6420576f726c642042616e6b20647572696e67206d656574696e677320696e2057617368696e67746f6e2044432c2068656c642066726f6d20417072696c20313320746f2031372c20323032362c20636974696e672061207374726f6e672066697363616c20706f736974696f6e2e00";
  uint32_t const GENESIS_NONCE = 10000;

  // Hash domain separators
  const char HASH_KEY_BULLETPROOF_EXPONENT[] = "bulletproof";
  const char HASH_KEY_BULLETPROOF_PLUS_EXPONENT[] = "bulletproof_plus";
  const char HASH_KEY_BULLETPROOF_PLUS_TRANSCRIPT[] = "bulletproof_plus_transcript";
  const char HASH_KEY_RINGDB[] = "ringdsb";
  const char HASH_KEY_SUBADDRESS[] = "SubAddr";
  const unsigned char HASH_KEY_ENCRYPTED_PAYMENT_ID = 0x8d;
  const unsigned char HASH_KEY_WALLET = 0x8c;
  const unsigned char HASH_KEY_WALLET_CACHE = 0x8d;
  const unsigned char HASH_KEY_BACKGROUND_CACHE = 0x8e;
  const unsigned char HASH_KEY_BACKGROUND_KEYS_FILE = 0x8f;
  const unsigned char HASH_KEY_MEMORY = 'k';
  const unsigned char HASH_KEY_MULTISIG[] = {'M', 'u', 'l', 't' , 'i', 's', 'i', 'g', 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00 };
  const unsigned char HASH_KEY_MULTISIG_KEY_AGGREGATION[] = "Multisig_key_agg";
  const unsigned char HASH_KEY_CLSAG_ROUND_MULTISIG[] = "CLSAG_round_ms_merge_factor";
  const unsigned char HASH_KEY_TXPROOF_V2[] = "TXPROOF_V2";
  const unsigned char HASH_KEY_CLSAG_ROUND[] = "CLSAG_round";
  const unsigned char HASH_KEY_CLSAG_AGG_0[] = "CLSAG_agg_0";
  const unsigned char HASH_KEY_CLSAG_AGG_1[] = "CLSAG_agg_1";
  const char HASH_KEY_MESSAGE_SIGNING[] = "ORIMessageSignature";
  const unsigned char HASH_KEY_MM_SLOT = 'm';
  const constexpr char HASH_KEY_MULTISIG_TX_PRIVKEYS_SEED[] = "multisig_tx_privkeys_seed";
  const constexpr char HASH_KEY_MULTISIG_TX_PRIVKEYS[] = "multisig_tx_privkeys";
  const constexpr char HASH_KEY_TXHASH_AND_MIXRING[] = "txhash_and_mixring";

  const uint32_t MULTISIG_MAX_SIGNERS{16};

  namespace testnet
  {
    uint64_t const CRYPTONOTE_PUBLIC_ADDRESS_BASE58_PREFIX = 130;
    uint64_t const CRYPTONOTE_PUBLIC_INTEGRATED_ADDRESS_BASE58_PREFIX = 131;
    uint64_t const CRYPTONOTE_PUBLIC_SUBADDRESS_BASE58_PREFIX = 132;
    uint16_t const P2P_DEFAULT_PORT = 38080;
    uint16_t const RPC_DEFAULT_PORT = 38081;
    uint16_t const ZMQ_RPC_DEFAULT_PORT = 38082;
    boost::uuids::uuid const NETWORK_ID = { {
        0x6f, 0x72, 0x69, 0x00, 0x74, 0x65, 0x73, 0x74,
        0x6e, 0x65, 0x74, 0x00, 0x00, 0x00, 0x00, 0x00
      } };
    std::string const GENESIS_TX = "023c01ff00018086f7840d021c19ef6c3f26587a7ec00b3ac1bf72e91de8309789f73723f92391abeba808d5e1010171c0c0ed676caf278b5046a3f878e191056edb0a7ccce04ed9facc50a7daf58902bd0154686520496e646f6e657369616e20676f7665726e6d656e74206465636c696e65642061202432352d33302062696c6c696f6e206c6f616e206f666665722066726f6d2074686520494d4620616e6420576f726c642042616e6b20647572696e67206d656574696e677320696e2057617368696e67746f6e2044432c2068656c642066726f6d20417072696c20313320746f2031372c20323032362c20636974696e672061207374726f6e672066697363616c20706f736974696f6e2e00";
    uint32_t const GENESIS_NONCE = 10001;
  }

  namespace stagenet
  {
    uint64_t const CRYPTONOTE_PUBLIC_ADDRESS_BASE58_PREFIX = 140;
    uint64_t const CRYPTONOTE_PUBLIC_INTEGRATED_ADDRESS_BASE58_PREFIX = 141;
    uint64_t const CRYPTONOTE_PUBLIC_SUBADDRESS_BASE58_PREFIX = 142;
    uint16_t const P2P_DEFAULT_PORT = 48080;
    uint16_t const RPC_DEFAULT_PORT = 48081;
    uint16_t const ZMQ_RPC_DEFAULT_PORT = 48082;
    boost::uuids::uuid const NETWORK_ID = { {
        0x6f, 0x72, 0x69, 0x00, 0x73, 0x74, 0x61, 0x67,
        0x65, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
      } };
    std::string const GENESIS_TX = "023c01ff00018086f7840d021c19ef6c3f26587a7ec00b3ac1bf72e91de8309789f73723f92391abeba808d5e1010171c0c0ed676caf278b5046a3f878e191056edb0a7ccce04ed9facc50a7daf58902bd0154686520496e646f6e657369616e20676f7665726e6d656e74206465636c696e65642061202432352d33302062696c6c696f6e206c6f616e206f666665722066726f6d2074686520494d4620616e6420576f726c642042616e6b20647572696e67206d656574696e677320696e2057617368696e67746f6e2044432c2068656c642066726f6d20417072696c20313320746f2031372c20323032362c20636974696e672061207374726f6e672066697363616c20706f736974696f6e2e00";
    uint32_t const GENESIS_NONCE = 10002;
  }
}

namespace cryptonote
{
  enum network_type : uint8_t
  {
    MAINNET = 0,
    TESTNET,
    STAGENET,
    FAKECHAIN,
    UNDEFINED = 255
  };
  struct config_t
  {
    uint64_t const CRYPTONOTE_PUBLIC_ADDRESS_BASE58_PREFIX;
    uint64_t const CRYPTONOTE_PUBLIC_INTEGRATED_ADDRESS_BASE58_PREFIX;
    uint64_t const CRYPTONOTE_PUBLIC_SUBADDRESS_BASE58_PREFIX;
    uint16_t const P2P_DEFAULT_PORT;
    uint16_t const RPC_DEFAULT_PORT;
    uint16_t const ZMQ_RPC_DEFAULT_PORT;
    boost::uuids::uuid const NETWORK_ID;
    std::string const GENESIS_TX;
    uint32_t const GENESIS_NONCE;
  };
  inline const config_t& get_config(network_type nettype)
  {
    static const config_t mainnet = {
      ::config::CRYPTONOTE_PUBLIC_ADDRESS_BASE58_PREFIX,
      ::config::CRYPTONOTE_PUBLIC_INTEGRATED_ADDRESS_BASE58_PREFIX,
      ::config::CRYPTONOTE_PUBLIC_SUBADDRESS_BASE58_PREFIX,
      ::config::P2P_DEFAULT_PORT,
      ::config::RPC_DEFAULT_PORT,
      ::config::ZMQ_RPC_DEFAULT_PORT,
      ::config::NETWORK_ID,
      ::config::GENESIS_TX,
      ::config::GENESIS_NONCE
    };
    static const config_t testnet = {
      ::config::testnet::CRYPTONOTE_PUBLIC_ADDRESS_BASE58_PREFIX,
      ::config::testnet::CRYPTONOTE_PUBLIC_INTEGRATED_ADDRESS_BASE58_PREFIX,
      ::config::testnet::CRYPTONOTE_PUBLIC_SUBADDRESS_BASE58_PREFIX,
      ::config::testnet::P2P_DEFAULT_PORT,
      ::config::testnet::RPC_DEFAULT_PORT,
      ::config::testnet::ZMQ_RPC_DEFAULT_PORT,
      ::config::testnet::NETWORK_ID,
      ::config::testnet::GENESIS_TX,
      ::config::testnet::GENESIS_NONCE
    };
    static const config_t stagenet = {
      ::config::stagenet::CRYPTONOTE_PUBLIC_ADDRESS_BASE58_PREFIX,
      ::config::stagenet::CRYPTONOTE_PUBLIC_INTEGRATED_ADDRESS_BASE58_PREFIX,
      ::config::stagenet::CRYPTONOTE_PUBLIC_SUBADDRESS_BASE58_PREFIX,
      ::config::stagenet::P2P_DEFAULT_PORT,
      ::config::stagenet::RPC_DEFAULT_PORT,
      ::config::stagenet::ZMQ_RPC_DEFAULT_PORT,
      ::config::stagenet::NETWORK_ID,
      ::config::stagenet::GENESIS_TX,
      ::config::stagenet::GENESIS_NONCE
    };
    switch (nettype)
    {
      case MAINNET: return mainnet;
      case TESTNET: return testnet;
      case STAGENET: return stagenet;
      case FAKECHAIN: return mainnet;
      default: throw std::runtime_error("Invalid network type");
    }
  };
}
