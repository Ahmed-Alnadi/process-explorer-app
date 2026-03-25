import os
import platform
import shutil
import subprocess
import time
from collections import deque

import psutil
from PySide6.QtCore import QPointF, Qt, QTimer, QSettings
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

from core.refresh_profiles import DEFAULT_REFRESH_PROFILE, REFRESH_PROFILES
from core.subprocess_utils import hidden_subprocess_kwargs
from core.windows_native import (
    NativeDiskActivityMonitor,
    NativeGpuUsageMonitor,
    native_memory_status,
    native_network_adapters,
    native_uptime_seconds,
)
from ui.export_utils import export_rows_to_csv


class HistoryGraph(QWidget):
    def __init__(self, line_color):
        super().__init__()
        self.setMinimumHeight(84)
        self.setMaximumHeight(96)
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
        border_pen = QPen(QColor(160, 210, 240, 46))
        border_pen.setWidth(1)
        painter.setPen(border_pen)
        painter.drawRoundedRect(rect, 8, 8)

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

        label_pen = QPen(QColor(236, 245, 252, 215))
        painter.setPen(label_pen)
        painter.drawText(rect.adjusted(6, 4, -6, -4), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop, "100%")
        painter.drawText(rect.adjusted(6, 4, -6, -4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, "60s")
        painter.drawText(rect.adjusted(6, 4, -6, -4), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, "0")


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
        self.history_label = QLabel("60-second history")
        self.history_label.setObjectName("perfGraphCaption")

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.subtitle_label)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.graph)
        layout.addWidget(self.history_label)

    def update_metric(self, value_text, progress_value, subtitle):
        bounded_value = max(0, min(int(progress_value), 100))
        self.value_label.setText(value_text)
        self.subtitle_label.setText(subtitle)
        self.progress_bar.setValue(bounded_value)
        self.graph.push(progress_value)

    def export_rows(self):
        return [
            ("Metric", self.title_label.text()),
            ("Value", self.value_label.text()),
            ("Subtitle", self.subtitle_label.text()),
            ("Progress", f"{self.progress_bar.value()}%"),
        ]


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

    def export_rows(self):
        return [(key, label.text()) for key, label in self._value_labels.items()]


