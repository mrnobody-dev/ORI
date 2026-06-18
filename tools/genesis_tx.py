#!/usr/bin/env python3
"""
Genesis Transaction Generator for ORI
Creates a valid coinbase transaction with the Indonesia government message.

IMPORTANT: Variant type tags (txin_gen, txout_to_key, extra fields) are
RAW BYTES (uint8_t), NOT varint-encoded. Varint is only used for:
  - version, unlock_time, counts, amounts, heights, lengths.

Run: python3 tools/genesis_tx.py
Output: GENESIS_TX hex string for cryptonote_config.h

Message:
"The Indonesian government declined a $25-30 billion loan offer from the IMF 
and World Bank during meetings in Washington DC, held from April 13 to 17, 2026, 
citing a strong fiscal position."
"""

import hashlib
import struct

COIN = 100000000        # 8 decimal places
INITIAL_REWARD = 35 * COIN  # 35 ORI
UNLOCK_TIME = 60        # MINED_MONEY_UNLOCK_WINDOW
HEIGHT = 0

# Variant type tags (raw bytes, NOT varint-encoded)
TXIN_GEN_TAG    = 0xff
TXOUT_TO_KEY_TAG = 0x02
TX_EXTRA_PUBKEY_TAG = 0x01
TX_EXTRA_NONCE_TAG  = 0x02

def varint_encode(value):
    """Encode an integer as Monero-style varint (little-endian 7-bit groups)"""
    result = bytearray()
    while value > 0x7f:
        result.append((value & 0x7f) | 0x80)
        value >>= 7
    result.append(value & 0x7f)
    return bytes(result)

def sha256(data):
    return hashlib.sha256(data).digest()

def construct_genesis_tx(message):
    """
    Construct a Monero v1 coinbase transaction.
    Serialization format based on binary_archive.h:
    - VARINT_FIELD for version, unlock_time, counts, amounts, heights
    - FIELD for variant tags (raw byte), keys (32 bytes raw)
    """
    # Derive deterministic keys from message hash
    msg_hash = sha256(message.encode('utf-8'))
    msg_hash2 = sha256(msg_hash)
    
    # Use first 32 bytes as the output key (burn address)
    output_key = msg_hash
    # Use second hash as the tx pubkey
    tx_pubkey = msg_hash2
    
    # Build transaction binary
    tx = bytearray()
    
    # --- TRANSACTION PREFIX ---
    # version (varint) — must be 2 for HF_VERSION_MIN_V2_COINBASE_TX=1
    # At genesis height 0, hf_version=1, and the prevalidate_miner_transaction
    # check requires: tx.version > 1 OR hf_version < HF_VERSION_MIN_V2_COINBASE_TX(1).
    # Since hf_version=1 at genesis, tx.version must be > 1 (i.e., version 2).
    tx.extend(varint_encode(2))
    # unlock_time (varint)
    tx.extend(varint_encode(UNLOCK_TIME))
    
    # --- VIN ---
    # vin count (varint)
    tx.extend(varint_encode(1))
    # txin_gen variant tag (RAW BYTE, not varint)
    tx.append(TXIN_GEN_TAG)
    # height (varint)
    tx.extend(varint_encode(HEIGHT))
    
    # --- VOUT ---
    # vout count (varint)
    tx.extend(varint_encode(1))
    # amount (varint)
    tx.extend(varint_encode(INITIAL_REWARD))
    # txout_to_key variant tag (RAW BYTE)
    tx.append(TXOUT_TO_KEY_TAG)
    # 32-byte public key (raw bytes)
    tx.extend(output_key)
    
    # --- EXTRA ---
    extra = bytearray()
    # Extra tag 0x01 = tx_pub_key (RAW BYTE)
    extra.append(TX_EXTRA_PUBKEY_TAG)
    extra.extend(tx_pubkey)
    # Extra tag 0x02 = nonce/message data (RAW BYTE)
    extra.append(TX_EXTRA_NONCE_TAG)
    msg_bytes = message.encode('utf-8')
    # nonce length (varint)
    extra.extend(varint_encode(len(msg_bytes)))
    # nonce data (raw bytes)
    extra.extend(msg_bytes)
    
    # Extra blob size (varint) + blob data
    tx.extend(varint_encode(len(extra)))
    tx.extend(extra)
    
    # --- RCT SIGNATURES (v2 coinbase with RCTTypeNull) ---
    # For v2 transactions, rct_signatures are serialized.
    # For RCTTypeNull, only the type byte (0x00) is serialized,
    # then the function returns immediately. No prunable data.
    tx.append(0x00)  # rct_signatures.type = RCTTypeNull (0)
    
    return bytes(tx)

