import json
import socket
import struct
import threading
import time

from utils import log_info, now

CMD_SIZE = 12


class Peer(threading.Thread):
    def __init__(self, network, sock, addr, outbound: bool):
        super().__init__(daemon=True)
        self.network = network
        self.sock = sock
        self.addr = addr
        self.outbound = outbound
        self.height = -1
        self.best_hash = None
        self.ua = "unknown"
        self.peer_best = None
        self.requested = set()
        self.pending_children = {}
        self._alive = True
        self._send_lock = threading.Lock()
        self.last_seen = now()
        self._ping_sent_at = None
        self._hb_log_at = now()

    def run(self):
        try:
            self.sock.settimeout(30)
            self.send_version()
            self._read_loop()
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        finally:
            self.close()

    def _read_loop(self):
        while self._alive:
            try:
                msg = read_msg(self.sock, self.network.cfg.max_msg_bytes, self.network.cfg.network_magic)
            except socket.timeout:
                if self._ping_sent_at and (now() - self._ping_sent_at) > 30:
                    log_info(f"P2P peer {self.addr} DEAD (no pong after ping) -> dropping")
                    break
                self._ping_sent_at = now()
                self.send("ping", self.network.cfg.network_magic)
                continue
            if msg is None:
                break
            cmd, payload = msg
            self._dispatch(cmd, payload)

    def _dispatch(self, cmd: str, payload: bytes):
        self.last_seen = now()
        if now() - self._hb_log_at >= 120:
            self._hb_log_at = now()
            log_info(f"P2P heartbeat peer={self.addr} alive (msg={cmd})")
        if cmd == "version":
            data = json.loads(payload)
            node = self.network.node
            peer_genesis = data.get("genesis")
            if peer_genesis and peer_genesis != node.chain.genesis_hash():
                raise ValueError("chain mismatch (genesis)")
            self.height = int(data.get("height", -1))
            self.peer_best = data.get("best_hash")
            self.ua = data.get("ua", "unknown")
            self.send("verack", b"{}")
            self.send_addr()
            if self.height > node.chain.storage.height() or (
                self.peer_best and self.peer_best != node.chain.tip()["hash"]
            ):
                self.network.request_blocks_from(self)
        elif cmd == "verack":
            self.network.node.on_peer_ready(self)
        elif cmd == "addr":
            data = json.loads(payload)
            self.network.learn_peers(data.get("peers", []))
        elif cmd == "getblocks":
            data = json.loads(payload)
            self.network.reply_blocks(self, data.get("from"), data.get("stop"))
        elif cmd == "getdata":
            data = json.loads(payload)
            for item in data.get("items", []):
                self.network.reply_item(self, item)
        elif cmd == "inv":
            data = json.loads(payload)
            wanted = []
            for item in data.get("items", []):
                if len(self.requested) >= 100:
                    break
                key = (item.get("type"), item.get("hash"))
                if self.network.node.knows(item) or key in self.requested:
                    continue
                self.requested.add(key)
                wanted.append(item)
            if wanted:
                self.send("getdata", json.dumps({"items": wanted}).encode())
        elif cmd == "block":
            data = json.loads(payload)
            self.network.node.on_peer_block_hex(data.get("block", ""), self)
        elif cmd == "tx":
            data = json.loads(payload)
            self.network.node.on_peer_tx_hex(data.get("tx", ""), self)
        elif cmd == "ping":
            if payload != self.network.cfg.network_magic:
                log_info(f"P2P peer {self.addr} sent ping with wrong magic -> drop")
                self.close()
                return
            self.send("pong", payload)
        elif cmd == "pong":
            if payload != self.network.cfg.network_magic:
                log_info(f"P2P peer {self.addr} sent pong with wrong magic -> drop")
                self.close()
                return
            self._ping_sent_at = None

    def send(self, command: str, payload: bytes):
        try:
            with self._send_lock:
                self.sock.sendall(encode_msg(self.network.cfg, command, payload))
        except OSError:
            self.close()

    def send_version(self):
        node = self.network.node
        data = {
            "version": 1,
            "services": 1,
            "port": self.network.cfg.p2p_port,
            "height": node.chain.storage.height(),
            "best_hash": node.chain.tip()["hash"],
            "genesis": node.chain.genesis_hash(),
            "ua": "ORI/0.2",
        }
        self.send("version", json.dumps(data).encode())

    def send_addr(self):
        peers = self.network.known_peers()[:10]
        self.send("addr", json.dumps({"peers": peers}).encode())

    def close(self):
        if not self._alive:
            return
        self._alive = False
        try:
            self.sock.close()
        except OSError:
            pass
        if getattr(self, "link_established", False):
            log_info(f"P2P disconnected peer={self.addr} outbound={self.outbound}")
        self.network.drop(self)


