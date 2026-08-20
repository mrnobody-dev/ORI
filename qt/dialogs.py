import os
import shutil
import time as _time

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from api import VERSION
from qt.controller import format_time
from wallet import WalletError, format_ori


def _separator():
    """Horizontal divider line."""
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    line.setStyleSheet("color: #D0D0D0;")
    return line


class TxDetailDialog(QDialog):
    def __init__(self, controller, txid: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transaction Details")
        self.setMinimumWidth(600)
        self.setMinimumHeight(420)
        try:
            d = controller.tx_detail(txid)
        except WalletError as exc:
            layout = QVBoxLayout(self)
            err = QLabel(str(exc))
            err.setObjectName("negative")
            layout.addWidget(err)
            return

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # ── Status badge ─────────────────────────────────────────
        confs = int(d.get("confirmations") or 0)
        is_mempool = bool(d.get("mempool"))
        tx_type = d.get("type", "")

        if is_mempool:
            status_text = "⏳  Unconfirmed — in mempool"
            status_color = "#E67E22"
        elif confs <= 0:
            status_text = "🔄  Confirming…"
            status_color = "#E67E22"
        elif tx_type == "generate" and confs < 100:
            status_text = f"⛏  Mined — {confs} confirmation(s), immature"
            status_color = "#2980B9"
        else:
            status_text = f"✅  Confirmed — {confs} confirmation(s)"
            status_color = "#27AE60"

        status_lbl = QLabel(status_text)
        status_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {status_color}; padding: 6px;"
        )
        layout.addWidget(status_lbl)
        layout.addWidget(_separator())

        # ── ETA for mempool txs ───────────────────────────────────
        if is_mempool and controller.node:
            height = controller.node.storage.height()
            block_time = controller.node.cfg.block_time_seconds
            # Default tier 5 estimate
            eta_blocks = 5
            eta_sec = eta_blocks * block_time
            eta_str = _format_eta(eta_sec)
            eta_lbl = QLabel(
                f"📦  Entered queue at height {height}  —  "
                f"expected to confirm at height {height + eta_blocks} "
                f"({eta_str})"
            )
            eta_lbl.setStyleSheet("color: #7A7A7A; font-size: 12px; padding: 2px 6px;")
            eta_lbl.setWordWrap(True)
            layout.addWidget(eta_lbl)

            if not d.get("replaced"):
                self.bump_btn = QPushButton("Bump Fee (RBF)")
                self.bump_btn.setIcon(QIcon("qt/assets/icons/transaction.png"))  # placeholder
                self.bump_btn.setStyleSheet("background-color: #F39C12; color: white;")
                self.bump_btn.clicked.connect(self._bump_fee)
                layout.addWidget(self.bump_btn)
                
        # ── Form fields ───────────────────────────────────────────
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(8)

        amount = d.get("amount_sats", 0)
        fee = d.get("fee_sats", 0)

        rows = [
            ("Date:", format_time(d.get("timestamp", 0))),
            ("To:", d.get("label") or d.get("address") or "(unknown)"),
            ("Amount:", f"{format_ori(amount)}   ({amount:,} sats)"),
            ("Fee:", f"{format_ori(fee)}   ({fee:,} sats)"),
            ("Size:", f"{d.get('size', 0):,} bytes"),
        ]
        if d.get("height") is not None:
            rows.append(("Block:", f"#{d['height']}  (position {d.get('position', '?')})"))
            rows.append(("Confirmations:", str(confs)))

        rows.append(("Version:", str(d.get("version", ""))))

        for k, v in rows:
            lbl = QLabel(v)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(k, lbl)

        # TxID row with copy button
        txid_edit = QLineEdit(d.get("txid", ""))
        txid_edit.setReadOnly(True)
        txid_edit.setFont(QFont("Consolas", 9))
        txid_edit.setCursorPosition(0)
        copy_btn = QPushButton("Copy")
        copy_btn.setMaximumWidth(70)
        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(d.get("txid", ""))
        )
        txid_row = QHBoxLayout()
        txid_row.addWidget(txid_edit, 1)
        txid_row.addWidget(copy_btn)
        form.addRow("Transaction ID:", txid_row)

        if d.get("block_hash"):
            bh_edit = QLineEdit(d["block_hash"])
            bh_edit.setReadOnly(True)
            bh_edit.setFont(QFont("Consolas", 9))
            bh_edit.setCursorPosition(0)
            form.addRow("Block hash:", bh_edit)

        layout.addLayout(form)
        layout.addWidget(_separator())

        # ── Raw hex ───────────────────────────────────────────────
        raw_box = QGroupBox("Raw transaction hex")
        raw_v = QVBoxLayout(raw_box)
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setFont(QFont("Consolas", 9))
        self.raw.setPlainText(d.get("hex", ""))
        self.raw.setMaximumHeight(100)
        raw_v.addWidget(self.raw)
        layout.addWidget(raw_box)

        # ── Close button ──────────────────────────────────────────
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)


