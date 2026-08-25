"""Stale-chain template guard + dead-peer pruning tests."""
import os
import sys
import tempfile

os.environ.setdefault("ORI_LOG_CONSOLE", "0")
os.environ.setdefault("ORI_LOG_FILE", "0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from fastapi.testclient import TestClient
from node import Node


def make_node(data_dir):
    cfg = Config(data_dir=data_dir, enable_p2p=True, api_host="127.0.0.1",
                 coinbase_maturity=3)
    n = Node(cfg)
    n.chain.load()
    return n


class FakePeer:
    """Enough of a Peer for the height-scan in the template guard."""

    def __init__(self, height, handshake_complete=True):
        self.addr = ("203.0.113.9", 1)
        self.height = height
        self.handshake_complete = handshake_complete


def main():
    root = tempfile.mkdtemp(prefix="ori_guard_")
    node = make_node(os.path.join(root, "n"))

    from api import create_app

    app = create_app(node)
    client = TestClient(app)

    addr = "ori1q" + "0" * 34

    # 1) no peers -> template served (no false positive)
    r = client.get("/mining/template", params={"address": addr})
    assert r.status_code == 200, r.text

    # 2) peer slightly ahead (+1/+2) -> still served (normal sync jitter)
    node.network.peers[("203.0.113.9", 1)] = FakePeer(node.storage.height() + 2)
    r = client.get("/mining/template", params={"address": addr})
    assert r.status_code == 200, r.text

    # 3) peer far ahead (fresh datadir joining real network) -> REFUSED 503
    node.network.peers[("203.0.113.9", 1)] = FakePeer(4900)
    r = client.get("/mining/template", params={"address": addr})
    assert r.status_code == 503, r.text
    assert "STALE CHAIN" in r.json()["detail"]

    # 4) incomplete-handshake peers don't count toward the guard
    node.network.peers[("203.0.113.9", 1)] = FakePeer(4900, handshake_complete=False)
    r = client.get("/mining/template", params={"address": addr})
    assert r.status_code == 200, r.text

    # ── dead-peer pruning on save ────────────────────────────────────────
    net = node.network
    net.peers_file = os.path.join(root, "peers.json")
    with net._lock:
        net.known.add(("bad.example.com", 1234))          # dies pre-handshake x5
        net._protocol_fails[("bad.example.com", 1234)] = 5
        net.known.add(("good.example.com", 1234))         # healthy peer
    net._save_peers()
    saved = {p["host"] for p in json_load(net.peers_file)}
    assert "bad.example.com" not in saved, "dead endpoint persisted"
    assert "good.example.com" in saved

    print("STALE_GUARD_OK")


def json_load(path):
    import json

    return json.load(open(path))


if __name__ == "__main__":
    main()
