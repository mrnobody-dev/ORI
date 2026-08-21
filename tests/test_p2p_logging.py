import socket

from config import Config
from p2p import Network, Peer


class CapturingLogger:
    def __init__(self):
        self.debugs = []
        self.infos = []

    def debug(self, category, message, **fields):
        self.debugs.append((category, message, fields))

    def info(self, category, message, **fields):
        self.infos.append((category, message, fields))

    def warn(self, category, message, **fields):
        pass

    def error(self, category, message, **fields):
        pass


def test_peer_lifecycle_is_debug_and_summary_info(tmp_path, monkeypatch):
    capture = CapturingLogger()
    monkeypatch.setattr("p2p.logger", capture)

    cfg = Config(
        data_dir=str(tmp_path),
        enable_p2p=False,
        p2p_peer_log_interval_seconds=60,
    )
    network = Network(cfg, node=None)
    sock_a, sock_b = socket.socketpair()
    try:
        peer = Peer(network, sock_a, ("203.0.113.10", 8033), outbound=True)

        assert network._register(peer) is True
        peer.close()
        with network._lock:
            network._maybe_log_peer_summary_locked(force=True)

        info_messages = [message for _, message, _ in capture.infos]
        debug_messages = [message for _, message, _ in capture.debugs]

        assert "Peer connected" in debug_messages
        assert "Peer disconnected" in debug_messages
        assert "Peer connected" not in info_messages
        assert "Peer disconnected" not in info_messages
        assert "Peer lifecycle summary" in info_messages
    finally:
        sock_a.close()
        sock_b.close()


def test_manual_dns_peer_is_retained_as_known_peer(tmp_path):
    cfg = Config(data_dir=str(tmp_path), enable_p2p=False)
    network = Network(cfg, node=None)

    assert network.add_manual_peer("nozomi.proxy.rlwy.net", 32346) is True

    assert {"host": "nozomi.proxy.rlwy.net", "port": 32346} in network.known_peers()
    assert network._get_subnet("nozomi.proxy.rlwy.net") == "dns:nozomi.proxy.rlwy.net"