def _format_eta(seconds: int) -> str:
    if seconds < 60:
        return f"~{seconds}s"
    elif seconds < 3600:
        m = seconds // 60
        s = seconds % 60
        return f"~{m}m {s}s" if s else f"~{m}m"
    else:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"~{h}h {m}m" if m else f"~{h}h"


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About ORI Core")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("ORI Core")
        f = title.font()
        f.setPointSize(22)
        f.setBold(True)
        title.setFont(f)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        ver = QLabel(f"Version {VERSION}")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ver.setStyleSheet("color: #6D6D6D;")

        layout.addWidget(title)
        layout.addWidget(ver)
        layout.addWidget(_separator())

        body = QLabel(
            "ORI Core is a full node and wallet for the ORI blockchain.\n\n"
            "This is experimental software — use at your own risk.\n\n"
            "Mining is not included in this application.\n"
            "Run miner.py separately to mine blocks.\n\n"
            "Copyright © 2026 The ORI Developers\n"
            "Based on the Bitcoin Core Qt layout."
        )
        body.setWordWrap(True)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setStyleSheet("line-height: 1.5;")
        layout.addWidget(body)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)


class InformationDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Node Information — ORI Core")
        self.resize(580, 460)
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFont("Consolas", 10))
        layout.addWidget(self.text)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def apply_snapshot(self, snap: dict):
        bal = snap.get("balances", {})
        lines = [
            f"ORI Core  v{snap.get('version')}",
            "═" * 48,
            "",
            "General",
            f"  data dir  : {snap.get('data_dir')}",
            f"  uptime    : {snap.get('uptime')} s",
            "",
            "Network",
            f"  P2P port  : {snap.get('p2p_port')}",
            f"  REST API  : http://{snap.get('api_host')}:{snap.get('api_port')}",
            f"  peers     : {snap.get('peers')}",
            "",
            "Blockchain",
            f"  height     : {snap.get('height')}",
            f"  best hash  : {snap.get('best_hash')}",
            f"  difficulty : {snap.get('difficulty')}",
            f"  last block : {format_time(snap.get('last_block_time', 0))}",
            f"  block time : {snap.get('block_time')} s (target)",
            f"  supply     : {format_ori(snap.get('supply_sats', 0))}",
            f"  UTXOs      : {snap.get('utxo_count')}",
            "",
            "Mempool",
            f"  transactions : {snap.get('mempool')}",
            "",
            "Wallet",
            f"  account  : {snap.get('default_account')}",
            f"  address  : {snap.get('default_address')}",
            f"  keys     : {len(snap.get('accounts') or [])}",
            "",
            "Balances",
            f"  available : {format_ori(bal.get('available', 0))}",
            f"  pending   : {format_ori(bal.get('pending', 0))}",
            f"  total     : {format_ori(bal.get('total', 0))}",
        ]
        self.text.setPlainText("\n".join(lines))


