# -*- coding: utf-8 -*-
"""
SerialTerminal.py
Giao diện công cụ Serial Terminal (giống SSCOM/CoolTerm) dùng PyQt5 + pyserial.

Cài đặt thư viện cần thiết:
    pip install PyQt5 pyserial

Chạy:
    python SerialTerminal.py
"""

import sys
import time
import binascii
from datetime import datetime

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer, QSize
from PyQt5.QtGui import QFont, QIcon
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QComboBox, QPushButton,
    QCheckBox, QTextEdit, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QFileDialog, QAction, QToolBar, QLineEdit, QSpinBox, QFrame, QSizePolicy,
    QMessageBox, QSplitter
)

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


# --------------------------------------------------------------------------- #
# Luồng đọc dữ liệu Serial (chạy nền để không block giao diện)
# --------------------------------------------------------------------------- #
class SerialReaderThread(QThread):
    data_received = pyqtSignal(bytes)
    error_occurred = pyqtSignal(str)

    def __init__(self, ser):
        super().__init__()
        self.ser = ser
        self._running = True

    def run(self):
        while self._running:
            try:
                if self.ser and self.ser.is_open:
                    n = self.ser.in_waiting
                    if n:
                        data = self.ser.read(n)
                        if data:
                            self.data_received.emit(data)
                    else:
                        self.msleep(10)
                else:
                    self.msleep(50)
            except Exception as e:
                self.error_occurred.emit(str(e))
                self._running = False

    def stop(self):
        self._running = False
        self.wait(500)


