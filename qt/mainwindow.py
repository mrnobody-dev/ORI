import time

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QToolButton,
)

from qt.dialogs import (
    AboutDialog,
    AddressBookDialog,
    ConsoleDialog,
    InformationDialog,
    OptionsDialog,
    PeersDialog,
    backup_wallet,
)
from qt.wallet_dialogs import (
    EncryptWalletDialog,
    LoadWalletDialog,
    NewWalletCreatedDialog,
    UnlockWalletDialog,
)
from qt.icons import (
    app_icon,
    connection_icon,
    history_icon,
    overview_icon,
    receive_icon,
    send_icon,
    sync_icon,
)
from qt.overview_page import OverviewPage
from qt.receive_page import ReceivePage
from qt.send_page import SendPage
from qt.tx_page import TransactionsPage
from qt.theme import apply_theme, load_dark_pref


def _fmt_dur(seconds: int) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


class MainWindow(QMainWindow):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.setWindowTitle("ORI Core - Wallet")
        self.setWindowIcon(app_icon(32))
        self.resize(980, 620)
        self.setMinimumSize(760, 480)
        self._dark_mode = load_dark_pref()

        self.overview = OverviewPage(controller)
        self.send = SendPage(controller)
        self.receive = ReceivePage(controller)
        self.txs = TransactionsPage(controller)

        self.stack = QStackedWidget()
        self.stack.addWidget(self.overview)
        self.stack.addWidget(self.send)
        self.stack.addWidget(self.receive)
        self.stack.addWidget(self.txs)
        self.setCentralWidget(self.stack)

        self.info_dlg = InformationDialog(controller, self)
        self.console_dlg = ConsoleDialog(controller, self)
        self.peers_dlg = PeersDialog(controller, self)
        self.addr_dlg = AddressBookDialog(controller, self)

        self._build_menu()
        self._build_toolbar()
        self._build_status()
        self._sync_started = None

        controller.snapshot_ready.connect(self._on_snapshot)
        controller.history_ready.connect(self._on_history)
        controller.log_line.connect(self.console_dlg.append_log)
        controller.error.connect(self._on_error)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("&File")
        
        act_load = QAction("&Load Wallet…", self)
        act_load.triggered.connect(self._act_load_wallet)
        file_menu.addAction(act_load)
        
        act_encrypt = QAction("E&ncrypt Wallet…", self)
        act_encrypt.triggered.connect(self._act_encrypt_wallet)
        file_menu.addAction(act_encrypt)
        
        file_menu.addSeparator()

        act_backup = QAction("&Backup Wallet…", self)
        act_backup.triggered.connect(lambda: backup_wallet(self, self.controller))
        act_recv = QAction("&Receiving addresses…", self)
        act_recv.triggered.connect(self.addr_dlg.exec)
        act_quit = QAction("E&xit", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        
        file_menu.addAction(act_backup)
        file_menu.addAction(act_recv)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

        settings = self.menuBar().addMenu("&Settings")
        act_opt = QAction("&Options…", self)
        act_opt.triggered.connect(lambda: OptionsDialog(self.controller, self).exec())
        settings.addAction(act_opt)
        settings.addSeparator()
        self._act_dark = QAction("🌙  Dark Mode", self)
        self._act_dark.setCheckable(True)
        self._act_dark.setChecked(self._dark_mode)
        self._act_dark.triggered.connect(self._toggle_dark_mode)
        settings.addAction(self._act_dark)

        window = self.menuBar().addMenu("&Window")
        act_info = QAction("&Information", self)
        act_info.triggered.connect(self.info_dlg.show)
        act_console = QAction("&Console", self)
        act_console.triggered.connect(self.console_dlg.show)
        act_peers = QAction("&Peers", self)
        act_peers.triggered.connect(self.peers_dlg.show)
        window.addAction(act_info)
        window.addAction(act_console)
        window.addAction(act_peers)

        help_menu = self.menuBar().addMenu("&Help")
        act_about_qt = QAction("About &Qt", self)
        act_about_qt.triggered.connect(lambda: QMessageBox.aboutQt(self, "About Qt"))
        act_about = QAction("&About ORI Core", self)
        act_about.triggered.connect(lambda: AboutDialog(self).exec())
        help_menu.addAction(act_about_qt)
        help_menu.addAction(act_about)

    def _build_toolbar(self):
        bar = QToolBar("Tabs")
        bar.setObjectName("mainToolbar")
        bar.setMovable(False)
        bar.setIconSize(QSize(28, 28))
        bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)

        self._nav = []
        specs = [
            ("&Overview", overview_icon(), 0),
            ("&Send", send_icon(), 1),
            ("Re&ceive", receive_icon(), 2),
            ("&Transactions", history_icon(), 3),
        ]
        for text, icon, index in specs:
            btn = QToolButton()
            btn.setObjectName("navButton")
            btn.setText(text.replace("&", ""))
            btn.setIcon(icon)
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            btn.setAutoExclusive(True)
            btn.clicked.connect(lambda checked, i=index: self.stack.setCurrentIndex(i))
            bar.addWidget(btn)
            self._nav.append(btn)
        self._nav[0].setChecked(True)
        self.stack.currentChanged.connect(self._sync_nav)

    def _sync_nav(self, index: int):
        for i, btn in enumerate(self._nav):
            btn.setChecked(i == index)

    def _act_load_wallet(self):
        dlg = LoadWalletDialog(self)
        if dlg.exec():
            path, pw = dlg.result_data()
            try:
                self.controller.load_wallet_file(path, pw)
                QMessageBox.information(self, "Load Wallet", f"Wallet loaded successfully from:\n{path}")
            except Exception as exc:
                QMessageBox.critical(self, "Load Wallet Error", f"Failed to load wallet:\n{exc}")

    def _act_encrypt_wallet(self):
        if self.controller.is_encrypted():
            QMessageBox.information(self, "Encrypt Wallet", "This wallet is already encrypted.")
            return
        dlg = EncryptWalletDialog(self)
        if dlg.exec():
            pw = dlg.passphrase()
            try:
                self.controller.encrypt_wallet_with(pw)
                QMessageBox.information(
                    self, "Wallet Encrypted",
                    "Your wallet has been encrypted.\n"
                    "Make sure you remember the passphrase!"
                )
            except Exception as exc:
                QMessageBox.critical(self, "Encryption Error", f"Failed to encrypt wallet:\n{exc}")

    def _toggle_dark_mode(self, checked: bool):
        self._dark_mode = checked
        app = self.__class__._app_ref if hasattr(self.__class__, "_app_ref") else None
        from PySide6.QtWidgets import QApplication
        apply_theme(QApplication.instance(), checked)

    def _build_status(self):

        bar = QStatusBar()
        self.setStatusBar(bar)
        self.conn_btn = QToolButton()
        self.conn_btn.setAutoRaise(True)
        self.conn_btn.setToolTip("Network connections")
        self.conn_btn.clicked.connect(self.peers_dlg.show)
        self.sync_lbl = QLabel("Connecting to network…")
        self.sync_icon_lbl = QLabel()
        self.progress = QProgressBar()
        self.progress.setFixedWidth(160)
        self.progress.setTextVisible(False)
        self.progress.hide()
        bar.addWidget(self.conn_btn)
        bar.addWidget(self.sync_icon_lbl)
        bar.addWidget(self.sync_lbl, 1)
        bar.addPermanentWidget(self.progress)
        self.height_lbl = QLabel("")
        bar.addPermanentWidget(self.height_lbl)

    def _on_snapshot(self, snap: dict):
        self.overview.apply_snapshot(snap)
        self.send.apply_snapshot(snap)
        self.receive.apply_snapshot(snap)
        self.info_dlg.apply_snapshot(snap)
        self.peers_dlg.apply_snapshot(snap)
        self.addr_dlg.apply_snapshot(snap)

        peers = int(snap.get("peers") or 0)
        if peers <= 0:
            level = 0
        elif peers < 3:
            level = 1
        elif peers < 8:
            level = 2
        else:
            level = 3
        self.conn_btn.setIcon(connection_icon(level))
        self.conn_btn.setText(f"  {peers}")
        self.conn_btn.setToolTip(f"{peers} active connection(s)")

        synced = snap.get("synced", True)
        behind = int(snap.get("behind") or 0)
        height = snap.get("height", 0)
        best = snap.get("best_peer_height", height)
        self.sync_icon_lbl.setPixmap(sync_icon(synced).pixmap(18, 18))
        if not synced and best and height < best:
            if self._sync_started is None:
                self._sync_started = time.time()
            elapsed = time.time() - self._sync_started
            block_time = int(snap.get("block_time") or 60)
            left = _fmt_dur(behind * block_time)
            self.sync_lbl.setText(
                f"Synchronizing…  {height} / {best}   ·   ~{left} left   ·   {_fmt_dur(elapsed)} elapsed"
            )
            self.progress.show()
            self.progress.setRange(0, max(best, 1))
            self.progress.setValue(height)
        else:
            self._sync_started = None
            self.sync_lbl.setText("Up to date")
            self.progress.hide()
        self.height_lbl.setText(f"  Block {height}   Mempool {snap.get('mempool', 0)}  ")
        name = snap.get("default_account") or "wallet"
        self.setWindowTitle(f"ORI Core - {name}")

    def _on_history(self, history: list):
        self.overview.apply_history(history)
        self.txs.apply_history(history)

    def _on_error(self, message: str):
        if message:
            self.statusBar().showMessage(message, 8000)

    def closeEvent(self, event):
        self.controller.shutdown()
        event.accept()
