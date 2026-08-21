#!/usr/bin/env python3
"""ORI Core — Bitcoin-Qt style wallet + full node (no mining)."""

import argparse
import ctypes
import os
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from config import Config
from qt.controller import NodeController
from qt.icons import app_icon
from qt.mainwindow import MainWindow
from qt.splash import BootThread, Splash
from qt.theme import QSS, apply_theme, load_dark_pref
from qt.wallet_dialogs import NewWalletCreatedDialog, UnlockWalletDialog
from wallet import DEFAULT_WALLET, wallet_is_encrypted


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information, False, int(pid)
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _lock_owner_pid(lock_file: str) -> int | None:
    try:
        with open(lock_file, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _acquire_datadir_lock(lock_file: str):
    if os.path.exists(lock_file):
        pid = _lock_owner_pid(lock_file)
        if pid is not None and _pid_is_running(pid):
            raise RuntimeError(
                f"A lock file was found at:\n{lock_file}\n\n"
                f"It belongs to running process PID {pid}. Close that ORI Core "
                "instance first, or choose a different data directory."
            )
        try:
            os.remove(lock_file)
        except OSError as exc:
            raise RuntimeError(
                f"A stale lock file was found at:\n{lock_file}\n\n"
                f"ORI Core could not remove it automatically: {exc}"
            ) from exc
    with open(lock_file, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))


def parse_args(argv):
    p = argparse.ArgumentParser(description="ORI Core Qt")
    p.add_argument("--wallet", default=DEFAULT_WALLET)
    p.add_argument("--config", default=None, help="JSON config file (defaults to config.json in project root)")
    p.add_argument("--datadir", default=None, help="overrides BTPY_DATA_DIR")
    p.add_argument("--api-host", default=None, help="overrides BTPY_API_HOST")
    p.add_argument("--api-port", default=None, help="overrides BTPY_API_PORT")
    p.add_argument("--p2p-port", default=None, help="overrides BTPY_P2P_PORT")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.config:
        os.environ["BTPY_CONFIG_FILE"] = args.config
    if args.datadir:
        os.environ["BTPY_DATA_DIR"] = args.datadir
    if args.api_host:
        os.environ["BTPY_API_HOST"] = args.api_host
    if args.api_port:
        os.environ["BTPY_API_PORT"] = args.api_port
    if args.p2p_port:
        os.environ["BTPY_P2P_PORT"] = args.p2p_port

    app = QApplication(sys.argv)
    app.setApplicationName("ORI Core")
    app.setOrganizationName("ORI")
    app.setWindowIcon(app_icon(64))
    app.setStyle("Fusion")
    # Apply saved theme preference (dark or light)
    apply_theme(app, load_dark_pref())

    cfg = Config.from_env()
    os.makedirs(cfg.data_dir, exist_ok=True)
    lock_file = os.path.join(cfg.data_dir, ".lock")
    try:
        _acquire_datadir_lock(lock_file)
    except RuntimeError as exc:
        QMessageBox.critical(None, "ORI Core Already Running", str(exc))
        sys.exit(1)

    controller = NodeController(cfg, wallet_path=args.wallet)

    splash = Splash()
    splash.show()
    splash.set_status("Loading block index…")
    app.processEvents()

    window = MainWindow(controller)
    boot = BootThread(controller)
    error = {"msg": None}

    def on_fail(msg):
        error["msg"] = msg

    boot.failed.connect(on_fail)

    def on_boot_finished():
        splash.set_status("Loading wallet…")
        app.processEvents()
        splash.close()
        if error["msg"]:
            QMessageBox.critical(None, "ORI Core", "Failed to start node:\n" + error["msg"])
            app.quit()
            return
        
        passphrase = None
        if wallet_is_encrypted(args.wallet):
            dlg = UnlockWalletDialog(args.wallet)
            if dlg.exec():
                passphrase = dlg.passphrase()
            else:
                app.quit()
                return

        try:
            controller.load_wallet_and_timers(passphrase)
        except Exception as exc:
            QMessageBox.critical(None, "ORI Core", "Failed to load wallet:\n" + str(exc))
            controller.shutdown()
            app.quit()
            return

        def on_started(first_run: bool):
            if first_run:
                _, info = controller.default_account()
                if info:
                    dlg = NewWalletCreatedDialog(info.get("address", ""), args.wallet, window)
                    dlg.exec()

        controller.started.connect(on_started)
        
        window.show()
        window.raise_()
        auto = os.environ.get("ORI_TEST_AUTOQUIT_MS")
        if auto:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(int(auto), app.quit)

    boot.finished.connect(on_boot_finished)
    boot.start()
    
    try:
        ret = app.exec()
        return ret
    finally:
        controller.shutdown()
        if os.path.exists(lock_file):
            try:
                os.remove(lock_file)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