# --------------------------------------------------------------------------- #
# Cửa sổ chính
# --------------------------------------------------------------------------- #
class SerialTerminal(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ser = None
        self.reader_thread = None
        self.send_count = 0
        self.recv_count = 0
        self.last_speed_time = time.time()
        self.last_send_count = 0
        self.last_recv_count = 0

        self.setWindowTitle("Serial Terminal")
        self.resize(900, 620)

        self._build_toolbar()
        self._build_body()
        self._refresh_ports()

        # Timer tính tốc độ gửi/nhận (Byte/s)
        self.speed_timer = QTimer(self)
        self.speed_timer.timeout.connect(self._update_speed)
        self.speed_timer.start(1000)

        # Timer gửi định kỳ
        self.regular_timer = QTimer(self)
        self.regular_timer.timeout.connect(self._regular_send)

    # ------------------------------------------------------------------ #
    # Toolbar: New / Connect / Disconnect + Port Desc / trạng thái
    # ------------------------------------------------------------------ #
    def _build_toolbar(self):
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(28, 28))
        self.addToolBar(toolbar)

        act_new = QAction("New", self)
        act_new.triggered.connect(self._on_new)
        toolbar.addAction(act_new)

        self.act_connect = QAction("Connect", self)
        self.act_connect.triggered.connect(self._on_connect)
        toolbar.addAction(self.act_connect)

        self.act_disconnect = QAction("Disconnect", self)
        self.act_disconnect.triggered.connect(self._on_disconnect)
        self.act_disconnect.setEnabled(False)
        toolbar.addAction(self.act_disconnect)

        toolbar.addSeparator()

        info_widget = QWidget()
        info_layout = QVBoxLayout(info_widget)
        info_layout.setContentsMargins(10, 0, 0, 0)
        info_layout.setSpacing(0)
        self.port_desc_label = QLabel("Port Desc: ")
        self.status_label = QLabel("Serial not open")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        info_layout.addWidget(self.port_desc_label)
        info_layout.addWidget(self.status_label)
        toolbar.addWidget(info_widget)

    # ------------------------------------------------------------------ #
    # Thân chính: panel trái (Options/Line State/Count) + phải (Receive/Send)
    # ------------------------------------------------------------------ #
    def _build_body(self): # Phương thức tạm thời, chưa dùng splitter
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        main_layout.addWidget(self._build_left_panel(), 0)
        main_layout.addWidget(self._build_right_panel(), 1)

    def _build_body_StubFunc(self): # Phương thức xây dựng thân chính với splitter
        splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(splitter)

        left_panel = self._build_left_panel()
        right_panel = self._build_right_panel()

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)

        # Panel trái co giãn theo nội dung (không bị kéo giãn khi resize cửa sổ),
        # panel phải chiếm phần còn lại. Người dùng vẫn có thể kéo thanh chia để
        # tự chỉnh độ rộng panel trái khi chạy app.
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([190, 710])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

    # ---- Panel trái ---- #
    def _build_left_panel(self):
        panel = QWidget()
        panel.setFixedWidth(250) # Fix cứng độ rộng panel trái, chưa dùng splitter
        # Đặt chiều rộng tối thiểu và chính sách kích thước để panel trái co giãn hợp lý khi dùng splitter
        # panel.setMinimumWidth(170)
        # panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        # end thiết lập kích thước panel trái khi dùng splitter
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)

        # Options
        opt_box = QGroupBox("Options")
        opt_layout = QGridLayout(opt_box)

        self.cmb_port = QComboBox()
        self.cmb_baud = QComboBox()
        self.cmb_baud.addItems(
            ["1200", "2400", "4800", "9600", "19200", "38400",
             "57600", "115200", "230400", "460800", "921600"]
        )
        self.cmb_baud.setCurrentText("115200")

        self.cmb_databits = QComboBox()
        self.cmb_databits.addItems(["5", "6", "7", "8"])
        self.cmb_databits.setCurrentText("8")

        self.cmb_flow = QComboBox()
        self.cmb_flow.addItems(["None", "RTS/CTS", "XON/XOFF"])

        self.cmb_stopbits = QComboBox()
        self.cmb_stopbits.addItems(["1", "1.5", "2"])

        self.cmb_parity = QComboBox()
        self.cmb_parity.addItems(["None", "Odd", "Even", "Mark", "Space"])

        rows = [
            ("SerialName", self.cmb_port),
            ("BaudRate", self.cmb_baud),
            ("DataBits", self.cmb_databits),
            ("FlowControl", self.cmb_flow),
            ("StopBits", self.cmb_stopbits),
            ("Parity", self.cmb_parity),
        ]
        for i, (label, widget) in enumerate(rows):
            opt_layout.addWidget(QLabel(label), i, 0)
            opt_layout.addWidget(widget, i, 1)

        layout.addWidget(opt_box)

        # Line State
        ls_box = QGroupBox("Line State")
        ls_layout = QGridLayout(ls_box)
        self.chk_rts = QCheckBox("RTS")
        self.chk_dtr = QCheckBox("DTR")
        self.chk_break = QCheckBox("BREAK")
        self.chk_cts = QCheckBox("CTS")
        self.chk_dsr = QCheckBox("DSR")
        self.chk_dcd = QCheckBox("DCD")
        self.chk_ri = QCheckBox("RI")
        for chk in (self.chk_cts, self.chk_dsr, self.chk_dcd, self.chk_ri):
            chk.setEnabled(False)  # các tín hiệu input chỉ hiển thị, không cho tick
        self.chk_rts.toggled.connect(self._apply_line_state)
        self.chk_dtr.toggled.connect(self._apply_line_state)
        self.chk_break.toggled.connect(self._apply_line_state)

        ls_layout.addWidget(self.chk_rts, 0, 0)
        ls_layout.addWidget(self.chk_dtr, 0, 1)
        ls_layout.addWidget(self.chk_break, 0, 2)
        ls_layout.addWidget(self.chk_cts, 1, 0)
        ls_layout.addWidget(self.chk_dsr, 1, 1)
        ls_layout.addWidget(self.chk_dcd, 1, 2)
        ls_layout.addWidget(self.chk_ri, 2, 0)
        layout.addWidget(ls_box)

        # Count
        count_box = QGroupBox("Count")
        count_layout = QGridLayout(count_box)
        self.lbl_send_count = QLabel("0")
        self.lbl_recv_count = QLabel("0")
        self.lbl_send_speed = QLabel("0.0B/S")
        self.lbl_recv_speed = QLabel("0.0B/S")
        count_layout.addWidget(QLabel("Send Count"), 0, 0)
        count_layout.addWidget(self.lbl_send_count, 0, 1)
        count_layout.addWidget(QLabel("Receive Count"), 1, 0)
        count_layout.addWidget(self.lbl_recv_count, 1, 1)
        count_layout.addWidget(QLabel("Send Speed"), 2, 0)
        count_layout.addWidget(self.lbl_send_speed, 2, 1)
        count_layout.addWidget(QLabel("Receive Speed"), 3, 0)
        count_layout.addWidget(self.lbl_recv_speed, 3, 1)
        layout.addWidget(count_box)

        btn_clear_count = QPushButton("Clear Count")
        btn_clear_count.clicked.connect(self._clear_count)
        layout.addWidget(btn_clear_count)

        layout.addStretch()
        return panel

    # ---- Panel phải ---- #
    def _build_right_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)

        # --- Receive Section ---
        recv_box = QGroupBox("Receive Section (Byte < 128KB)")
        recv_layout = QVBoxLayout(recv_box)

        recv_opts = QHBoxLayout()
        self.chk_autoscroll = QCheckBox("AutoScroll")
        self.chk_autoscroll.setChecked(True)
        self.chk_addtime = QCheckBox("AddTime")
        self.chk_hidetext = QCheckBox("HideText")
        self.chk_hexshow = QCheckBox("HexShow")
        recv_opts.addStretch()
        recv_opts.addWidget(self.chk_autoscroll)
        recv_opts.addWidget(self.chk_addtime)
        recv_opts.addWidget(self.chk_hidetext)
        recv_opts.addWidget(self.chk_hexshow)
        recv_layout.addLayout(recv_opts)

        self.txt_receive = QTextEdit()
        self.txt_receive.setReadOnly(True)
        self.txt_receive.setFont(QFont("Consolas", 10))
        recv_layout.addWidget(self.txt_receive)

        save_row = QHBoxLayout()
        self.chk_savefile = QCheckBox("Save File")
        self.lbl_savefile = QLabel("Unselected File")
        btn_choose_save = QPushButton("...")
        btn_choose_save.setMaximumWidth(30)
        btn_choose_save.clicked.connect(self._choose_save_file)
        btn_clear_recv = QPushButton("Clear")
        btn_clear_recv.clicked.connect(self.txt_receive.clear)
        save_row.addWidget(self.chk_savefile)
        save_row.addWidget(self.lbl_savefile, 1)
        save_row.addWidget(btn_choose_save)
        save_row.addStretch()
        save_row.addWidget(btn_clear_recv)
        recv_layout.addLayout(save_row)

        compare_row = QHBoxLayout()
        self.chk_comparefile = QCheckBox("Compare File")
        self.lbl_comparefile = QLabel("Unselected File")
        self.chk_errpause = QCheckBox("Error Pause")
        self.chk_errpause.setEnabled(False)
        compare_row.addWidget(self.chk_comparefile)
        compare_row.addWidget(self.lbl_comparefile, 1)
        compare_row.addStretch()
        compare_row.addWidget(self.chk_errpause)
        recv_layout.addLayout(compare_row)

        tail_row = QHBoxLayout()
        self.chk_addtail_recv = QCheckBox("AddTail")
        self.cmb_tail_recv = QComboBox()
        self.cmb_tail_recv.addItems(["CR (\\r)", "LF (\\n)", "CRLF (\\r\\n)", "None"])
        tail_row.addStretch()
        tail_row.addWidget(self.chk_addtail_recv)
        tail_row.addWidget(self.cmb_tail_recv)
        recv_layout.addLayout(tail_row)

        layout.addWidget(recv_box, 2)

        # --- Send Section ---
        send_box = QGroupBox("Send Section (Byte < 64KB)")
        send_layout = QVBoxLayout(send_box)

        send_opts = QHBoxLayout()
        self.chk_hexsend = QCheckBox("HexSend")
        send_opts.addStretch()
        send_opts.addWidget(self.chk_hexsend)
        send_layout.addLayout(send_opts)

        self.txt_send = QTextEdit()
        self.txt_send.setFont(QFont("Consolas", 10))
        self.txt_send.setMaximumHeight(100)
        send_layout.addWidget(self.txt_send)

        regular_row = QHBoxLayout()
        self.chk_regular = QCheckBox("Regularly Send")
        self.chk_regular.toggled.connect(self._toggle_regular_send)
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(10, 600000)
        self.spin_interval.setValue(1000)
        self.spin_interval.setSuffix(" ms")
        btn_send = QPushButton("Send")
        btn_send.clicked.connect(self._on_send_clicked)
        btn_clear_send = QPushButton("Clear")
        btn_clear_send.clicked.connect(self.txt_send.clear)
        regular_row.addWidget(self.chk_regular)
        regular_row.addWidget(self.spin_interval)
        regular_row.addStretch()
        regular_row.addWidget(btn_send)
        regular_row.addWidget(btn_clear_send)
        send_layout.addLayout(regular_row)

        file_row = QHBoxLayout()
        btn_datafile = QPushButton("Data File")
        btn_datafile.clicked.connect(self._choose_data_file)
        self.lbl_datafile = QLabel("Unselected file")
        self.chk_fileloop = QCheckBox("File Loop")
        self.spin_loop = QSpinBox()
        self.spin_loop.setRange(0, 999999)
        file_row.addWidget(btn_datafile)
        file_row.addWidget(self.lbl_datafile, 1)
        file_row.addWidget(self.chk_fileloop)
        file_row.addWidget(self.spin_loop)
        send_layout.addLayout(file_row)

        file_row2 = QHBoxLayout()
        btn_sendfile = QPushButton("Send File")
        btn_sendfile.clicked.connect(self._send_data_file)
        self.chk_addtail_send = QCheckBox("AddTail")
        self.cmb_tail_send = QComboBox()
        self.cmb_tail_send.addItems(["CR (\\r)", "LF (\\n)", "CRLF (\\r\\n)", "None"])
        file_row2.addWidget(btn_sendfile)
        file_row2.addStretch()
        file_row2.addWidget(self.chk_addtail_send)
        file_row2.addWidget(self.cmb_tail_send)
        send_layout.addLayout(file_row2)

        layout.addWidget(send_box, 1)

        self._save_file_path = None
        self._data_file_path = None
        return panel

    # ------------------------------------------------------------------ #
    # Logic: cổng COM
    # ------------------------------------------------------------------ #
    def _refresh_ports(self):
        self.cmb_port.clear()
        if list_ports is None:
            self.cmb_port.addItem("pyserial not yet installed")
            return
        ports = list_ports.comports()
        if not ports:
            self.cmb_port.addItem("No ports available")
        for p in ports:
            self.cmb_port.addItem(p.device)

    def _on_new(self):
        self.txt_receive.clear()
        self.txt_send.clear()
        self._refresh_ports()

    def _on_connect(self):
        if serial is None:
            QMessageBox.warning(self, "Missing library", "Please install pyserial: pip install pyserial")
            return

        port = self.cmb_port.currentText()
        if not port or "No ports available" in port or "pyserial not yet installed" in port:
            QMessageBox.warning(self, "Error", "No valid COM port.")
            return

        baud = int(self.cmb_baud.currentText())
        databits_map = {"5": serial.FIVEBITS, "6": serial.SIXBITS,
                         "7": serial.SEVENBITS, "8": serial.EIGHTBITS}
        stopbits_map = {"1": serial.STOPBITS_ONE, "1.5": serial.STOPBITS_ONE_POINT_FIVE,
                         "2": serial.STOPBITS_TWO}
        parity_map = {"None": serial.PARITY_NONE, "Odd": serial.PARITY_ODD,
                      "Even": serial.PARITY_EVEN, "Mark": serial.PARITY_MARK,
                      "Space": serial.PARITY_SPACE}

        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=baud,
                bytesize=databits_map[self.cmb_databits.currentText()],
                parity=parity_map[self.cmb_parity.currentText()],
                stopbits=stopbits_map[self.cmb_stopbits.currentText()],
                rtscts=(self.cmb_flow.currentText() == "RTS/CTS"),
                xonxoff=(self.cmb_flow.currentText() == "XON/XOFF"),
                timeout=0,
            )
        except Exception as e:
            QMessageBox.critical(self, "Cannot open port", str(e))
            return

        self.reader_thread = SerialReaderThread(self.ser)
        self.reader_thread.data_received.connect(self._on_data_received)
        self.reader_thread.error_occurred.connect(self._on_serial_error)
        self.reader_thread.start()

        self.port_desc_label.setText(f"Port Desc: {port} @ {baud}")
        self.status_label.setText("Serial open")
        self.status_label.setStyleSheet("color: green; font-weight: bold;")
        self.act_connect.setEnabled(False)
        self.act_disconnect.setEnabled(True)

    def _on_disconnect(self):
        if self.reader_thread:
            self.reader_thread.stop()
            self.reader_thread = None
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.ser = None

        self.status_label.setText("Serial not open")
        self.status_label.setStyleSheet("color: red; font-weight: bold;")
        self.act_connect.setEnabled(True)
        self.act_disconnect.setEnabled(False)

    def _on_serial_error(self, msg):
        QMessageBox.critical(self, "Serial Error", msg)
        self._on_disconnect()

    def _apply_line_state(self):
        if self.ser and self.ser.is_open:
            try:
                self.ser.rts = self.chk_rts.isChecked()
                self.ser.dtr = self.chk_dtr.isChecked()
                self.ser.break_condition = self.chk_break.isChecked()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Nhận dữ liệu
    # ------------------------------------------------------------------ #
    def _on_data_received(self, data: bytes):
        self.recv_count += len(data)
        self.lbl_recv_count.setText(str(self.recv_count))

        if self.chk_hexshow.isChecked():
            text = binascii.hexlify(data, " ").decode().upper() + " "
        else:
            text = data.decode(errors="replace")

        if self.chk_addtime.isChecked():
            text = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] " + text

        if self.chk_hidetext.isChecked():
            self.txt_receive.append("*" * max(len(text), 1))
        else:
            cursor = self.txt_receive.textCursor()
            cursor.movePosition(cursor.End)
            self.txt_receive.setTextCursor(cursor)
            self.txt_receive.insertPlainText(text)

        if self.chk_autoscroll.isChecked():
            sb = self.txt_receive.verticalScrollBar()
            sb.setValue(sb.maximum())

        if self.chk_savefile.isChecked() and self._save_file_path:
            try:
                with open(self._save_file_path, "ab") as f:
                    f.write(data)
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Gửi dữ liệu
    # ------------------------------------------------------------------ #
    def _build_tail_bytes(self, combo: QComboBox):
        choice = combo.currentText()
        return {
            "CR (\\r)": b"\r",
            "LF (\\n)": b"\n",
            "CRLF (\\r\\n)": b"\r\n",
            "None": b"",
        }.get(choice, b"")

    def _on_send_clicked(self):
        text = self.txt_send.toPlainText()
        if not text:
            return
        self._send_text(text)

    def _send_text(self, text):
        if not (self.ser and self.ser.is_open):
            QMessageBox.warning(self, "Not connected", "Please connect the serial port first.")
            return
        try:
            if self.chk_hexsend.isChecked():
                hex_str = "".join(text.split())
                payload = binascii.unhexlify(hex_str)
            else:
                payload = text.encode()

            if self.chk_addtail_send.isChecked():
                payload += self._build_tail_bytes(self.cmb_tail_send)

            self.ser.write(payload)
            self.send_count += len(payload)
            self.lbl_send_count.setText(str(self.send_count))
        except Exception as e:
            QMessageBox.critical(self, "Send Error", str(e))

    def _toggle_regular_send(self, checked):
        if checked:
            self.regular_timer.start(self.spin_interval.value())
        else:
            self.regular_timer.stop()

    def _regular_send(self):
        self._on_send_clicked()

    # ------------------------------------------------------------------ #
    # File: Save / Compare / Data file
    # ------------------------------------------------------------------ #
    def _choose_save_file(self):
        path, _ = QFileDialog.getSaveFileName(self, "Chọn file lưu dữ liệu nhận")
        if path:
            self._save_file_path = path
            self.lbl_savefile.setText(path)

    def _choose_data_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Chọn file dữ liệu để gửi")
        if path:
            self._data_file_path = path
            self.lbl_datafile.setText(path)

    def _send_data_file(self):
        if not self._data_file_path:
            QMessageBox.warning(self, "No file selected", "Please choose a Data File first.")
            return
        if not (self.ser and self.ser.is_open):
            QMessageBox.warning(self, "Not connected", "Please connect the serial port first.")
            return
        try:
            with open(self._data_file_path, "rb") as f:
                payload = f.read()
            if self.chk_addtail_send.isChecked():
                payload += self._build_tail_bytes(self.cmb_tail_send)
            self.ser.write(payload)
            self.send_count += len(payload)
            self.lbl_send_count.setText(str(self.send_count))
        except Exception as e:
            QMessageBox.critical(self, "Send File Error", str(e))

    # ------------------------------------------------------------------ #
    # Count / tốc độ
    # ------------------------------------------------------------------ #
    def _clear_count(self):
        self.send_count = 0
        self.recv_count = 0
        self.last_send_count = 0
        self.last_recv_count = 0
        self.lbl_send_count.setText("0")
        self.lbl_recv_count.setText("0")
        self.lbl_send_speed.setText("0.0B/S")
        self.lbl_recv_speed.setText("0.0B/S")

    def _update_speed(self):
        send_speed = self.send_count - self.last_send_count
        recv_speed = self.recv_count - self.last_recv_count
        self.lbl_send_speed.setText(f"{send_speed:.1f}B/S")
        self.lbl_recv_speed.setText(f"{recv_speed:.1f}B/S")
        self.last_send_count = self.send_count
        self.last_recv_count = self.recv_count

    def closeEvent(self, event):
        self._on_disconnect()
        event.accept()


def main():
    app = QApplication(sys.argv)
    win = SerialTerminal()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