def main():
    message = (
        "The Indonesian government declined a $25-30 billion loan offer "
        "from the IMF and World Bank during meetings in Washington DC, "
        "held from April 13 to 17, 2026, citing a strong fiscal position."
    )
    
    tx_blob = construct_genesis_tx(message)
    
    print("=" * 70)
    print("ORI GENESIS TRANSACTION")
    print("=" * 70)
    print(f"Message: {message}")
    print(f"Message length: {len(message)} bytes")
    print(f"Output amount: {INITIAL_REWARD} atomic units ({INITIAL_REWARD/COIN} ORI)")
    print(f"Tx blob size: {len(tx_blob)} bytes")
    print()
    print("GENESIS_TX hex:")
    print(tx_blob.hex())
    print()
    print("Output key:", output_key.hex())
    print("Tx pubkey:", tx_pubkey.hex())
    print()
    
    # Verify decoding
    print("=" * 70)
    print("DECODED STRUCTURE")
    print("=" * 70)
    decode_tx(tx_blob)

def decode_tx(data):
    i = 0
    def read_varint():
        nonlocal i
        val = 0
        shift = 0
        while i < len(data):
            byte = data[i]
            i += 1
            val |= (byte & 0x7f) << shift
            shift += 7
            if not (byte & 0x80):
                return val
        return val
    
    def read_byte():
        nonlocal i
        val = data[i]
        i += 1
        return val
    
    print(f"[{i:3d}] version: {read_varint()}")
    print(f"[{i:3d}] unlock_time: {read_varint()}")
    
    vin_count = read_varint()
    print(f"[{i:3d}] vin_count: {vin_count}")
    for v in range(vin_count):
        vtype = read_byte()  # variant tag is RAW BYTE
        if vtype == 0xff:
            print(f"[{i:3d}] vin[{v}] type: 0xff (txin_gen), height: {read_varint()}")
        else:
            print(f"[{i:3d}] vin[{v}] type: {vtype}")
    
    vout_count = read_varint()
    print(f"[{i:3d}] vout_count: {vout_count}")
    for v in range(vout_count):
        amount = read_varint()
        print(f"[{i:3d}] vout[{v}] amount: {amount} ({amount/COIN} ORI)")
        ttype = read_byte()  # variant tag is RAW BYTE
        key = data[i:i+32]; i += 32
        print(f"[{i:3d}] vout[{v}] target_type: {ttype}, key: {key.hex()}")
    
    extra_len = read_varint()
    print(f"[{i:3d}] extra size: {extra_len}")
    extra_end = i + extra_len
    while i < extra_end:
        tag = read_byte()  # extra tag is RAW BYTE
        if tag == 0x01:
            key = data[i:i+32]; i += 32
            print(f"[{i:3d}] extra tag 0x01 (tx_pubkey): {key.hex()}")
        elif tag == 0x02:
            msg_len = read_varint()
            msg = data[i:i+msg_len]; i += msg_len
            print(f"[{i:3d}] extra tag 0x02 (message): \"{msg.decode('utf-8')}\"")
        else:
            print(f"[{i:3d}] extra unknown tag: {tag}")
            i += 1
    
    read_byte()  # rct_signatures.type (RCTTypeNull = 0)
    print(f"[{i:3d}] rct_signatures.type: RCTTypeNull (no more rct data)")

if __name__ == '__main__':
    output_key = hashlib.sha256(
        "The Indonesian government declined a $25-30 billion loan offer "
        "from the IMF and World Bank during meetings in Washington DC, "
        "held from April 13 to 17, 2026, citing a strong fiscal position.".encode()
    ).digest()
    tx_pubkey = hashlib.sha256(output_key).digest()
    main()
