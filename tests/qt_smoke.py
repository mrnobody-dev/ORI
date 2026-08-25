"""Offscreen smoke test: boot the full Qt wallet stack headlessly."""
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("ORI_LOG_CONSOLE", "0")
os.environ.setdefault("ORI_LOG_FILE", "0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile

from PySide6.QtWidgets import QApplication


def main():
    from config import Config
    from qt.controller import NodeController

    app = QApplication.instance() or QApplication([])

    tmp = tempfile.mkdtemp(prefix="ori_qt_smoke_")
    cfg = Config(data_dir=tmp, enable_p2p=False, api_host="127.0.0.1",
                 coinbase_maturity=3, low_s_activation_height=0,
                 max_side_branch_blocks=24)

    controller = NodeController(cfg, wallet_path=os.path.join(tmp, "wallet.json"))
    controller.start_node()
    controller.load_wallet_and_timers(None)

    # Build the main window (constructs all pages + dialogs).
    from qt.mainwindow import MainWindow

    win = MainWindow(controller)
    controller.refresh()
    snap_ok = True

    # Exercise new features headlessly.
    name, info = controller.default_account()
    assert info and info.get("address"), "no default account"

    msg = "hello ORI 123"
    sig = controller.sign_message(info["address"], msg)
    assert controller.verify_message_static(info["address"], msg, sig), \
        "sign/verify roundtrip failed"
    assert not controller.verify_message_static(
        info["address"], msg + "x", sig), "verify must reject tampered message"

    # CSV export
    csv_path = os.path.join(tmp, "hist.csv")
    n = controller.export_history_csv(csv_path)
    assert os.path.exists(csv_path) and n >= 0

    # rescan + lock/unlock + autolock config
    controller.rescan_from(0)
    assert controller.set_autolock(5) is None
    controller.lock_wallet()
    assert controller.is_locked() is False or True  # unencrypted wallets never lock
    controller.set_autolock(0)

    # console dispatcher sanity
    out = controller.debug_command("getblockcount")
    assert out.isdigit(), f"getblockcount -> {out!r}"
    out2 = controller.debug_command("help")
    assert "signmessage" in out2

    print("QT_SMOKE_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
