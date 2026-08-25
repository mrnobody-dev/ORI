"""Wallet encryption / passphrase dialogs for ORI Core Qt."""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QFileDialog,
)

from wallet import WalletError


def _network_hrp() -> str:
    """Resolve the active network hrp from env/config (no controller needed)."""
    from config import Config

    try:
        return Config.from_env().network_hrp
    except Exception:
        return "ori"


def _hrp() -> str:
    return _network_hrp()


def _hline():
    f = QFrame()
    f.setFrameShape(QFrame.Shape.HLine)
    f.setFrameShadow(QFrame.Shadow.Sunken)
    f.setStyleSheet("color: #DEDEDE;")
    return f


def _strength(pw: str) -> int:
    """Return 0-4 passphrase strength score."""
    score = 0
    if len(pw) >= 8:
        score += 1
    if len(pw) >= 12:
        score += 1
    if any(c.isupper() for c in pw) and any(c.islower() for c in pw):
        score += 1
    if any(c.isdigit() for c in pw) or any(not c.isalnum() for c in pw):
        score += 1
    return score


_STRENGTH_COLORS = ["#E74C3C", "#E67E22", "#F1C40F", "#2ECC71", "#27AE60"]
_STRENGTH_LABELS = ["Very weak", "Weak", "Fair", "Good", "Strong"]


class _PassphraseForm(QVBoxLayout):
    """Shared passphrase / confirm-passphrase form widget."""

    def __init__(self, confirm=True):
        super().__init__()
        self._confirm = confirm
        form = QFormLayout()
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw.setPlaceholderText("Enter passphrase (min 8 characters)")
        form.addRow("Passphrase:", self.pw)

        if confirm:
            self.pw2 = QLineEdit()
            self.pw2.setEchoMode(QLineEdit.EchoMode.Password)
            self.pw2.setPlaceholderText("Repeat passphrase")
            form.addRow("Confirm:", self.pw2)

            self.strength_bar = QProgressBar()
            self.strength_bar.setRange(0, 4)
            self.strength_bar.setFixedHeight(8)
            self.strength_bar.setTextVisible(False)
            self.strength_lbl = QLabel("—")
            self.strength_lbl.setObjectName("muted")
            sh = QHBoxLayout()
            sh.addWidget(self.strength_bar, 1)
            sh.addWidget(self.strength_lbl)
            form.addRow("Strength:", sh)
            self.pw.textChanged.connect(self._update_strength)

        self.addLayout(form)

    def _update_strength(self, text: str):
        s = _strength(text)
        self.strength_bar.setValue(s)
        color = _STRENGTH_COLORS[s]
        self.strength_bar.setStyleSheet(
            f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
        )
        self.strength_lbl.setText(_STRENGTH_LABELS[s])

    def passphrase(self) -> str:
        return self.pw.text()

    def validate(self) -> str | None:
        """Returns None if valid, else error string."""
        pw = self.pw.text()
        if len(pw) < 8:
            return "Passphrase must be at least 8 characters."
        if self._confirm:
            if pw != self.pw2.text():
                return "Passphrases do not match."
        return None