class ConsoleDialog(QDialog):
    def __init__(self, controller: "NodeController", parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Debug Console - ORI Core")
        self.resize(750, 480)
        layout = QVBoxLayout(self)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log)

        in_layout = QHBoxLayout()
        self.cmd = QLineEdit()
        self.cmd.setPlaceholderText("Enter command (e.g. getinfo, help)...")
        self.cmd.setFont(QFont("Consolas", 10))
        self.cmd.returnPressed.connect(self._exec_cmd)
        
        # Command history
        self._history = []
        self._history_idx = -1
        self.cmd.installEventFilter(self)

        self.btn = QPushButton("Execute")
        self.btn.clicked.connect(self._exec_cmd)

        in_layout.addWidget(self.cmd)
        in_layout.addWidget(self.btn)
        layout.addLayout(in_layout)
        
        self._welcome_shown = False

    def eventFilter(self, obj, event):
        if obj == self.cmd and event.type() == event.Type.KeyPress:
            if event.key() == Qt.Key.Key_Up:
                if self._history:
                    self._history_idx = max(0, self._history_idx - 1)
                    self.cmd.setText(self._history[self._history_idx])
                return True
            elif event.key() == Qt.Key.Key_Down:
                if self._history:
                    self._history_idx = min(len(self._history), self._history_idx + 1)
                    if self._history_idx == len(self._history):
                        self.cmd.clear()
                    else:
                        self.cmd.setText(self._history[self._history_idx])
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, e):
        super().showEvent(e)
        self.cmd.setFocus()
        if not self._welcome_shown:
            self._welcome_shown = True
            self._append_html(
                "<b>Welcome to the ORI Core RPC console.</b><br>"
                "Use up and down arrows to navigate history.<br>"
                'Type <code>help</code> for an overview of available commands.<br>'
                '<span style="color:red">WARNING: Scammers have been active, telling users to type '
                'commands here, stealing their wallet contents. Do not use this console '
                'without fully understanding the ramifications of a command.</span><br>'
                "──────────────────────────────────────────────────────────────────────"
            )

    def append_log(self, text: str):
        # Colorize log lines (naive method)
        if "ERROR" in text or "FAILED" in text or "WARN" in text:
            self._append_html(f'<span style="color:#E74C3C">{text}</span>')
        elif "ACCEPTED" in text or "OK" in text:
            self._append_html(f'<span style="color:#2ECC71">{text}</span>')
        else:
            self._append_html(f'<span style="color:#7A7A7A">{text}</span>')

    def _append_html(self, html: str):
        self.log.moveCursor(QTextCursor.MoveOperation.End)
        self.log.insertHtml(html + "<br>")
        self.log.moveCursor(QTextCursor.MoveOperation.End)

    def _exec_cmd(self):
        cmd = self.cmd.text().strip()
        if not cmd:
            return
        
        self._history.append(cmd)
        self._history_idx = len(self._history)
        self.cmd.clear()

        self._append_html(f'<br><span style="color:#3498DB"><b>> {cmd}</b></span>')

        parts = cmd.split()
        op = parts[0].lower()
        args = parts[1:]

        node = self.controller.node
        if not node:
            self._append_html('<span style="color:#E74C3C">Error: Node not running</span>')
            return

        try:
            import json
            if op == "help":
                self._append_html(
                    "<b>Commands:</b><br>"
                    "  <code>getinfo</code> - Node status<br>"
                    "  <code>getblockcount</code> - Current tip height<br>"
                    "  <code>getblock &lt;hash&gt;</code> - Block by hash<br>"
                    "  <code>getblockhash &lt;height&gt;</code> - Hash of block at height<br>"
                    "  <code>getrawmempool</code> - All mempool txids<br>"
                    "  <code>getpeerinfo</code> - Connected peers<br>"
                    "  <code>getblocktime</code> - Expected seconds per block<br>"
                    "  <code>getsupply</code> - Current circulating supply<br>"
                    "  <code>getutxo &lt;txid&gt; &lt;vout&gt;</code> - Inspect UTXO<br>"
                    "  <code>getdifficulty</code> - Current PoW difficulty bits<br>"
                    "  <code>sendrawtransaction &lt;hex&gt;</code> - Submit tx<br>"
                    "  <code>decoderawtransaction &lt;hex&gt;</code> - Decode tx hex<br>"
                    "  <code>listunspent</code> - Wallet UTXOs<br>"
                    "  <code>bumpfee &lt;txid&gt; &lt;tier&gt;</code> - Bump fee of unconfirmed tx<br>"
                    "  <code>stop</code> - Shutdown node"
                )
            elif op == "getinfo":
                res = {
                    "version": "ORI Core v0.2.0",
                    "blocks": node.chain.storage.height(),
                    "difficulty": node.chain.storage.get_meta("tip_work"),
                    "connections": len(node.network.peers),
                    "mempool_size": node.mempool.size(),
                    "testnet": node.cfg.network_magic != b"\xf9\xbe\xb4\xd9",
                }
                self._append_html(f"<pre>{json.dumps(res, indent=2)}</pre>")
            elif op == "getblockcount":
                self._append_html(str(node.chain.storage.height()))
            elif op == "getblock":
                if not args:
                    self._append_html('<span style="color:#E74C3C">Usage: getblock &lt;hash&gt;</span>')
                else:
                    blk = node.chain.storage.block_by_hash(args[0])
                    if blk:
                        self._append_html(f"<pre>{json.dumps(blk, indent=2)}</pre>")
                    else:
                        self._append_html('<span style="color:#E74C3C">Block not found</span>')
            elif op == "getblockhash":
                if not args:
                    self._append_html('<span style="color:#E74C3C">Usage: getblockhash &lt;height&gt;</span>')
                else:
                    h = node.chain.storage.get_hash_by_height(int(args[0]))
                    if h:
                        self._append_html(h)
                    else:
                        self._append_html('<span style="color:#E74C3C">Height out of bounds</span>')
            elif op == "getrawmempool":
                self._append_html(f"<pre>{json.dumps(node.mempool.txids(), indent=2)}</pre>")
            elif op == "getpeerinfo":
                peers = [{"ip": p.addr[0], "port": p.addr[1], "user_agent": p.ua} for p in node.network.peers.values()]
                self._append_html(f"<pre>{json.dumps(peers, indent=2)}</pre>")
            elif op == "getblocktime":
                self._append_html(str(node.cfg.block_time_seconds))
            elif op == "getsupply":
                base = 50 * 100_000_000
                total = 0
                for h in range(1, node.chain.storage.height() + 1):
                    total += base >> (h // node.cfg.halving_interval)
                self._append_html(str(total))
            elif op == "getutxo":
                if len(args) != 2:
                    self._append_html('<span style="color:#E74C3C">Usage: getutxo &lt;txid&gt; &lt;vout&gt;</span>')
                else:
                    txid, vout = args[0], int(args[1])
                    val = node.chain.utxo.get(bytes.fromhex(txid), vout)
                    if val is None:
                        self._append_html('<span style="color:#E74C3C">UTXO not found (or already spent)</span>')
                    else:
                        height, amt, addr, cb = val
                        res = {"height": height, "value_sats": amt, "address": addr, "coinbase": cb}
                        self._append_html(f"<pre>{json.dumps(res, indent=2)}</pre>")
            elif op == "getdifficulty":
                h = node.chain.storage.tip_hash()
                parent = node.chain.storage.block_by_hash(h)
                bits = parent["bits"] if parent else node.cfg.genesis_bits
                self._append_html(hex(bits))
            elif op == "sendrawtransaction":
                if not args:
                    self._append_html('<span style="color:#E74C3C">Usage: sendrawtransaction &lt;hex&gt;</span>')
                else:
                    ok, msg, txid = node.submit_raw_tx(args[0])
                    if ok:
                        self._append_html(f"OK. TxID: {txid}")
                    else:
                        self._append_html(f'<span style="color:#E74C3C">Error: {msg}</span>')
            elif op == "decoderawtransaction":
                if not args:
                    self._append_html('<span style="color:#E74C3C">Usage: decoderawtransaction &lt;hex&gt;</span>')
                else:
                    from tx import Transaction
                    try:
                        tx = Transaction.from_hex(args[0])
                        from api import _tx_to_dict
                        self._append_html(f"<pre>{json.dumps(_tx_to_dict(tx), indent=2)}</pre>")
                    except Exception as e:
                        self._append_html(f'<span style="color:#E74C3C">Decode failed: {e}</span>')
            elif op == "listunspent":
                _, acc = self.controller.default_account()
                if acc:
                    utxos = node.chain.utxos_of(acc.get("address"))
                    self._append_html(f"<pre>{json.dumps(utxos, indent=2)}</pre>")
                else:
                    self._append_html('<span style="color:#E74C3C">No default wallet account</span>')
            elif op == "bumpfee":
                if len(args) != 2:
                    self._append_html('<span style="color:#E74C3C">Usage: bumpfee &lt;txid&gt; &lt;tier&gt;</span>')
                else:
                    txid, tier = args[0], int(args[1])
                    try:
                        res = self.controller.bump_fee(txid, tier)
                        self._append_html(f"<pre>{json.dumps(res, indent=2)}</pre>")
                    except Exception as e:
                        self._append_html(f'<span style="color:#E74C3C">Error: {e}</span>')
            elif op == "stop":
                self._append_html("Stopping node...")
                self.controller.shutdown()
                from PySide6.QtWidgets import QApplication
                QApplication.quit()
            else:
                self._append_html(f'<span style="color:#E74C3C">Unknown command: {op}</span>')
        except Exception as exc:
            self._append_html(f'<span style="color:#E74C3C">Exception: {str(exc)}</span>')


class PeersDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Peers — ORI Core")
        self.resize(800, 440)
        layout = QVBoxLayout(self)

        add = QHBoxLayout()
        add.addWidget(QLabel("Add node:"))
        self.host = QLineEdit()
        self.host.setPlaceholderText("127.0.0.1")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(8033)
        btn = QPushButton("Connect")
        btn.clicked.connect(self._add)
        add.addWidget(self.host, 1)
        add.addWidget(self.port)
        add.addWidget(btn)
        layout.addLayout(add)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Direction", "Address", "Port", "Height", "User agent", "Best hash"]
        )
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)

        known_box = QGroupBox("Known peers")
        kv = QVBoxLayout(known_box)
        self.known = QLabel("")
        self.known.setWordWrap(True)
        self.known.setObjectName("muted")
        kv.addWidget(self.known)
        layout.addWidget(known_box)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def apply_snapshot(self, snap: dict):
        peers = snap.get("peer_list") or []
        self.table.setRowCount(len(peers))
        for i, p in enumerate(peers):
            vals = [
                "→ Outbound" if p.get("outbound") else "← Inbound",
                p.get("host", ""),
                str(p.get("port", "")),
                str(p.get("height", "")),
                p.get("user_agent", ""),
                p.get("best_hash", ""),
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c == 5:
                    item.setFont(QFont("Consolas", 8))
                self.table.setItem(i, c, item)
        known = snap.get("known_peers") or []
        self.known.setText(
            ", ".join(f"{k['host']}:{k['port']}" for k in known) or "(none)"
        )

    def _add(self):
        host = self.host.text().strip()
        if not host:
            return
        try:
            self.controller.add_peer(host, int(self.port.value()))
        except WalletError as exc:
            QMessageBox.warning(self, "Peers", str(exc))
            return
        QMessageBox.information(self, "Peers", f"Connecting to {host}:{self.port.value()}…")


class AddressBookDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Receiving Addresses")
        self.resize(700, 380)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Label", "Address", "Account"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        copy = QPushButton("Copy address")
        copy.clicked.connect(self._copy)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(copy)
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)

    def apply_snapshot(self, snap: dict):
        acc = snap.get("accounts") or []
        self.table.setRowCount(len(acc))
        for i, a in enumerate(acc):
            self.table.setItem(i, 0, QTableWidgetItem(a.get("label") or ""))
            addr_item = QTableWidgetItem(a.get("address") or "")
            addr_item.setFont(QFont("Consolas", 9))
            self.table.setItem(i, 1, addr_item)
            self.table.setItem(i, 2, QTableWidgetItem(a.get("name") or ""))

    def _copy(self):
        items = self.table.selectedItems()
        if not items:
            return
        row = items[0].row()
        addr = self.table.item(row, 1)
        if addr:
            QApplication.clipboard().setText(addr.text())


