from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QProgressBar, QSplashScreen

from qt.theme import BITCOIN_ORANGE


def make_splash_pixmap(width=480, height=320) -> QPixmap:
    pm = QPixmap(width, height)
    pm.fill(QColor("#1B1B1B"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QColor(BITCOIN_ORANGE))
    font = QFont("Segoe UI", 28)
    font.setBold(True)
    p.setFont(font)
    p.drawText(pm.rect().adjusted(0, 40, 0, -80), Qt.AlignmentFlag.AlignHCenter, "ORI Core")
    p.setPen(QColor("#EDEDED"))
    font.setPointSize(11)
    font.setBold(False)
    p.setFont(font)
    p.drawText(pm.rect().adjusted(0, 100, 0, -40), Qt.AlignmentFlag.AlignHCenter, "Wallet and full node")
    p.end()
    return pm


class BootThread(QThread):
    failed = Signal(str)

    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def run(self):
        try:
            self.controller.start_node()
        except Exception as exc:
            self.failed.emit(str(exc))


class Splash(QSplashScreen):
    def __init__(self):
        super().__init__(make_splash_pixmap())
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        bar = QProgressBar(self)
        bar.setRange(0, 0)
        bar.setGeometry(40, 260, 400, 16)
        bar.setTextVisible(False)
        self._bar = bar

    def set_status(self, text: str):
        self.showMessage(text, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, QColor("#F7931A"))
