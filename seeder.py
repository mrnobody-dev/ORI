import argparse
import json
import os
import random
import secrets
import socket
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from config import Config
from dns import _read_name, build_answer
from node import Node


class Seeder:
    def __init__(
        self,
        domain: str = "seed.ori",
        dns_port: int = 5353,
        http_port: int = 8123,
        p2p_port: int = 8353,
        dns_p2p_port: int = 8033,
        bootstrap: str = None,
        announce_ips: str = None,
        scan_interval: int = 10,
    ):
        self.domain = domain.strip(".")
        self.dns_port = dns_port
        self.http_port = http_port
        self.p2p_port = p2p_port
        self.dns_p2p_port = dns_p2p_port
        self.bootstrap = bootstrap
        self.announce = [ip for ip in (announce_ips or "").split(",") if ip]
        self.scan_interval = scan_interval
        self.register_token = os.environ.get("ORI_SEEDER_TOKEN", "")
        self.peers = {}
        self._lock = threading.Lock()
        self._running = False
        cfg = Config(
            data_dir=tempfile.mkdtemp(prefix="ori-seeder-"),
            enable_p2p=True,
            p2p_port=p2p_port,
            seed_peers=[],
        )
        self.node = Node(cfg)

    def start(self):
        self.node.start()
        if self.bootstrap:
            host, _, port = self.bootstrap.partition(":")
            self.node.add_peer(host, int(port or 8033))
            with self._lock:
                self.peers[(host, int(port or 8033))] = time.time()
        for ip in self.announce:
            with self._lock:
                self.peers[(ip, self.dns_p2p_port)] = time.time()
        self._running = True
        threading.Thread(target=self._dns_loop, daemon=True).start()
        threading.Thread(target=self._scan_loop, daemon=True).start()
        threading.Thread(target=self._http_loop, daemon=True).start()

    def stop(self):
        self._running = False
        self.node.stop()

    def _dns_loop(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", self.dns_port))
        sock.settimeout(1)
        while self._running:
            try:
                data, addr = sock.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                name, _ = _read_name(data, 12)
                if name == self.domain:
                    ips = self._pick_ips(3)
                    sock.sendto(build_answer(data, ips, ttl=60), addr)
                else:
                    sock.sendto(build_answer(data, [], ttl=60), addr)
            except Exception:
                pass

    def _scan_loop(self):
        while self._running:
            try:
                for p in self.node.network.known_peers():
                    with self._lock:
                        self.peers[(p["host"], p["port"])] = time.time()
                cutoff = time.time() - 24 * 3600
                for key in list(self.peers):
                    if self.peers[key] < cutoff:
                        del self.peers[key]
            except Exception:
                pass
            time.sleep(self.scan_interval)

    def _pick_ips(self, count: int) -> list:
        ips = []
        with self._lock:
            candidates = [host for host, _ in self.peers]
        if self.announce:
            candidates = [ip for ip in candidates if ip in self.announce]
        if not candidates and self.bootstrap:
            candidates = [self.bootstrap.partition(":")[0]]
        return random.sample(candidates, min(count, len(candidates)))

    def _http_loop(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code: int, body: dict):
                payload = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                if self.path == "/count":
                    with outer._lock:
                        n = len(outer.peers)
                    self._send(200, {"count": n})
                elif self.path == "/":
                    with outer._lock:
                        peers = [
                            {"host": h, "port": p} for h, p in outer.peers
                        ]
                    self._send(200, {"peers": peers})

            def do_POST(self):
                if self.path != "/register":
                    self._send(404, {"error": "not found"})
                    return
                token = outer.register_token
                if not token:
                    self._send(403, {"error": "register disabled"})
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if length > 4096:
                        self._send(400, {"error": "body too large"})
                        return
                    body = json.loads(self.rfile.read(length))
                    provided = str(body.get("token", ""))
                    if len(provided) != len(token) or not secrets.compare_digest(provided, token):
                        self._send(401, {"error": "unauthorized"})
                        return
                    host, port = body["host"], int(body.get("port", 8033))
                    if not host or not (0 < port < 65536):
                        self._send(400, {"error": "bad body"})
                        return
                    with outer._lock:
                        outer.peers[(host, port)] = time.time()
                    self._send(200, {"status": "registered"})
                except Exception:
                    self._send(400, {"error": "bad body"})

        server = ThreadingHTTPServer(("0.0.0.0", self.http_port), Handler)
        while self._running:
            server.handle_request()


def main():
    parser = argparse.ArgumentParser(description="ORI DNS seeder")
    parser.add_argument("--domain", default=os.environ.get("ORI_SEEDER_DOMAIN", "seed.ori"))
    parser.add_argument("--dns-port", type=int, default=int(os.environ.get("ORI_SEEDER_DNS_PORT", "5353")))
    parser.add_argument("--http-port", type=int, default=int(os.environ.get("ORI_SEEDER_HTTP_PORT", "8123")))
    parser.add_argument("--p2p-port", type=int, default=int(os.environ.get("ORI_SEEDER_P2P_PORT", "8353")))
    parser.add_argument("--dns-p2p-port", type=int, default=int(os.environ.get("ORI_SEEDER_DNS_P2P_PORT", "8033")))
    parser.add_argument("--bootstrap", default=os.environ.get("ORI_SEEDER_BOOTSTRAP"))
    parser.add_argument("--announce", default=os.environ.get("ORI_SEEDER_ANNOUNCE"))
    args = parser.parse_args()

    seeder = Seeder(
        domain=args.domain,
        dns_port=args.dns_port,
        http_port=args.http_port,
        p2p_port=args.p2p_port,
        dns_p2p_port=args.dns_p2p_port,
        bootstrap=args.bootstrap,
        announce_ips=args.announce,
    )
    seeder.start()
    print(
        f"ORI seeder up -> dns {seeder.domain} udp:{seeder.dns_port} "
        f"http:{seeder.http_port} p2p:{seeder.p2p_port} "
        f"bootstrap: {seeder.bootstrap or 'none'} announce: {seeder.announce or 'none'}"
    )
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        seeder.stop()
        print("seeder stopped")


if __name__ == "__main__":
    main()