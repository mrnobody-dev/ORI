"""In-process node + wallet controller for the Qt GUI."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QTimer, Signal

from api import VERSION, create_app
from bech32 import validate_address
from config import Config
from node import Node
from tx import NULL_HASH
from wallet import (
    DEFAULT_WALLET,
    WalletError,
    apply_mempool_utxos,
    create_account,
    decrypt_wallet,
    encrypt_wallet,
    format_ori,
    load_default_wallet,
    load_wallet,
    ori_to_sats,
    plan_send,
    save_wallet,
    sign_planned_wallet,
    wallet_is_encrypted,
)
from ecdsa import SECP256k1
from utils import sha256d

COIN = 100_000_000


class LogBridge(logging.Handler, QObject):
    message = Signal(str)

    def __init__(self):
        logging.Handler.__init__(self)
        QObject.__init__(self)

    def emit(self, record):
        try:
            self.message.emit(self.format(record))
        except Exception:
            pass


class NodeController(QObject):
    snapshot_ready = Signal(dict)
    history_ready = Signal(list)
    log_line = Signal(str)
    started = Signal(bool)  # bool = first_run
    error = Signal(str)

    # Shared signed-message prefix (also used by the standalone verifier).
    _MSG_PREFIX = b"ORI Signed Message:\n"

    @staticmethod
    def verify_message_static(address: str, message: str, sig_hex: str) -> bool:
        """Verify a message signature against any bech32 address.

        Implements ECDSA public-key recovery directly on secp256k1 (the
        python-ecdsa recovery API proved unreliable for normalized low-S
        signatures): try both R parities / overflow cases, derive the signer
        pubkey, compress it and compare hash160 against the address program.
        """
        from bech32 import address_to_program
        from utils import hash160 as _h160

        curve_p = SECP256k1.curve.p()
        n = SECP256k1.order
        gx, gy = SECP256k1.generator.x(), SECP256k1.generator.y()
        G = (gx, gy)

        def _add(P, Q):
            if P is None:
                return Q
            if Q is None:
                return P
            if P[0] == Q[0] and (P[1] + Q[1]) % curve_p == 0:
                return None
            if P == Q:
                lam = (3 * P[0] * P[0]) % curve_p * pow(2 * P[1] % curve_p,
                                                        curve_p - 2, curve_p) % curve_p
            else:
                lam = (Q[1] - P[1]) % curve_p * pow((Q[0] - P[0]) % curve_p,
                                                    curve_p - 2, curve_p) % curve_p
            x3 = (lam * lam - P[0] - Q[0]) % curve_p
            y3 = (lam * (P[0] - x3) - P[1]) % curve_p
            return x3, y3

        def _mul(k, P):
            R = None
            A = P
            while k:
                if k & 1:
                    R = _add(R, A)
                A = _add(A, A)
                k >>= 1
            return R

        try:
            sig = bytes.fromhex(sig_hex)
            if len(sig) != 64:
                return False
            r = int.from_bytes(sig[:32], "big")
            s = int.from_bytes(sig[32:], "big")
            if not (0 < r < n and 0 < s < n):
                return False
            program = address_to_program(address)
            digest = sha256d(NodeController._MSG_PREFIX + message.encode("utf-8"))
            e = int.from_bytes(digest, "big")

            alpha = (pow(r, 3, curve_p) + 7) % curve_p
            beta = pow(alpha, (curve_p + 1) // 4, curve_p)
            for par in (0, 1):
                y = beta if beta % 2 == par else (curve_p - beta)
                R = (r, y)
                # skip if R not on curve (x >= n case ignored: r < n << p)
                rinv = pow(r, n - 2, n)
                eG = _mul(e % n, G)
                neg_eG = (eG[0], (-eG[1]) % curve_p)
                Q = _mul(rinv, _add(_mul(s, R), neg_eG))
                if Q is None:
                    continue
                comp = (b"\x02" if Q[1] % 2 == 0 else b"\x03") + \
                    int(Q[0]).to_bytes(32, "big")
                if _h160(comp) == program:
                    return True
            return False
        except Exception:
            return False

    def __init__(self, cfg: Config, wallet_path: str = DEFAULT_WALLET):
        super().__init__()
        self.cfg = cfg
        self.wallet_path = wallet_path
        self.meta_path = os.path.splitext(wallet_path)[0] + ".qt.json"
        self.node: Node | None = None
        self.wallet: dict = {}
        self._passphrase: str | None = None  # None = not encrypted / not unlocked
        self._wallet_encrypted: bool = False
        self.meta: dict = {
            "last_scan_height": -1,
            "labels": {},
            "history": [],
            "receive_requests": [],
            "address_book": [],
            "default_account": "",
            "fee_tier": 3,
        }
        self._api_server = None
        self._lock = threading.RLock()
        self._started_at = time.time()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self._scan_budget = 500
        # Adaptive polling: fast while catching up / active, relaxed when idle.
        self._interval_active_ms = 1000
        self._interval_idle_ms = 3000
        self._last_snapshot_key = None
        self._meta_dirty = False
        self._meta_last_save = 0.0
        # Wallet auto-lock (minutes; 0 = disabled).
        self.autolock_minutes = int(self.meta.get("autolock_minutes", 0) or 0)
        self._autolock_timer = QTimer(self)
        self._autolock_timer.setSingleShot(True)
        self._autolock_timer.timeout.connect(self._on_autolock)
        self._history_cap = 5000
        self._requests_cap = 500

        self._log_bridge = LogBridge()
        self._log_bridge.setFormatter(
            logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%dT%H:%M:%SZ")
        )
        self._log_bridge.message.connect(self.log_line.emit)
        _ori_logger = logging.getLogger("ori")
        if self._log_bridge not in _ori_logger.handlers:
            _ori_logger.addHandler(self._log_bridge)

    # --- lifecycle ---------------------------------------------------------

    def start_node(self):
        """Heavy init — safe to call from a worker thread."""
        self.node = Node(self.cfg)
        self.node.start()
        self._start_api()

    def load_wallet_and_timers(self, passphrase: str = None):
        """GUI-thread follow-up after start_node()."""
        first_run = False
        self._wallet_encrypted = wallet_is_encrypted(self.wallet_path)
        if self._wallet_encrypted:
            self._passphrase = passphrase
            if passphrase:
                self.wallet = load_wallet(self.wallet_path, passphrase)
            else:
                self.wallet = {}
        else:
            self._passphrase = None
            self.wallet = load_default_wallet(self.wallet_path)
        self._load_meta()
        if self._wallet_encrypted and self._passphrase is None:
            # Wallet locked — skip auto-account creation; unlock dialog later.
            self._timer.start()
            self.refresh()
            return
        if not self.wallet:
            first_run = True
            name, info = create_account(self.wallet, self.wallet_path)
            self.meta["default_account"] = name
            self._save_meta()
        elif not self.meta.get("default_account") or self.meta["default_account"] not in self.wallet:
            self.meta["default_account"] = next(iter(self.wallet))
            self._save_meta()
        self._timer.start()
        self.refresh()
        self.started.emit(first_run)

    def _start_api(self):
        """Start the in-process FastAPI/REST API on cfg.api_port.

        Security default: bind to 127.0.0.1 unless the user explicitly
        configured a public host via env/config. A public bind without an
        API token makes protected endpoints (send tx, mining, addpeer) return
        403 even for local CLI tools — binding loopback keeps every local
        feature working while staying unreachable from the network.

        If the port is already taken (e.g. an old node still running), try
        the next free port instead of silently failing. Exposes
        `cfg.api_port` and `api_url` so the UI can show the real endpoint.
        """
        import socket
        import uvicorn

        # Loopback-by-default for the desktop wallet's embedded API.
        if os.environ.get("BTPY_API_HOST") is None and \
                not getattr(self.cfg, "_api_host_explicit", False):
            self.cfg.api_host = "127.0.0.1"

        port = self.cfg.api_port
        free_port = None
        for _ in range(100):
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((self.cfg.api_host, port))
                free_port = port
            except OSError:
                free_port = None
            finally:
                probe.close()
            if free_port is not None:
                break
            port += 1
        if free_port is None:
            self._api_server = None
            return
        self.cfg.api_port = free_port
        try:
            app = create_app(self.node, lifespan=None)
            config = uvicorn.Config(
                app,
                host=self.cfg.api_host,
                port=free_port,
                log_level="warning",
                access_log=False,
                # Frozen builds (PyInstaller) miss uvicorn's default logging
                # config classes -> 'Unable to configure formatter default'.
                log_config=None,
            )
            self._api_server = uvicorn.Server(config)
            self._api_thread = threading.Thread(target=self._api_server.run, daemon=True)
            self._api_thread.start()
        except Exception as exc:
            from utils import logger, LogCategory

            logger.warn(LogCategory.API, "Embedded API failed to start", error=str(exc))
            self._api_server = None

    def shutdown(self):
        self._timer.stop()
        self._save_meta(force=True)
        # Cleanly stop the embedded API server (release the port).
        if self._api_server is not None:
            try:
                self._api_server.should_exit = True
            except Exception:
                pass
            self._api_server = None
        if self.node:
            try:
                self.node.stop()
            except Exception:
                pass

    # --- wallet persistence ------------------------------------------------

    def _load_meta(self):
        if not os.path.exists(self.meta_path):
            return
        try:
            with open(self.meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.meta.update(data)
        except (OSError, json.JSONDecodeError):
            pass

    def _save_meta(self, force: bool = False):
        """Debounced meta persistence — at most once per 30s unless forced.

        History is pruned to `_history_cap` records so the meta file stays
        small (it previously grew unbounded and reached multi-MB sizes)."""
        self._meta_dirty = True
        now_ts = time.time()
        if not force and now_ts - self._meta_last_save < 30:
            return
        self._meta_last_save = now_ts
        self._meta_dirty = False
        try:
            hist = self.meta.get("history") or []
            if len(hist) > self._history_cap:
                hist.sort(key=lambda r: (r.get("timestamp", 0), r.get("txid", "")), reverse=True)
                self.meta["history"] = hist[: self._history_cap]
            reqs = self.meta.get("receive_requests") or []
            if len(reqs) > self._requests_cap:
                self.meta["receive_requests"] = reqs[-self._requests_cap:]
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(self.meta, f, indent=2)
        except OSError:
            pass

    def accounts(self) -> list:
        return list(self.wallet.items())

    def default_account(self) -> tuple:
        name = self.meta.get("default_account") or next(iter(self.wallet), "")
        return name, self.wallet.get(name, {})

    def addresses(self) -> set:
        return {info["address"] for info in self.wallet.values()}

    def label_of(self, address: str) -> str:
        if address in self.meta.get("labels", {}):
            return self.meta["labels"][address]
        for name, info in self.wallet.items():
            if info["address"] == address:
                return name
        return ""

    def set_label(self, address: str, label: str):
        self.meta.setdefault("labels", {})[address] = label
        self._save_meta()

    def new_receiving_address(self, label: str = "") -> tuple:
        n = 1
        while f"receive_{n}" in self.wallet:
            n += 1
        name, info = create_account(self.wallet, self.wallet_path, f"receive_{n}")
        if label:
            self.set_label(info["address"], label)
        self._save_meta(force=True)  # force-save so label/account survives a crash
        return name, info

    def add_receive_request(self, entry: dict):
        self.meta.setdefault("receive_requests", []).insert(0, entry)
        self._save_meta(force=True)

    # --- address book ------------------------------------------------------

    def book_list(self) -> list:
        return list(self.meta.get("address_book", []))

    def book_add(self, label: str, address: str) -> bool:
        from bech32 import validate_address
        if not validate_address(address, self.cfg.network_hrp):
            raise WalletError("invalid ORI address")
        book = self.meta.setdefault("address_book", [])
        for entry in book:
            if entry.get("address") == address:
                if label:
                    entry["label"] = label
                    self._save_meta(force=True)
                return False
        book.append({"label": label, "address": address})
        self._save_meta(force=True)
        return True

    def book_remove(self, address: str):
        book = self.meta.setdefault("address_book", [])
        self.meta["address_book"] = [e for e in book if e.get("address") != address]
        self._save_meta(force=True)

    def book_set_label(self, address: str, label: str):
        for entry in self.meta.setdefault("address_book", []):
            if entry.get("address") == address:
                entry["label"] = label
                break
        self._save_meta(force=True)

    # --- node snapshot -----------------------------------------------------

    def connected_peers(self) -> list:
        if not self.node:
            return []
        with self.node.network._lock:
            peers = list(self.node.network.peers.values())
        out = []
        for p in peers:
            out.append({
                "host": p.addr[0],
                "port": p.addr[1],
                "outbound": p.outbound,
                "ready": bool(getattr(p, "handshake_complete", False)),
                "age_sec": max(0, int(time.time() - getattr(p, "connected_at", time.time()))),
                "height": p.height,
                "user_agent": p.ua,
                "best_hash": p.peer_best or "",
            })
        return out

    def normalize_peer_target(self, host: str, port: int) -> tuple[str, int]:
        raw = (host or "").strip()
        if not raw:
            raise WalletError("peer host is required")
        explicit_scheme = "://" in raw
        parsed = urlparse(raw if "://" in raw else f"//{raw}")
        if explicit_scheme and parsed.scheme in ("http", "https"):
            raise WalletError(
                "that is a REST API URL, not a P2P address. "
                "Use the node's P2P host:port instead."
            )
        clean_host = parsed.hostname or raw
        clean_port = parsed.port or int(port)
        clean_host = clean_host.strip("[]")
        if not clean_host:
            raise WalletError("peer host is required")
        if not (0 < int(clean_port) < 65536):
            raise WalletError("invalid port")
        return clean_host, int(clean_port)

    def add_peer(self, host: str, port: int):
        if not self.node:
            raise WalletError("node not started")
        host, port = self.normalize_peer_target(host, port)
        self.node.add_peer(host.strip(), int(port))
        return host, int(port)

    def _balances(self) -> dict:
        node = self.node
        addrs = self.addresses()
        available = 0
        immature = 0
        for addr in addrs:
            available += node.chain.balance(addr)
            immature += node.chain.immature_balance(addr)

        pending_in = 0
        pending_out = 0
        mempool = node.mempool.summary()
        for entry in mempool:
            for out in entry.get("outputs", []):
                if out.get("script_pubkey") in addrs:
                    pending_in += int(out.get("value", 0))
            for txin in entry.get("inputs", []):
                prev = node.chain.get_tx(txin.get("prev_txid") or "")
                if not prev:
                    continue
                prev_tx, _ = prev
                vout = int(txin.get("prev_vout", 0))
                if 0 <= vout < len(prev_tx.outputs):
                    script = prev_tx.outputs[vout].script_pubkey.decode(errors="replace")
                    if script in addrs:
                        pending_out += prev_tx.outputs[vout].value
        pending = pending_in - pending_out
        # Immature coinbase rewards are reserved for mining, not part of the
        # spendable balance — keep them out of Available, Pending and Total.
        return {
            "available": available,
            "pending": pending,
            "immature": immature,
            "total": available + pending,
        }

    def refresh(self):
        if not self.node:
            return
        try:
            self._scan_history_step()
            snap = self._build_snapshot()
            # UI dirty-check: skip widget rebuilds when nothing changed.
            key = (
                snap.get("height"), snap.get("best_hash"), snap.get("peers"),
                snap.get("mempool"), snap.get("balances", {}).get("available"),
                snap.get("balances", {}).get("pending"), snap.get("synced"),
                snap.get("behind"), len(snap.get("history_count", ()) or ()),
                self.meta.get("history") and self.meta["history"][0].get("txid", "")
                if self.meta.get("history") else "",
                self.is_locked(),
            )
            changed = key != self._last_snapshot_key
            # Adaptive cadence: relax to idle interval when nothing happens
            # and we're in sync; tighten while catching up or busy.
            interval = (self._interval_active_ms
                        if (not snap.get("synced", True) or snap.get("mempool", 0)
                           or self._meta_dirty or snap.get("height") != getattr(self, "_last_height_seen", -1))
                        else self._interval_idle_ms)
            self._last_height_seen = snap.get("height")
            if self._timer.interval() != interval:
                self._timer.setInterval(interval)
            if not changed:
                self._maybe_flush_meta()
                return
            self._last_snapshot_key = key
            self.snapshot_ready.emit(snap)
            self.history_ready.emit(list(self.meta.get("history", [])))
            self._maybe_flush_meta()
        except Exception as exc:
            self.error.emit(str(exc))

    def _maybe_flush_meta(self):
        if self._meta_dirty and time.time() - self._meta_last_save >= 30:
            self._save_meta(force=True)

    def _build_snapshot(self) -> dict:
        node = self.node
        tip = node.chain.tip()
        height = tip["height"]
        peers = self.connected_peers()
        peer_heights = [p["height"] for p in peers if p["height"] >= 0]
        best_peer = max(peer_heights) if peer_heights else height
        behind = max(0, best_peer - height)
        synced = behind == 0
        last_row = node.storage.block_by_height(height)
        last_time = last_row["timestamp"] if last_row else 0
        name, info = self.default_account()
        mempool = node.mempool.summary()
        api_display_host = node.cfg.api_host
        if api_display_host in ("0.0.0.0", "::", ""):
            api_display_host = "127.0.0.1"
        return {
            "version": VERSION,
            "coin": node.cfg.coin_name,
            "height": height,
            "best_hash": tip["hash"],
            "difficulty": tip["difficulty"],
            "total_work": str(tip["work"]),
            "peers": len(peers),
            "peer_list": peers,
            "known_peers": node.network.known_peers(),
            "peer_failures": node.network.recent_peer_failures(),
            "mempool": len(mempool),
            "mempool_txs": mempool,
            "balances": self._balances(),
            "synced": synced,
            "behind": behind,
            "best_peer_height": best_peer,
            "last_block_time": last_time,
            "p2p_port": node.cfg.p2p_port,
            "api_port": node.cfg.api_port,
            "api_host": node.cfg.api_host,
            "api_ready": self._api_server is not None,
            "api_url": f"http://{api_display_host}:{node.cfg.api_port}/docs"
            if self._api_server is not None
            else "",
            "data_dir": os.path.abspath(node.cfg.data_dir),
            "uptime": int(time.time() - self._started_at),
            "default_account": name,
            "default_address": info.get("address", ""),
            "accounts": [
                {"name": n, "address": i["address"], "label": self.label_of(i["address"])}
                for n, i in self.wallet.items()
            ],
            "fee_tier": int(self.meta.get("fee_tier", 3)),
            "block_time": node.cfg.block_time_seconds,
            "receive_requests": list(self.meta.get("receive_requests", [])),
            "supply_sats": node.chain.utxo.total_supply(),
            "utxo_count": node.chain.utxo.count(),
        }

    # --- history scan ------------------------------------------------------

    def _scan_history_step(self):
        node = self.node
        addrs = self.addresses()
        if not addrs:
            return
        tip = node.storage.height()
        last = int(self.meta.get("last_scan_height", -1))
        end = min(tip, last + self._scan_budget)
        changed = False
        by_txid = {h["txid"]: h for h in self.meta.get("history", [])}

        for height in range(last + 1, end + 1):
            block = node.chain.block_at(height)
            if block is None:
                continue
            ts = block.header.timestamp
            for tx in block.transactions:
                rec = self._classify_tx(tx, addrs, height=height, timestamp=ts, mempool=False)
                if rec is None:
                    continue
                by_txid[rec["txid"]] = rec
                changed = True
        self.meta["last_scan_height"] = end

        mempool_ids = set()
        for entry in node.mempool.summary():
            txid = entry["txid"]
            mempool_ids.add(txid)
            raw = node.mempool.get(bytes.fromhex(txid))
            if raw is None:
                continue
            rec = self._classify_tx(raw, addrs, height=-1, timestamp=int(time.time()), mempool=True)
            if rec is None:
                continue
            by_txid[txid] = rec
            changed = True

        history = []
        for rec in by_txid.values():
            if rec.get("mempool") and rec["txid"] not in mempool_ids:
                rec = dict(rec)
                rec["mempool"] = False
                if rec.get("height", -1) < 0:
                    found = node.chain.get_tx(rec["txid"])
                    if found:
                        _, entry = found
                        rec["height"] = entry["height"]
            if rec.get("height", -1) >= 0:
                rec["confirmations"] = tip - rec["height"] + 1
            else:
                rec["confirmations"] = 0
            history.append(rec)

        history.sort(key=lambda r: (r.get("timestamp", 0), r.get("txid", "")), reverse=True)
        self.meta["history"] = history
        if changed or last != end:
            self._save_meta()

    def _classify_tx(self, tx, addrs: set, height: int, timestamp: int, mempool: bool):
        received = 0
        sent = 0
        from_addr = ""
        to_addr = ""
        is_coinbase = tx.is_coinbase()
        for txin in tx.inputs:
            if txin.prev_txid == NULL_HASH:
                continue
            prev = self.node.chain.get_tx(txin.prev_txid.hex())
            if not prev:
                continue
            prev_tx, _ = prev
            if txin.prev_vout >= len(prev_tx.outputs):
                continue
            out = prev_tx.outputs[txin.prev_vout]
            addr = out.script_pubkey.decode(errors="replace")
            if addr in addrs:
                sent += out.value
                from_addr = addr
        for out in tx.outputs:
            addr = out.script_pubkey.decode(errors="replace")
            if addr in addrs:
                received += out.value
                if not to_addr:
                    to_addr = addr
            elif not to_addr:
                to_addr = addr
        if sent == 0 and received == 0:
            return None
        if is_coinbase:
            # Default: mined reward
            kind = "generate"
            if height >= 0:
                mature_at = height + self.node.cfg.coinbase_maturity
                if self.node.storage.height() < mature_at:
                    kind = "immature"
            amount = received
            address = to_addr
        elif sent > 0:
            kind = "send"
            amount = received - sent
            address = to_addr
        else:
            kind = "receive"
            amount = received
            address = to_addr
        fee = 0
        if sent > 0:
            fee = sent - sum(o.value for o in tx.outputs)
            if fee < 0:
                fee = 0
        return {
            "txid": tx.txid().hex(),
            "type": kind,
            "amount_sats": amount,
            "fee_sats": fee,
            "address": address,
            "from_address": from_addr,
            "label": self.label_of(address),
            "height": height,
            "timestamp": timestamp,
            "mempool": mempool,
            "confirmations": 0 if mempool or height < 0 else 1,
        }

    def tx_detail(self, txid: str) -> dict:
        node = self.node
        found = node.chain.get_tx(txid)
        if found:
            tx, entry = found
            height = entry["height"]
            block = node.chain.block_at(height)
            ts = block.header.timestamp if block else 0
            confirmations = node.storage.height() - height + 1
            mempool = False
            position = entry.get("position")
        else:
            tx = node.mempool.get(bytes.fromhex(txid))
            if tx is None:
                raise WalletError("transaction not found")
            height, block_hash, position = None, None, None
            ts, confirmations, mempool = int(time.time()), 0, True
        if found:
            block_hash = entry["block_hash"]
        rec = None
        try:
            rec = self._classify_tx(tx, self.addresses(), height if height is not None else -1, ts, mempool)
        except Exception:
            pass
        inputs_total = 0
        for txin in tx.inputs:
            if txin.prev_txid == NULL_HASH:
                continue
            prev = node.chain.get_tx(txin.prev_txid.hex())
            if prev is not None:
                prev_tx, _ = prev
                if txin.prev_vout < len(prev_tx.outputs):
                    inputs_total += prev_tx.outputs[txin.prev_vout].value
        outputs_total = sum(o.value for o in tx.outputs)
        fee = inputs_total - outputs_total if inputs_total else 0
        return {
            "txid": txid,
            "confirmations": confirmations,
            "height": height,
            "block_hash": block_hash,
            "position": position,
            "mempool": mempool,
            "timestamp": ts,
            "type": rec.get("type", "") if rec else ("generate" if tx.is_coinbase() else ""),
            "amount_sats": rec.get("amount_sats", 0) if rec else 0,
            "address": rec.get("address", "") if rec else "",
            "label": rec.get("label", "") if rec else "",
            "fee_sats": max(fee, 0),
            "size": len(tx.serialize()),
            "version": tx.version,
            "locktime": tx.locktime,
            "hex": tx.to_hex(),
        }

    # --- wallet encryption -------------------------------------------------

    def is_locked(self) -> bool:
        return self._wallet_encrypted and self._passphrase is None

    def is_encrypted(self) -> bool:
        return self._wallet_encrypted

    def unlock_wallet(self, passphrase: str, timeout_minutes: int = 0) -> bool:
        """Decrypt wallet with given passphrase. Returns True on success.

        `timeout_minutes > 0` arms the auto-lock timer (walletpassphrase-style)."""
        try:
            self.wallet = load_wallet(self.wallet_path, passphrase)
            self._passphrase = passphrase
            if timeout_minutes > 0:
                self.autolock_minutes = timeout_minutes
            if self.autolock_minutes > 0:
                self._autolock_timer.start(self.autolock_minutes * 60_000)
            return True
        except WalletError:
            return False

    def lock_wallet(self):
        """Clear in-memory decrypted wallet keys."""
        self._passphrase = None
        self.wallet = {}
        self._autolock_timer.stop()

    def _on_autolock(self):
        if self.is_encrypted() and not self.is_locked():
            self.lock_wallet()

    def set_autolock(self, minutes: int):
        """Configure auto-lock (0 disables). Persists in meta."""
        minutes = max(0, int(minutes))
        self.autolock_minutes = minutes
        self.meta["autolock_minutes"] = minutes
        self._save_meta(force=True)
        if minutes > 0 and not self.is_locked():
            self._autolock_timer.start(minutes * 60_000)
        else:
            self._autolock_timer.stop()

    def encrypt_wallet_with(self, passphrase: str):
        """Encrypt (or re-encrypt) the wallet file."""
        if not self.wallet:
            raise WalletError("wallet is empty")
        save_wallet(self.wallet_path, self.wallet, passphrase)
        self._passphrase = passphrase
        self._wallet_encrypted = True

    def load_wallet_file(self, path: str, passphrase: str = None):
        """Load a different wallet file, resetting history scan."""
        new_wallet = load_wallet(path, passphrase)
        self.wallet_path = path
        self.meta_path = os.path.splitext(path)[0] + ".qt.json"
        self.wallet = new_wallet
        self._passphrase = passphrase
        self._wallet_encrypted = wallet_is_encrypted(path)
        self.meta = {
            "last_scan_height": -1,
            "labels": {},
            "history": [],
            "receive_requests": [],
            "address_book": [],  # preserved across wallet-file switches
            "default_account": next(iter(new_wallet), ""),
            "fee_tier": 3,
        }
        self._load_meta()
        self._save_meta(force=True)
        self.refresh()

    # --- send --------------------------------------------------------------

    def wallet_utxos(self) -> list:
        """All wallet UTXOs (confirmed + mempool change), for coin control."""
        if not self.node:
            return []
        mempool = self.node.mempool.summary()
        tip = self.node.storage.height()
        out = []
        for addr in self.addresses():
            confirmed = self.node.chain.utxos_of(addr)
            for u in apply_mempool_utxos(confirmed, mempool, addr):
                u = dict(u)
                u["mempool"] = u.get("height", -1) < 0
                u["confirmations"] = tip - u["height"] + 1 if u["height"] >= 0 else 0
                u["label"] = self.label_of(addr)
                out.append(u)
        return out

    def address_detail(self, address: str) -> dict:
        """Return UTXOs and balance for a single address (used by AddressDetailDialog)."""
        if not self.node:
            return {"utxos": []}
        mempool = self.node.mempool.summary()
        tip = self.node.storage.height()
        confirmed = self.node.chain.utxos_of(address)
        utxos = []
        for u in apply_mempool_utxos(confirmed, mempool, address):
            u = dict(u)
            u["mempool"] = u.get("height", -1) < 0
            u["confirmations"] = tip - u["height"] + 1 if u["height"] >= 0 else 0
            utxos.append(u)
        return {"utxos": utxos}

    def _spendable_utxos(self) -> list:
        """Confirmed spendable UTXOs merged with mempool change outputs."""
        mempool = self.node.mempool.summary()
        utxos = []
        for addr in self.addresses():
            confirmed = self.node.chain.utxos_of(addr)
            utxos.extend(apply_mempool_utxos(confirmed, mempool, addr))
        return utxos

    def estimate_send(self, to_addr: str, amount_ori, tier: int, subtract_fee: bool = False,
                      utxo_sel: set | None = None) -> dict:
        name, info = self.default_account()
        if not info:
            raise WalletError("wallet is empty")
        amount_sats = ori_to_sats(amount_ori)
        change_addr = info["address"]
        utxos = self._spendable_utxos()
        if utxo_sel is not None:
            if not utxo_sel:
                raise WalletError("coin control: no transaction inputs selected")
            by_key = {(u["txid"], u["vout"]): u for u in utxos}
            utxos = [by_key[k] for k in utxo_sel if k in by_key]
            if not utxos:
                raise WalletError("coin control: selected inputs are no longer available (already spent)")
        plan = plan_send(
            utxos, to_addr, change_addr, amount_sats, int(tier), self.cfg, subtract_fee
        )
        plan["from_name"] = name
        plan["from_address"] = change_addr
        plan["to"] = to_addr
        plan["amount_text"] = format_ori(plan["send_amount"])
        plan["fee_text"] = format_ori(plan["fee"])
        return plan

    def send_coins(self, to_addr: str, amount_ori, tier: int, subtract_fee: bool = False,
                   label: str = "", utxo_sel: set | None = None) -> dict:
        if self.is_locked():
            raise WalletError("wallet is locked — unlock it first")
        plan = self.estimate_send(to_addr, amount_ori, tier, subtract_fee, utxo_sel)
        tx = sign_planned_wallet(self.wallet, plan)
        ok, reason, txid = self.node.submit_raw_tx(tx.to_hex())
        if not ok:
            raise WalletError(reason)
        if label:
            self.set_label(to_addr, label)
            known = {info["address"] for info in self.wallet.values()}
            if to_addr not in known:
                try:
                    self.book_add(label, to_addr)
                except WalletError:
                    pass
        rec = {
            "txid": txid,
            "type": "send",
            "amount_sats": -plan["send_amount"] - plan["fee"],
            "fee_sats": plan["fee"],
            "address": to_addr,
            "from_address": plan["from_address"],
            "label": label,
            "height": -1,
            "timestamp": int(time.time()),
            "mempool": True,
            "confirmations": 0,
        }
        hist = [h for h in self.meta.get("history", []) if h.get("txid") != txid]
        hist.insert(0, rec)
        self.meta["history"] = hist
        self.meta["fee_tier"] = int(tier)
        self._save_meta()
        return {"txid": txid, **plan}

    def bump_fee(self, old_txid: str, new_tier: int) -> dict:
        """RBF: replace old_txid in mempool with higher-fee tx."""
        if self.is_locked():
            raise WalletError("wallet is locked — unlock it first")
        if not self.node:
            raise WalletError("node not running")
        _, info = self.default_account()
        if not info:
            raise WalletError("no default account")
        
        # 1. Get original payee, amount, and label from history
        hist = self.meta.get("history", [])
        old_rec = None
        for rec in hist:
            if rec.get("txid") == old_txid:
                old_rec = rec
                break
        if not old_rec:
            raise WalletError("Transaction not found in wallet history")
        if not old_rec.get("mempool"):
            raise WalletError("Transaction is already confirmed")
            
        to_addr = old_rec["address"]
        fee_sats = old_rec.get("fee_sats", 0)
        # amount_sats is negative and includes fee if it's a send.
        # send_amount = abs(amount) - fee.
        send_amount_sats = abs(old_rec["amount_sats"]) - fee_sats
        amount_ori = send_amount_sats / COIN
        label = old_rec.get("label", "")
        
        # 2. Get old tx inputs from mempool
        base = self.cfg.api_url() if hasattr(self.cfg, 'api_url') else "http://127.0.0.1:8000"
        mempool_data = self.node.mempool.get(bytes.fromhex(old_txid))
        if not mempool_data:
            raise WalletError("Old transaction not found in mempool")
            
        utxo_sel = set()
        for txin in mempool_data.inputs:
            utxo_sel.add((txin.prev_txid.hex(), txin.prev_vout))
            
        # 3. Create replacement using estimate_send but exclude old_txid from mempool spent list
        from_addr = info["address"]
        # Fetch confirmed UTXOs from chain via Node directly to avoid HTTP if we can, or just use chain.
        # Actually, self.node.chain.utxo is accessible!
        confirmed_utxos = []
        for (txid, vout), out in self.node.chain.utxo._utxo.items():
            try:
                addr = out.script_pubkey.decode("ascii")
                if addr == from_addr:
                    confirmed_utxos.append({
                        "txid": txid.hex(),
                        "vout": vout,
                        "value": out.value,
                        "mature": True,
                    })
            except Exception:
                pass
                
        # Simulate apply_mempool_utxos but EXCLUDE old_txid
        spent_in_mempool = set()
        for mem_txid, mem_tx in self.node.mempool._txs.items():
            if mem_txid.hex() == old_txid:
                continue # don't mark its inputs as spent
            for txin in mem_tx.inputs:
                if txin.prev_txid != b"\x00" * 32:
                    spent_in_mempool.add((txin.prev_txid.hex(), txin.prev_vout))
                    
        available_utxos = [u for u in confirmed_utxos if (u["txid"], u["vout"]) not in spent_in_mempool]
        
        # Select the ones we need
        by_key = {(u["txid"], u["vout"]): u for u in available_utxos}
        selected_utxos = [by_key[k] for k in utxo_sel if k in by_key]
        if not selected_utxos:
            raise WalletError("Original inputs are no longer available")
            
        plan = plan_send(
            selected_utxos, to_addr, from_addr, send_amount_sats, new_tier, self.cfg, subtract_fee=False
        )
        plan["from_address"] = from_addr
        plan["rbf"] = True
        
        tx = sign_planned_wallet(self.wallet, plan)
        ok, reason, new_txid = self.node.submit_raw_tx(tx.to_hex())
        if not ok:
            raise WalletError(reason)
            
        # Update history
        for rec in hist:
            if rec.get("txid") == old_txid:
                rec["replaced"] = True
                rec["mempool"] = False
                break
                
        rec_new = {
            "txid": new_txid,
            "type": "send",
            "amount_sats": -plan["send_amount"] - plan["fee"],
            "fee_sats": plan["fee"],
            "address": to_addr,
            "from_address": plan["from_address"],
            "label": label,
            "height": -1,
            "timestamp": int(time.time()),
            "mempool": True,
            "confirmations": 0,
        }
        hist.insert(0, rec_new)
        self.meta["history"] = hist
        self.meta["fee_tier"] = new_tier
        self._save_meta()
        return {"old_txid": old_txid, "new_txid": new_txid, "tier": new_tier}

    def validate_addr(self, address: str) -> bool:
        return validate_address(address, self.cfg.network_hrp)

    # --- Bitcoin Core-style wallet features ---------------------------------

    _MSG_PREFIX = b"ORI Signed Message:\n"

    def account_for_address(self, address: str):
        for name, info in self.wallet.items():
            if isinstance(info, dict) and info.get("address") == address:
                return name, info
        return None, None

    def sign_message(self, address: str, message: str) -> str:
        """Sign an arbitrary message with the key owning `address`."""
        from crypto import sign as _sign

        if self.is_locked():
            raise WalletError("wallet is locked — unlock it first")
        _, info = self.account_for_address(address)
        if not info:
            raise WalletError("address not found in this wallet")
        digest = sha256d(self._MSG_PREFIX + message.encode("utf-8"))
        return _sign(bytes.fromhex(info["priv_hex"]), digest).hex()

    def verify_message(self, address: str, message: str, sig_hex: str) -> bool:
        """Verify a signed message against any bech32 address."""
        return self.verify_message_static(address, message, sig_hex)

    def change_passphrase(self, old_pass: str, new_pass: str):
        """Re-encrypt the wallet under a new passphrase (verifies old first)."""
        if not self.is_encrypted():
            raise WalletError("wallet is not encrypted")
        try:
            check = load_wallet(self.wallet_path, old_pass)
        except WalletError as exc:
            raise WalletError(f"current passphrase is wrong: {exc}")
        if check != self.wallet and self.wallet:
            # decrypted contents differ only if file changed externally;
            # proceed using freshly loaded copy to be safe.
            self.wallet = check
        if len(new_pass) < 8:
            raise WalletError("new passphrase must be at least 8 characters")
        save_wallet(self.wallet_path, self.wallet, new_pass)
        self._passphrase = new_pass

    def export_history_csv(self, path: str) -> int:
        """Export transaction history to CSV. Returns row count."""
        import csv

        rows = list(self.meta.get("history", []))
        rows.sort(key=lambda r: (r.get("timestamp", 0), r.get("txid", "")), reverse=True)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["txid", "type", "address", "label", "amount_sats",
                        "fee_sats", "height", "confirmations", "timestamp_iso",
                        "mempool"])
            for r in rows:
                ts = r.get("timestamp") or 0
                iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else ""
                w.writerow([r.get("txid"), r.get("type"), r.get("address"),
                            r.get("label", ""), r.get("amount_sats", 0),
                            r.get("fee_sats", 0), r.get("height", ""),
                            r.get("confirmations", ""), iso,
                            bool(r.get("mempool"))])
        return len(rows)

    def dump_private_key(self, address: str) -> tuple:
        """Return (name, priv_hex) for `address`. Requires unlocked wallet."""
        if self.is_locked():
            raise WalletError("wallet is locked — unlock it first")
        name, info = self.account_for_address(address)
        if not info:
            raise WalletError("address not found in this wallet")
        return name, info["priv_hex"]

    def rescan_from(self, start_height: int = 0):
        """Force a wallet history rescan starting at `start_height`."""
        self.meta["last_scan_height"] = max(-1, int(start_height) - 1)
        self.meta["history"] = []
        self._last_snapshot_key = None
        self._save_meta(force=True)

    def disconnect_peer(self, host: str, port: int) -> bool:
        if not self.node:
            return False
        peer = self.node.network.peers.get((host, int(port)))
        if peer is None:
            return False
        peer.close()
        return True

    def connected_peers_detailed(self) -> list:
        return self.connected_peers()

    def debug_command(self, line: str) -> str:
        if not self.node:
            return "node is not running"
        parts = line.strip().split()
        if not parts:
            return ""
        cmd = parts[0].lower()
        args = parts[1:]
        try:
            return self._dispatch_debug(cmd, args)
        except Exception as exc:
            return f"error: {exc}"

    def _dispatch_debug(self, cmd: str, args: list) -> str:
        node = self.node
        if cmd in ("help", "?"):
            return (
                "Commands:\n"
                "  help\n"
                "  getblockchaininfo\n"
                "  getnetworkinfo\n"
                "  getpeerinfo\n"
                "  getmempoolinfo\n"
                "  getbalance\n"
                "  listaccounts\n"
                "  getnewaddress (label)\n"
                "  validateaddress <addr>\n"
                "  getblock <height|hash>\n"
                "  getblockhash <height>\n"
                "  gettransaction <txid>\n"
                "  getrawmempool [n]\n"
                "  getbestblockhash | getblockcount | uptime\n"
                "  addnode <host> <port>\n"                "Wallet:\n"
                "  encryptwallet | lockwallet | unlockwallet <pass> [minutes]\n"
                "  walletpassphrasechange <old> <new>\n"
                "  signmessage <addr> <message>\n"
                "  verifymessage <addr> <message> <sighex>\n"
                "  dumpprivkey <addr>\n"
                "  exportcsv <path>\n"
                "  rescan [height]\n"
                "Node:\n"
                "  stop"
            )
        if cmd == "stop":
            from PySide6.QtWidgets import QApplication

            QApplication.instance().quit()
            return "stopping…"
        if cmd == "getblockchaininfo":
            return json.dumps(node.stats(), indent=2)
        if cmd == "getnetworkinfo":
            return json.dumps({
                "version": VERSION,
                "p2p_port": node.cfg.p2p_port,
                "api_port": node.cfg.api_port,
                "connections": node.network.peer_count(),
                "known": len(node.network.known),
            }, indent=2)
        if cmd == "getpeerinfo":
            return json.dumps(self.connected_peers(), indent=2)
        if cmd == "getmempoolinfo":
            entries = node.mempool.summary()
            return json.dumps({
                "size": len(entries),
                "bytes": sum(t["size"] for t in entries),
                "total_fee": sum(t["fee"] for t in entries),
            }, indent=2)
        if cmd == "getbalance":
            return format_ori(self._balances()["available"])
        if cmd == "listaccounts":
            return json.dumps(
                [{"name": n, "address": i["address"]} for n, i in self.wallet.items()],
                indent=2,
            )
        if cmd == "getnewaddress":
            label = args[0] if args else ""
            name, info = self.new_receiving_address(label)
            return f"{info['address']}  ({name})"
        if cmd == "validateaddress":
            if not args:
                return "usage: validateaddress <addr>"
            return json.dumps({"isvalid": self.validate_addr(args[0]), "address": args[0]}, indent=2)
        if cmd == "getblock":
            if not args:
                return "usage: getblock <height|hash>"
            arg = args[0]
            if arg.isdigit():
                block = node.chain.block_at(int(arg))
                height = int(arg)
            else:
                block = node.chain.block_by_hash(arg)
                height = node.chain.storage.chain_height_of(arg)
            if block is None:
                return "block not found"
            return json.dumps(block.to_dict(height), indent=2)
        if cmd == "gettransaction":
            if not args:
                return "usage: gettransaction <txid>"
            found = node.chain.get_tx(args[0])
            if found:
                tx, entry = found
                return json.dumps({
                    "txid": tx.txid().hex(),
                    "height": entry["height"],
                    "block_hash": entry["block_hash"],
                    "hex": tx.to_hex(),
                }, indent=2)
            pending = node.mempool.get(bytes.fromhex(args[0]))
            if pending:
                return json.dumps({"txid": args[0], "mempool": True, "hex": pending.to_hex()}, indent=2)
            return "transaction not found"
        if cmd == "getbestblockhash":
            return node.chain.tip()["hash"]
        if cmd == "getblockcount":
            return str(node.storage.height())
        if cmd == "addnode":
            if len(args) < 2:
                return "usage: addnode <host> <port>"
            self.add_peer(args[0], int(args[1]))
            return "connecting"
        if cmd == "uptime":
            return str(int(time.time() - self._started_at))
        # ── wallet commands ──────────────────────────────────────────
        if cmd == "encryptwallet":
            return "already encrypted" if self.is_encrypted() else \
                "use File > Encrypt Wallet (interactive passphrase required)"
        if cmd == "lockwallet":
            if not self.is_encrypted():
                return "wallet is not encrypted"
            self.lock_wallet()
            return "wallet locked"
        if cmd == "unlockwallet":
            if len(args) < 1:
                return "usage: unlockwallet <passphrase> [timeout_minutes]"
            minutes = int(args[1]) if len(args) > 1 else 0
            ok = self.unlock_wallet(args[0], timeout_minutes=minutes)
            return "wallet unlocked" + (f" (auto-lock in {minutes} min)" if minutes else "") \
                if ok else "wrong passphrase"
        if cmd == "walletpassphrasechange":
            if len(args) < 2:
                return "usage: walletpassphrasechange <old> <new>"
            self.change_passphrase(args[0], args[1])
            return "passphrase changed"
        if cmd == "signmessage":
            if len(args) < 2:
                return "usage: signmessage <addr> <message...>"
            sig = self.sign_message(args[0], " ".join(args[1:]))
            return sig
        if cmd == "verifymessage":
            if len(args) < 3:
                return "usage: verifymessage <addr> <message...> <sighex>"
            msg = " ".join(args[1:-1])
            ok = self.verify_message(args[0], msg, args[-1])
            return "true" if ok else "false"
        if cmd == "dumpprivkey":
            if not args:
                return "usage: dumpprivkey <addr>"
            name, priv = self.dump_private_key(args[0])
            return f"{priv}   ({name})"
        if cmd == "exportcsv":
            if not args:
                return "usage: exportcsv <path.csv>"
            n = self.export_history_csv(args[0])
            return f"exported {n} rows -> {args[0]}"
        if cmd == "rescan":
            h = int(args[0]) if args and args[0].isdigit() else 0
            self.rescan_from(h)
            return f"rescanning from height {h}"
        if cmd == "getblockhash":
            if not args or not args[0].isdigit():
                return "usage: getblockhash <height>"
            row = node.storage.block_by_height(int(args[0]))
            return row["hash"] if row else "not found"
        if cmd == "getrawmempool":
            entries = node.mempool.summary()
            lines = [f"{e['txid']}  fee={e['fee']} rate={e['fee_rate']}" for e in entries]
            return "\n".join(lines[:50]) + (f"\n… ({len(lines)} total)" if len(lines) > 50 else "") or "empty"
        if cmd == "getdifficulty":
            tip = node.chain.tip()
            return json.dumps({"bits": hex(tip["bits"]),
                               "difficulty": tip.get("difficulty"),
                               "next_bits": hex(node.chain.next_bits())}, indent=2)
        if cmd == "bumpfee":
            if len(args) != 2:
                return "usage: bumpfee <txid> <tier>"
            return json.dumps(self.bump_fee(args[0], int(args[1])), indent=2)
        # ── legacy console aliases ───────────────────────────────────
        if cmd == "getinfo":
            return json.dumps({
                "version": f"ORI Core v{VERSION}",
                "blocks": node.storage.height(),
                "difficulty": node.chain.tip()["difficulty"],
                "connections": node.network.peer_count(),
                "mempool_size": node.mempool.size(),
            }, indent=2)
        if cmd == "getblocktime":
            return str(node.cfg.block_time_seconds)
        if cmd == "getsupply":
            return str(node.chain.utxo.total_supply())
        if cmd == "getutxo":
            if len(args) != 2:
                return "usage: getutxo <txid> <vout>"
            val = node.chain.utxo.get(bytes.fromhex(args[0]), int(args[1]))
            if val is None:
                return "UTXO not found (or already spent)"
            addr, amount, height, cb = val
            return json.dumps({"height": height, "value_sats": amount,
                               "address": addr, "coinbase": cb}, indent=2)
        if cmd == "listunspent":
            _, acc = self.default_account()
            if not acc:
                return "no default wallet account"
            return json.dumps(node.chain.utxos_of(acc["address"]), indent=2)
        if cmd == "sendrawtransaction":
            if not args:
                return "usage: sendrawtransaction <hex>"
            ok, msg, txid = node.submit_raw_tx(args[0])
            return f"OK. TxID: {txid}" if ok else f"Error: {msg}"
        if cmd == "decoderawtransaction":
            from tx import Transaction as _Tx

            if not args:
                return "usage: decoderawtransaction <hex>"
            try:
                tx = _Tx.from_hex(args[0])
                return json.dumps({
                    "txid": tx.txid().hex(), "version": tx.version,
                    "locktime": tx.locktime, "coinbase": tx.is_coinbase(),
                    "size": len(tx.serialize()),
                    "inputs": [{"prev_txid": i.prev_txid.hex(), "prev_vout": i.prev_vout}
                               for i in tx.inputs],
                    "outputs": [{"vout": n, "value": o.value,
                                 "script_pubkey": o.script_pubkey.decode(errors="replace")}
                                for n, o in enumerate(tx.outputs)],
                }, indent=2)
            except Exception as exc:
                return f"decode failed: {exc}"
        return f"unknown command '{cmd}' (type help)"


def format_time(ts: int) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
