from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qt.controller import format_time
from qt.dialogs import TxDetailDialog
from qt.icons import tx_arrow
from wallet import format_ori


class TransactionsPage(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._all = []

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        filt = QHBoxLayout()
        filt.addWidget(QLabel("Type:"))
        self.kind = QComboBox()
        self.kind.addItems(["All", "Received", "Sent", "Mined", "To be confirmed"])
        filt.addWidget(self.kind)
        filt.addWidget(QLabel("Min amount:"))
        self.min_amt = QLineEdit()
        self.min_amt.setPlaceholderText("0")
        self.min_amt.setFixedWidth(120)
        filt.addWidget(self.min_amt)
        filt.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("txid, address, label")
        filt.addWidget(self.search, 1)
        root.addLayout(filt)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Date", "Type", "Label / Address", "Amount", "Confirmations", "ID"]
        )
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setIconSize(self.table.iconSize())
        self.table.verticalHeader().setDefaultSectionSize(36)
        root.addWidget(self.table, 1)

        self.kind.currentIndexChanged.connect(self._apply_filter)
        self.min_amt.textChanged.connect(self._apply_filter)
        self.search.textChanged.connect(self._apply_filter)
        self.table.cellDoubleClicked.connect(self._open_detail)

    def _open_detail(self, row: int, col: int):
        item = self.table.item(row, 5)
        if item and item.text():
            TxDetailDialog(self.controller, item.text(), self).exec()

    def apply_history(self, history: list):
        self._all = history
        self._apply_filter()

    def _apply_filter(self):
        kind = self.kind.currentText()
        try:
            min_sats = int(round(float(self.min_amt.text() or "0") * 100_000_000))
        except ValueError:
            min_sats = 0
        q = self.search.text().strip().lower()
        rows = []
        for rec in self._all:
            t = rec.get("type")
            amount = rec.get("amount_sats", 0)
            if kind == "Received" and t not in ("receive",):
                continue
            if kind == "Sent" and t != "send":
                continue
            if kind == "Mined" and t not in ("generate", "immature"):
                continue
            if kind == "To be confirmed" and (rec.get("confirmations") or 0) > 0 and not rec.get("mempool"):
                continue
            if abs(amount) < min_sats:
                continue
            blob = " ".join([
                rec.get("txid", ""),
                rec.get("address", ""),
                rec.get("label", ""),
                t or "",
            ]).lower()
            if q and q not in blob:
                continue
            rows.append(rec)

        self.table.setRowCount(len(rows))
        for i, rec in enumerate(rows):
            amount = rec.get("amount_sats", 0)
            incoming = rec.get("type") in ("receive", "generate", "immature") or amount > 0
            confirmed = (rec.get("confirmations") or 0) > 0 and not rec.get("mempool")
            tlabel = {
                "send": "Sent to",
                "receive": "Received with",
                "generate": "Mined",
                "immature": "Mined (immature)",
            }.get(rec.get("type"), rec.get("type", ""))
            vals = [
                format_time(rec.get("timestamp", 0)),
                tlabel,
                rec.get("label") or rec.get("address") or "",
                format_ori(amount),
                "Unconfirmed" if rec.get("mempool") or rec.get("confirmations", 0) == 0 else str(rec.get("confirmations", 0)),
                rec.get("txid", ""),
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c == 1:
                    item.setIcon(tx_arrow(incoming, confirmed))
                if c == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    if not confirmed:
                        item.setForeground(QColor("#7A7A7A"))
                    elif incoming:
                        item.setForeground(QColor("#2E7D32"))
                    else:
                        item.setForeground(QColor("#C0392B"))
                if c == 5:
                    f = item.font()
                    f.setFamily("Consolas")
                    item.setFont(f)
                self.table.setItem(i, c, item)
