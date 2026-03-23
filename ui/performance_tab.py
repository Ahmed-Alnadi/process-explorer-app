import os
import platform
import shutil
import subprocess
import time
from collections import deque

import psutil
from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QProgressBar,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)


class HistoryGraph(QWidget):
    def __init__(self, line_color):
        super().__init__()
        self.setMinimumHeight(92)
        self._line_color = QColor(line_color)
        self._fill_start = QColor(line_color)
        self._fill_start.setAlpha(120)
        self._fill_end = QColor(line_color)
        self._fill_end.setAlpha(18)
        self._values = deque([0.0] * 50, maxlen=50)

    def push(self, value):
        value = max(0.0, min(float(value), 100.0))
        self._values.append(value)
        if self.isVisible():
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(8, 8, -8, -8)
        painter.fillRect(rect, QColor(8, 18, 29, 85))

        grid_pen = QPen(QColor(160, 210, 240, 24))
        grid_pen.setWidth(1)
        painter.setPen(grid_pen)
        for step in range(1, 4):
            y = rect.top() + (rect.height() * step / 4.0)
            painter.drawLine(rect.left(), int(y), rect.right(), int(y))

        if len(self._values) < 2:
            return

        points = []
        width = max(rect.width(), 1)
        height = max(rect.height(), 1)
        for index, value in enumerate(self._values):
            x = rect.left() + (width * index / (len(self._values) - 1))
            y = rect.bottom() - (value / 100.0) * height
            points.append(QPointF(x, y))

        line_path = QPainterPath(points[0])
        for point in points[1:]:
            line_path.lineTo(point)

        fill_path = QPainterPath(line_path)
        fill_path.lineTo(rect.right(), rect.bottom())
        fill_path.lineTo(rect.left(), rect.bottom())
        fill_path.closeSubpath()

        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, self._fill_start)
        gradient.setColorAt(1.0, self._fill_end)
        painter.fillPath(fill_path, gradient)

        line_pen = QPen(self._line_color)
        line_pen.setWidth(2)
        painter.setPen(line_pen)
        painter.drawPath(line_path)


class MetricCard(QFrame):
    def __init__(self, title, accent_color):
        super().__init__()
        self.setObjectName("perfCard")

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        self.setLayout(layout)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("perfCardTitle")

        self.value_label = QLabel("--")
        self.value_label.setObjectName("perfCardValue")

        self.subtitle_label = QLabel("")
        self.subtitle_label.setObjectName("perfCardSubtitle")
        self.subtitle_label.setWordWrap(True)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("perfProgress")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(False)

        self.graph = HistoryGraph(accent_color)

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.subtitle_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.graph)

    def update_metric(self, value_text, progress_value, subtitle):
        bounded_value = max(0, min(int(progress_value), 100))
        self.value_label.setText(value_text)
        self.subtitle_label.setText(subtitle)
        self.progress_bar.setValue(bounded_value)
        self.graph.push(progress_value)