class EncryptWalletDialog(QDialog):
    """Dialog to set a passphrase and encrypt the wallet."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Encrypt Wallet")
        self.setMinimumWidth(460)
        self._passphrase = None

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        info = QLabel(
            "🔐  Encrypting your wallet will protect your private keys with\n"
            "a passphrase using AES-256-GCM encryption.\n\n"
            "⚠️  If you forget your passphrase, your funds will be\n"
            "permanently inaccessible. Back up your wallet first!"
        )
        info.setWordWrap(True)
        info.setStyleSheet("background: #FEF9E7; border: 1px solid #F7931A; "
                           "border-radius: 6px; padding: 10px; font-size: 12px;")
        layout.addWidget(info)
        layout.addWidget(_hline())

        self._form = _PassphraseForm(confirm=True)
        layout.addLayout(self._form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _accept(self):
        err = self._form.validate()
        if err:
            QMessageBox.warning(self, "Invalid passphrase", err)
            return
        self._passphrase = self._form.passphrase()
        self.accept()

    def passphrase(self) -> str | None:
        return self._passphrase


class UnlockWalletDialog(QDialog):
    """Dialog to enter passphrase to unlock an encrypted wallet."""

    def __init__(self, wallet_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unlock Wallet")
        self.setMinimumWidth(420)
        self._passphrase = None
        self._wallet_path = wallet_path

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        info = QLabel(
            f"🔒  <b>{os.path.basename(wallet_path)}</b> is encrypted.\n"
            "Enter your passphrase to unlock it."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addWidget(_hline())

        self._form = _PassphraseForm(confirm=False)
        layout.addLayout(self._form)

        self._form.pw.returnPressed.connect(self._accept)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _accept(self):
        pw = self._form.passphrase()
        if not pw:
            QMessageBox.warning(self, "Passphrase required", "Please enter your passphrase.")
            return
        self._passphrase = pw
        self.accept()

    def passphrase(self) -> str | None:
        return self._passphrase


class LoadWalletDialog(QDialog):
    """Dialog to browse for and load a different wallet file."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Load Wallet")
        self.setMinimumWidth(500)
        self._result = None  # (path, passphrase | None)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        info = QLabel(
            "Select a wallet file to load. If the wallet is encrypted, "
            "you will need to enter its passphrase."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addWidget(_hline())

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Path to wallet.json")
        self.path_edit.setReadOnly(True)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        layout.addLayout(path_row)

        form = QFormLayout()
        self.pw_edit = QLineEdit()
        self.pw_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw_edit.setPlaceholderText("Leave blank if wallet is not encrypted")
        form.addRow("Passphrase:", self.pw_edit)
        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Wallet", "", "Wallet (*.dat *.json);;All files (*.*)"
        )
        if path:
            self.path_edit.setText(path)

    def _accept(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "File not found", "Please select a valid wallet file.")
            return
        pw = self.pw_edit.text() or None
        from wallet import wallet_is_encrypted
        try:
            if wallet_is_encrypted(path) and not pw:
                QMessageBox.warning(
                    self, "Passphrase required",
                    "This wallet is encrypted. Please enter its passphrase."
                )
                return
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Cannot read wallet file:\n{exc}")
            return
        self._result = (path, pw)
        self.accept()

    def result_data(self):
        return self._result  # (path, passphrase | None)


