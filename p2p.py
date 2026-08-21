import json
import socket
import struct
import threading
import time
import ipaddress
from collections import defaultdict
from utils import log_info, now
from block import Block, BlockHeader
from pow import hash_meets_target

CMD_SIZE = 12

# P2P DoS Protection Constants
MSG_TOKEN_REFILL_RATE = 10.0      # tokens per second
MSG_TOKEN_BUCKET_SIZE = 100.0     # max tokens
MSG_TOKEN_COST_PER_KB = 1.0       # tokens per KB
BAN_SCORE_THRESHOLD = 100
BAN_SCORE_DECAY_PER_HOUR = 10     # decay per hour
MAX_INBOUND_PER_SUBNET = 3        # max inbound connections per /16
MAX_OUTBOUND_PER_SUBNET = 1       # max outbound connections per /16
ANCHOR_CONNECTIONS = 2            # minimum anchor connections to maintain
CONNECTION_RATE_LIMIT = 2.0       # max outbound connections per second
INBOUND_RATE_LIMIT_PER_SUBNET = 3 # max inbound per /16 per minute

# Ban score weights
BAN_SCORE = {
    "invalid_pow": 100,
    "invalid_merkle": 50,
    "invalid_block": 50,
    "invalid_tx": 10,
    "bad_magic": 50,
    "protocol_violation": 20,
    "timeout": 5,
    "large_message": 20,
    "addr_spam": 10,
    "invalid_header": 30,
}


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
        
        # Rate limiting (token bucket)
        self._msg_tokens = MSG_TOKEN_BUCKET_SIZE
        self._msg_tokens_last = now()
        
        # Ban score
        self.ban_score = 0
        self._ban_score_last_decay = now()
        
        # Reputation (0.0 to 1.0, higher = better)
        self.reputation = 0.5
        
        # Connection tracking
        self.link_established = False
        self._bytes_recv = 0
        self._bytes_recv_last_minute = now()
        
        # Subnet for diversity
        self._subnet = self._get_subnet(addr[0])
    
    def _get_subnet(self, host: str):
        try:
            ip = ipaddress.ip_address(host)
            if ip.version == 4:
                return ipaddress.ip_network(f"{ip}/16", strict=False)
            else:
                return ipaddress.ip_network(f"{ip}/32", strict=False)
        except ValueError:
            return None
    
    def _consume_msg_tokens(self, payload_bytes: int) -> bool:
        """Token bucket rate limiting. Returns True if allowed."""
        now_ts = now()
        elapsed = now_ts - self._msg_tokens_last
        self._msg_tokens = min(MSG_TOKEN_BUCKET_SIZE, self._msg_tokens + elapsed * MSG_TOKEN_REFILL_RATE)
        self._msg_tokens_last = now_ts
        
        cost = (payload_bytes / 1024.0) * MSG_TOKEN_COST_PER_KB
        if self._msg_tokens >= cost:
            self._msg_tokens -= cost
            return True
        return False
    
    def _add_ban_score(self, reason: str):
        """Add ban score and check threshold."""
        score = BAN_SCORE.get(reason, 10)
        self.ban_score += score
        self.reputation = max(0.0, self.reputation - 0.1)
        
        # Decay ban score over time
        now_ts = now()
        hours_elapsed = (now_ts - self._ban_score_last_decay) / 3600.0
        if hours_elapsed > 0:
            self.ban_score = max(0, self.ban_score - int(hours_elapsed * BAN_SCORE_DECAY_PER_HOUR))
            self._ban_score_last_decay = now_ts
        
        if self.ban_score >= BAN_SCORE_THRESHOLD:
            log_info(f"P2P peer {self.addr} banned (score={self.ban_score}, reason={reason})")
            self.close()
            return True
        return False
    
    def _track_bytes_received(self, payload_bytes: int):
        """Track bytes received for rate limiting."""
        now_ts = now()
        if now_ts - self._bytes_recv_last_minute > 60:
            self._bytes_recv = 0
            self._bytes_recv_last_minute = now_ts
        self._bytes_recv += payload_bytes
        if self._bytes_recv > 10 * 1024 * 1024:  # 10 MB/min
            self._add_ban_score("large_message")

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
            
            # Rate limiting: check token bucket
            if not self._consume_msg_tokens(len(payload)):
                self._add_ban_score("protocol_violation")
                break
            
            # Track bytes received
            self._track_bytes_received(len(payload))
            
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
                # Fast sync: headers-first if significantly behind
                local_height = node.chain.storage.height()
                if self.height - local_height > 10:
                    self.network.request_headers_from(self)
                else:
                    self.network.request_blocks_from(self)
        elif cmd == "verack":
            self.network.node.on_peer_ready(self)
        elif cmd == "addr":
            data = json.loads(payload)
            self.network.learn_peers(data.get("peers", []))
        elif cmd == "getblocks":
            data = json.loads(payload)
            self.network.reply_blocks(self, data.get("from"), data.get("stop"))
        elif cmd == "getheaders":
            data = json.loads(payload)
            self.network.reply_headers(self, data.get("from"), data.get("stop"), data.get("count", 2000))
        elif cmd == "getdata":
            data = json.loads(payload)
            for item in data.get("items", []):
                self.network.reply_item(self, item)
        elif cmd == "inv":
            data = json.loads(payload)
            wanted = []
            for item in data.get("items", []):
                if len(self.requested) >= 1000:
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
            block_hex = data.get("block", "")
            if block_hex:
                try:
                    block = Block.from_hex(block_hex)
                    # Basic validation before forwarding to node
                    if not block.merkle_ok():
                        self._add_ban_score("invalid_merkle")
                        return
                    if not hash_meets_target(block.hash(), block.header.bits):
                        self._add_ban_score("invalid_pow")
                        return
                except Exception:
                    self._add_ban_score("invalid_block")
                    return
            self.network.node.on_peer_block_hex(block_hex, self)
        elif cmd == "headers":
            data = json.loads(payload)
            headers = data.get("headers", [])
            if headers:
                # Validate headers chain
                prev_hash = None
                for h_hex in headers:
                    try:
                        header = BlockHeader.from_hex(h_hex)
                        if prev_hash is not None and header.prev_hash.hex() != prev_hash:
                            self._add_ban_score("invalid_header")
                            return
                        if not hash_meets_target(header.hash(), header.bits):
                            self._add_ban_score("invalid_pow")
                            return
                        prev_hash = header.hash().hex()
                    except Exception:
                        self._add_ban_score("invalid_header")
                        return
            self.network.on_peer_headers(self, headers)
        elif cmd == "tx":
            data = json.loads(payload)
            tx_hex = data.get("tx", "")
            if tx_hex:
                # Basic tx size check
                if len(tx_hex) > self.network.cfg.max_block_bytes * 2:
                    self._add_ban_score("invalid_tx")
                    return
            self.network.node.on_peer_tx_hex(tx_hex, self)
        elif cmd == "ping":
            if payload != self.network.cfg.network_magic:
                log_info(f"P2P peer {self.addr} sent ping with wrong magic -> drop")
                self._add_ban_score("bad_magic")
                self.close()
                return
            self.send("pong", payload)
        elif cmd == "pong":
            if payload != self.network.cfg.network_magic:
                log_info(f"P2P peer {self.addr} sent pong with wrong magic -> drop")
                self._add_ban_score("bad_magic")
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
        """Send addr message with only high-quality peers."""
        with self.network._lock:
            # Filter: connected > 10 min, good reputation, not banned
            now_ts = now()
            good_peers = []
            for peer in self.network.peers.values():
                if not peer.link_established:
                    continue
                if now_ts - peer.last_seen > 600:  # 10 min
                    continue
                if peer.reputation < 0.3:
                    continue
                if peer.ban_score > 50:
                    continue
                good_peers.append({"host": peer.addr[0], "port": peer.addr[1]})
            
            # Limit per subnet
            by_subnet = defaultdict(list)
            for p in good_peers:
                try:
                    ip = ipaddress.ip_address(p["host"])
                    subnet = str(ipaddress.ip_network(f"{ip}/16", strict=False)) if ip.version == 4 else p["host"]
                except ValueError:
                    continue
                by_subnet[subnet].append(p)
            
            selected = []
            for subnet, peers in by_subnet.items():
                selected.extend(peers[:2])  # Max 2 per subnet
                if len(selected) >= 10:
                    break
            
            if selected:
                self.send("addr", json.dumps({"peers": selected}).encode())

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
        
        # Connection rate limiting
        self._last_outbound_connect = 0.0
        self._inbound_attempts = defaultdict(list)  # subnet -> [timestamps]
        
        # Subnet tracking for diversity
        self._subnet_peers = defaultdict(set)  # subnet -> set of peer addrs
        self._anchor_peers = set()  # anchor peer addresses (never evict)
        
        # Banned peers (persisted)
        self._banned_peers = {}  # addr -> ban_expiry_timestamp
        self._load_banned_peers()
    
    def _load_banned_peers(self):
        import os
        import json
        ban_file = os.path.join(self.cfg.data_dir, "banned_peers.json")
        if os.path.exists(ban_file):
            try:
                with open(ban_file, "r") as f:
                    data = json.load(f)
                    now_ts = now()
                    for addr_str, expiry in data.items():
                        host, port = addr_str.split(":")
                        if expiry > now_ts:
                            self._banned_peers[(host, int(port))] = expiry
            except Exception:
                pass
    
    def _save_banned_peers(self):
        import os
        import json
        ban_file = os.path.join(self.cfg.data_dir, "banned_peers.json")
        try:
            with open(ban_file, "w") as f:
                json.dump({f"{h}:{p}": exp for (h, p), exp in self._banned_peers.items()}, f)
        except Exception:
            pass
    
    def _get_subnet(self, host: str):
        try:
            ip = ipaddress.ip_address(host)
            if ip.version == 4:
                return ipaddress.ip_network(f"{ip}/16", strict=False)
            else:
                return ipaddress.ip_network(f"{ip}/32", strict=False)
        except ValueError:
            return None
    
    def _is_banned(self, addr) -> bool:
        expiry = self._banned_peers.get(addr)
        if expiry and expiry > now():
            return True
        elif expiry:
            del self._banned_peers[addr]
        return False
    
    def _ban_peer(self, addr, duration_hours=24):
        expiry = now() + duration_hours * 3600
        self._banned_peers[addr] = expiry
        self._save_banned_peers()

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
        threading.Thread(target=self._connect_known, daemon=True).start()
        self._listener = threading.Thread(target=self._accept_loop, daemon=True)
        self._listener.start()
        self._reconnect_loop = threading.Thread(target=self._reconnect_seeds, daemon=True)
        self._reconnect_loop.start()

    def _connect_known(self):
        while self._running:
            with self._lock:
                known = list(self.known)
            
            # Group by subnet for diversity
            by_subnet = defaultdict(list)
            for host, port in known:
                subnet = self._get_subnet(host)
                if subnet:
                    by_subnet[subnet].append((host, port))
            
            # Try to connect to one peer per subnet, preferring high reputation
            for subnet, peers in by_subnet.items():
                if not self._running:
                    return
                if len(self.peers) >= self.cfg.max_peers:
                    break
                
                # Check outbound limit per subnet
                outbound_in_subnet = sum(1 for p in self.peers.values() 
                                         if p.outbound and p._subnet == subnet)
                if outbound_in_subnet >= MAX_OUTBOUND_PER_SUBNET:
                    continue
                
                # Rate limit: max CONNECTION_RATE_LIMIT per second
                now_ts = now()
                if now_ts - self._last_outbound_connect < 1.0 / CONNECTION_RATE_LIMIT:
                    time.sleep(1.0 / CONNECTION_RATE_LIMIT)
                
                # Pick best peer by reputation
                best_peer = max(peers, key=lambda p: self._peer_reputation(p))
                self.connect(*best_peer)
                self._last_outbound_connect = now()
            
            time.sleep(5)  # Wait before next round

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
                threading.Thread(target=self.connect, args=(host, port), daemon=True).start()

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

    def _peer_reputation(self, peer_addr) -> float:
        """Get peer reputation (0.0 to 1.0). Higher = better."""
        with self._lock:
            peer = self.peers.get(peer_addr)
            if peer:
                return peer.reputation
        # Unknown peer: neutral reputation
        return 0.5
    
    def _register(self, peer: Peer) -> bool:
        with self._lock:
            if peer.addr in self.peers:
                peer.close()
                return False
            if self._is_banned(peer.addr):
                peer.close()
                return False
            
            # Check inbound limit per subnet
            if not peer.outbound:
                inbound_in_subnet = sum(1 for p in self.peers.values() 
                                        if not p.outbound and p._subnet == peer._subnet)
                if inbound_in_subnet >= MAX_INBOUND_PER_SUBNET:
                    peer.close()
                    return False
                
                # Rate limit inbound per subnet per minute
                now_ts = now()
                subnet_attempts = self._inbound_attempts[peer._subnet]
                subnet_attempts[:] = [t for t in subnet_attempts if now_ts - t < 60]
                if len(subnet_attempts) >= INBOUND_RATE_LIMIT_PER_SUBNET:
                    peer.close()
                    return False
                subnet_attempts.append(now_ts)
            
            self.known.add(peer.addr)
            self.peers[peer.addr] = peer
            self._subnet_peers[peer._subnet].add(peer.addr)
            peer.link_established = True
            log_info(f"P2P connected peer={peer.addr[0]}:{peer.addr[1]} outbound={peer.outbound} subnet={peer._subnet}")
            
            # Promote to anchor if we have few anchors and peer is outbound
            if peer.outbound and len(self._anchor_peers) < ANCHOR_CONNECTIONS:
                self._anchor_peers.add(peer.addr)
            
            return True

    def drop(self, peer: Peer):
        with self._lock:
            self.peers.pop(peer.addr, None)
            if peer.outbound:
                self._outbound.discard(peer.addr)
            self._subnet_peers[peer._subnet].discard(peer.addr)
            self._anchor_peers.discard(peer.addr)
            
            # If we lost an anchor, try to promote another outbound peer
            if len(self._anchor_peers) < ANCHOR_CONNECTIONS:
                for p in self.peers.values():
                    if p.outbound and p.addr not in self._anchor_peers:
                        self._anchor_peers.add(p.addr)
                        break

    def peer_count(self) -> int:
        return len(self.peers)

    def known_peers(self) -> list:
        with self._lock:
            return [{"host": h, "port": p} for h, p in self.known]

    def learn_peers(self, peers: list):
        fresh = []
        for p in peers:
            host, port = p.get("host"), int(p.get("port", 0))
            if not host or not 0 < port < 65536:
                continue
            # Filter non-routable addresses
            try:
                ip = ipaddress.ip_address(host)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast:
                    continue
                if ip.version == 4:
                    # Filter CGNAT (100.64.0.0/10)
                    if ipaddress.ip_network("100.64.0.0/10").overlaps(ipaddress.ip_network(f"{ip}/32")):
                        continue
                    # Filter reserved
                    if ipaddress.ip_network("192.0.0.0/24").overlaps(ipaddress.ip_network(f"{ip}/32")):
                        continue
            except ValueError:
                continue
            
            if port == self.cfg.p2p_port and host in (
                self.cfg.p2p_host,
                "127.0.0.1",
                "localhost",
                "::1",
            ):
                continue
            if _is_cgnat(host):
                continue
            with self._lock:
                if (host, port) in self.known:
                    continue
                if self._is_banned((host, port)):
                    continue
                self.known.add((host, port))
                fresh.append((host, port))
        for host, port in fresh[:8]:
            threading.Thread(target=self.connect, args=(host, port), daemon=True).start()

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

    def request_headers_from(self, peer: Peer, from_hash: str = None):
        """Request headers-first sync (Satoshi-style fast sync)."""
        node = self.node
        data = {
            "from": from_hash or node.chain.tip()["hash"],
            "stop": "0" * 64,
            "count": 2000,
        }
        peer.send("getheaders", json.dumps(data).encode())

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

    def reply_headers(self, peer: Peer, from_hash: str, stop_hash: str, count: int = 2000):
        node = self.node
        rows = node.storage.all_blocks()
        headers = []
        start = None
        for i, row in enumerate(rows):
            if row["hash"] == from_hash:
                start = i + 1
                break
        if start is None:
            start = 1
        for row in rows[start : start + count]:
            if stop_hash and stop_hash != "0" * 64 and row["hash"] == stop_hash:
                break
            block = Block.from_bytes(row["raw"])
            headers.append(block.header.to_hex())
        peer.send("headers", json.dumps({"headers": headers}).encode())

    def on_peer_headers(self, peer: Peer, headers_hex: list):
        """Process received headers — verify PoW chain, then request blocks in parallel."""
        if not headers_hex:
            return
        node = self.node
        prev_hash = None
        valid_headers = []
        for h_hex in headers_hex:
            try:
                header = BlockHeader.from_hex(h_hex)
            except Exception:
                break
            if prev_hash is not None and header.prev_hash.hex() != prev_hash:
                break
            if not hash_meets_target(header.hash(), header.bits):
                break
            valid_headers.append((header.hash().hex(), header))
            prev_hash = header.hash().hex()
        
        if not valid_headers:
            return
        
        # Request blocks in parallel from multiple peers
        self._request_blocks_parallel(valid_headers)

    def _request_blocks_parallel(self, headers: list):
        """Request blocks from multiple peers in parallel."""
        with self._lock:
            peers = list(self.peers.values())
        if not peers:
            return
        
        # Distribute blocks across peers (round-robin)
        for i, (block_hash, header) in enumerate(headers):
            peer = peers[i % len(peers)]
            if peer._alive:
                self.request_block(peer, block_hash)

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