class Network:
    def __init__(self, cfg, node):
        self.cfg = cfg
        self.node = node
        self.peers = {}
        self.known = set()
        self._outbound = set()
        self._lock = threading.RLock()
        self._listener = None
        self._reconnect_loop = None
        self._running = False

    def start(self):
        from utils import log_info
        import os
        import json
        if not self.cfg.enable_p2p:
            return
        
        self.peers_file = os.path.join(self.cfg.data_dir, "peers.json")
        if os.path.exists(self.peers_file):
            try:
                with open(self.peers_file, "r") as f:
                    saved = json.load(f)
                    for p in saved:
                        if isinstance(p, dict) and "host" in p and "port" in p:
                            self.known.add((p["host"], int(p["port"])))
                log_info(f"Loaded {len(self.known)} known peer(s) from peers.json")
            except Exception:
                pass

        self._running = True
        for host, port in self.cfg.seed_peers:
            self.connect(host, int(port))
        with self._lock:
            known = list(self.known)
        for host, port in known:
            if len(self.peers) >= self.cfg.max_peers:
                break
            self.connect(host, int(port))
        self._listener = threading.Thread(target=self._accept_loop, daemon=True)
        self._listener.start()
        self._reconnect_loop = threading.Thread(target=self._reconnect_seeds, daemon=True)
        self._reconnect_loop.start()

    def _save_peers(self):
        import json
        import os
        try:
            with self._lock:
                snapshot = list(self.known)
            with open(self.peers_file, "w") as f:
                json.dump([{"host": h, "port": p} for h, p in snapshot], f)
        except Exception:
            pass

    def _reconnect_seeds(self):
        while self._running:
            time.sleep(15)
            with self._lock:
                targets = list(self.cfg.seed_peers) + list(self.known)
            for host, port in targets:
                if len(self.peers) >= self.cfg.max_peers:
                    break
                try:
                    self.connect(host, int(port))
                except Exception:
                    pass

    def stop(self):
        self._running = False
        if hasattr(self, "peers_file"):
            self._save_peers()
        with self._lock:
            for peer in list(self.peers.values()):
                peer.close()
            self.peers.clear()
        try:
            if self._listener is not None and getattr(self._listener, "_listener_sock", None) is not None:
                self._listener._listener_sock.close()
        except Exception:
            pass

    def _accept_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((self.cfg.p2p_host, self.cfg.p2p_port))
        except OSError as exc:
            log_info(f"P2P listen FAILED on {self.cfg.p2p_host}:{self.cfg.p2p_port} ({exc}) — no inbound peers")
            return
        s.listen(16)
        s.settimeout(1)
        self._listener._listener_sock = s
        while self._running:
            try:
                sock, addr = s.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if len(self.peers) >= self.cfg.max_peers:
                try:
                    sock.close()
                except OSError:
                    pass
                continue
            peer = Peer(self, sock, addr, outbound=False)
            if self._register(peer):
                peer.start()

    def connect(self, host: str, port):
        if not self.cfg.enable_p2p:
            return
        port = int(port)
        if port == self.cfg.p2p_port and host in (
            "127.0.0.1",
            "localhost",
            "::1",
            self.cfg.p2p_host,
        ):
            return
        with self._lock:
            key = (host, port)
            if key in self._outbound or key in self.peers:
                return
            if len(self.peers) >= self.cfg.max_peers:
                return
            self._outbound.add(key)
        try:
            sock = socket.create_connection((host, port), timeout=10)
        except OSError:
            self._outbound.discard(key)
            return
        peer = Peer(self, sock, (host, port), outbound=True)
        if self._register(peer):
            peer.start()

    def _register(self, peer: Peer) -> bool:
        with self._lock:
            if peer.addr in self.peers:
                peer.close()
                return False
            self.known.add(peer.addr)
            self.peers[peer.addr] = peer
            log_info(f"P2P connected peer={peer.addr[0]}:{peer.addr[1]} outbound={peer.outbound}")
            return True

    def drop(self, peer: Peer):
        with self._lock:
            self.peers.pop(peer.addr, None)
            if peer.outbound:
                self._outbound.discard(peer.addr)

    def peer_count(self) -> int:
        return len(self.peers)

    def known_peers(self) -> list:
        with self._lock:
            return [{"host": h, "port": p} for h, p in self.known]

    def learn_peers(self, peers: list):
        for p in peers:
            host, port = p.get("host"), int(p.get("port", 0))
            if not host or not 0 < port < 65536:
                continue
            if port == self.cfg.p2p_port and host in (
                self.cfg.p2p_host,
                "127.0.0.1",
                "localhost",
                "::1",
            ):
                continue
            with self._lock:
                if (host, port) in self.known:
                    continue
                self.known.add((host, port))
            self.connect(host, port)

    def broadcast(self, command: str, payload: bytes, exclude=None):
        with self._lock:
            targets = list(self.peers.values())
        for peer in targets:
            if peer is exclude:
                continue
            peer.send(command, payload)

    def broadcast_inv(self, kind: str, item_hash: str, exclude=None):
        payload = json.dumps({"items": [{"type": kind, "hash": item_hash}]}).encode()
        self.broadcast("inv", payload, exclude)

    def request_blocks_from(self, peer: Peer, from_hash: str = None):
        node = self.node
        data = {
            "from": from_hash or node.chain.tip()["hash"],
            "stop": "0" * 64,
        }
        peer.send("getblocks", json.dumps(data).encode())

    def request_block(self, peer: Peer, block_hash_hex: str):
        payload = json.dumps({"items": [{"type": "block", "hash": block_hash_hex}]})
        peer.send("getdata", payload.encode())

    def reply_blocks(self, peer: Peer, from_hash: str, stop_hash: str):
        node = self.node
        rows = node.storage.all_blocks()
        hashes = []
        start = None
        for i, row in enumerate(rows):
            if row["hash"] == from_hash:
                start = i + 1
                break
        if start is None:
            # Requester's tip not found in our chain (different/diverged chain).
            # Serve from block 1 onward so the requester can build the fork
            # forward instead of being forced into a slow backward traversal.
            hashes = [r["hash"] for r in rows[1:501]]
        else:
            for row in rows[start : start + 500]:
                if stop_hash and stop_hash != "0" * 64 and row["hash"] == stop_hash:
                    break
                hashes.append(row["hash"])
        items = [{"type": "block", "hash": h} for h in hashes]
        if not items:
            items = [{"type": "tx", "hash": "0"}]
        peer.send("inv", json.dumps({"items": items}).encode())

    def reply_item(self, peer: Peer, item: dict):
        node = self.node
        kind = item.get("type")
        item_hash = item.get("hash")
        if kind == "block":
            block = node.chain.block_by_hash(item_hash)
            if block is not None:
                peer.send("block", json.dumps({"block": block.to_hex()}).encode())
        elif kind == "tx":
            try:
                raw = bytes.fromhex(item_hash)
            except ValueError:
                return
            tx = node.mempool.get(raw)
            if tx is not None:
                peer.send("tx", json.dumps({"tx": tx.to_hex()}).encode())


def encode_msg(cfg, command: str, payload: bytes) -> bytes:
    cmd = command.encode()[:CMD_SIZE].ljust(CMD_SIZE, b"\x00")
    return cfg.network_magic + cmd + struct.pack("<I", len(payload)) + payload


def read_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def read_msg(sock: socket.socket, max_size: int, magic: bytes):
    header = read_exact(sock, 4 + CMD_SIZE + 4)
    if header is None:
        return None
    if header[0:4] != magic:
        raise ValueError("bad magic")
    length = struct.unpack_from("<I", header, 4 + CMD_SIZE)[0]
    if length > max_size:
        raise ValueError("message too large")
    payload = read_exact(sock, length)
    if payload is None:
        return None
    cmd = header[4:4 + CMD_SIZE].rstrip(b"\x00").decode()
    return cmd, payload