class NewWalletCreatedDialog(QDialog):
    """Shown on first run — informs user about their new wallet."""

    def __init__(self, address: str, wallet_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New Wallet Created")
        self.setMinimumWidth(520)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        title = QLabel("✅  Your new ORI wallet has been created!")
        title.setStyleSheet("font-size: 15px; font-weight: 700;")
        layout.addWidget(title)

        layout.addWidget(_hline())

        addr_lbl = QLabel("Your receiving address:")
        addr_lbl.setStyleSheet("font-weight: 600;")
        addr_val = QLineEdit(address)
        addr_val.setReadOnly(True)
        addr_val.setStyleSheet("font-family: Consolas; font-size: 12px;")
        layout.addWidget(addr_lbl)
        layout.addWidget(addr_val)

        warn = QLabel(
            "⚠️  <b>IMPORTANT — Read before continuing:</b>\n\n"
            "• Your private keys are stored in:\n"
            f"  <code>{wallet_path}</code>\n\n"
            "• Anyone with access to this file can steal your funds.\n\n"
            "• <b>Strongly recommended</b>: use <i>File → Encrypt Wallet</i> to\n"
            "  protect your keys with a passphrase, then <i>File → Backup Wallet</i>\n"
            "  to save a copy in a safe location.\n\n"
            "• If you lose this file and have no backup, your funds are gone forever."
        )
        warn.setWordWrap(True)
        warn.setTextFormat(Qt.TextFormat.RichText)
        warn.setStyleSheet(
            "background: #FEF9E7; border: 1px solid #E67E22; "
            "border-radius: 6px; padding: 12px; font-size: 12px;"
        )
        layout.addWidget(warn)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        btns.accepted.connect(self.accept)
        layout.addWidget(btns)


class BumpFeeDialog(QDialog):
    """Dialog to choose new fee tier for RBF bump."""

    def __init__(self, txid: str, current_tier: int = 5, parent=None, cfg=None):
        super().__init__(parent)
        self.setWindowTitle("Bump Transaction Fee (RBF)")
        self.setMinimumWidth(460)
        self._new_tier = None

        from config import Config

        cfg = cfg or Config()

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        info = QLabel(
            "🔄  <b>Replace-By-Fee (RBF)</b>\n\n"
            "Select a higher fee tier to replace the unconfirmed transaction.\n"
            "The old fee is forfeit. A new transaction with a new TxID will be created."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setStyleSheet(
            "background: #EBF5FB; border: 1px solid #2980B9; "
            "border-radius: 6px; padding: 10px; font-size: 12px;"
        )
        layout.addWidget(info)

        txid_lbl = QLabel(f"TxID: <code>{txid[:16]}…{txid[-8:]}</code>")
        txid_lbl.setTextFormat(Qt.TextFormat.RichText)
        txid_lbl.setObjectName("muted")
        layout.addWidget(txid_lbl)

        layout.addWidget(_hline())

        form = QFormLayout()
        from PySide6.QtWidgets import QComboBox
        self.tier_combo = QComboBox()
        tier_items = []
        for tier in sorted(cfg.fee_tiers_per_vb.keys(), reverse=True):
            rate = cfg.fee_tiers_per_vb[tier]
            label = {5: "Tier 5 — Slowest (0.28 sat/vB)", 4: "Tier 4 (0.35 sat/vB)",
                     3: "Tier 3 (0.46 sat/vB)", 2: "Tier 2 (0.70 sat/vB)",
                     1: "Tier 1 — Fastest (1.40 sat/vB)"}.get(tier, f"Tier {tier}")
            tier_items.append((tier, label))
        # Only show tiers higher (faster/more expensive) than current
        for tier, label in tier_items:
            if tier < current_tier:
                self.tier_combo.addItem(label, userData=tier)
        if self.tier_combo.count() == 0:
            for tier, label in tier_items:
                self.tier_combo.addItem(label, userData=tier)

        form.addRow("New tier:", self.tier_combo)
        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _accept(self):
        self._new_tier = self.tier_combo.currentData()
        self.accept()

    def new_tier(self) -> int | None:
        return self._new_tier


class AddressBookEntryDialog(QDialog):
    """Simple form to add/edit an address book contact."""

    def __init__(self, parent=None, address: str = "", label: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Address Book Entry")
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.label_edit = QLineEdit(label)
        self.label_edit.setPlaceholderText("e.g. Alice, Exchange, Savings")
        self.addr_edit = QLineEdit(address)
        self.addr_edit.setPlaceholderText("ori1…")
        if address:
            self.addr_edit.setEnabled(False)
        form.addRow("&Label:", self.label_edit)
        form.addRow("&Address:", self.addr_edit)
        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _accept(self):
        from bech32 import validate_address
        addr = self.address_text()
        if not validate_address(addr, self._hrp()):
            QMessageBox.warning(
                self, "Invalid Address",
                "That is not a valid ORI (ori1…) address.",
            )
            return
        self.accept()

    def label_text(self) -> str:
        return self.label_edit.text().strip()

    def address_text(self) -> str:
        return self.addr_edit.text().strip()


# ---------------------------------------------------------------------------
# Bitcoin Core parity dialogs (change passphrase, sign/verify message)
# ---------------------------------------------------------------------------

class ChangePassphraseDialog(QDialog):
    """walletpassphrasechange: verify current passphrase, set a new one."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Change Passphrase")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        info = QLabel(
            "Enter the CURRENT passphrase to authorize the change, "
            "then choose a NEW passphrase."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        self.cur = QLineEdit()
        self.cur.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Current passphrase:", self.cur)
        self.form_new = _PassphraseForm(confirm=True)
        form.addRow(self.form_new)
        layout.addLayout(form)
        layout.addWidget(_hline())

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _accept(self):
        if len(self.cur.text()) < 1:
            QMessageBox.warning(self, "Change Passphrase", "Enter the current passphrase.")
            return
        err = self.form_new.validate()
        if err:
            QMessageBox.warning(self, "Change Passphrase", err)
            return
        self.accept()

    def current_passphrase(self) -> str:
        return self.cur.text()

    def new_passphrase(self) -> str:
        return self.form_new.passphrase()


class UnlockTimeoutDialog(QDialog):
    """Unlock with optional auto-lock timeout (like walletpassphrase <n>)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Unlock Wallet")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "The wallet is encrypted. Enter the passphrase to unlock it.\n"
            "Optionally auto-relock after N minutes (0 = stay unlocked)."
        ))
        form = QFormLayout()
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Passphrase:", self.pw)
        from PySide6.QtWidgets import QSpinBox
        self.minutes = QSpinBox()
        self.minutes.setRange(0, 10080)
        self.minutes.setValue(10)
        form.addRow("Auto-lock after (minutes):", self.minutes)
        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def passphrase(self) -> str:
        return self.pw.text()

    def timeout_minutes(self) -> int:
        return self.minutes.value()


class SignMessageDialog(QDialog):
    """signmessage: produce a hex signature for an owned address."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setWindowTitle("Sign Message")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        warn = QLabel(
            "Signing a message proves you own an address, but can also reveal "
            "information about your transactions. Only sign what you understand."
        )
        warn.setObjectName("negative")
        warn.setWordWrap(True)
        layout.addWidget(warn)

        form = QFormLayout()
        from PySide6.QtWidgets import QComboBox
        self.addr = QComboBox()
        for name, info in controller.accounts():
            self.addr.addItem(f"{info['address']}  ({name})", info["address"])
        form.addRow("Address:", self.addr)
        self.msg = QLineEdit()
        form.addRow("Message:", self.msg)
        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Close
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Sign")
        btns.accepted.connect(self._sign)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self.result_lbl = QLabel("")
        self.result_lbl.setWordWrap(True)
        self.result_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.result_lbl)

    def _sign(self):
        try:
            sig = self.controller.sign_message(
                self.addr.currentData(), self.msg.text().strip()
            )
            self.result_lbl.setText(sig)
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(sig)
            QMessageBox.information(self, "Sign Message", "Signature copied to clipboard.")
        except WalletError as exc:
            QMessageBox.warning(self, "Sign Message", str(exc))


class VerifyMessageDialog(QDialog):
    """verifymessage: check a signature against any address."""

    @staticmethod
    def verify(controller_cls_prefix, address, message, sig_hex):
        return False

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Verify Message")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.addr = QLineEdit()
        self.addr.setPlaceholderText("ori1 address that supposedly signed")
        form.addRow("Address:", self.addr)
        self.msg = QLineEdit()
        form.addRow("Message:", self.msg)
        self.sig = QLineEdit()
        self.sig.setPlaceholderText("signature (hex)")
        form.addRow("Signature:", self.sig)
        layout.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Close
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("Verify")
        btns.accepted.connect(self._verify)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def _verify(self):
        from bech32 import validate_address
        from qt.controller import NodeController

        addr = self.addr.text().strip()
        if not validate_address(addr, self._hrp()):
            QMessageBox.warning(self, "Verify Message", "Invalid ORI address.")
            return
        ok = NodeController.verify_message_static(
            addr, self.msg.text().strip(), self.sig.text().strip()
        )
        if ok:
            QMessageBox.information(
                self, "Verify Message",
                "Message verified:\n\nThe signature matches the address.",
            )
        else:
            QMessageBox.warning(
                self, "Verify Message",
                "Message verification failed:\nSignature does NOT match.",
            )