class PerformanceTab(QWidget):
    def __init__(self):
        super().__init__()

        self._last_disk_snapshot = None
        self._last_network_snapshot = None
        self._last_adapter_snapshot = {}
        self._last_temperature_update = 0.0
        self._cached_gpu_temp_c = None
        self._last_connections_update = 0.0
        self._cached_connections_text = "N/A"
        self._nvidia_smi_path = shutil.which("nvidia-smi") or "C:\\Windows\\System32\\nvidia-smi.exe"
        self._disk_monitor = NativeDiskActivityMonitor()
        self._gpu_monitor = NativeGpuUsageMonitor()
        self._cpu_name_value = self._cpu_name()
        self._hostname_value = self._hostname()
        self._partition_count = self._read_partition_count()
        self._last_refresh_text = "Performance updated: --"
        self._last_metrics = None
        self._active = True
        self._runtime_paused = False
        self._refresh_profile_name = DEFAULT_REFRESH_PROFILE
        self._low_overhead_mode = False
        self._temperature_cache_ttl = 5.0
        self._connections_cache_ttl = 5.0
        self._timer_interval_ms = REFRESH_PROFILES[DEFAULT_REFRESH_PROFILE]["performance_timer_ms"]
        self._hidden_page_refresh_every = 3
        self._refresh_cycle = 0
        self.settings = QSettings("CodexTaskManager", "TaskManagerClone")
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self._resume_refresh)

        root_layout = QHBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(14)
        self.setLayout(root_layout)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("perfSidebar")
        self.sidebar.addItems(["Overview", "CPU", "Memory", "Disk", "Network", "GPU"])
        self.sidebar.setFixedWidth(170)
        root_layout.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("perfStack")
        root_layout.addWidget(self.stack, 1)

        self._build_overview_page()
        self._build_focus_pages()

        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.currentRowChanged.connect(self._save_current_page)
        self.sidebar.currentRowChanged.connect(lambda _index: self._refresh_for_visible_page())
        saved_page = int(self.settings.value("performance/current_page", 0))
        self.sidebar.setCurrentRow(max(0, min(saved_page, self.sidebar.count() - 1)))

        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_metrics)
        if self._timer_interval_ms > 0:
            self.timer.start(self._timer_interval_ms)

        self.refresh_metrics()

    def _build_overview_page(self):
        page, layout = self._create_scroll_page()

        metrics_layout = QGridLayout()
        metrics_layout.setHorizontalSpacing(14)
        metrics_layout.setVerticalSpacing(14)

        self.cpu_card = MetricCard("CPU Usage", "#5ec8ff")
        self.memory_card = MetricCard("Physical Memory", "#2ed3a8")
        self.disk_card = MetricCard("Disk Active Time", "#ffb84d")
        self.network_card = MetricCard("Network Activity", "#ff6bb0")
        self.gpu_usage_card = MetricCard("GPU Usage", "#ff8b6b")
        self.gpu_temp_card = MetricCard("GPU Temp", "#ff6b8f")

        metrics_layout.addWidget(self.cpu_card, 0, 0)
        metrics_layout.addWidget(self.memory_card, 0, 1)
        metrics_layout.addWidget(self.disk_card, 0, 2)
        metrics_layout.addWidget(self.network_card, 1, 0)
        metrics_layout.addWidget(self.gpu_usage_card, 1, 1)
        metrics_layout.addWidget(self.gpu_temp_card, 1, 2)
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
        self.network_adapter_section = DetailSection("Adapters")
        self.network_adapter_section.set_rows(["No active adapters"])
        self.stack.widget(4).widget().layout().addWidget(self.network_adapter_section)
        self.gpu_focus_card, self.gpu_focus_details = self._add_focus_page(
            "GPU",
            "#ff8b6b",
            "GPU Analysis",
            ["Usage", "Busiest Engine", "Adapter", "Adapters", "Temperature", "Status"],
        )
        self.gpu_engine_section = DetailSection("GPU Engines")
        self.gpu_engine_section.set_rows(["No active GPU engines"])
        self.stack.widget(5).widget().layout().addWidget(self.gpu_engine_section)

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
        self._last_refresh_text = f"Performance updated: {time.strftime('%I:%M:%S %p').lstrip('0')}"
        self._refresh_cycle += 1
        current_page = max(self.sidebar.currentRow(), 0)
        cpu_percent = psutil.cpu_percent(interval=None)
        native_memory = native_memory_status()
        memory = psutil.virtual_memory()
        if native_memory is not None:
            total_memory = native_memory["total_phys"]
            available_memory = native_memory["avail_phys"]
            used_memory = native_memory["used_phys"]
            memory_percent = native_memory["memory_load_percent"]
            cached_memory = max(memory.cached if hasattr(memory, "cached") else available_memory, 0)
        else:
            total_memory = memory.total
            available_memory = memory.available
            used_memory = memory.used
            memory_percent = memory.percent
            cached_memory = getattr(memory, "cached", 0)
        swap = psutil.swap_memory()
        disk_active_percent, read_bytes_per_sec, write_bytes_per_sec = self._disk_metrics()
        adapter_metrics, download_per_sec, upload_per_sec, total_received, total_sent = self._network_metrics()
        gpu_temp_c = self._gpu_temperature()
        gpu_metrics = self._gpu_metrics()
        cpu_freq = psutil.cpu_freq()
        physical_cores = psutil.cpu_count(logical=False) or 0
        logical_cores = psutil.cpu_count(logical=True) or 0
        system_drive = self._system_drive_usage()
        connections = self._connection_count_text()
        gpu_percent = gpu_metrics["busiest_percent"] if gpu_metrics else 0.0
        gpu_busiest_name = gpu_metrics["busiest_name"] if gpu_metrics else "GPU counters unavailable"
        gpu_adapter_name = ", ".join(gpu_metrics.get("adapters", [])[:2]) if gpu_metrics else "Unavailable"
        network_progress = min(((download_per_sec + upload_per_sec) / (10 * 1024 * 1024)) * 100, 100.0)

        metrics = {
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
            "total_memory": total_memory,
            "available_memory": available_memory,
            "used_memory": used_memory,
            "cached_memory": cached_memory,
            "swap_used": swap.used,
            "swap_total": swap.total,
            "disk_active_percent": disk_active_percent,
            "read_bytes_per_sec": read_bytes_per_sec,
            "write_bytes_per_sec": write_bytes_per_sec,
            "adapter_metrics": adapter_metrics,
            "download_per_sec": download_per_sec,
            "upload_per_sec": upload_per_sec,
            "total_received": total_received,
            "total_sent": total_sent,
            "gpu_temp_c": gpu_temp_c,
            "gpu_metrics": gpu_metrics,
            "gpu_percent": gpu_percent,
            "gpu_busiest_name": gpu_busiest_name,
            "gpu_adapter_name": gpu_adapter_name,
            "cpu_freq": cpu_freq,
            "physical_cores": physical_cores,
            "logical_cores": logical_cores,
            "system_drive": system_drive,
            "connections": connections,
            "network_progress": network_progress,
        }
        self._last_metrics = metrics

        pages_to_update = {current_page}
        if self._refresh_cycle % self._hidden_page_refresh_every == 0:
            pages_to_update.update(range(self.stack.count()))

        if 0 in pages_to_update:
            self._update_overview(metrics)
        if 1 in pages_to_update:
            self._update_cpu_focus(metrics)
        if 2 in pages_to_update:
            self._update_memory_focus(metrics)
        if 3 in pages_to_update:
            self._update_disk_focus(metrics)
        if 4 in pages_to_update:
            self._update_network_focus(metrics)
        if 5 in pages_to_update:
            self._update_gpu_focus(metrics)
    def set_active(self, active):
        self._active = active
        if active:
            if (
                not self._runtime_paused
                and self._timer_interval_ms > 0
                and not self.timer.isActive()
                and not self._resume_timer.isActive()
            ):
                self.timer.start(self._timer_interval_ms)
            if not self._runtime_paused and self._refresh_profile_name != "Paused":
                self.refresh_metrics()
            return

        self._resume_timer.stop()
        self.timer.stop()

    def set_refresh_profile(self, profile_name):
        config = REFRESH_PROFILES.get(profile_name, REFRESH_PROFILES[DEFAULT_REFRESH_PROFILE])
        self._refresh_profile_name = profile_name if profile_name in REFRESH_PROFILES else DEFAULT_REFRESH_PROFILE
        self._timer_interval_ms = config["performance_timer_ms"]
        self._apply_runtime_budget()
        self._resume_timer.stop()
        self.timer.stop()
        if self._active and not self._runtime_paused and self._timer_interval_ms > 0:
            self.timer.start(self._timer_interval_ms)
            self.refresh_metrics()

    def set_low_overhead_mode(self, enabled):
        self._low_overhead_mode = bool(enabled)
        self._hidden_page_refresh_every = 5 if self._low_overhead_mode else 3
        self._apply_runtime_budget()

    def set_compact_mode(self, enabled):
        self.sidebar.setProperty("compactMode", bool(enabled))
        self.sidebar.setFixedWidth(150 if enabled else 170)
        self._repolish_widget(self.sidebar)

    def refresh_now(self):
        if self._runtime_paused:
            return
        self.refresh_metrics()

    def trigger_primary_action(self):
        self.refresh_now()

    def shutdown(self):
        try:
            self._disk_monitor.close()
        except Exception:
            pass
        try:
            self._gpu_monitor.close()
        except Exception:
            pass

    def export_csv(self):
        headers = ["Section", "Metric", "Value"]
        rows = self._export_rows_for_current_page()
        success, file_path = export_rows_to_csv(
            self,
            f"task-manager-performance-{time.strftime('%Y%m%d-%H%M%S')}.csv",
            headers,
            rows,
        )
        return success

    def status_refresh_text(self):
        return self._last_refresh_text

    def pause_refresh_temporarily(self, duration_ms=450):
        if not self._active or self._runtime_paused:
            return
        self.timer.stop()
        self._resume_timer.start(duration_ms)

    def pause_for_menu_open(self):
        if not self._active or self._runtime_paused:
            return
        self.timer.stop()
        self._resume_timer.stop()

    def resume_after_menu_close(self, delay_ms=250):
        if not self._active or self._runtime_paused:
            return
        self._resume_timer.start(delay_ms)
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
        active_percent = self._disk_monitor.read_active_percent()
        if active_percent is None:
            busy_time_ms = max(
                (snapshot["read_time"] - self._last_disk_snapshot["read_time"])
                + (snapshot["write_time"] - self._last_disk_snapshot["write_time"]),
                0,
            )
            active_percent = min((busy_time_ms / (interval * 1000.0)) * 100.0, 100.0)
        self._last_disk_snapshot = snapshot
        return active_percent, read_bytes_per_sec, write_bytes_per_sec

    def _network_metrics(self):
        adapters = native_network_adapters()
        now = time.time()
        if adapters:
            adapter_metrics = []
            total_received = 0
            total_sent = 0
            total_download = 0.0
            total_upload = 0.0
            for adapter in adapters:
                key = adapter["index"]
                previous = self._last_adapter_snapshot.get(key)
                interval = max(now - previous["time"], 0.001) if previous else 1.0
                rx_per_sec = 0.0
                tx_per_sec = 0.0
                if previous is not None:
                    rx_per_sec = max(adapter["rx_bytes"] - previous["rx_bytes"], 0) / interval
                    tx_per_sec = max(adapter["tx_bytes"] - previous["tx_bytes"], 0) / interval
                adapter_metrics.append(
                    {
                        **adapter,
                        "download_per_sec": rx_per_sec,
                        "upload_per_sec": tx_per_sec,
                    }
                )
                total_received += adapter["rx_bytes"]
                total_sent += adapter["tx_bytes"]
                total_download += rx_per_sec
                total_upload += tx_per_sec
            self._last_adapter_snapshot = {
                adapter["index"]: {
                    "time": now,
                    "rx_bytes": adapter["rx_bytes"],
                    "tx_bytes": adapter["tx_bytes"],
                }
                for adapter in adapters
            }
            return adapter_metrics, total_download, total_upload, total_received, total_sent

        counters = psutil.net_io_counters()
        if counters is None:
            self._last_network_snapshot = None
            return [], 0.0, 0.0, 0.0, 0.0

        snapshot = {
            "time": now,
            "bytes_recv": counters.bytes_recv,
            "bytes_sent": counters.bytes_sent,
        }
        if self._last_network_snapshot is None:
            self._last_network_snapshot = snapshot
            return [], 0.0, 0.0, counters.bytes_recv, counters.bytes_sent

        interval = max(now - self._last_network_snapshot["time"], 0.001)
        download_per_sec = max(snapshot["bytes_recv"] - self._last_network_snapshot["bytes_recv"], 0) / interval
        upload_per_sec = max(snapshot["bytes_sent"] - self._last_network_snapshot["bytes_sent"], 0) / interval
        self._last_network_snapshot = snapshot
        return [], download_per_sec, upload_per_sec, counters.bytes_recv, counters.bytes_sent

    def _gpu_temperature(self):
        current_time = time.time()
        if current_time - self._last_temperature_update < self._temperature_cache_ttl:
            return self._cached_gpu_temp_c

        self._last_temperature_update = current_time
        self._cached_gpu_temp_c = self._read_nvidia_gpu_temperature()
        return self._cached_gpu_temp_c

    def _gpu_metrics(self):
        return self._gpu_monitor.read()

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
                **hidden_subprocess_kwargs(),
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
            return subprocess.check_output(
                ["hostname"],
                text=True,
                timeout=1.0,
                **hidden_subprocess_kwargs(),
            ).strip()
        except Exception:
            return "Unknown"

    def _connection_count_text(self):
        current_time = time.time()
        if current_time - self._last_connections_update < self._connections_cache_ttl:
            return self._cached_connections_text

        self._last_connections_update = current_time
        try:
            self._cached_connections_text = str(len(psutil.net_connections(kind="inet")))
        except Exception:
            self._cached_connections_text = "N/A"
        return self._cached_connections_text

    def _update_overview(self, metrics):
        self.cpu_card.update_metric(
            f"{metrics['cpu_percent']:.1f}%",
            metrics["cpu_percent"],
            f"{self._frequency_text(metrics['cpu_freq'])} | {metrics['logical_cores']} logical / {metrics['physical_cores']} physical",
        )
        self.memory_card.update_metric(
            f"{metrics['memory_percent']:.1f}%",
            metrics["memory_percent"],
            f"{self._format_bytes(metrics['used_memory'])} used of {self._format_bytes(metrics['total_memory'])}",
        )
        self.disk_card.update_metric(
            f"{metrics['disk_active_percent']:.1f}%",
            metrics["disk_active_percent"],
            f"Read {self._format_bytes(metrics['read_bytes_per_sec'])}/s | Write {self._format_bytes(metrics['write_bytes_per_sec'])}/s",
        )
        self.network_card.update_metric(
            f"{self._format_bytes(metrics['download_per_sec'])}/s",
            metrics["network_progress"],
            f"Upload {self._format_bytes(metrics['upload_per_sec'])}/s",
        )
        self.gpu_usage_card.update_metric(
            f"{metrics['gpu_percent']:.1f}%",
            metrics["gpu_percent"],
            metrics["gpu_busiest_name"],
        )
        if metrics["gpu_temp_c"] is None:
            self.gpu_temp_card.update_metric("N/A", 0, "GPU temperature not available")
        else:
            self.gpu_temp_card.update_metric(
                self._format_temperature(metrics["gpu_temp_c"]),
                min(max(metrics["gpu_temp_c"], 0.0), 100.0),
                "Temperature read from NVIDIA telemetry",
            )
        self.processor_section.update_values(
            {
                "Model": self._cpu_name_value,
                "Logical Cores": str(metrics["logical_cores"]),
                "Physical Cores": str(metrics["physical_cores"]),
                "Current Frequency": self._frequency_text(metrics["cpu_freq"]),
                "Max Frequency": self._max_frequency_text(metrics["cpu_freq"]),
                "Uptime": self._uptime_text(),
            }
        )
        self.memory_section.update_values(
            {
                "Total": self._format_bytes(metrics["total_memory"]),
                "Available": self._format_bytes(metrics["available_memory"]),
                "Used": self._format_bytes(metrics["used_memory"]),
                "Cached": self._format_bytes(metrics["cached_memory"]),
                "Swap Used": self._format_bytes(metrics["swap_used"]),
                "Swap Total": self._format_bytes(metrics["swap_total"]),
            }
        )
        self.storage_section.update_values(
            {
                "System Drive": metrics["system_drive"]["label"],
                "Drive Used": metrics["system_drive"]["used"],
                "Drive Free": metrics["system_drive"]["free"],
                "Read Speed": f"{self._format_bytes(metrics['read_bytes_per_sec'])}/s",
                "Write Speed": f"{self._format_bytes(metrics['write_bytes_per_sec'])}/s",
                "Partitions": str(self._partition_count),
            }
        )
        self.network_section.update_values(
            {
                "Download Speed": f"{self._format_bytes(metrics['download_per_sec'])}/s",
                "Upload Speed": f"{self._format_bytes(metrics['upload_per_sec'])}/s",
                "Downloaded": self._format_bytes(metrics["total_received"]),
                "Uploaded": self._format_bytes(metrics["total_sent"]),
                "Connections": metrics["connections"],
                "Hostname": self._hostname_value,
            }
        )

    def _update_cpu_focus(self, metrics):
        self.cpu_focus_card.update_metric(
            f"{metrics['cpu_percent']:.1f}%",
            metrics["cpu_percent"],
            "Realtime processor utilization",
        )
        self.cpu_focus_details.update_values(
            {
                "Usage": f"{metrics['cpu_percent']:.1f}%",
                "Current Frequency": self._frequency_text(metrics["cpu_freq"]),
                "Max Frequency": self._max_frequency_text(metrics["cpu_freq"]),
                "Logical Cores": str(metrics["logical_cores"]),
                "Physical Cores": str(metrics["physical_cores"]),
                "Uptime": self._uptime_text(),
            }
        )

    def _update_memory_focus(self, metrics):
        self.memory_focus_card.update_metric(
            f"{metrics['memory_percent']:.1f}%",
            metrics["memory_percent"],
            "Physical memory pressure",
        )
        self.memory_focus_details.update_values(
            {
                "Load": f"{metrics['memory_percent']:.1f}%",
                "Total": self._format_bytes(metrics["total_memory"]),
                "Available": self._format_bytes(metrics["available_memory"]),
                "Used": self._format_bytes(metrics["used_memory"]),
                "Cached": self._format_bytes(metrics["cached_memory"]),
                "Swap Used": self._format_bytes(metrics["swap_used"]),
            }
        )

    def _update_disk_focus(self, metrics):
        self.disk_focus_card.update_metric(
            f"{metrics['disk_active_percent']:.1f}%",
            metrics["disk_active_percent"],
            f"{metrics['system_drive']['label']} | {metrics['system_drive']['used']} used",
        )
        self.disk_focus_details.update_values(
            {
                "Active Time": f"{metrics['disk_active_percent']:.1f}%",
                "System Drive": metrics["system_drive"]["label"],
                "Used": metrics["system_drive"]["used"],
                "Free": metrics["system_drive"]["free"],
                "Read Speed": f"{self._format_bytes(metrics['read_bytes_per_sec'])}/s",
                "Write Speed": f"{self._format_bytes(metrics['write_bytes_per_sec'])}/s",
            }
        )

    def _update_network_focus(self, metrics):
        self.network_focus_card.update_metric(
            f"{self._format_bytes(metrics['download_per_sec'])}/s",
            metrics["network_progress"],
            f"Upload {self._format_bytes(metrics['upload_per_sec'])}/s",
        )
        self.network_focus_details.update_values(
            {
                "Download Speed": f"{self._format_bytes(metrics['download_per_sec'])}/s",
                "Upload Speed": f"{self._format_bytes(metrics['upload_per_sec'])}/s",
                "Downloaded": self._format_bytes(metrics["total_received"]),
                "Uploaded": self._format_bytes(metrics["total_sent"]),
                "Connections": metrics["connections"],
                "Hostname": self._hostname_value,
            }
        )
        self._update_adapter_section(metrics["adapter_metrics"])

    def _update_gpu_focus(self, metrics):
        gpu_metrics = metrics["gpu_metrics"]
        self.gpu_focus_card.update_metric(
            f"{metrics['gpu_percent']:.1f}%",
            metrics["gpu_percent"],
            metrics["gpu_busiest_name"] if gpu_metrics else "Unavailable",
        )
        self.gpu_focus_details.update_values(
            {
                "Usage": f"{metrics['gpu_percent']:.1f}%",
                "Busiest Engine": metrics["gpu_busiest_name"] if gpu_metrics else "Unavailable",
                "Adapter": metrics["gpu_adapter_name"] or "Unavailable",
                "Adapters": str(len(gpu_metrics.get("adapters", []))) if gpu_metrics else "0",
                "Temperature": self._format_temperature(metrics["gpu_temp_c"]),
                "Status": "Native GPU counters active" if gpu_metrics else "GPU counters unavailable",
            }
        )
        self._update_gpu_engine_section(gpu_metrics)

    def _refresh_for_visible_page(self):
        if self._active and not self._runtime_paused and self._refresh_profile_name != "Paused":
            self.refresh_metrics()
    def _apply_runtime_budget(self):
        config = REFRESH_PROFILES.get(
            self._refresh_profile_name,
            REFRESH_PROFILES[DEFAULT_REFRESH_PROFILE],
        )
        multiplier = 1.8 if self._low_overhead_mode else 1.0
        self._temperature_cache_ttl = max(config["performance_timer_ms"] / 1000.0 * 2.5, 5.0) * multiplier
        self._connections_cache_ttl = max(config["performance_timer_ms"] / 1000.0 * 2.5, 5.0) * multiplier

    def _save_current_page(self, index):
        self.settings.setValue("performance/current_page", int(index))

    def _repolish_widget(self, widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _format_temperature(self, temperature_c):
        if temperature_c is None:
            return "N/A"
        return f"{temperature_c:.0f}\N{DEGREE SIGN}C"

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
        uptime_seconds = native_uptime_seconds()
        if uptime_seconds is None:
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
        if not self._active or self._runtime_paused:
            return
        if self._timer_interval_ms <= 0:
            return
        if not self.timer.isActive():
            self.timer.start(self._timer_interval_ms)
        self.refresh_metrics()

    def set_runtime_paused(self, paused):
        paused = bool(paused)
        if paused == self._runtime_paused:
            return
        self._runtime_paused = paused
        if paused:
            self._resume_timer.stop()
            self.timer.stop()
            return
        if self._active and self._timer_interval_ms > 0:
            self.timer.start(self._timer_interval_ms)
            if self._refresh_profile_name != "Paused":
                self.refresh_metrics()
    def _read_partition_count(self):
        try:
            return len(psutil.disk_partitions(all=False))
        except Exception:
            return 0

    def _update_adapter_section(self, adapters):
        if not adapters:
            self.network_adapter_section.set_rows(["No active adapters"])
            self.network_adapter_section.update_values({"No active adapters": "Native adapter counters unavailable"})
            return

        rows = [adapter["alias"] for adapter in adapters[:6]]
        self.network_adapter_section.set_rows(rows)
        values = {}
        for adapter in adapters[:6]:
            link_speed = max(adapter.get("rx_link_speed", 0), adapter.get("tx_link_speed", 0))
            link_text = self._format_bits(link_speed) if link_speed else "Unknown link"
            values[adapter["alias"]] = (
                f"Down {self._format_bytes(adapter['download_per_sec'])}/s | "
                f"Up {self._format_bytes(adapter['upload_per_sec'])}/s | "
                f"{link_text}"
            )
        self.network_adapter_section.update_values(values)

    def _update_gpu_engine_section(self, gpu_metrics):
        engines = list((gpu_metrics or {}).get("engines", []))
        if not engines:
            self.gpu_engine_section.set_rows(["No active GPU engines"])
            self.gpu_engine_section.update_values({"No active GPU engines": "Native GPU counters unavailable"})
            return

        rows = [engine["name"] for engine in engines]
        self.gpu_engine_section.set_rows(rows)
        self.gpu_engine_section.update_values(
            {engine["name"]: f"{engine['percent']:.1f}%" for engine in engines}
        )

    def _format_bits(self, bits_per_sec):
        units = ["bps", "Kbps", "Mbps", "Gbps", "Tbps"]
        value = float(bits_per_sec)
        for unit in units:
            if value < 1000 or unit == units[-1]:
                if unit == "bps":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1000.0

    def _export_rows_for_current_page(self):
        page_index = self.sidebar.currentRow()
        rows = []
        if page_index == 0:
            rows.extend(self._section_export_rows("Overview CPU", self.cpu_card.export_rows()))
            rows.extend(self._section_export_rows("Overview Memory", self.memory_card.export_rows()))
            rows.extend(self._section_export_rows("Overview Disk", self.disk_card.export_rows()))
            rows.extend(self._section_export_rows("Overview Network", self.network_card.export_rows()))
            rows.extend(self._section_export_rows("Overview GPU Usage", self.gpu_usage_card.export_rows()))
            rows.extend(self._section_export_rows("Overview GPU Temp", self.gpu_temp_card.export_rows()))
            rows.extend(self._section_export_rows("Processor", self.processor_section.export_rows()))
            rows.extend(self._section_export_rows("Memory", self.memory_section.export_rows()))
            rows.extend(self._section_export_rows("Storage", self.storage_section.export_rows()))
            rows.extend(self._section_export_rows("Network", self.network_section.export_rows()))
            return rows

        focus_pages = {
            1: ("CPU", self.cpu_focus_card, self.cpu_focus_details, None),
            2: ("Memory", self.memory_focus_card, self.memory_focus_details, None),
            3: ("Disk", self.disk_focus_card, self.disk_focus_details, None),
            4: ("Network", self.network_focus_card, self.network_focus_details, self.network_adapter_section),
            5: ("GPU", self.gpu_focus_card, self.gpu_focus_details, self.gpu_engine_section),
        }
        page_name, card, section, extra_section = focus_pages.get(
            page_index,
            ("Overview", self.cpu_card, self.processor_section, None),
        )
        rows.extend(self._section_export_rows(page_name, card.export_rows()))
        rows.extend(self._section_export_rows(f"{page_name} Details", section.export_rows()))
        if extra_section is not None:
            rows.extend(self._section_export_rows(f"{page_name} Extra", extra_section.export_rows()))
        return rows

    def _section_export_rows(self, section_name, items):
        return [[section_name, label, value] for label, value in items]