class CoinControlDialog(QDialog):
    """Bitcoin-style coin control — pick which UTXOs to spend."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Select Inputs (Coin Control)")
        self.resize(760, 460)
        self._preselect = None
        self._utxos = []

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.lbl_summary = QLabel("No inputs selected")
        self.lbl_summary.setObjectName("balanceValue")
        top.addWidget(self.lbl_summary)
        top.addStretch(1)
        self.chk_confirmed = QCheckBox("Only show confirmed inputs")
        self.chk_confirmed.setChecked(True)
        self.chk_confirmed.toggled.connect(self._reload)
        top.addWidget(self.chk_confirmed)
        layout.addLayout(top)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["", "Status", "Label / Address", "Amount", "Confirmations", "TxID : Out"]
        )
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._update_summary)
        layout.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.btn_all = QPushButton("Select all")
        self.btn_none = QPushButton("Deselect all")
        self.btn_all.clicked.connect(lambda: self._check_all(True))
        self.btn_none.clicked.connect(lambda: self._check_all(False))
        row.addWidget(self.btn_all)
        row.addWidget(self.btn_none)
        row.addStretch(1)
        self.btn_clear = QPushButton("Automatically select")
        self.btn_clear.setToolTip("Clear selection — use all spendable inputs automatically")
        self.btn_clear.clicked.connect(self._auto_select)
        row.addWidget(self.btn_clear)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        row.addWidget(btns)
        layout.addLayout(row)

    def set_selection(self, keys):
        self._preselect = set(keys or ())
        if self.table.rowCount() == 0:
            self._reload()

    def showEvent(self, e):
        super().showEvent(e)
        self._reload()

    def _reload(self):
        utxos = self.controller.wallet_utxos() if self.controller.node else []
        if self.chk_confirmed.isChecked():
            utxos = [u for u in utxos if not u.get("mempool")]
        else:
            utxos = [u for u in utxos if u.get("mature") or not u.get("mempool")]

        self._utxos = utxos
        self.table.blockSignals(True)
        self.table.setRowCount(len(utxos))
        preselect = self._preselect
        for i, u in enumerate(utxos):
            key = (u["txid"], u["vout"])
            selectable = bool(u.get("mature"))
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            if preselect is None:
                checked = selectable
            else:
                checked = key in preselect and selectable
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            if not selectable:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setToolTip("Immature coinbase reward — cannot be spent yet")
            self.table.setItem(i, 0, item)

            if u.get("mempool"):
                status = "Unconfirmed"
            elif u.get("coinbase") and not u.get("mature"):
                status = "Immature"
            else:
                status = "Confirmed"
            self.table.setItem(i, 1, QTableWidgetItem(status))

            label = u.get("label") or u.get("address", "")
            color_item = QTableWidgetItem(label)
            if u.get("label"):
                color_item.setForeground(QColor("#2471A3"))
            if label.startswith("ori1") and len(label) > 20:
                color_item.setToolTip(label)
            self.table.setItem(i, 2, color_item)

            amt = QTableWidgetItem(format_ori(u["value"]))
            amt.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(i, 3, amt)

            confs = u.get("confirmations", 0)
            self.table.setItem(i, 4, QTableWidgetItem("—" if confs <= 0 else str(confs)))

            id_item = QTableWidgetItem(f"{u['txid'][:10]}…:{u['vout']}")
            id_item.setToolTip(f"{u['txid']}:{u['vout']}")
            f = id_item.font()
            f.setFamily("Consolas")
            f.setPointSize(8)
            id_item.setFont(f)
            id_item.setData(Qt.ItemDataRole.UserRole, key)
            self.table.setItem(i, 5, id_item)

            if not selectable:
                for c in range(6):
                    it = self.table.item(i, c)
                    if it is not None:
                        it.setForeground(QColor("#7A7A7A"))
        self.table.blockSignals(False)
        self._update_summary()

    def _check_all(self, checked: bool):
        self.table.blockSignals(True)
        for i, u in enumerate(self._utxos):
            item = self.table.item(i, 0)
            if u.get("mature"):
                item.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
        self.table.blockSignals(False)
        self._update_summary()

    def _auto_select(self):
        self._check_all(True)

    def _update_summary(self):
        total = 0
        count = 0
        for i, u in enumerate(self._utxos):
            item = self.table.item(i, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                total += u["value"]
                count += 1
        self.lbl_summary.setText(
            f"{count} input(s) selected — {format_ori(total)}"
        )

    def selected_keys(self) -> set:
        keys = set()
        for i, u in enumerate(self._utxos):
            item = self.table.item(i, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                keys.add((u["txid"], u["vout"]))
        return keys


class OptionsDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Options")
        self.resize(540, 380)
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        main = QWidget()
        mf = QFormLayout(main)
        self.datadir = QLineEdit(os.path.abspath(controller.cfg.data_dir))
        self.datadir.setReadOnly(True)
        self.wallet = QLineEdit(os.path.abspath(controller.wallet_path))
        self.wallet.setReadOnly(True)
        mf.addRow("Data directory:", self.datadir)
        mf.addRow("Wallet file:", self.wallet)
        note = QLabel(
            "Restart ORI Core to apply a different data directory.\n"
            "Set BTPY_DATA_DIR environment variable or use --datadir flag."
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        mf.addRow(note)

        net = QWidget()
        nf = QFormLayout(net)
        self.p2p = QLineEdit(str(controller.cfg.p2p_port))
        self.p2p.setReadOnly(True)
        self.api = QLineEdit(f"{controller.cfg.api_host}:{controller.cfg.api_port}")
        self.api.setReadOnly(True)
        self.dns = QLineEdit(controller.cfg.seed_dns_host or "(none)")
        self.dns.setReadOnly(True)
        nf.addRow("P2P port:", self.p2p)
        nf.addRow("REST API:", self.api)
        nf.addRow("DNS seeder:", self.dns)

        wallet_tab = QWidget()
        wf = QFormLayout(wallet_tab)
        hint = QLabel(
            "Fee tiers are protocol-defined (1–5). Choose the default on the Send tab.\n\n"
            "Tier 5 = Slowest (0.28 sat/vB) — cheapest\n"
            "Tier 1 = Fastest (1.40 sat/vB) — most expensive\n\n"
            "Mining is not available in ORI Core — use miner.py separately."
        )
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        wf.addRow(hint)

        tabs.addTab(main, "&Main")
        tabs.addTab(net, "&Network")
        tabs.addTab(wallet_tab, "&Wallet")
        layout.addWidget(tabs)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)


def backup_wallet(parent, controller):
    path, _ = QFileDialog.getSaveFileName(
        parent, "Backup Wallet", "wallet-backup.json", "Wallet (*.json);;All files (*.*)"
    )
    if not path:
        return
    try:
        shutil.copy2(controller.wallet_path, path)
        meta = controller.meta_path
        if os.path.exists(meta):
            shutil.copy2(meta, os.path.splitext(path)[0] + ".qt.json")
        QMessageBox.information(parent, "Backup Wallet", "Wallet backed up successfully.")
    except OSError as exc:
        QMessageBox.critical(parent, "Backup Wallet", str(exc))
