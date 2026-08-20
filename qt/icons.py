"""Toolbar / status icons painted in Bitcoin-Qt orange (no coin logo)."""

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygon

ORANGE = QColor("#F7931A")
DARK = QColor("#3D3D3D")
GREEN = QColor("#2E7D32")
YELLOW = QColor("#C9A227")
RED = QColor("#C0392B")
GRAY = QColor("#8A8A8A")


def _canvas(size: int, color: QColor = None) -> tuple:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if color:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
    return pm, p


def overview_icon(size: int = 32) -> QIcon:
    pm, p = _canvas(size, ORANGE)
    m = size * 0.18
    path = QPainterPath()
    path.moveTo(size / 2, m)
    path.lineTo(size - m, size * 0.46)
    path.lineTo(size - m * 1.35, size * 0.46)
    path.lineTo(size - m * 1.35, size - m)
    path.lineTo(size * 0.55, size - m)
    path.lineTo(size * 0.55, size * 0.62)
    path.lineTo(size * 0.45, size * 0.62)
    path.lineTo(size * 0.45, size - m)
    path.lineTo(m * 1.35, size - m)
    path.lineTo(m * 1.35, size * 0.46)
    path.lineTo(m, size * 0.46)
    path.closeSubpath()
    p.drawPath(path)
    p.end()
    return QIcon(pm)


def send_icon(size: int = 32) -> QIcon:
    pm, p = _canvas(size, ORANGE)
    s = size
    p.drawPolygon(QPolygon([
        QPoint(int(s * 0.22), int(s * 0.70)),
        QPoint(int(s * 0.38), int(s * 0.54)),
        QPoint(int(s * 0.28), int(s * 0.54)),
        QPoint(int(s * 0.28), int(s * 0.22)),
        QPoint(int(s * 0.72), int(s * 0.22)),
        QPoint(int(s * 0.72), int(s * 0.38)),
        QPoint(int(s * 0.82), int(s * 0.28)),
        QPoint(int(s * 0.82), int(s * 0.55)),
        QPoint(int(s * 0.55), int(s * 0.55)),
        QPoint(int(s * 0.64), int(s * 0.46)),
        QPoint(int(s * 0.48), int(s * 0.62)),
    ]))
    p.end()
    return QIcon(pm)


def receive_icon(size: int = 32) -> QIcon:
    pm, p = _canvas(size, ORANGE)
    s = size
    p.drawPolygon(QPolygon([
        QPoint(int(s * 0.78), int(s * 0.30)),
        QPoint(int(s * 0.62), int(s * 0.46)),
        QPoint(int(s * 0.72), int(s * 0.46)),
        QPoint(int(s * 0.72), int(s * 0.78)),
        QPoint(int(s * 0.28), int(s * 0.78)),
        QPoint(int(s * 0.28), int(s * 0.62)),
        QPoint(int(s * 0.18), int(s * 0.72)),
        QPoint(int(s * 0.18), int(s * 0.45)),
        QPoint(int(s * 0.45), int(s * 0.45)),
        QPoint(int(s * 0.36), int(s * 0.54)),
        QPoint(int(s * 0.52), int(s * 0.38)),
    ]))
    p.end()
    return QIcon(pm)


def history_icon(size: int = 32) -> QIcon:
    pm, p = _canvas(size)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(ORANGE)
    y = size * 0.22
    h = size * 0.12
    gap = size * 0.18
    x = size * 0.20
    w = size * 0.60
    for i in range(3):
        p.drawRoundedRect(QRect(int(x), int(y + i * gap), int(w), int(h)), 2, 2)
    p.end()
    return QIcon(pm)


def connection_icon(level: int, size: int = 18) -> QIcon:
    """0 none, 1 few, 2 some, 3 many — Bitcoin-Qt statusbar bars."""
    if level <= 0:
        color = RED
        bars = 1
    elif level == 1:
        color = YELLOW
        bars = 2
    elif level == 2:
        color = GREEN
        bars = 3
    else:
        color = GREEN
        bars = 4
    pm, p = _canvas(size)
    p.setPen(Qt.PenStyle.NoPen)
    widths = [3, 3, 3, 3]
    gap = 2
    total_w = sum(widths) + gap * 3
    x0 = (size - total_w) // 2
    heights = [5, 8, 12, 16]
    for i in range(4):
        h = heights[i]
        p.setBrush(color if i < bars else QColor("#D0D0D0"))
        p.drawRect(x0, size - 2 - h, widths[i], h)
        x0 += widths[i] + gap
    p.end()
    return QIcon(pm)


def sync_icon(ok: bool, size: int = 18) -> QIcon:
    pm, p = _canvas(size)
    color = GREEN if ok else ORANGE
    p.setPen(QPen(color, 2))
    p.setBrush(Qt.BrushStyle.NoBrush)
    m = 3
    p.drawEllipse(m, m, size - 2 * m, size - 2 * m)
    p.drawArc(m + 2, m + 2, size - 2 * m - 4, size - 2 * m - 4, 90 * 16, 220 * 16)
    p.end()
    return QIcon(pm)


def tx_arrow(incoming: bool, confirmed: bool, size: int = 16) -> QIcon:
    color = GREEN if incoming else RED
    if not confirmed:
        color = GRAY
    pm, p = _canvas(size, color)
    s = size
    if incoming:
        p.drawPolygon(QPolygon([
            QPoint(int(s * 0.50), int(s * 0.78)),
            QPoint(int(s * 0.22), int(s * 0.42)),
            QPoint(int(s * 0.40), int(s * 0.42)),
            QPoint(int(s * 0.40), int(s * 0.18)),
            QPoint(int(s * 0.60), int(s * 0.18)),
            QPoint(int(s * 0.60), int(s * 0.42)),
            QPoint(int(s * 0.78), int(s * 0.42)),
        ]))
    else:
        p.drawPolygon(QPolygon([
            QPoint(int(s * 0.50), int(s * 0.18)),
            QPoint(int(s * 0.22), int(s * 0.54)),
            QPoint(int(s * 0.40), int(s * 0.54)),
            QPoint(int(s * 0.40), int(s * 0.82)),
            QPoint(int(s * 0.60), int(s * 0.82)),
            QPoint(int(s * 0.60), int(s * 0.54)),
            QPoint(int(s * 0.78), int(s * 0.54)),
        ]))
    p.end()
    return QIcon(pm)


def app_icon(size: int = 64) -> QIcon:
    """Placeholder mark (circle + ORI) until a logo is supplied."""
    pm, p = _canvas(size)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(ORANGE)
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.setPen(QColor("#FFFFFF"))
    font = p.font()
    font.setBold(True)
    font.setPixelSize(int(size * 0.32))
    p.setFont(font)
    p.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, "ORI")
    p.end()
    return QIcon(pm)


def icon_size() -> QSize:
    return QSize(32, 32)