class DetailSection(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("detailSection")
        self._value_labels = {}

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.setLayout(layout)

        title_label = QLabel(title)
        title_label.setObjectName("detailSectionTitle")
        layout.addWidget(title_label)

        self.rows_layout = QGridLayout()
        self.rows_layout.setHorizontalSpacing(16)
        self.rows_layout.setVerticalSpacing(10)
        layout.addLayout(self.rows_layout)

    def set_rows(self, rows):
        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._value_labels.clear()

        for row_index, label_text in enumerate(rows):
            label = QLabel(label_text)
            label.setObjectName("detailKey")
            value = QLabel("--")
            value.setObjectName("detailValue")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.rows_layout.addWidget(label, row_index, 0)
            self.rows_layout.addWidget(value, row_index, 1)
            self._value_labels[label_text] = value

    def update_values(self, values):
        for key, value in values.items():
            label = self._value_labels.get(key)
            if label is not None:
                label.setText(value)


class PerformanceTab(QWidget):
    def __init__(self):
        super().__init__()

        self._last_disk_snapshot = None
        self._last_network_snapshot = None
        self._last_temperature_update = 0.0
        self._cached_gpu_temp_c = None
        self._last_connections_update = 0.0
        self._cached_connections_text = "N/A"
        self._nvidia_smi_path = shutil.which("nvidia-smi") or "C:\\Windows\\System32\\nvidia-smi.exe"
        self._cpu_name_value = self._cpu_name()
        self._hostname_value = self._hostname()
        self._partition_count = self._read_partition_count()
        self._active = True
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self._resume_refresh)

        root_layout = QHBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(14)
        self.setLayout(root_layout)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("perfSidebar")
        self.sidebar.addItems(["Overview", "CPU", "Memory", "Disk", "Network"])
        self.sidebar.setFixedWidth(170)
        root_layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("perfStack")
        root_layout.addWidget(self.stack)

        self._build_overview_page()
        self._build_focus_pages()

        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_metrics)
        self.timer.start(2000)

        self.refresh_metrics()

    def _build_overview_page(self):
        page, layout = self._create_scroll_page()

        metrics_layout = QGridLayout()
        metrics_layout.setHorizontalSpacing(14)
        metrics_layout.setVerticalSpacing(14)

        self.cpu_card = MetricCard("CPU Usage", "#5ec8ff")
        self.memory_card = MetricCard("Physical Memory", "#2ed3a8")
        self.disk_card = MetricCard("Disk Active Time", "#ffb84d")
        self.gpu_temp_card = MetricCard("GPU Temp", "#ff6b8f")

        metrics_layout.addWidget(self.cpu_card, 0, 0)
        metrics_layout.addWidget(self.memory_card, 0, 1)
        metrics_layout.addWidget(self.disk_card, 1, 0)
        metrics_layout.addWidget(self.gpu_temp_card, 1, 1)
        layout.addLayout(metrics_layout)

        detail_layout = QGridLayout()
        detail_layout.setHorizontalSpacing(14)
        detail_layout.setVerticalSpacing(14)

        self.processor_section = DetailSection("Processor")
        self.processor_section.set_rows(
            ["Model", "Logical Cores", "Physical Cores", "Current Frequency", "Max Frequency", "Uptime"]
        )
        self.memory_section = DetailSection("Memory")
        self.memory_section.set_rows(
            ["Total", "Available", "Used", "Cached", "Swap Used", "Swap Total"]
        )
        self.storage_section = DetailSection("Storage")
        self.storage_section.set_rows(
            ["System Drive", "Drive Used", "Drive Free", "Read Speed", "Write Speed", "Partitions"]
        )
        self.network_section = DetailSection("Network")
        self.network_section.set_rows(
            ["Download Speed", "Upload Speed", "Downloaded", "Uploaded", "Connections", "Hostname"]
        )

        detail_layout.addWidget(self.processor_section, 0, 0)
        detail_layout.addWidget(self.memory_section, 0, 1)
        detail_layout.addWidget(self.storage_section, 1, 0)
        detail_layout.addWidget(self.network_section, 1, 1)
        layout.addLayout(detail_layout)

        self.stack.addWidget(page)

    def _build_focus_pages(self):
        self.cpu_focus_card, self.cpu_focus_details = self._add_focus_page(
            "CPU",
            "#5ec8ff",
            "CPU Analysis",
            ["Usage", "Current Frequency", "Max Frequency", "Logical Cores", "Physical Cores", "Uptime"],
        )
        self.memory_focus_card, self.memory_focus_details = self._add_focus_page(
            "Memory",
            "#2ed3a8",
            "Memory Analysis",
            ["Load", "Total", "Available", "Used", "Cached", "Swap Used"],
        )
        self.disk_focus_card, self.disk_focus_details = self._add_focus_page(
            "Disk",
            "#ffb84d",
            "Disk Analysis",
            ["Active Time", "System Drive", "Used", "Free", "Read Speed", "Write Speed"],
        )
        self.network_focus_card, self.network_focus_details = self._add_focus_page(
            "Network",
            "#ff6bb0",
            "Network Analysis",
            ["Download Speed", "Upload Speed", "Downloaded", "Uploaded", "Connections", "Hostname"],
        )

    def _add_focus_page(self, title, accent_color, section_title, rows):
        page, layout = self._create_scroll_page()
        card = MetricCard(title, accent_color)
        section = DetailSection(section_title)
        section.set_rows(rows)
        layout.addWidget(card)
        layout.addWidget(section)
        self.stack.addWidget(page)
        return card, section

    def _create_scroll_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setObjectName("perfScroll")

        content = QWidget()
        content.setObjectName("perfRoot")
        scroll.setWidget(content)

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 12)
        layout.setSpacing(14)
        content.setLayout(layout)
        return scroll, layout

    def refresh_metrics(self):
        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk_active_percent, read_bytes_per_sec, write_bytes_per_sec = self._disk_metrics()
        download_per_sec, upload_per_sec, total_received, total_sent = self._network_metrics()
        gpu_temp_c = self._gpu_temperature()
        cpu_freq = psutil.cpu_freq()
        physical_cores = psutil.cpu_count(logical=False) or 0
        logical_cores = psutil.cpu_count(logical=True) or 0
        system_drive = self._system_drive_usage()
        connections = self._connection_count_text()

        self.cpu_card.update_metric(
            f"{cpu_percent:.1f}%",
            cpu_percent,
            f"{self._frequency_text(cpu_freq)} | {logical_cores} logical / {physical_cores} physical",
        )
        self.memory_card.update_metric(
            f"{memory.percent:.1f}%",
            memory.percent,
            f"{self._format_bytes(memory.used)} used of {self._format_bytes(memory.total)}",
        )
        self.disk_card.update_metric(
            f"{disk_active_percent:.1f}%",
            disk_active_percent,
            f"Read {self._format_bytes(read_bytes_per_sec)}/s | Write {self._format_bytes(write_bytes_per_sec)}/s",
        )
        if gpu_temp_c is None:
            self.gpu_temp_card.update_metric("N/A", 0, "GPU temperature not available")
        else:
            self.gpu_temp_card.update_metric(
                f"{gpu_temp_c:.0f} C",
                min(max(gpu_temp_c, 0.0), 100.0),
                "Temperature read from NVIDIA telemetry",
            )

        self.processor_section.update_values(
            {
                "Model": self._cpu_name_value,
                "Logical Cores": str(logical_cores),
                "Physical Cores": str(physical_cores),
                "Current Frequency": self._frequency_text(cpu_freq),
                "Max Frequency": self._max_frequency_text(cpu_freq),
                "Uptime": self._uptime_text(),
            }
        )
        self.memory_section.update_values(
            {
                "Total": self._format_bytes(memory.total),
                "Available": self._format_bytes(memory.available),
                "Used": self._format_bytes(memory.used),
                "Cached": self._format_bytes(getattr(memory, "cached", 0)),
                "Swap Used": self._format_bytes(swap.used),
                "Swap Total": self._format_bytes(swap.total),
            }
        )
        self.storage_section.update_values(
            {
                "System Drive": system_drive["label"],
                "Drive Used": system_drive["used"],
                "Drive Free": system_drive["free"],
                "Read Speed": f"{self._format_bytes(read_bytes_per_sec)}/s",
                "Write Speed": f"{self._format_bytes(write_bytes_per_sec)}/s",
                "Partitions": str(self._partition_count),
            }
        )
        self.network_section.update_values(
            {
                "Download Speed": f"{self._format_bytes(download_per_sec)}/s",
                "Upload Speed": f"{self._format_bytes(upload_per_sec)}/s",
                "Downloaded": self._format_bytes(total_received),
                "Uploaded": self._format_bytes(total_sent),
                "Connections": connections,
                "Hostname": self._hostname_value,
            }
        )

        self.cpu_focus_card.update_metric(
            f"{cpu_percent:.1f}%",
            cpu_percent,
            "Realtime processor utilization",
        )
        self.cpu_focus_details.update_values(
            {
                "Usage": f"{cpu_percent:.1f}%",
                "Current Frequency": self._frequency_text(cpu_freq),
                "Max Frequency": self._max_frequency_text(cpu_freq),
                "Logical Cores": str(logical_cores),
                "Physical Cores": str(physical_cores),
                "Uptime": self._uptime_text(),
            }
        )

        self.memory_focus_card.update_metric(
            f"{memory.percent:.1f}%",
            memory.percent,
            "Physical memory pressure",
        )
        self.memory_focus_details.update_values(
            {
                "Load": f"{memory.percent:.1f}%",
                "Total": self._format_bytes(memory.total),
                "Available": self._format_bytes(memory.available),
                "Used": self._format_bytes(memory.used),
                "Cached": self._format_bytes(getattr(memory, "cached", 0)),
                "Swap Used": self._format_bytes(swap.used),
            }
        )

        self.disk_focus_card.update_metric(
            f"{disk_active_percent:.1f}%",
            disk_active_percent,
            f"{system_drive['label']} | {system_drive['used']} used",
        )
        self.disk_focus_details.update_values(
            {
                "Active Time": f"{disk_active_percent:.1f}%",
                "System Drive": system_drive["label"],
                "Used": system_drive["used"],
                "Free": system_drive["free"],
                "Read Speed": f"{self._format_bytes(read_bytes_per_sec)}/s",
                "Write Speed": f"{self._format_bytes(write_bytes_per_sec)}/s",
            }
        )

        network_progress = min(((download_per_sec + upload_per_sec) / (10 * 1024 * 1024)) * 100, 100.0)
        self.network_focus_card.update_metric(
            f"{self._format_bytes(download_per_sec)}/s",
            network_progress,
            f"Upload {self._format_bytes(upload_per_sec)}/s",
        )
        self.network_focus_details.update_values(
            {
                "Download Speed": f"{self._format_bytes(download_per_sec)}/s",
                "Upload Speed": f"{self._format_bytes(upload_per_sec)}/s",
                "Downloaded": self._format_bytes(total_received),
                "Uploaded": self._format_bytes(total_sent),
                "Connections": connections,
                "Hostname": self._hostname_value,
            }
        )

    def set_active(self, active):
        self._active = active
        if active:
            if not self.timer.isActive() and not self._resume_timer.isActive():
                self.timer.start(2000)
            self.refresh_metrics()
            return

        self._resume_timer.stop()
        self.timer.stop()

    def pause_refresh_temporarily(self, duration_ms=450):
        if not self._active:
            return
        self.timer.stop()
        self._resume_timer.start(duration_ms)

    def _disk_metrics(self):
        counters = psutil.disk_io_counters()
        now = time.time()
        if counters is None:
            self._last_disk_snapshot = None
            return 0.0, 0.0, 0.0

        snapshot = {
            "time": now,
            "read_bytes": counters.read_bytes,
            "write_bytes": counters.write_bytes,
            "read_time": getattr(counters, "read_time", 0),
            "write_time": getattr(counters, "write_time", 0),
        }
        if self._last_disk_snapshot is None:
            self._last_disk_snapshot = snapshot
            return 0.0, 0.0, 0.0

        interval = max(now - self._last_disk_snapshot["time"], 0.001)
        read_bytes_per_sec = max(snapshot["read_bytes"] - self._last_disk_snapshot["read_bytes"], 0) / interval
        write_bytes_per_sec = max(snapshot["write_bytes"] - self._last_disk_snapshot["write_bytes"], 0) / interval
        busy_time_ms = max(
            (snapshot["read_time"] - self._last_disk_snapshot["read_time"])
            + (snapshot["write_time"] - self._last_disk_snapshot["write_time"]),
            0,
        )
        active_percent = min((busy_time_ms / (interval * 1000.0)) * 100.0, 100.0)
        self._last_disk_snapshot = snapshot
        return active_percent, read_bytes_per_sec, write_bytes_per_sec

    def _network_metrics(self):
        counters = psutil.net_io_counters()
        now = time.time()
        if counters is None:
            self._last_network_snapshot = None
            return 0.0, 0.0, 0.0, 0.0

        snapshot = {
            "time": now,
            "bytes_recv": counters.bytes_recv,
            "bytes_sent": counters.bytes_sent,
        }
        if self._last_network_snapshot is None:
            self._last_network_snapshot = snapshot
            return 0.0, 0.0, counters.bytes_recv, counters.bytes_sent

        interval = max(now - self._last_network_snapshot["time"], 0.001)
        download_per_sec = max(snapshot["bytes_recv"] - self._last_network_snapshot["bytes_recv"], 0) / interval
        upload_per_sec = max(snapshot["bytes_sent"] - self._last_network_snapshot["bytes_sent"], 0) / interval
        self._last_network_snapshot = snapshot
        return download_per_sec, upload_per_sec, counters.bytes_recv, counters.bytes_sent

    def _gpu_temperature(self):
        current_time = time.time()
        if current_time - self._last_temperature_update < 5.0:
            return self._cached_gpu_temp_c

        self._last_temperature_update = current_time
        self._cached_gpu_temp_c = self._read_nvidia_gpu_temperature()
        return self._cached_gpu_temp_c

    def _read_nvidia_gpu_temperature(self):
        if not self._nvidia_smi_path:
            return None

        try:
            result = subprocess.run(
                [
                    self._nvidia_smi_path,
                    "--query-gpu=temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=True,
            )
        except Exception:
            return None

        temperatures = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                temperatures.append(float(line))
            except ValueError:
                continue

        if not temperatures:
            return None

        return max(temperatures)

    def _cpu_name(self):
        try:
            return platform.processor() or "Processor"
        except Exception:
            return "Processor"

    def _hostname(self):
        try:
            return subprocess.check_output(["hostname"], text=True, timeout=1.0).strip()
        except Exception:
            return "Unknown"

    def _connection_count_text(self):
        current_time = time.time()
        if current_time - self._last_connections_update < 5.0:
            return self._cached_connections_text

        self._last_connections_update = current_time
        try:
            self._cached_connections_text = str(len(psutil.net_connections(kind="inet")))
        except Exception:
            self._cached_connections_text = "N/A"
        return self._cached_connections_text

    def _system_drive_usage(self):
        home_drive = (os.environ.get("SystemDrive") or "C:") + "\\"
        try:
            usage = psutil.disk_usage(home_drive)
            return {
                "label": f"{home_drive} ({usage.percent:.1f}% used)",
                "used": self._format_bytes(usage.used),
                "free": self._format_bytes(usage.free),
            }
        except Exception:
            return {
                "label": home_drive,
                "used": "N/A",
                "free": "N/A",
            }

    def _uptime_text(self):
        uptime_seconds = max(time.time() - psutil.boot_time(), 0)
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        return f"{hours}h {minutes}m"

    def _frequency_text(self, cpu_freq):
        if cpu_freq is None or not cpu_freq.current:
            return "N/A"
        return f"{cpu_freq.current / 1000:.2f} GHz"

    def _max_frequency_text(self, cpu_freq):
        if cpu_freq is None or not cpu_freq.max:
            return "N/A"
        return f"{cpu_freq.max / 1000:.2f} GHz"

    def _format_bytes(self, value):
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(value)
        for unit in units:
            if size < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024.0

    def _resume_refresh(self):
        if not self._active:
            return
        if not self.timer.isActive():
            self.timer.start(2000)
        self.refresh_metrics()

    def _read_partition_count(self):
        try:
            return len(psutil.disk_partitions(all=False))
        except Exception:
            return 0
