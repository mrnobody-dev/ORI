from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from qt.controller import format_time
from qt.dialogs import AddressDetailDialog
from wallet import format_ori


class ReceivePage(QWidget):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)

        box = QGroupBox("Request payment")
        form = QFormLayout(box)
        self.amount = QDoubleSpinBox()
        self.amount.setDecimals(8)
        self.amount.setMaximum(194_600_000)
        self.label_edit = QLineEdit()
        self.message = QLineEdit()
        form.addRow("&Amount:", self.amount)
        form.addRow("&Label:", self.label_edit)
        form.addRow("&Message:", self.message)

        self.addr_box = QGroupBox("Receiving address")
        av = QVBoxLayout(self.addr_box)
        addr_row = QHBoxLayout()
        left_col = QVBoxLayout()
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        self.address.setMinimumHeight(32)
        font = self.address.font()
        font.setFamily("Consolas")
        font.setPointSize(11)
        self.address.setFont(font)
        row = QHBoxLayout()
        self.btn_copy = QPushButton("Copy address")
        self.btn_new = QPushButton("Create new receiving address")
        self.btn_request = QPushButton("&Request payment")
        self.btn_request.setObjectName("primaryButton")
        row.addWidget(self.btn_copy)
        row.addWidget(self.btn_new)
        row.addStretch(1)
        row.addWidget(self.btn_request)
        left_col.addWidget(self.address)
        left_col.addLayout(row)
        hint = QLabel("Share this ori1… address to receive payments.")
        hint.setObjectName("muted")
        left_col.addWidget(hint)
        addr_row.addLayout(left_col, 1)

        # Inline QR code for the current receiving address.
        self.qr_label = QLabel()
        self.qr_label.setFixedSize(150, 150)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setObjectName("qrBox")
        addr_row.addWidget(self.qr_label)
        av.addLayout(addr_row)

        hist = QGroupBox("Requested payments history")
        hv = QVBoxLayout(hist)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Date", "Label", "Address", "Amount"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        hv.addWidget(self.table)

        self.table.cellDoubleClicked.connect(self._open_address_detail)

        root.addWidget(box)
        root.addWidget(self.addr_box)
        root.addWidget(hist, 1)

        self.btn_copy.clicked.connect(self._copy)
        self.btn_new.clicked.connect(self._new_addr)
        self.btn_request.clicked.connect(self._request)

    def apply_snapshot(self, snap: dict):
        if not self.address.text():
            self.address.setText(snap.get("default_address", ""))
        self._fill_requests(snap.get("receive_requests", []))
        self._update_qr()

    def _update_qr(self):
        addr = self.address.text().strip()
        if not addr:
            self.qr_label.clear()
            return
        try:
            import qrcode
            from PySide6.QtGui import QImage, QPixmap

            img = qrcode.make(f"ori:{addr}")
            # Convert to RGBA so Qt's image format matches (1-bit PIL is packed bytes,
            # not 1 byte per pixel — using RGBA8888 is safe on all PIL versions).
            img_rgba = img.convert("RGBA")
            w, h = img_rgba.size
            qimg = QImage(
                img_rgba.tobytes("raw", "RGBA"), w, h,
                QImage.Format.Format_RGBA8888,
            )
            pix = QPixmap.fromImage(qimg).scaled(
                140, 140,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.qr_label.setPixmap(pix)
        except ImportError:
            self.qr_label.setText("QR\nn/a\n(install\nqrcode)")
        except Exception as exc:
            self.qr_label.setText(f"QR\nerror")

    def _fill_requests(self, rows: list):
        self.table.setRowCount(len(rows))
        for i, rec in enumerate(rows):
            vals = [
                format_time(rec.get("timestamp", 0)),
                rec.get("label", ""),
                rec.get("address", ""),
                format_ori(rec.get("amount_sats", 0)) if rec.get("amount_sats") else "",
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c in (0, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(i, c, item)

    def _copy(self):
        from PySide6.QtWidgets import QApplication

        text = self.address.text().strip()
        if text:
            QApplication.clipboard().setText(text)

    def _new_addr(self):
        name, info = self.controller.new_receiving_address(self.label_edit.text().strip())
        self.address.setText(info["address"])
        self._update_qr()
        QMessageBox.information(
            self,
            "New receiving address",
            f"A new receiving address was generated.\n\n{info['address']}\n({name})",
        )

    def _request(self):
        import time
        from urllib.parse import quote

        addr = self.address.text().strip()
        if not addr:
            QMessageBox.warning(self, "Request payment", "No receiving address.")
            return
        amount = self.amount.value()
        sats = int(round(amount * 100_000_000)) if amount else 0
        label = self.label_edit.text().strip()
        if label:
            self.controller.set_label(addr, label)
        uri = f"ori:{addr}"
        params = []
        if amount:
            params.append(f"amount={amount:.8f}".rstrip("0").rstrip("."))
        if label:
            params.append("label=" + quote(label))
        if self.message.text().strip():
            params.append("message=" + quote(self.message.text().strip()))
        if params:
            uri += "?" + "&".join(params)
        self.controller.add_receive_request({
            "timestamp": int(time.time()),
            "label": label,
            "address": addr,
            "amount_sats": sats,
            "message": self.message.text().strip(),
            "uri": uri,
        })
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(uri)
        QMessageBox.information(
            self,
            "Request payment",
            f"Payment request copied to clipboard:\n\n{uri}",
        )
        self.controller.refresh()

    def _open_address_detail(self, row: int, col: int):
        item = self.table.item(row, 2)
        if item and item.text():
            AddressDetailDialog(self.controller, item.text(), self).exec()
