"""Live P2P handshake probe: does the remote speak the ORI wire protocol?"""
import json
import os
import socket
import sys

os.environ.setdefault("ORI_LOG_CONSOLE", "0")
os.environ.setdefault("ORI_LOG_FILE", "0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from p2p import encode_msg
from config import Config

GENESIS = "f7ac49f8a6a0872bd57a5a6fbde13b758e4f13ffa181239a6d12d192e5dc0000"


def probe(host, port):
    tag = f"{host}:{port}"
    try:
        s = socket.create_connection((host, port), timeout=10)
    except Exception as exc:
        print(f"{tag:42s} TCP-CONNECT FAIL: {exc}")
        return
    try:
        cfg = Config()
        payload = json.dumps({
            "version": 1, "services": 1, "port": 8033, "height": 0,
            "best_hash": GENESIS, "genesis": GENESIS, "ua": "probe/0.1",
        }).encode()
        s.sendall(encode_msg(cfg, "version", payload))
        s.settimeout(8)
        try:
            data = s.recv(4096)
            if not data:
                print(f"{tag:42s} CLOSED BY REMOTE, 0 bytes (bukan protokol P2P)")
            elif data[:4] == cfg.network_magic:
                cmd = data[4:16].rstrip(b"\x00").decode(errors="replace")
                print(f"{tag:42s} P2P OK — balasan '{cmd}' ({len(data)} B)")
            else:
                print(f"{tag:42s} BALASAN ASING ({len(data)} B): {data[:48]!r}")
        except socket.timeout:
            print(f"{tag:42s} TIMEOUT — tidak pernah membalas")
    finally:
        s.close()


for host, port in [
    ("sakura.proxy.rlwy.net", 24044),
    ("nozomi.proxy.rlwy.net", 32346),
]:
    probe(host, port)
