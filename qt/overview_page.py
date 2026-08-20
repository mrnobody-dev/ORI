from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from qt.controller import format_time
from qt.dialogs import TxDetailDialog
from qt.icons import tx_arrow
from wallet import format_ori


def _hline():
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    line.setStyleSheet("color: #EBEBEB; margin: 4px 0;")
    return line


class OverviewPage(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # ── Left: Balances ─────────────────────────────────────
        left = QGroupBox("Balances")
        grid = QGridLayout(left)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(6)
        grid.setContentsMargins(16, 20, 16, 16)

        def _val_lbl(obj_name="balanceValue"):
            l = QLabel("0.00000000 ORI")
            l.setObjectName(obj_name)
            l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            return l

        def _hint_lbl(text):
            l = QLabel(text)
            l.setObjectName("muted")
            return l

        self.lbl_avail = _val_lbl("balanceValue")
        self.lbl_pend  = _val_lbl("pending")
        self.lbl_total = _val_lbl("balanceTotal")

        row_specs = [
            ("Available",  self.lbl_avail, "Your spendable balance (mature coins)"),
            ("Pending",    self.lbl_pend,  "Unconfirmed (mempool)"),
        ]
        for i, (title, value, hint) in enumerate(row_specs):
            t = QLabel(title + ":")
            t.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            t.setStyleSheet("color: #555555; font-weight: 500;")
            grid.addWidget(t, i, 0)
            grid.addWidget(value, i, 1)
            grid.addWidget(_hint_lbl(hint), i, 2)

        grid.addWidget(_hline(), len(row_specs), 0, 1, 3)

        t_total = QLabel("Total:")
        t_total.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        t_total.setStyleSheet("color: #333333; font-weight: 700;")
        grid.addWidget(t_total, len(row_specs) + 1, 0)
        grid.addWidget(self.lbl_total, len(row_specs) + 1, 1)

        grid.addWidget(_hline(), len(row_specs) + 2, 0, 1, 3)

        grid.setColumnStretch(2, 1)
        left.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        # ── Right: Recent transactions ─────────────────────────
        right = QGroupBox("Recent transactions")
        rv = QVBoxLayout(right)
        rv.setContentsMargins(10, 20, 10, 10)
        self.recent = QListWidget()
        self.recent.setAlternatingRowColors(True)
        self.recent.setSpacing(1)
        self.recent.setFont(QFont("Segoe UI", 11))
        self.recent.itemDoubleClicked.connect(self._open_detail)

        self._empty_lbl = QLabel("No transactions yet.")
        self._empty_lbl.setObjectName("muted")
        self._empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

        rv.addWidget(self.recent)
        rv.addWidget(self._empty_lbl)

        root.addWidget(left, 3)
        root.addWidget(right, 2)

    def apply_snapshot(self, snap: dict):
        b = snap.get("balances", {})
        avail = b.get("available", 0)
        pend  = b.get("pending", 0)
        total = b.get("total", 0)

        self.lbl_avail.setText(format_ori(avail))
        self.lbl_pend.setText(format_ori(pend))
        self.lbl_total.setText(format_ori(total))

        # Dynamic object name for color
        self.lbl_pend.setObjectName("positive" if pend > 0 else ("negative" if pend < 0 else "pending"))
        self.lbl_pend.style().unpolish(self.lbl_pend)
        self.lbl_pend.style().polish(self.lbl_pend)

    def apply_history(self, history: list):
        self.recent.clear()
        recent = history[:15]
        self._empty_lbl.setVisible(len(recent) == 0)
        self.recent.setVisible(len(recent) > 0)

        for rec in recent:
            amount = rec.get("amount_sats", 0)
            incoming = rec.get("type") in ("receive", "generate", "immature") or amount > 0
            confirmed = (rec.get("confirmations") or 0) > 0 and not rec.get("mempool")

            item = QListWidgetItem(tx_arrow(incoming, confirmed), self._row_text(rec))
            item.setData(Qt.ItemDataRole.UserRole, rec.get("txid", ""))

            if not confirmed:
                item.setForeground(Qt.GlobalColor.gray)
            elif incoming:
                item.setForeground(Qt.GlobalColor.darkGreen)
            else:
                item.setForeground(Qt.GlobalColor.darkRed)

            self.recent.addItem(item)

    def _row_text(self, rec: dict) -> str:
        amount = rec.get("amount_sats", 0)
        date = format_time(rec.get("timestamp", 0))
        label = rec.get("label") or rec.get("address") or rec.get("type", "")
        # Truncate address for display
        if label and label.startswith("ori1") and len(label) > 20:
            label = label[:12] + "…" + label[-6:]
        confs = rec.get("confirmations", 0) or 0
        status = ""
        if rec.get("mempool"):
            status = "  [unconfirmed]"
        elif rec.get("type") == "immature":
            status = "  [immature]"
        return f"{date}    {label}{status}\n{format_ori(amount)}"

    def _open_detail(self, item: QListWidgetItem):
        txid = item.data(Qt.ItemDataRole.UserRole)
        if txid:
            TxDetailDialog(self.controller, txid, self).exec()
