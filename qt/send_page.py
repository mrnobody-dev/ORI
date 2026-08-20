from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from qt.dialogs import CoinControlDialog
from qt.theme import BITCOIN_ORANGE
from wallet import WalletError, format_ori


TIER_LABELS = {
    5: "Recommended  (≈ 5 blocks, 0.28 sat/vB)",
    4: "Economy      (≈ 4 blocks, 0.35 sat/vB)",
    3: "Normal       (≈ 3 blocks, 0.46 sat/vB)",
    2: "Priority     (≈ 2 blocks, 0.70 sat/vB)",
    1: "Urgent       (≈ 1 block,  1.40 sat/vB)",
}


class SendPage(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._balance = 0
        self._utxo_sel: set | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)

        box = QGroupBox("Send coins")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        self.pay_to = QLineEdit()
        self.pay_to.setPlaceholderText("ori1…")
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Enter a label for this address to add it to your address book")

        amt_row = QHBoxLayout()
        self.amount = QDoubleSpinBox()
        self.amount.setDecimals(8)
        self.amount.setMaximum(194_600_000)
        self.amount.setSingleStep(0.01)
        self.amount.setGroupSeparatorShown(False)
        self.unit = QLabel("ORI")
        self.btn_max = QPushButton("Use available balance")
        self.btn_max.setAutoDefault(False)
        amt_row.addWidget(self.amount, 1)
        amt_row.addWidget(self.unit)
        amt_row.addWidget(self.btn_max)

        self.subtract_fee = QCheckBox("Subtract fee from amount")
        self.balance_lbl = QLabel("Balance: 0.00000000 ORI")
        self.balance_lbl.setObjectName("balanceValue")

        inputs_row = QHBoxLayout()
        self.btn_inputs = QPushButton("Inputs…")
        self.btn_inputs.setAutoDefault(False)
        self.btn_inputs.setToolTip("Coin control — choose which coins to spend")
        self.auto_lbl = QLabel("Auto")
        self.auto_lbl.setObjectName("muted")
        inputs_row.addWidget(self.btn_inputs)
        inputs_row.addWidget(self.auto_lbl)
        inputs_row.addStretch(1)

        form.addRow("Pay &To:", self.pay_to)
        form.addRow("&Label:", self.label_edit)
        form.addRow("&Amount:", amt_row)
        form.addRow("", self.subtract_fee)
        form.addRow("", self.balance_lbl)
        form.addRow("", inputs_row)

        fee_box = QGroupBox("Transaction fee")
        fee_l = QVBoxLayout(fee_box)
        self.tier = QComboBox()
        for t in (5, 4, 3, 2, 1):
            self.tier.addItem(TIER_LABELS[t], t)
        self.fee_hint = QLabel("Confirmation time is an estimate based on ORI fee tiers.")
        self.fee_hint.setObjectName("muted")
        self.fee_preview = QLabel("")
        fee_l.addWidget(self.tier)
        fee_l.addWidget(self.fee_hint)
        fee_l.addWidget(self.fee_preview)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_clear = QPushButton("Clear &All")
        self.btn_send = QPushButton("&Send")
        self.btn_send.setObjectName("primaryButton")
        self.btn_send.setDefault(True)
        btns.addWidget(self.btn_clear)
        btns.addWidget(self.btn_send)

        root.addWidget(box)
        root.addWidget(fee_box)
        root.addStretch(1)
        root.addLayout(btns)

        self.btn_clear.clicked.connect(self.clear)
        self.btn_send.clicked.connect(self._send)
        self.btn_max.clicked.connect(self._use_max)
        self.btn_inputs.clicked.connect(self._pick_inputs)
        self.amount.valueChanged.connect(self._preview)
        self.pay_to.textChanged.connect(self._preview)
        self.tier.currentIndexChanged.connect(self._preview)
        self.subtract_fee.toggled.connect(self._preview)

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(250)
        self._preview_timer.timeout.connect(self._run_preview)

    def apply_snapshot(self, snap: dict):
        self._balance = snap.get("balances", {}).get("available", 0)
        self.balance_lbl.setText("Balance: " + format_ori(self._balance))
        wanted = int(snap.get("fee_tier", 3))
        idx = self.tier.findData(wanted)
        if idx >= 0 and self.tier.currentData() != wanted and not self.pay_to.text():
            self.tier.setCurrentIndex(idx)
        self._prune_selection()

    def _selected_utxos(self) -> list:
        """Current wallet UTXOs restricted to the coin-control selection."""
        if not self._utxo_sel:
            return []
        return [
            u for u in self.controller.wallet_utxos()
            if (u["txid"], u["vout"]) in self._utxo_sel
        ]

    def _prune_selection(self):
        """Drop selected inputs that are no longer spendable (spent/merged)."""
        if not self._utxo_sel or not self.controller.node:
            return
        available = {(u["txid"], u["vout"]) for u in self.controller.wallet_utxos()}
        current = {k for k in self._utxo_sel if k in available}
        if current != self._utxo_sel:
            self._utxo_sel = current
            self._update_auto_label()

    def _update_auto_label(self):
        sel = self._selected_utxos()
        if not sel:
            self.auto_lbl.setText("Auto")
            self.auto_lbl.setToolTip("Use all spendable inputs automatically")
        else:
            total = sum(u["value"] for u in sel)
            self.auto_lbl.setText(
                f"{len(sel)} input(s) — {format_ori(total)}"
            )
            self.auto_lbl.setToolTip("Coin control: only the selected inputs will be spent")

    def _pick_inputs(self):
        if not self.controller.node:
            return
        dlg = CoinControlDialog(self.controller, self)
        dlg.set_selection(self._utxo_sel)
        if dlg.exec():
            self._utxo_sel = dlg.selected_keys()
            self._update_auto_label()
            self._preview()

    def clear(self):
        self.pay_to.clear()
        self.label_edit.clear()
        self.amount.setValue(0)
        self.subtract_fee.setChecked(False)
        self._utxo_sel = None
        self._update_auto_label()
        self.fee_preview.setText("")

    def _use_max(self):
        selected = self._selected_utxos()
        if selected:
            total = sum(u["value"] for u in selected)
        else:
            total = self._balance
        self.amount.setValue(total / 100_000_000)
        self.subtract_fee.setChecked(True)

    def _preview(self):
        self._preview_timer.start()

    def _run_preview(self):
        to = self.pay_to.text().strip()
        if not to or self.amount.value() <= 0:
            self.fee_preview.setText("")
            return
        if self.controller.is_locked():
            self.fee_preview.setText("Wallet is locked. Unlock it in File → Load Wallet.")
            self.fee_preview.setStyleSheet("color: #E74C3C;")
            return
        try:
            plan = self.controller.estimate_send(
                to, self.amount.value(), int(self.tier.currentData()),
                self.subtract_fee.isChecked(), self._utxo_sel
            )
            self.fee_preview.setText(
                f"Fee: {format_ori(plan['fee'])}  ({plan['fee']:,} sats, {plan['size']} vB @ {plan['rate']} sat/vB)"
                f"    Recipient gets {format_ori(plan['send_amount'])}"
                f"   — {len(plan['selected'])} input(s)"
            )
            self.fee_preview.setStyleSheet(f"color: {BITCOIN_ORANGE};")
        except WalletError as exc:
            self.fee_preview.setText(str(exc))
            self.fee_preview.setStyleSheet("color: #E74C3C;")
        except Exception:
            self.fee_preview.setText("")

    def _send(self):
        if self.controller.is_locked():
            QMessageBox.warning(self, "Wallet Locked", "Your wallet is encrypted and currently locked.\nPlease go to File → Load Wallet to unlock it first.")
            return
        to = self.pay_to.text().strip()
        if not to:
            QMessageBox.warning(self, "Send Coins", "Please enter a recipient address.")
            return
        if self.amount.value() <= 0:
            QMessageBox.warning(self, "Send Coins", "Please enter an amount.")
            return
        try:
            plan = self.controller.estimate_send(
                to, self.amount.value(), int(self.tier.currentData()),
                self.subtract_fee.isChecked(), self._utxo_sel
            )
        except WalletError as exc:
            QMessageBox.critical(self, "Send Coins", str(exc))
            return

        msg = (
            f"Do you want to send these coins?\n\n"
            f"Pay to:  {to}\n"
            f"Amount:  {format_ori(plan['send_amount'])}\n"
            f"Fee:     {format_ori(plan['fee'])}  ({plan['size']} vB)\n"
            f"Net:     {format_ori(plan['send_amount'] + plan['fee'])}"
        )
        if self.label_edit.text().strip():
            msg += f"\nLabel:   {self.label_edit.text().strip()}"
        reply = QMessageBox.question(
            self,
            "Confirm send coins",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.controller.send_coins(
                to,
                self.amount.value(),
                int(self.tier.currentData()),
                self.subtract_fee.isChecked(),
                self.label_edit.text().strip(),
                self._utxo_sel,
            )
        except WalletError as exc:
            QMessageBox.critical(self, "Send Coins", str(exc))
            return
        QMessageBox.information(
            self,
            "Send Coins",
            f"The transaction has been signed and broadcast.\n\n"
            f"Transaction ID:\n{result['txid']}",
        )
        self.clear()
        self.controller.refresh()
