import os

import pytest

try:
    from PySide6.QtWidgets import QApplication
except Exception:  # pragma: no cover - depends on GUI extras being installed
    QApplication = None


@pytest.fixture
def qt_app(monkeypatch):
    if QApplication is None:
        pytest.skip("PySide6 is not installed")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    return app


def test_qt_datadir_lock_replaces_stale_pid(tmp_path):
    from qt_app import _acquire_datadir_lock

    lock_file = tmp_path / ".lock"
    lock_file.write_text("999999999", encoding="utf-8")

    _acquire_datadir_lock(str(lock_file))

    assert lock_file.read_text(encoding="utf-8") == str(os.getpid())


def test_pending_tx_detail_dialog_constructs_without_rbf_crash(qt_app):
    from config import Config
    from qt.dialogs import TxDetailDialog

    class NodeStub:
        cfg = Config()

        class storage:
            @staticmethod
            def height():
                return 10

    class ControllerStub:
        node = NodeStub()

        def tx_detail(self, txid):
            return {
                "txid": txid,
                "confirmations": 0,
                "mempool": True,
                "type": "send",
                "timestamp": 0,
                "amount_sats": -1000,
                "fee_sats": 10,
                "size": 200,
                "version": 1,
                "locktime": 0,
                "hex": "00",
            }

    dialog = TxDetailDialog(ControllerStub(), "11" * 32)

    assert dialog.bump_btn.text() == "Bump Fee (RBF)"
