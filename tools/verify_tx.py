#!/usr/bin/env python3
"""Verify genesis transaction hex"""
hex_str = "013c01ff0100018086f7840d021c19ef6c3f26587a7ec00b3ac1bf72e91de8309789f73723f92391abeba808d5e1010171c0c0ed676caf278b5046a3f878e191056edb0a7ccce04ed9facc50a7daf58902bd0154686520496e646f6e657369616e20676f7665726e6d656e74206465636c696e65642061202432352d33302062696c6c696f6e206c6f616e206f666665722066726f6d2074686520494d4620616e6420576f726c642042616e6b20647572696e67206d656574696e677320696e2057617368696e67746f6e2044432c2068656c642066726f6d20417072696c20313320746f2031372c20323032362c20636974696e672061207374726f6e672066697363616c20706f736974696f6e2e00"
data = bytes.fromhex(hex_str)
i = 0

def read_varint():
    global i
    val = 0
    shift = 0
    while i < len(data):
        b = data[i]
        i += 1
        val |= (b & 0x7f) << shift
        shift += 7
        if not (b & 0x80):
            return val

print(f"Total length: {len(data)} bytes")
print(f"[{i:3d}] 0x{data[i]:02x} = version ({read_varint()})")
print(f"[{i:3d}] unlock_time = {read_varint()}")
print(f"[{i:3d}] vin_count = {read_varint()}")
print(f"[{i:3d}] 0x{data[i]:02x} = txin_gen tag"); i += 1
print(f"[{i:3d}] height = {read_varint()}")
print(f"[{i:3d}] vout_count = {read_varint()}")
amount = read_varint()
print(f"[{i:3d}] amount = {amount} ({amount / 100000000} ORI)")
print(f"[{i:3d}] 0x{data[i]:02x} = txout_to_key tag"); i += 1
key = data[i:i+32]; i += 32
print(f"[{i:3d}] output_key = {key.hex()}")
extra_size = read_varint()
print(f"[{i:3d}] extra_size = {extra_size}")
# tag 0x01
print(f"[{i:3d}] 0x{data[i]:02x} = extra tag"); i += 1
tx_pubkey = data[i:i+32]; i += 32
print(f"[{i:3d}] tx_pubkey = {tx_pubkey.hex()}")
# tag 0x02
print(f"[{i:3d}] 0x{data[i]:02x} = extra tag"); i += 1
msg_len = read_varint()
print(f"[{i:3d}] msg_len = {msg_len}")
msg = data[i:i+msg_len].decode("utf-8"); i += msg_len
print(f"[{i:3d}] message = \"{msg}\"")
print(f"[{i:3d}] sig_count = {read_varint()}")
print(f"Total consumed: {i}")
