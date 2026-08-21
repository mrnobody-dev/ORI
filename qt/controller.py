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

        self._log_bridge = LogBridge()
        self._log_bridge.setFormatter(
            logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%dT%H:%M:%SZ")
        )
        self._log_bridge.message.connect(self.log_line.emit)
        logging.getLogger("ori").addHandler(self._log_bridge)

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

        If the port is already taken (e.g. an old node still running), try
        the next free port instead of silently failing. Exposes
        `cfg.api_port` and `api_url` so the UI can show the real endpoint.
        """
        import socket
        import uvicorn

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
            )
            self._api_server = uvicorn.Server(config)
            threading.Thread(target=self._api_server.run, daemon=True).start()
        except Exception:
            self._api_server = None

    def shutdown(self):
        self._timer.stop()
        self._save_meta()
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

    def _save_meta(self):
        try:
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
        return name, info

    def add_receive_request(self, entry: dict):
        self.meta.setdefault("receive_requests", []).insert(0, entry)
        self._save_meta()

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
        mempool = node.mempool.to_json()
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
            self.snapshot_ready.emit(snap)
            self.history_ready.emit(list(self.meta.get("history", [])))
        except Exception as exc:
            self.error.emit(str(exc))

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
        mempool = node.mempool.to_json()
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
        for entry in node.mempool.to_json():
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

    def unlock_wallet(self, passphrase: str) -> bool:
        """Decrypt wallet with given passphrase. Returns True on success."""
        try:
            self.wallet = load_wallet(self.wallet_path, passphrase)
            self._passphrase = passphrase
            return True
        except WalletError:
            return False

    def lock_wallet(self):
        """Clear in-memory decrypted wallet keys."""
        self._passphrase = None
        self.wallet = {}

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
            "default_account": next(iter(new_wallet), ""),
            "fee_tier": 3,
        }
        self._load_meta()
        self._save_meta()
        self.refresh()

    # --- send --------------------------------------------------------------

    def wallet_utxos(self) -> list:
        """All wallet UTXOs (confirmed + mempool change), for coin control."""
        if not self.node:
            return []
        mempool = self.node.mempool.to_json()
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
        mempool = self.node.mempool.to_json()
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
        mempool = self.node.mempool.to_json()
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
        ok, reason, new_txid = self.node.bump_fee(old_txid, info, new_tier, self.cfg)
        if not ok:
            raise WalletError(reason)
        # Update history: mark old as replaced, add new
        hist = self.meta.get("history", [])
        for rec in hist:
            if rec.get("txid") == old_txid:
                rec["replaced"] = True
                rec["mempool"] = False
                break
        self.meta["history"] = hist
        self._save_meta()
        return {"old_txid": old_txid, "new_txid": new_txid, "tier": new_tier}

    def validate_addr(self, address: str) -> bool:
        return validate_address(address, self.cfg.network_hrp)

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
                "  gettransaction <txid>\n"
                "  getbestblockhash\n"
                "  getblockcount\n"
                "  addnode <host> <port>\n"
                "  uptime"
            )
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
            txs = node.mempool.to_json()
            return json.dumps({
                "size": len(txs),
                "bytes": sum(t["size"] for t in txs),
                "total_fee": sum(t["fee"] for t in txs),
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
        return f"unknown command '{cmd}' (type help)"


def format_time(ts: int) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
