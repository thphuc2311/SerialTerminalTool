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
from PyQt5.QtGui import QFont, QIcon, QTextCursor
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QComboBox, QPushButton,
    QCheckBox, QTextEdit, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QFileDialog, QAction, QActionGroup, QToolBar, QLineEdit, QSpinBox, QFrame,
    QSizePolicy, QMessageBox, QSplitter, QStackedWidget
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
    # Hằng số khung (frame) protocol USB
    USB_FRAME_LEN = 12
    USB_SOF_DATA = 0xA5    # SOF của frame dữ liệu chuẩn
    USB_SOF_ERROR = 0xEE   # SOF của frame báo lỗi

    # Giới hạn hiển thị để tránh văn bản phình to vô hạn gây lag khi chạy lâu
    MAX_RECV_CHARS = 128 * 1024   # khớp với nhãn "Byte<128KB" trên giao diện
    MAX_USB_LOG_BLOCKS = 1000     # số dòng tối đa giữ lại trong Frame Log

    def __init__(self):
        super().__init__()
        self.ser = None
        self.reader_thread = None
        self.send_count = 0
        self.recv_count = 0
        self.last_speed_time = time.time()
        self.last_send_count = 0
        self.last_recv_count = 0
        self._usb_buffer = bytearray()  # buffer gom byte để tách frame USB

        # Hàng chờ phục vụ batching: dữ liệu được xếp vào đây ngay khi nhận,
        # còn việc vẽ lên GUI do timer _flush_receive_display() đảm nhiệm
        # theo chu kỳ cố định, thay vì vẽ ngay mỗi lần nhận (gây lag khi dồn dập).
        self._pending_raw_text = []
        self._pending_usb_log_lines = []
        self._pending_autoscroll = False
        self._recv_count_dirty = False
        self._recv_char_count = 0  # theo dõi độ dài hiện tại của txt_receive

        self.setWindowTitle("Serial Terminal")
        self.resize(900, 620)

        self._build_toolbar()
        self._build_body()
        self._refresh_ports()

        # Timer gom (batch) nhiều lần nhận dữ liệu lại, chỉ vẽ GUI 1 lần mỗi
        # chu kỳ 50ms -> giảm hẳn số lần redraw khi dữ liệu đến dồn dập.
        self.ui_flush_timer = QTimer(self)
        self.ui_flush_timer.timeout.connect(self._flush_receive_display)
        self.ui_flush_timer.start(50)

        # Đồng bộ view mặc định = Serial, sau khi stack widget đã được tạo
        self.act_view_serial.toggled.connect(
            lambda checked: checked and self.stack_recv.setCurrentIndex(0))
        self.act_view_usb.toggled.connect(
            lambda checked: checked and self.stack_recv.setCurrentIndex(1))
        self.stack_recv.setCurrentIndex(0)

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

        # Nút chuyển đổi View Serial / View USB (loại trừ lẫn nhau, kiểu radio)
        self.act_view_serial = QAction("View Serial", self)
        self.act_view_serial.setCheckable(True)
        self.act_view_usb = QAction("View USB", self)
        self.act_view_usb.setCheckable(True)

        view_group = QActionGroup(self)
        view_group.setExclusive(True)
        view_group.addAction(self.act_view_serial)
        view_group.addAction(self.act_view_usb)
        self.act_view_serial.setChecked(True)  # mặc định khởi động: Serial view

        toolbar.addAction(self.act_view_serial)
        toolbar.addAction(self.act_view_usb)

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
        btn_clear_recv.clicked.connect(self._clear_all_logs)
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

        # --- USB View (hiển thị frame đã giải mã) ---
        usb_page = self._build_usb_view()

        # Stack để chuyển đổi Receive Section (Serial view) <-> USB View,
        # dữ liệu cả 2 bên vẫn luôn được cập nhật ngầm nên chuyển qua lại
        # không mất dữ liệu đã nhận trước đó.
        self.stack_recv = QStackedWidget()
        self.stack_recv.addWidget(recv_box)   # index 0: Serial view
        self.stack_recv.addWidget(usb_page)   # index 1: USB view
        layout.addWidget(self.stack_recv, 2)

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

    # ---- USB View: panel hiển thị frame đã giải mã (dạng card) ---- #
    def _make_field_card(self, title: str, span_full: bool = False):
        """Tạo 1 ô 'card' bo góc gồm nhãn nhỏ phía trên + giá trị lớn phía dưới.
        Trả về (frame_widget, value_label) để nơi gọi giữ tham chiếu value_label."""
        card = QFrame()
        card.setObjectName("fieldCard")
        card.setStyleSheet("""
            QFrame#fieldCard {
                background-color: #F5F7F8;
                border: 1px solid #DDE3E6;
                border-radius: 10px;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 10)
        card_layout.setSpacing(2)

        lbl_title = QLabel(title.upper())
        lbl_title.setStyleSheet("""
            color: #8A97A0;
            font-size: 11px;
            font-weight: 600;
            letter-spacing: 1px;
            border: none;
        """)
        card_layout.addWidget(lbl_title)

        lbl_value = QLabel("--")
        lbl_value.setStyleSheet("""
            color: #2C3E50;
            font-size: 17px;
            font-weight: 700;
            border: none;
        """)
        lbl_value.setFont(QFont("Consolas", 12, QFont.Bold))
        card_layout.addWidget(lbl_value)

        return card, lbl_value

    def _build_usb_view(self):
        box = QGroupBox("USB View — Decoded Frame")
        outer_layout = QVBoxLayout(box)
        outer_layout.setSpacing(10)

        # --- Banner trạng thái (đổi màu theo kết quả) ---
        self.frame_usb_status = QFrame()
        self.frame_usb_status.setObjectName("statusBanner")
        status_layout = QHBoxLayout(self.frame_usb_status)
        status_layout.setContentsMargins(14, 10, 14, 10)
        self.lbl_usb_status = QLabel("Chưa nhận frame nào")
        self.lbl_usb_status.setStyleSheet("font-size: 14px; font-weight: 700; border: none;")
        self.lbl_usb_timestamp = QLabel("")
        self.lbl_usb_timestamp.setStyleSheet("font-size: 11px; color: #5D6D7E; border: none;")
        status_layout.addWidget(self.lbl_usb_status)
        status_layout.addStretch()
        status_layout.addWidget(self.lbl_usb_timestamp)
        self._set_usb_banner("neutral", "Chưa nhận frame nào", "")
        outer_layout.addWidget(self.frame_usb_status)

        # --- Lưới card hiển thị các field ---
        grid = QGridLayout()
        grid.setSpacing(10)

        card_node, self.lbl_val_nodeid = self._make_field_card("NodeID")
        card_cmd, self.lbl_val_cmd = self._make_field_card("CMD")
        card_high, self.lbl_val_high = self._make_field_card("4 Byte High")
        card_low, self.lbl_val_low = self._make_field_card("4 Byte Low")
        card_cs, self.lbl_val_checksum = self._make_field_card("Checksum")

        grid.addWidget(card_node, 0, 0)
        grid.addWidget(card_cmd, 0, 1)
        grid.addWidget(card_high, 1, 0, 1, 2)   # chiếm cả 2 cột
        grid.addWidget(card_low, 2, 0, 1, 2)
        grid.addWidget(card_cs, 3, 0, 1, 2)
        outer_layout.addLayout(grid)

        # --- Log lịch sử (chữ màu theo trạng thái, nền tối dễ đọc hex) ---
        lbl_log_title = QLabel("Frame Log")
        lbl_log_title.setStyleSheet("color: #5D6D7E; font-size: 11px; font-weight: 600; letter-spacing: 1px;")
        outer_layout.addWidget(lbl_log_title)

        self.txt_usb_log = QTextEdit()
        self.txt_usb_log.setReadOnly(True)
        self.txt_usb_log.setFont(QFont("Consolas", 9))
        self.txt_usb_log.setStyleSheet("""
            QTextEdit {
                background-color: #1E2227;
                color: #D5DBDD;
                border-radius: 8px;
                padding: 6px;
                border: none;
            }
        """)
        outer_layout.addWidget(self.txt_usb_log, 1)

        btn_clear_usb = QPushButton("Clear Log")
        btn_clear_usb.clicked.connect(self._clear_all_logs)
        outer_layout.addWidget(btn_clear_usb)

        return box

    def _set_usb_banner(self, state: str, text: str, timestamp: str):
        """state: 'neutral' | 'ok' | 'error' — đổi màu nền/chữ banner trạng thái."""
        palette = {
            "neutral": ("#EEF1F2", "#5D6D7E"),
            "ok":      ("#E9F7EF", "#1E8449"),
            "error":   ("#FDEDEC", "#C0392B"),
        }
        bg, fg = palette.get(state, palette["neutral"])
        self.frame_usb_status.setStyleSheet(f"""
            QFrame#statusBanner {{
                background-color: {bg};
                border-radius: 10px;
            }}
        """)
        self.lbl_usb_status.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {fg}; border: none;")
        self.lbl_usb_status.setText(text)
        self.lbl_usb_timestamp.setText(timestamp)

    def _clear_all_logs(self):
        """Xoá log ở cả 2 view: text nhận của Serial view và frame log của USB view."""
        self._pending_raw_text.clear()
        self._pending_autoscroll = False
        self._recv_char_count = 0
        self.txt_receive.clear()
        self._clear_usb_view()

    def _clear_usb_view(self):
        self._pending_usb_log_lines.clear()
        self.txt_usb_log.clear()
        self._usb_buffer = bytearray()
        for lbl in (self.lbl_val_nodeid, self.lbl_val_cmd, self.lbl_val_high,
                    self.lbl_val_low, self.lbl_val_checksum):
            lbl.setText("--")
            lbl.setStyleSheet("color: #2C3E50; font-size: 17px; font-weight: 700; border: none;")
        self._set_usb_banner("neutral", "Chưa nhận frame nào", "")

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
        self._clear_all_logs()
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
        """Chạy mỗi khi có dữ liệu mới. CHỈ xử lý dữ liệu (rẻ) và xếp vào hàng
        chờ hiển thị — KHÔNG đụng vào GUI trực tiếp ở đây, để tránh việc vẽ lại
        QTextEdit hàng trăm/ngàn lần mỗi giây khi dữ liệu đến dồn dập (nguyên
        nhân chính gây lag). Việc vẽ GUI thật sự do self._flush_receive_display()
        đảm nhiệm, chạy theo timer (gom nhiều lần nhận lại, vẽ 1 lần)."""
        self.recv_count += len(data)
        self._recv_count_dirty = True

        if self.chk_hexshow.isChecked():
            text = binascii.hexlify(data, " ").decode().upper() + " "
        else:
            text = data.decode(errors="replace")

        if self.chk_addtime.isChecked():
            text = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] " + text

        if self.chk_hidetext.isChecked():
            text = "*" * max(len(text), 1)

        self._pending_raw_text.append(text)
        if self.chk_autoscroll.isChecked():
            self._pending_autoscroll = True

        if self.chk_savefile.isChecked() and self._save_file_path:
            try:
                with open(self._save_file_path, "ab") as f:
                    f.write(data)
            except Exception:
                pass

        # Tách frame USB vẫn chạy ngay (xử lý thuần Python, không đụng GUI nên
        # rẻ), không phụ thuộc đang ở view nào -> chuyển view không mất dữ liệu.
        self._process_usb_frames(data)

    def _flush_receive_display(self):
        """Được gọi định kỳ bởi self.ui_flush_timer (mặc định mỗi 50ms).
        Gom tất cả phần chờ (raw text + usb log) lại và vẽ GUI đúng 1 lần,
        thay vì vẽ lại mỗi khi có 1 gói dữ liệu nhỏ đến -> giảm lag rõ rệt
        khi luồng dữ liệu đến dồn dập (baud cao, đọc từng mẩu nhỏ)."""
        if self._pending_raw_text:
            combined = "".join(self._pending_raw_text)
            self._pending_raw_text.clear()

            self.txt_receive.setUpdatesEnabled(False)
            cursor = self.txt_receive.textCursor()
            cursor.movePosition(cursor.End)
            self.txt_receive.setTextCursor(cursor)
            self.txt_receive.insertPlainText(combined)
            self._recv_char_count += len(combined)
            self._trim_text_edit_if_needed(self.txt_receive)
            self.txt_receive.setUpdatesEnabled(True)

            if self._pending_autoscroll:
                sb = self.txt_receive.verticalScrollBar()
                sb.setValue(sb.maximum())
                self._pending_autoscroll = False

        if self._pending_usb_log_lines:
            combined_html = "<br>".join(self._pending_usb_log_lines)
            self._pending_usb_log_lines.clear()

            self.txt_usb_log.setUpdatesEnabled(False)
            self.txt_usb_log.append(combined_html)
            self._trim_usb_log_if_needed()
            self.txt_usb_log.setUpdatesEnabled(True)
            sb2 = self.txt_usb_log.verticalScrollBar()
            sb2.setValue(sb2.maximum())

        if self._recv_count_dirty:
            self.lbl_recv_count.setText(str(self.recv_count))
            self._recv_count_dirty = False

    def _trim_text_edit_if_needed(self, text_edit):
        """Giữ nội dung ô text không vượt quá MAX_RECV_CHARS (giống đúng cái
        nhãn 'Byte<128KB' đã ghi sẵn trên giao diện) — xóa bớt phần cũ nhất
        khi vượt, để văn bản không phình to vô hạn làm app càng chạy càng ì."""
        overflow = self._recv_char_count - self.MAX_RECV_CHARS
        if overflow > 0:
            cursor = text_edit.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.movePosition(cursor.Right, cursor.KeepAnchor, overflow)
            cursor.removeSelectedText()
            self._recv_char_count -= overflow

    def _trim_usb_log_if_needed(self):
        """Giới hạn số dòng trong Frame Log, xóa dòng cũ nhất khi vượt quá."""
        doc = self.txt_usb_log.document()
        while doc.blockCount() > self.MAX_USB_LOG_BLOCKS:
            cursor = QTextCursor(doc.findBlockByNumber(0))
            cursor.select(QTextCursor.BlockUnderCursor)
            cursor.removeSelectedText()
            cursor.deleteChar()  # xóa luôn ký tự xuống dòng còn sót lại

    # ------------------------------------------------------------------ #
    # Giải mã frame USB: [SOF][NodeId][CMD][8 Bytes Data][Checksum] = 12 byte
    # SOF = 0xA5 -> frame dữ liệu chuẩn | SOF = 0xEE -> frame báo lỗi
    # ------------------------------------------------------------------ #
    def _process_usb_frames(self, data: bytes):
        self._usb_buffer.extend(data)

        while True:
            # Tìm vị trí byte SOF hợp lệ gần nhất (0xA5 hoặc 0xEE) trong buffer
            idx = None
            for i, b in enumerate(self._usb_buffer):
                if b in (self.USB_SOF_DATA, self.USB_SOF_ERROR):
                    idx = i
                    break

            if idx is None:
                # Không có SOF nào trong buffer -> toàn rác, xóa để buffer không phình to
                self._usb_buffer.clear()
                return

            if idx > 0:
                # Bỏ các byte rác đứng trước SOF
                del self._usb_buffer[:idx]

            if len(self._usb_buffer) < self.USB_FRAME_LEN:
                return  # Chưa đủ 12 byte, chờ thêm dữ liệu ở lần nhận sau

            frame = bytes(self._usb_buffer[:self.USB_FRAME_LEN])
            del self._usb_buffer[:self.USB_FRAME_LEN]
            self._decode_usb_frame(frame)
            # Lặp lại: có thể còn frame khác đã nằm sẵn trong buffer

    def _decode_usb_frame(self, frame: bytes):
        sof = frame[0]
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        hex_str = frame.hex(" ").upper()

        if sof == self.USB_SOF_ERROR:
            for lbl in (self.lbl_val_nodeid, self.lbl_val_cmd, self.lbl_val_high,
                        self.lbl_val_low, self.lbl_val_checksum):
                lbl.setText("--")
            self._set_usb_banner("error", "⚠ Error Frame nhận được", timestamp)
            self._pending_usb_log_lines.append(
                f'<span style="color:#E74C3C;">[{timestamp}] LỖI</span> '
                f'<span style="color:#D5DBDD;">| {hex_str}</span>'
            )
            return

        # sof == USB_SOF_DATA (0xA5): frame dữ liệu chuẩn
        node_id = frame[1]
        cmd = frame[2]
        data8 = frame[3:11]
        high4 = data8[0:4]
        low4 = data8[4:8]
        checksum_recv = frame[11]
        # Checksum = tổng cộng dồn (sum) 11 byte đầu (SOF..byte data cuối), lấy 8 bit thấp.
        # (Trước đây dùng CRC8, đã comment lại ở hàm _crc8() bên dưới để dành dùng sau nếu cần.)
        checksum_calc = self._checksum_sum8(frame[0:11])
        is_ok = (checksum_calc == checksum_recv)

        self.lbl_val_nodeid.setText(f"0x{node_id:02X}")
        self.lbl_val_cmd.setText(f"0x{cmd:02X}")
        self.lbl_val_high.setText(high4.hex(" ").upper())
        self.lbl_val_low.setText(low4.hex(" ").upper())

        if is_ok:
            self.lbl_val_checksum.setText(f"0x{checksum_recv:02X}  ✓ OK")
            self.lbl_val_checksum.setStyleSheet(
                "color: #1E8449; font-size: 17px; font-weight: 700; border: none;")
            self._set_usb_banner("ok", "✓ Frame hợp lệ", timestamp)
            status_color = "#27AE60"
            status_str = "OK"
        else:
            self.lbl_val_checksum.setText(
                f"0x{checksum_recv:02X}  ✗ (tính được 0x{checksum_calc:02X})")
            self.lbl_val_checksum.setStyleSheet(
                "color: #C0392B; font-size: 17px; font-weight: 700; border: none;")
            self._set_usb_banner("error", "✗ Frame SAI checksum", timestamp)
            status_color = "#E74C3C"
            status_str = "SAI CHECKSUM"

        self._pending_usb_log_lines.append(
            f'<span style="color:{status_color}; font-weight:bold;">[{timestamp}] {status_str}</span> '
            f'<span style="color:#D5DBDD;">| {hex_str} | NodeID=0x{node_id:02X} '
            f'CMD=0x{cmd:02X} High={high4.hex(" ").upper()} Low={low4.hex(" ").upper()}</span>'
        )

    @staticmethod
    def _checksum_sum8(data: bytes) -> int:
        """Checksum đơn giản: cộng dồn các byte lại rồi lấy 8 bit thấp (sum & 0xFF)."""
        return sum(data) & 0xFF

    # --- CRC8 (đã tắt, không dùng nữa vì protocol thực tế dùng checksum cộng dồn) ---
    # @staticmethod
    # def _crc8(data: bytes, poly=0x07, init=0x00) -> int:
    #     crc = init
    #     for b in data:
    #         crc ^= b
    #         for _ in range(8):
    #             if crc & 0x80:
    #                 crc = ((crc << 1) ^ poly) & 0xFF
    #             else:
    #                 crc = (crc << 1) & 0xFF
    #     return crc

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

# standard data frame
# [SOF]     [NodeId]    [CMD]   [TypeCmd]   [8 Bytes Data]  [Checksum]
# [0xA5]    [0x01]      [0x23]  [0x01]      [8 Bytes Data]  [Checksum]
# [1Byte]   [1Byte]     [1Byte]             [8Bytes]        [2Byte]     = 13 bytes total

# dynamic data frame
# [SOF]     [NodeId_1]  [CMD]   [8 Bytes Data]  +    [NodeId_2]  [CMD]   [8 Bytes Data]  [Checksum]
# [0xA5]    [0x01]      [0x23]  [8 Bytes Data]  +    [0x02]      [0x23]  [8 Bytes Data]  [Checksum]
# [1Byte]   [1Byte]     [1Byte] [8Bytes]        +    [1Byte]     [1Byte] [8Bytes]        [2Byte]
# 22 bytes total = 1 byte SOF + 2*(1+1+1+8) + 2 byte checksum
# number CAN frames in dynamic data frame = (total bytes - 1 byte SOF - 2 byte checksum) / 13 bytes