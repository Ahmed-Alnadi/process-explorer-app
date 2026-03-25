import time
from collections import deque

import psutil
from PySide6.QtCore import QEvent, QFileInfo, QModelIndex, QObject, QPoint, QRect, QSettings, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileIconProvider,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSplitterHandle,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.process_manager import ProcessManager, ProcessTerminationBlockedError
from core.refresh_profiles import DEFAULT_REFRESH_PROFILE, REFRESH_PROFILES
from ui.export_utils import export_rows_to_csv
from ui.heatmap_utils import disk_intensity_from_rate, protected_heat_brush
from ui.process_actions import copy_text_to_clipboard, open_file_location, search_online
from ui.process_tree_model import ClearableTreeView, ProcessTreeModel


SORT_ROLE = int(Qt.ItemDataRole.UserRole)
ENTRY_ROLE = SORT_ROLE + 1
ENTRY_ID_ROLE = SORT_ROLE + 2
ENTRY_KIND_ROLE = SORT_ROLE + 3
SECONDARY_TEXT_ROLE = SORT_ROLE + 4


class SecondaryTextDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        primary_text = index.data(Qt.ItemDataRole.DisplayRole) or ""
        secondary_text = index.data(SECONDARY_TEXT_ROLE) or ""
        alignment = index.data(Qt.ItemDataRole.TextAlignmentRole)
        if alignment is None:
            alignment = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        align_right = bool(alignment & int(Qt.AlignmentFlag.AlignRight))

        item_option = QStyleOptionViewItem(option)
        self.initStyleOption(item_option, index)
        item_option.text = ""

        style = item_option.widget.style() if item_option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, item_option, painter, item_option.widget)

        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText,
            item_option,
            item_option.widget,
        ).adjusted(4, 0, -4, 0)

        painter.save()
        if item_option.state & QStyle.StateFlag.State_Selected:
            primary_color = item_option.palette.highlightedText().color()
            secondary_color = item_option.palette.highlightedText().color()
            secondary_color.setAlpha(170)
        else:
            primary_color = item_option.palette.text().color()
            secondary_color = item_option.palette.text().color()
            secondary_color.setAlpha(165)

        primary_font = QFont(item_option.font)
        secondary_font = QFont(item_option.font)
        secondary_font.setPointSizeF(max(primary_font.pointSizeF() - 2, 8))

        painter.setPen(primary_color)
        painter.setFont(primary_font)
        primary_width = painter.fontMetrics().horizontalAdvance(primary_text)

        if secondary_text:
            painter.setFont(secondary_font)
            secondary_width = painter.fontMetrics().horizontalAdvance(secondary_text)
            total_width = primary_width + secondary_width + 10
        else:
            secondary_width = 0
            total_width = primary_width

        if align_right:
            primary_rect = text_rect.adjusted(max(text_rect.width() - total_width, 0), 0, 0, 0)
        else:
            primary_rect = text_rect

        painter.setPen(primary_color)
        painter.setFont(primary_font)
        painter.drawText(
            primary_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            primary_text,
        )

        if secondary_text:
            if align_right:
                secondary_left = primary_rect.left() + primary_width + 10
                secondary_rect = text_rect.adjusted(secondary_left - text_rect.left(), 0, 0, 0)
            else:
                secondary_rect = text_rect.adjusted(primary_width + 10, 0, 0, 0)
            painter.setPen(secondary_color)
            painter.setFont(secondary_font)
            painter.drawText(
                secondary_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                secondary_text,
            )

        painter.restore()


class ClearableTreeWidget(QTreeWidget):
    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if not index.isValid():
            self.clearSelection()
            self.setCurrentItem(None)
            event.accept()
            return
        super().mousePressEvent(event)


class SortableTreeWidgetItem(QTreeWidgetItem):
    def __lt__(self, other):
        column = self.treeWidget().sortColumn()
        left_value = self.data(column, SORT_ROLE)
        right_value = other.data(column, SORT_ROLE)
        if left_value is not None and right_value is not None:
            return left_value < right_value
        return self.text(column).lower() < other.text(column).lower()


class ProcessRefreshWorker(QObject):
    snapshot_ready = Signal(object, object, bool, float)

    def __init__(self):
        super().__init__()
        self._manager = ProcessManager()
        self._busy = False
        self._cached_groups = []
        self._last_full_refresh = 0.0
        self._full_refresh_interval_s = 4.0

    @Slot()
    def refresh(self):
        if self._busy:
            return

        self._busy = True
        try:
            current_time = time.time()
            summary = self._manager.system_summary()
            full_refresh = (
                not self._cached_groups
                or current_time - self._last_full_refresh >= self._full_refresh_interval_s
            )
            groups = None
            if full_refresh:
                self._cached_groups = self._manager.list_processes()
                self._last_full_refresh = current_time
                groups = self._cached_groups
            self.snapshot_ready.emit(groups, summary, full_refresh, current_time)
        finally:
            self._busy = False

    def set_refresh_profile(self, profile_name):
        self._manager.set_refresh_profile(profile_name)

    def set_low_overhead_mode(self, enabled):
        self._manager.set_low_overhead_mode(enabled)

    @Slot()
    def force_refresh(self):
        self._last_full_refresh = 0.0
        self.refresh()


class MiniSparkline(QWidget):
    def __init__(self, color_hex):
        super().__init__()
        self._line_color = QColor(color_hex)
        self._fill_start = QColor(color_hex)
        self._fill_start.setAlpha(120)
        self._fill_end = QColor(color_hex)
        self._fill_end.setAlpha(20)
        self._values = []
        self.setMinimumHeight(32)
        self.setMaximumHeight(36)

    def set_values(self, values):
        self._values = [max(0.0, min(float(value), 100.0)) for value in values]
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(2, 3, -2, -3)
        painter.fillRect(rect, QColor(8, 18, 29, 110))

        border_pen = QPen(QColor(140, 210, 245, 45))
        border_pen.setWidth(1)
        painter.setPen(border_pen)
        painter.drawRoundedRect(rect, 5, 5)

        if len(self._values) < 2:
            return

        points = []
        width = max(rect.width(), 1)
        height = max(rect.height(), 1)
        for index, value in enumerate(self._values):
            x = rect.left() + (width * index / (len(self._values) - 1))
            y = rect.bottom() - (value / 100.0) * height
            points.append((x, y))

        line_path = QPainterPath()
        line_path.moveTo(*points[0])
        for point in points[1:]:
            line_path.lineTo(*point)

        fill_path = QPainterPath(line_path)
        fill_path.lineTo(rect.right(), rect.bottom())
        fill_path.lineTo(rect.left(), rect.bottom())
        fill_path.closeSubpath()

        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, self._fill_start)
        gradient.setColorAt(1.0, self._fill_end)
        painter.fillPath(fill_path, gradient)

        pen = QPen(self._line_color)
        pen.setWidth(1)
        painter.setPen(pen)
        painter.drawPath(line_path)


class SelectionInfoPanel(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("sidePanel")
        self.setProperty("protectedState", False)
        self._value_labels = {}
        self.current_entry = None

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.setLayout(layout)

        self.title_label = QLabel("Selection")
        self.title_label.setObjectName("sidePanelTitle")
        layout.addWidget(self.title_label)

        self.badge_label = QLabel("PROTECTED")
        self.badge_label.setObjectName("protectedBadge")
        self.badge_label.hide()
        layout.addWidget(self.badge_label, 0, Qt.AlignmentFlag.AlignLeft)

        self.subtitle_label = QLabel("Pick an app or process to inspect it.")
        self.subtitle_label.setObjectName("sidePanelSubtitle")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        layout.addLayout(grid)

        fields = [
            "Type",
            "Protection",
            "Service",
            "Startup",
            "Publisher",
            "Path",
            "Window",
            "PID / Count",
            "CPU",
            "Memory",
            "Disk",
            "Product",
            "Description",
            "User",
            "Started",
            "Threads",
        ]
        for row, field in enumerate(fields):
            key = QLabel(field)
            key.setObjectName("sideKey")
            value = QLabel("--")
            value.setObjectName("sideValue")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(key, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, row, 1)
            self._value_labels[field] = value

        self.open_button = QPushButton("Open File Location")
        self.open_button.setObjectName("secondaryButton")
        self.open_button.setToolTip("Open the folder that contains this executable.")
        self.search_button = QPushButton("Search Online")
        self.search_button.setObjectName("secondaryButton")
        self.search_button.setToolTip("Search online for the selected app or process.")
        self.service_button = QPushButton("Go to Service")
        self.service_button.setObjectName("secondaryButton")
        self.service_button.setToolTip("Jump to the linked Windows service when one exists.")
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addWidget(self.open_button)
        actions.addWidget(self.search_button)
        actions.addWidget(self.service_button)
        layout.addLayout(actions)

        history_title = QLabel("Recent Usage")
        history_title.setObjectName("sideKey")
        layout.addWidget(history_title)

        self.cpu_history_graph = MiniSparkline("#5ec8ff")
        self.memory_history_graph = MiniSparkline("#2ed3a8")
        self.disk_history_graph = MiniSparkline("#ffb84d")

        for label_text, graph in (
            ("CPU", self.cpu_history_graph),
            ("Memory", self.memory_history_graph),
            ("Disk", self.disk_history_graph),
        ):
            row = QHBoxLayout()
            row.setSpacing(10)
            label = QLabel(label_text)
            label.setObjectName("sideKey")
            row.addWidget(label)
            row.addWidget(graph, 1)
            layout.addLayout(row)
        layout.addStretch()

    def set_entry(self, entry, extra_details, history=None):
        self.current_entry = entry
        if entry is None:
            self.title_label.setText("Selection")
            self.badge_label.hide()
            self.subtitle_label.setText("Pick an app or process to inspect it.")
            for label in self._value_labels.values():
                label.setText("--")
            self._set_protected_state(False)
            self.open_button.setEnabled(False)
            self.open_button.setToolTip("")
            self.search_button.setEnabled(False)
            self.search_button.setToolTip("Search online for the selected app or process.")
            self.service_button.setEnabled(False)
            self.service_button.setToolTip("Jump to the linked Windows service when one exists.")
            self.cpu_history_graph.set_values([])
            self.memory_history_graph.set_values([])
            self.disk_history_graph.set_values([])
            return

        self.title_label.setText(entry["name"])
        if entry.get("is_protected"):
            self.badge_label.show()
            self.subtitle_label.setText("Protected item. End task is disabled in this app.")
        else:
            self.badge_label.hide()
            self.subtitle_label.setText("On-demand details for the selected item.")
        self._set_protected_state(bool(entry.get("is_protected")))

        field_values = {
            "Type": entry["type_display"],
            "Protection": entry.get("protection_reason") or ("Protected" if entry.get("is_protected") else "Normal"),
            "Service": entry.get("service_display") or "None",
            "Startup": extra_details.get("startup_display") or entry.get("startup_display") or "Not listed",
            "Publisher": entry["publisher"],
            "Path": entry["exe_path"] or entry.get("location_reason") or "Unavailable",
            "Window": entry["window_display"],
            "PID / Count": str(entry["pid"]) if "pid" in entry else str(entry["process_count"]),
            "CPU": entry["cpu_display"],
            "Memory": f"{entry['memory_display']}  {entry['memory_value_display']}",
            "Disk": entry["disk_display"],
            "Product": entry.get("product_name") or "Unknown",
            "Description": entry.get("description") or "Unknown",
            "User": extra_details["user"],
            "Started": extra_details["started"],
            "Threads": extra_details["threads"],
        }

        for field, value in field_values.items():
            self._value_labels[field].setText(value)

        self.open_button.setEnabled(True)
        self.open_button.setToolTip(
            entry.get("location_reason") or "Open the folder that contains this executable."
        )
        self.search_button.setEnabled(True)
        self.search_button.setToolTip("Search online for the selected app or process.")
        self.service_button.setEnabled(True)
        self.service_button.setToolTip(
            "Open the linked Windows service."
            if entry.get("primary_service_name")
            else "No linked Windows service is available for this selection."
        )
        history = history or {}
        self.cpu_history_graph.set_values(history.get("cpu", []))
        self.memory_history_graph.set_values(history.get("memory", []))
        self.disk_history_graph.set_values(history.get("disk", []))

    def _set_protected_state(self, protected):
        protected = bool(protected)
        if self.property("protectedState") == protected:
            return
        self.setProperty("protectedState", protected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class ProcessesTab(QWidget):
    request_refresh = Signal()
    request_force_refresh = Signal()
    page_status_changed = Signal(str)
    go_to_details_requested = Signal(object)
    go_to_service_requested = Signal(str)

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout()
        layout.setSpacing(12)

        summary_layout = QHBoxLayout()
        summary_layout.setSpacing(10)
        self.cpu_summary_label = QLabel("CPU: 0.0%")
        self.cpu_summary_label.setObjectName("metricCard")
        self.memory_summary_label = QLabel("Physical Memory: 0.0%")
        self.memory_summary_label.setObjectName("metricCard")
        self.disk_summary_label = QLabel("Disk Active Time: 0.0%")
        self.disk_summary_label.setObjectName("metricCard")
        self.gpu_summary_label = QLabel("GPU Temp: N/A")
        self.gpu_summary_label.setObjectName("metricCard")
        self.last_updated_label = QLabel("List updated: --")
        self.last_updated_label.setObjectName("statusLabel")
        summary_layout.addWidget(self.cpu_summary_label)
        summary_layout.addWidget(self.memory_summary_label)
        summary_layout.addWidget(self.disk_summary_label)
        summary_layout.addWidget(self.gpu_summary_label)
        summary_layout.addStretch()
        summary_layout.addWidget(self.last_updated_label, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(summary_layout)

        self.vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        self.vertical_splitter.setChildrenCollapsible(False)
        self.vertical_splitter.setHandleWidth(10)

        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.setHandleWidth(10)

        self.tree = ClearableTreeView()
        self.tree.setObjectName("processTree")
        self.model = ProcessTreeModel(
            icon_resolver=self._icon_for_entry,
            roles={
                "sort": SORT_ROLE,
                "entry": ENTRY_ROLE,
                "entry_id": ENTRY_ID_ROLE,
                "entry_kind": ENTRY_KIND_ROLE,
                "secondary": SECONDARY_TEXT_ROLE,
            },
            parent=self,
        )
        self.tree.setModel(self.model)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setRootIsDecorated(True)
        self.tree.setItemsExpandable(True)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.header().setMinimumSectionSize(72)
        self.tree.header().setStretchLastSection(False)
        self.tree.setItemDelegateForColumn(6, SecondaryTextDelegate(self.tree))
        self.content_splitter.addWidget(self.tree)

        self.info_scroll = QScrollArea()
        self.info_scroll.setObjectName("sidePanelScroll")
        self.info_scroll.setWidgetResizable(True)
        self.info_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.info_panel = SelectionInfoPanel()
        self.info_panel.setMinimumWidth(0)
        self.info_panel.setMaximumWidth(16777215)
        self.info_scroll.setMinimumWidth(320)
        self.info_scroll.setMaximumWidth(16777215)
        self.info_scroll.setWidget(self.info_panel)
        self.content_splitter.addWidget(self.info_scroll)
        self.content_splitter.setStretchFactor(0, 5)
        self.content_splitter.setStretchFactor(1, 2)
        self.content_splitter.setSizes([1280, 440])
        self.vertical_splitter.addWidget(self.content_splitter)

        bottom_panel = QWidget()
        bottom_layout = QVBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)
        bottom_panel.setLayout(bottom_layout)
        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self.end_btn = QPushButton("End Task")
        self.end_btn.setObjectName("dangerButton")
        self.end_btn.setToolTip("Terminate the selected app or process.")
        self.end_btn.clicked.connect(self.end_task)
        self.expand_all_button = QPushButton("Expand All")
        self.expand_all_button.setObjectName("secondaryButton")
        self.expand_all_button.setToolTip("Expand every grouped process row.")
        self.expand_all_button.clicked.connect(self.expand_all_groups)
        self.collapse_all_button = QPushButton("Collapse All")
        self.collapse_all_button.setObjectName("secondaryButton")
        self.collapse_all_button.setToolTip("Collapse every grouped process row.")
        self.collapse_all_button.clicked.connect(self.collapse_all_groups)
        action_row.addWidget(self.end_btn)
        action_row.addWidget(self.expand_all_button)
        action_row.addWidget(self.collapse_all_button)
        action_row.addStretch()
        bottom_layout.addLayout(action_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        bottom_layout.addWidget(self.status_label)

        self.notice_label = QLabel("")
        self.notice_label.setObjectName("statusLabel")
        self.notice_label.setWordWrap(True)
        bottom_layout.addWidget(self.notice_label)

        self.vertical_splitter.addWidget(bottom_panel)
        self.vertical_splitter.setStretchFactor(0, 7)
        self.vertical_splitter.setStretchFactor(1, 1)
        self.vertical_splitter.setSizes([760, 120])
        layout.addWidget(self.vertical_splitter)

        self.setLayout(layout)

        self.process_manager = ProcessManager()
        self.filter_text = ""
        self.current_groups = []
        self.latest_groups = []
        self.latest_summary = {
            "cpu_display": "CPU: 0.0%",
            "memory_display": "Physical Memory: 0.0%",
            "disk_active_time_display": "Disk Active Time: 0.0%",
            "disk_active_time_percent": 0.0,
            "gpu_temp_display": "GPU Temp: N/A",
        }
        self.icon_provider = QFileIconProvider()
        self.default_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self.icon_cache = {}
        self.settings = QSettings("CodexTaskManager", "TaskManagerClone")
        self.entry_detail_cache = {}
        self.entry_detail_cache_ttl = 8.0
        self.entry_history = {}
        self._visible_entry_ids = set()
        self._runtime_paused = False
        self._column_labels = [
            "Name",
            "Type",
            "Publisher",
            "Window",
            "PID / Count",
            "CPU %",
            "Memory %",
            "Disk",
        ]
        self._active = True
        self._refresh_profile_name = DEFAULT_REFRESH_PROFILE
        self._low_overhead_mode = False
        self._compact_mode = False
        self._timer_interval_ms = REFRESH_PROFILES[DEFAULT_REFRESH_PROFILE]["processes_timer_ms"]
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self._resume_refresh)
        self._icon_refresh_timer = QTimer(self)
        self._icon_refresh_timer.setSingleShot(True)
        self._icon_refresh_timer.timeout.connect(self._refresh_visible_icons)

        self.refresh_thread = QThread(self)
        self.refresh_worker = ProcessRefreshWorker()
        self.refresh_worker.moveToThread(self.refresh_thread)
        self.request_refresh.connect(self.refresh_worker.refresh)
        self.request_force_refresh.connect(self.refresh_worker.force_refresh)
        self.refresh_worker.snapshot_ready.connect(self._handle_snapshot)
        self.refresh_thread.start()
        self.refresh_worker._full_refresh_interval_s = REFRESH_PROFILES[DEFAULT_REFRESH_PROFILE][
            "processes_full_s"
        ]

        self.timer = QTimer()
        self.timer.timeout.connect(self.request_refresh.emit)
        if self._timer_interval_ms > 0:
            self.timer.start(self._timer_interval_ms)

        self.tree.selectionModel().selectionChanged.connect(lambda *_: self.on_select())
        self.tree.pressed.connect(self._on_tree_pressed)
        self.tree.expanded.connect(self._on_item_expanded)
        self.tree.collapsed.connect(self._on_tree_collapsed)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.header().sectionPressed.connect(self._on_header_pressed)
        self.tree.header().sortIndicatorChanged.connect(self._save_sort_settings)
        self.tree.header().sectionResized.connect(self._save_header_state)
        self.tree.header().sectionMoved.connect(self._save_header_state)
        self.tree.header().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.header().customContextMenuRequested.connect(self._show_column_chooser)
        self.content_splitter.splitterMoved.connect(self._save_splitter_state)
        self.vertical_splitter.splitterMoved.connect(self._save_vertical_splitter_state)
        self.tree.verticalScrollBar().valueChanged.connect(self._schedule_visible_row_refresh)
        self.tree.horizontalScrollBar().valueChanged.connect(self._schedule_visible_row_refresh)
        self.info_panel.open_button.clicked.connect(self._open_selected_location)
        self.info_panel.search_button.clicked.connect(self._search_selected_entry)
        self.info_panel.service_button.pressed.connect(self._go_to_selected_service)
        self._restore_sort_settings()
        self._restore_header_state()
        self._restore_column_visibility()
        self._restore_splitter_state()
        self._restore_vertical_splitter_state()
        self._install_clear_filters(self)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.info_panel.set_entry(None, self._blank_extra_details(), None)
        self.request_refresh.emit()

    def mousePressEvent(self, event):
        if not self._preserves_selection_click(self, event):
            self.clear_selection()
        super().mousePressEvent(event)

    def update_tree(self):
        self._rebuild_tree()

    @Slot(object, object, bool, float)
    def _handle_snapshot(self, groups, summary, full_refresh, updated_at):
        self.latest_summary = summary
        self._apply_summary(summary, full_refresh, updated_at)
        if not full_refresh:
            return

        self.latest_groups = groups or []
        self._update_entry_histories(self.latest_groups)
        self._rebuild_tree()

    def _rebuild_tree(self):
        selected_entry_id = self._selected_entry_id()
        expanded_keys = self._expanded_group_keys()
        vertical_scroll = self.tree.verticalScrollBar().value()
        horizontal_scroll = self.tree.horizontalScrollBar().value()
        self.current_groups = self._filter_groups(self.latest_groups)
        load_children_keys = set(expanded_keys)
        if self.filter_text:
            load_children_keys.update(group["group_key"] for group in self.current_groups)
        self.tree.setUpdatesEnabled(False)
        try:
            self.model.set_groups(
                self.current_groups,
                expanded_group_keys=load_children_keys,
                load_all_children=bool(self.filter_text),
            )
            self._apply_expansion_state(expanded_keys)
            self._restore_selection(selected_entry_id)
        finally:
            self.tree.setUpdatesEnabled(True)
        self.tree.verticalScrollBar().setValue(vertical_scroll)
        self.tree.horizontalScrollBar().setValue(horizontal_scroll)
        self.on_select()
        self._update_notice_state(self.current_groups)
        self._emit_page_status()
        self._schedule_visible_row_refresh()

    def on_select(self):
        entry = self._selected_entry()
        if entry is None:
            self.end_btn.setEnabled(False)
            self.info_panel.set_entry(None, self._blank_extra_details(), None)
            return

        self.end_btn.setEnabled(not entry["is_protected"])
        self.info_panel.set_entry(
            entry,
            self._entry_additional_details(entry),
            self.entry_history.get(entry["id"]),
        )

    def end_task(self):
        entry = self._selected_entry()
        if entry is None:
            return

        if not self._confirm_end_task(entry):
            return

        try:
            self.process_manager.terminate_processes(entry["pids"])
            self.status_label.setText(f"Sent terminate signal to {entry['name']}.")
        except ProcessTerminationBlockedError as error:
            self.status_label.setText(str(error))
        except Exception as error:
            self.status_label.setText(f"Error ending {entry['name']}: {error}")

    def set_filter_text(self, text):
        self.filter_text = text.strip().lower()
        self._rebuild_tree()

    def clear_selection(self):
        self.tree.selectionModel().clearSelection()
        self.tree.setCurrentIndex(QModelIndex())
        self.on_select()

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
                self.request_refresh.emit()
            return

        self._resume_timer.stop()
        self.timer.stop()

    def pause_refresh_temporarily(self, duration_ms=450):
        if not self._active or self._runtime_paused:
            return
        self.timer.stop()
        self._resume_timer.start(duration_ms)

    def shutdown(self):
        self._icon_refresh_timer.stop()
        self._resume_timer.stop()
        self.timer.stop()
        if self.refresh_thread.isRunning():
            self.refresh_thread.quit()
            self.refresh_thread.wait(1000)

    def set_runtime_paused(self, paused):
        paused = bool(paused)
        if paused == self._runtime_paused:
            return
        self._runtime_paused = paused
        self._resume_timer.stop()
        self.timer.stop()
        if not paused and self._active and self._timer_interval_ms > 0:
            self.timer.start(self._timer_interval_ms)
            if self._refresh_profile_name != "Paused":
                self.request_refresh.emit()

    def set_refresh_profile(self, profile_name):
        config = REFRESH_PROFILES.get(profile_name, REFRESH_PROFILES[DEFAULT_REFRESH_PROFILE])
        self._refresh_profile_name = profile_name if profile_name in REFRESH_PROFILES else DEFAULT_REFRESH_PROFILE
        self._timer_interval_ms = config["processes_timer_ms"]
        self.refresh_worker._full_refresh_interval_s = config["processes_full_s"]
        self.refresh_worker.set_refresh_profile(self._refresh_profile_name)
        self.process_manager.set_refresh_profile(self._refresh_profile_name)
        self._resume_timer.stop()
        self.timer.stop()
        if self._active and not self._runtime_paused and self._timer_interval_ms > 0:
            self.timer.start(self._timer_interval_ms)
            self.request_refresh.emit()

    def set_low_overhead_mode(self, enabled):
        self._low_overhead_mode = bool(enabled)
        self.refresh_worker.set_low_overhead_mode(enabled)
        self.process_manager.set_low_overhead_mode(enabled)

    def set_compact_mode(self, enabled):
        self._compact_mode = bool(enabled)
        self.tree.setProperty("compactMode", self._compact_mode)
        self.tree.header().setProperty("compactMode", self._compact_mode)
        self.tree.setIconSize(QSize(16, 16) if self._compact_mode else QSize(18, 18))
        self._repolish_widget(self.tree)
        self._repolish_widget(self.tree.header())

    def refresh_now(self):
        if self._runtime_paused:
            return
        self.request_force_refresh.emit()

    def trigger_primary_action(self):
        if self._selected_entry() is not None:
            self.end_task()

    def expand_all_groups(self):
        self.pause_refresh_temporarily(1200)
        for group in self.current_groups:
            self.model.ensure_group_children_loaded(group["id"])
        self.tree.expandAll()

    def collapse_all_groups(self):
        self.pause_refresh_temporarily(900)
        self.tree.collapseAll()

    def footer_counts(self):
        visible_groups = self.current_groups if self.current_groups else self._filter_groups(self.latest_groups)
        counts = {"apps": 0, "background": 0, "windows": 0}
        for entry in visible_groups:
            normalized_type = (entry.get("type_display") or "").strip().lower()
            if normalized_type == "app":
                counts["apps"] += 1
            elif normalized_type == "windows process":
                counts["windows"] += 1
            else:
                counts["background"] += 1
        return counts

    def pause_for_menu_open(self):
        if not self._active:
            return
        self.timer.stop()
        self._resume_timer.stop()

    def resume_after_menu_close(self, delay_ms=250):
        if not self._active:
            return
        self._resume_timer.start(delay_ms)

    def _update_entry_histories(self, groups):
        active_ids = set()
        for group in groups:
            active_ids.add(group["id"])
            history = self._history_bucket(group["id"])
            history["cpu"].append(group["cpu_percent"])
            history["memory"].append(group["memory_percent"])
            history["disk"].append(disk_intensity_from_rate(group["disk_mb_per_sec"]) * 100.0)

            for child in group.get("children", []):
                active_ids.add(child["id"])
                child_history = self._history_bucket(child["id"])
                child_history["cpu"].append(child["cpu_percent"])
                child_history["memory"].append(child["memory_percent"])
                child_history["disk"].append(
                    disk_intensity_from_rate(child["disk_rate_mb_per_sec"]) * 100.0
                )

        stale_ids = [entry_id for entry_id in self.entry_history if entry_id not in active_ids]
        for entry_id in stale_ids:
            self.entry_history.pop(entry_id, None)

    def _history_bucket(self, entry_id):
        bucket = self.entry_history.get(entry_id)
        if bucket is not None:
            return bucket
        bucket = {
            "cpu": deque(maxlen=24),
            "memory": deque(maxlen=24),
            "disk": deque(maxlen=24),
        }
        self.entry_history[entry_id] = bucket
        return bucket

    def _repolish_widget(self, widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _update_group_item(self, item, group):
        display_name = group["name"]
        if group["process_count"] > 1:
            display_name = f"{display_name} ({group['process_count']})"

        self._set_item_text(item, 0, display_name)
        self._set_item_text(item, 1, group["type_display"])
        self._set_item_text(item, 2, group["publisher"])
        self._set_item_text(item, 3, group["window_display"])
        self._set_item_text(item, 4, str(group["process_count"]))
        self._set_item_text(item, 5, group["cpu_display"])
        self._set_item_text(item, 6, group["memory_display"])
        self._set_item_text(item, 7, group["disk_display"])
        item.setIcon(0, self._icon_for_entry(group))
        self._set_item_data(item, 6, SECONDARY_TEXT_ROLE, group["memory_value_display"])

        self._set_sort_values(
            item,
            {
                0: group["name"].lower(),
                1: group["type_display"].lower(),
                2: group["publisher"].lower(),
                3: (0 if group["has_window"] else 1, group["window_display"].lower()),
                4: group["process_count"],
                5: group["cpu_percent"],
                6: group["memory_percent"],
                7: group["disk_mb_per_sec"],
            },
        )
        self._set_item_data(item, 0, ENTRY_ROLE, group)
        self._set_item_data(item, 0, ENTRY_ID_ROLE, group["id"])
        self._set_item_data(item, 0, ENTRY_KIND_ROLE, "group")
        if group["children"]:
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
        else:
            item.setChildIndicatorPolicy(
                QTreeWidgetItem.ChildIndicatorPolicy.DontShowIndicatorWhenChildless
            )

        name_tooltips = []
        if group["is_protected"]:
            name_tooltips.append("Protected process group: cannot be ended from this app.")
        if group["exe_path"]:
            name_tooltips.append(group["exe_path"])
        if name_tooltips:
            self._set_item_tooltip(item, 0, "\n".join(name_tooltips))
        else:
            self._set_item_tooltip(item, 0, "")
        self._set_item_tooltip(item, 3, group["window_tooltip"])
        self._set_item_tooltip(item, 6, group["memory_tooltip"])
        self._set_item_tooltip(item, 7, group["disk_tooltip"])

    def _update_child_item(self, item, child):
        self._set_item_text(item, 0, child["name"])
        self._set_item_text(item, 1, child["type_display"])
        self._set_item_text(item, 2, child["publisher"])
        self._set_item_text(item, 3, child["window_display"])
        self._set_item_text(item, 4, str(child["pid"]))
        self._set_item_text(item, 5, child["cpu_display"])
        self._set_item_text(item, 6, child["memory_display"])
        self._set_item_text(item, 7, child["disk_display"])
        item.setIcon(0, self._icon_for_entry(child))
        self._set_item_data(item, 6, SECONDARY_TEXT_ROLE, child["memory_value_display"])

        self._set_sort_values(
            item,
            {
                0: child["name"].lower(),
                1: child["type_display"].lower(),
                2: child["publisher"].lower(),
                3: (0 if child["has_window"] else 1, child["window_display"].lower()),
                4: child["pid"],
                5: child["cpu_percent"],
                6: child["memory_percent"],
                7: child["disk_rate_mb_per_sec"],
            },
        )
        self._set_item_data(item, 0, ENTRY_ROLE, child)
        self._set_item_data(item, 0, ENTRY_ID_ROLE, child["id"])
        self._set_item_data(item, 0, ENTRY_KIND_ROLE, "child")

        name_tooltips = []
        if child["is_protected"]:
            name_tooltips.append("Protected process: cannot be ended from this app.")
        if child["exe_path"]:
            name_tooltips.append(child["exe_path"])
        if name_tooltips:
            self._set_item_tooltip(item, 0, "\n".join(name_tooltips))
        else:
            self._set_item_tooltip(item, 0, "")
        self._set_item_tooltip(item, 3, child["window_tooltip"])
        self._set_item_tooltip(item, 6, child["memory_tooltip"])
        self._set_item_tooltip(item, 7, child["disk_tooltip"])

    def _filter_groups(self, groups):
        if not self.filter_text:
            return groups

        filtered_groups = []
        for group in groups:
            if self._entry_matches_filter(group):
                filtered_groups.append(group)
                continue

            matching_children = [
                child for child in group["children"] if self._entry_matches_filter(child)
            ]
            if matching_children:
                filtered_group = dict(group)
                filtered_group["children"] = matching_children
                filtered_groups.append(filtered_group)

        return filtered_groups

    def _entry_matches_filter(self, entry):
        searchable = [
            entry["name"].lower(),
            entry["type_display"].lower(),
            entry["publisher"].lower(),
            entry.get("description", "").lower(),
            entry.get("product_name", "").lower(),
            entry["window_display"].lower(),
            entry["exe_path"].lower(),
        ]
        if "pid" in entry:
            searchable.append(str(entry["pid"]))
        if "pids" in entry:
            searchable.append(" ".join(str(pid) for pid in entry["pids"]))
        return any(self.filter_text in value for value in searchable)

    def _selected_entry(self):
        index = self.tree.currentIndex()
        if not index.isValid():
            return None
        return index.data(ENTRY_ROLE)

    def _selected_entry_id(self):
        entry = self._selected_entry()
        if entry is None:
            return None
        return entry["id"]

    def _restore_selection(self, entry_id):
        if entry_id is None:
            return
        index = self.model.index_for_entry_id(entry_id)
        if not index.isValid():
            group_id = self._group_id_for_entry_id(entry_id)
            if group_id:
                group_index = self.model.index_for_entry_id(group_id)
                if group_index.isValid():
                    index = group_index
        if index.isValid():
            self.tree.setCurrentIndex(index)

    def _expanded_group_keys(self):
        expanded_keys = set()
        for group in self.current_groups:
            index = self.model.index_for_entry_id(group["id"])
            if index.isValid() and self.tree.isExpanded(index):
                expanded_keys.add(group["group_key"])
        return expanded_keys

    def _icon_for_entry(self, entry):
        exe_path = entry["exe_path"]
        cache_key = self._icon_cache_key(entry)
        cached_icon = self.icon_cache.get(cache_key)
        if cached_icon is not None:
            return cached_icon

        if entry["id"] not in self._visible_entry_ids:
            return self.default_icon

        icon = self.default_icon
        if exe_path:
            file_info = QFileInfo(exe_path)
            if file_info.exists():
                icon = self.icon_provider.icon(file_info)

        self.icon_cache[cache_key] = icon
        return icon

    def _icon_cache_key(self, entry):
        return entry["exe_path"] or entry["name"].lower()

    def _compute_visible_entry_ids(self):
        ids = set()
        if self.tree.viewport().height() <= 0:
            return ids
        step = max(self.tree.sizeHintForRow(0), 24)
        x = max(12, min(self.tree.viewport().width() // 3, 80))
        for y in range(0, self.tree.viewport().height(), step):
            index = self.tree.indexAt(QPoint(x, y))
            if index.isValid():
                entry_id = index.data(ENTRY_ID_ROLE)
                if entry_id:
                    ids.add(entry_id)
        current_index = self.tree.currentIndex()
        if current_index.isValid():
            entry_id = current_index.data(ENTRY_ID_ROLE)
            if entry_id:
                ids.add(entry_id)
        return ids

    def _schedule_visible_row_refresh(self, *_args):
        if not self._icon_refresh_timer.isActive():
            self._icon_refresh_timer.start(0)

    def _refresh_visible_icons(self):
        self._visible_entry_ids = self._compute_visible_entry_ids()
        for entry_id in self._visible_entry_ids:
            index = self.model.index_for_entry_id(entry_id)
            if not index.isValid():
                continue
            entry = index.data(ENTRY_ROLE)
            if entry is None:
                continue
            if self._icon_cache_key(entry) in self.icon_cache:
                continue
            self._icon_for_entry(entry)
            self.model.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def _set_sort_values(self, item, values):
        for column, value in values.items():
            self._set_item_data(item, column, SORT_ROLE, value)

    def _set_item_text(self, item, column, value):
        if item.text(column) != value:
            item.setText(column, value)

    def _set_item_data(self, item, column, role, value):
        if item.data(column, role) != value:
            item.setData(column, role, value)

    def _set_item_tooltip(self, item, column, value):
        if item.toolTip(column) != value:
            item.setToolTip(column, value)

    def _top_level_items_by_id(self):
        items = {}
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            items[item.data(0, ENTRY_ID_ROLE)] = item
        return items

    def _child_items_by_id(self, group_item):
        items = {}
        for index in range(group_item.childCount()):
            item = group_item.child(index)
            items[item.data(0, ENTRY_ID_ROLE)] = item
        return items

    def _sync_child_items(self, group_item, children):
        existing_child_items = self._child_items_by_id(group_item)
        visible_child_ids = set()

        for child in children:
            child_item = existing_child_items.get(child["id"])
            if child_item is None:
                child_item = SortableTreeWidgetItem()
                group_item.addChild(child_item)
            self._update_child_item(child_item, child)
            visible_child_ids.add(child["id"])

        for index in range(group_item.childCount() - 1, -1, -1):
            child_item = group_item.child(index)
            if child_item.data(0, ENTRY_ID_ROLE) not in visible_child_ids:
                group_item.takeChild(index)

    def _remove_missing_top_level_items(self, existing_group_items, visible_group_ids):
        for group_id, group_item in existing_group_items.items():
            if group_id in visible_group_ids:
                continue
            index = self.tree.indexOfTopLevelItem(group_item)
            if index >= 0:
                self.tree.takeTopLevelItem(index)

    def _set_header_labels(self, disk_active_percent=0.0):
        self.tree.setHeaderLabels(
            [
                "Name",
                "Type",
                "Publisher",
                "Window",
                "PID / Count",
                "CPU %",
                "Memory %",
                f"Disk ({disk_active_percent:.0f}%)",
            ]
        )

    def _apply_summary(self, summary, full_refresh, updated_at):
        self._set_label_text(self.cpu_summary_label, summary["cpu_display"])
        self._set_label_text(self.memory_summary_label, summary["memory_display"])
        self._set_label_text(self.disk_summary_label, summary["disk_active_time_display"])
        self._set_label_text(self.gpu_summary_label, summary["gpu_temp_display"])
        self.model.set_disk_header_percent(summary["disk_active_time_percent"])
        if full_refresh:
            self._set_label_text(
                self.last_updated_label,
                f"List updated: {self._format_timestamp(updated_at)}",
            )
        self._emit_page_status()

    def _resume_refresh(self):
        if not self._active:
            return
        if self._timer_interval_ms <= 0:
            return
        if not self.timer.isActive():
            self.timer.start(self._timer_interval_ms)
        self.request_refresh.emit()

    def status_refresh_text(self):
        return self.last_updated_label.text()

    def _on_item_expanded(self, index):
        self.pause_refresh_temporarily(1100)
        if index.data(ENTRY_KIND_ROLE) != "group":
            return
        group = index.data(ENTRY_ROLE)
        if not group:
            return
        self.model.ensure_group_children_loaded(group["id"])

    def _on_tree_collapsed(self, _index):
        self.pause_refresh_temporarily(900)
        group = _index.data(ENTRY_ROLE)
        if group and _index.data(ENTRY_KIND_ROLE) == "group":
            self.model.unload_group_children(group["id"])

    def _on_tree_pressed(self, index):
        if not index.isValid():
            return
        self.pause_refresh_temporarily(1100)
        if index.column() != 0 or index.data(ENTRY_KIND_ROLE) != "group":
            return

        group = index.data(ENTRY_ROLE)
        if not group or not group.get("children"):
            return
        self.model.ensure_group_children_loaded(group["id"])

    def _on_header_pressed(self, _section):
        self.pause_refresh_temporarily(1100)

    def _exec_menu_with_refresh_pause(self, menu, global_pos):
        self.pause_for_menu_open()
        try:
            return menu.exec(global_pos)
        finally:
            self.resume_after_menu_close()

    def _format_timestamp(self, timestamp):
        if not timestamp:
            return "--"
        return time.strftime("%I:%M:%S %p", time.localtime(timestamp)).lstrip("0")

    def _set_label_text(self, label, value):
        if label.text() != value:
            label.setText(value)

    def _show_context_menu(self, position):
        index = self.tree.indexAt(position)
        if not index.isValid():
            return

        self.tree.setCurrentIndex(index)
        entry = index.data(ENTRY_ROLE)
        if entry is None:
            return

        menu = QMenu(self)
        end_task_action = menu.addAction("End Task")
        end_task_action.setEnabled(not entry["is_protected"])
        end_tree_action = menu.addAction("End Process Tree")
        end_tree_action.setEnabled(not entry["is_protected"])
        menu.addSeparator()
        open_location_action = menu.addAction("Open File Location")
        search_action = menu.addAction("Search Online")
        menu.addSeparator()
        go_to_details_action = menu.addAction("Go to Details")
        go_to_service_action = menu.addAction("Go to Service")
        go_to_service_action.setEnabled(bool(entry.get("primary_service_name")))
        copy_details_action = menu.addAction("Copy Details")
        copy_path_action = menu.addAction("Copy Path")
        copy_pid_action = menu.addAction("Copy PID")

        chosen_action = self._exec_menu_with_refresh_pause(
            menu,
            self.tree.viewport().mapToGlobal(position),
        )
        if chosen_action == end_task_action:
            self.end_task()
        elif chosen_action == end_tree_action:
            self.end_process_tree()
        elif chosen_action == open_location_action:
            self._open_entry_location(entry)
        elif chosen_action == search_action:
            search_online(entry.get("search_query") or entry["name"])
        elif chosen_action == go_to_details_action:
            self.go_to_details_requested.emit(entry)
        elif chosen_action == go_to_service_action:
            self.go_to_service_requested.emit(entry["primary_service_name"])
        elif chosen_action == copy_details_action:
            self._copy_entry_details(entry)
        elif chosen_action == copy_path_action:
            self._copy_entry_path(entry)
        elif chosen_action == copy_pid_action:
            self._copy_entry_pids(entry)

    def show_column_chooser(self, global_pos=None):
        self._show_column_chooser(None, global_pos)

    def _show_column_chooser(self, _position=None, global_pos=None):
        menu = QMenu(self)
        for column, label in enumerate(self._column_labels):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not self.tree.isColumnHidden(column))
            if column == 0:
                action.setEnabled(False)
            else:
                action.toggled.connect(
                    lambda checked, col=column: self._set_column_visible(col, checked)
                )

        if global_pos is None:
            global_pos = self.tree.header().mapToGlobal(self.tree.header().rect().bottomLeft())
        self._exec_menu_with_refresh_pause(menu, global_pos)

    def _set_column_visible(self, column, visible):
        self.tree.setColumnHidden(column, not visible)
        self.settings.setValue(f"processes/column_hidden_{column}", not visible)

    def _restore_column_visibility(self):
        for column in range(1, len(self._column_labels)):
            hidden = self.settings.value(f"processes/column_hidden_{column}", False, type=bool)
            self.tree.setColumnHidden(column, hidden)

    def _save_header_state(self, *_args):
        self.settings.setValue("processes/header_state", self.tree.header().saveState())

    def _restore_header_state(self):
        state = self.settings.value("processes/header_state")
        if state and self.tree.header().restoreState(state):
            return
        header = self.tree.header()
        default_widths = {
            0: 300,
            1: 145,
            2: 180,
            3: 250,
            4: 100,
            5: 95,
            6: 120,
            7: 110,
        }
        for column, width in default_widths.items():
            header.resizeSection(column, width)

    def _save_splitter_state(self, *_args):
        self.settings.setValue("processes/content_splitter_state", self.content_splitter.saveState())

    def _restore_splitter_state(self):
        state = self.settings.value("processes/content_splitter_state")
        if state and self.content_splitter.restoreState(state):
            return
        self.content_splitter.setSizes([1280, 440])

    def _save_vertical_splitter_state(self, *_args):
        self.settings.setValue("processes/vertical_splitter_state", self.vertical_splitter.saveState())

    def _restore_vertical_splitter_state(self):
        state = self.settings.value("processes/vertical_splitter_state")
        if state and self.vertical_splitter.restoreState(state):
            return
        self.vertical_splitter.setSizes([760, 120])

    def _emit_page_status(self):
        if not self.current_groups:
            self.page_status_changed.emit("Processes: no matching items")
            return

        app_count = sum(1 for group in self.current_groups if group["type_display"] == "App")
        background_count = sum(
            1 for group in self.current_groups if group["type_display"] == "Background process"
        )
        windows_count = sum(
            1 for group in self.current_groups if group["type_display"] == "Windows process"
        )
        total_children = sum(group["process_count"] for group in self.current_groups)
        self.page_status_changed.emit(
            (
                f"Processes: {len(self.current_groups)} groups | "
                f"Apps {app_count} | Background {background_count} | "
                f"Windows {windows_count} | PIDs {total_children}"
            )
        )

    def _update_notice_state(self, groups):
        if not groups:
            if self.filter_text:
                self.notice_label.setText("No matching processes for the current filter.")
            else:
                self.notice_label.setText("No processes are currently available.")
            return

        limited_count = sum(
            1 for group in groups if not group.get("exe_path") or group.get("location_reason")
        )
        if limited_count:
            self.notice_label.setText(
                f"{limited_count} process groups have limited metadata or file-path access due to Windows restrictions."
            )
            return

        self.notice_label.setText("")

    def _entry_additional_details(self, entry):
        cached = self.entry_detail_cache.get(entry["id"])
        if cached is not None and time.time() - cached["time"] < self.entry_detail_cache_ttl:
            return dict(cached["data"])

        extra = self._blank_extra_details()
        primary_pid = None
        if "pid" in entry:
            primary_pid = entry["pid"]
        elif entry.get("pids"):
            primary_pid = entry["pids"][0]

        if primary_pid is None:
            self.entry_detail_cache[entry["id"]] = {"time": time.time(), "data": dict(extra)}
            return extra

        try:
            process = psutil.Process(primary_pid)
        except Exception:
            self.entry_detail_cache[entry["id"]] = {"time": time.time(), "data": dict(extra)}
            return extra

        try:
            extra["user"] = process.username()
        except Exception:
            extra["user"] = "Unavailable"

        try:
            create_time = process.create_time()
            extra["started"] = time.strftime("%Y-%m-%d %I:%M:%S %p", time.localtime(create_time))
        except Exception:
            extra["started"] = "Unavailable"

        try:
            extra["threads"] = str(process.num_threads())
        except Exception:
            extra["threads"] = "Unavailable"

        extra["startup_display"] = self.process_manager.startup_display_for_entry(entry)

        self.entry_detail_cache[entry["id"]] = {"time": time.time(), "data": dict(extra)}
        return extra

    def _blank_extra_details(self):
        return {
            "user": "--",
            "started": "--",
            "threads": "--",
            "startup_display": "Not listed",
        }

    def _open_selected_location(self):
        entry = self.info_panel.current_entry or self._selected_entry()
        if entry:
            self._open_entry_location(entry)

    def _search_selected_entry(self):
        entry = self.info_panel.current_entry or self._selected_entry()
        if entry:
            search_online(entry.get("search_query") or entry["name"])

    def _go_to_selected_service(self):
        entry = self.info_panel.current_entry or self._selected_entry()
        if not entry or not entry.get("primary_service_name"):
            self.status_label.setText("No linked Windows service is available for this selection.")
            return
        self.go_to_service_requested.emit(entry["primary_service_name"])

    def _open_entry_location(self, entry):
        success, message = open_file_location(entry.get("exe_path"))
        if not success:
            self.status_label.setText(message or "Could not open a file location for this item.")
            return
        self.status_label.setText("")

    def _copy_entry_details(self, entry):
        extra = self._entry_additional_details(entry)
        lines = [
            f"Name: {entry['name']}",
            f"Type: {entry['type_display']}",
            f"Protection: {entry.get('protection_reason') or ('Protected' if entry.get('is_protected') else 'Normal')}",
            f"Service: {entry.get('service_display') or 'None'}",
            f"Startup: {extra.get('startup_display') or entry.get('startup_display') or 'Not listed'}",
            f"Publisher: {entry['publisher']}",
            f"Path: {entry.get('exe_path') or entry.get('location_reason') or 'Unavailable'}",
            f"Window: {entry['window_display']}",
            f"PIDs: {', '.join(str(pid) for pid in entry.get('pids', []))}",
            f"CPU: {entry['cpu_display']}",
            f"Memory: {entry['memory_display']} {entry['memory_value_display']}",
            f"Disk: {entry['disk_display']}",
            f"Product: {entry.get('product_name') or 'Unknown'}",
            f"Description: {entry.get('description') or 'Unknown'}",
            f"User: {extra['user']}",
            f"Started: {extra['started']}",
            f"Threads: {extra['threads']}",
        ]
        if copy_text_to_clipboard("\n".join(lines)):
            self.status_label.setText(f"Copied details for {entry['name']}.")

    def _copy_entry_path(self, entry):
        text = entry.get("exe_path") or entry.get("location_reason") or "Unavailable"
        if copy_text_to_clipboard(text):
            self.status_label.setText(f"Copied path for {entry['name']}.")

    def _copy_entry_pids(self, entry):
        text = ", ".join(str(pid) for pid in entry.get("pids", [])) or "Unavailable"
        if copy_text_to_clipboard(text):
            self.status_label.setText(f"Copied PID information for {entry['name']}.")

    def export_csv(self):
        headers, rows = self._csv_rows()
        success, file_path = export_rows_to_csv(
            self,
            f"task-manager-processes-{time.strftime('%Y%m%d-%H%M%S')}.csv",
            headers,
            rows,
        )
        if success:
            self.status_label.setText(f"Exported Processes to {file_path}")
            return True
        return False

    def _csv_rows(self):
        visible_columns = [
            column for column in range(len(self._column_labels)) if not self.tree.isColumnHidden(column)
        ]
        headers = [self.model.headerData(column, Qt.Orientation.Horizontal) for column in visible_columns]
        rows = []
        for group in self.current_groups:
            rows.append(self._csv_row_for_entry(group, visible_columns, is_child=False))
            group_index = self.model.index_for_entry_id(group["id"])
            if self.filter_text or (group_index.isValid() and self.tree.isExpanded(group_index)):
                for child in group["children"]:
                    rows.append(self._csv_row_for_entry(child, visible_columns, is_child=True))
        return headers, rows

    def _csv_row_for_entry(self, entry, visible_columns, is_child):
        values = []
        for column in visible_columns:
            if column == 0:
                name = entry["name"]
                if "process_count" in entry and entry["process_count"] > 1:
                    name = f"{name} ({entry['process_count']})"
                if is_child:
                    name = f"  {name}"
                values.append(name)
            elif column == 1:
                values.append(entry["type_display"])
            elif column == 2:
                values.append(entry["publisher"])
            elif column == 3:
                values.append(entry["window_display"])
            elif column == 4:
                if "pid" in entry:
                    values.append(str(entry["pid"]))
                else:
                    values.append(str(entry["process_count"]))
            elif column == 5:
                values.append(entry["cpu_display"])
            elif column == 6:
                values.append(f"{entry['memory_display']} {entry['memory_value_display']}")
            elif column == 7:
                values.append(entry["disk_display"])
        return values

    def _confirm_end_task(self, entry):
        if entry["is_protected"]:
            return False

        if len(entry["pids"]) <= 1:
            return True

        response = QMessageBox.question(
            self,
            "End Task",
            (
                f"End task for {entry['name']}?\n\n"
                f"This will terminate {len(entry['pids'])} grouped processes."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return response == QMessageBox.StandardButton.Yes

    def end_process_tree(self):
        entry = self._selected_entry()
        if entry is None or entry["is_protected"]:
            return

        response = QMessageBox.question(
            self,
            "End Process Tree",
            (
                f"End process tree for {entry['name']}?\n\n"
                "This will terminate the selected process or processes and their descendants."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return

        try:
            self.process_manager.terminate_process_tree(entry["pids"])
            self.status_label.setText(f"Sent terminate signal to the process tree for {entry['name']}.")
        except ProcessTerminationBlockedError as error:
            self.status_label.setText(str(error))
        except Exception as error:
            self.status_label.setText(f"Error ending the process tree for {entry['name']}: {error}")

    def _restore_sort_settings(self):
        column = int(self.settings.value("processes/sort_column", 5))
        descending = self.settings.value("processes/sort_descending", True, type=bool)
        order = Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder
        self.tree.sortByColumn(column, order)

    def _save_sort_settings(self, column, order):
        self.settings.setValue("processes/sort_column", column)
        self.settings.setValue(
            "processes/sort_descending",
            order == Qt.SortOrder.DescendingOrder,
        )

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if self._preserves_selection_click(watched, event):
                return super().eventFilter(watched, event)
            if watched is not self.tree and not self.tree.isAncestorOf(watched):
                self.clear_selection()
        return super().eventFilter(watched, event)

    def _preserves_selection_click(self, watched, event=None):
        if watched in (
            self.end_btn,
            self.expand_all_button,
            self.collapse_all_button,
            self.info_panel.open_button,
            self.info_panel.search_button,
            self.info_panel.service_button,
            self.info_scroll,
            self.info_panel,
            self.content_splitter,
        ):
            return True
        if isinstance(watched, QSplitterHandle):
            return True
        if self.info_scroll.isAncestorOf(watched):
            return True
        if self.content_splitter.isAncestorOf(watched) and watched is not self.tree and not self.tree.isAncestorOf(watched):
            return True
        if event is not None:
            click_global_pos = self._global_click_position(watched, event)
            if click_global_pos is not None and self._is_point_inside_widget(self.info_scroll, click_global_pos):
                return True
        return False

    def _global_click_position(self, watched, event):
        local_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if not isinstance(local_pos, QPoint) or watched is None:
            return None
        try:
            return watched.mapToGlobal(local_pos)
        except Exception:
            return None

    def _is_point_inside_widget(self, widget, global_pos):
        if widget is None or global_pos is None or not widget.isVisible():
            return False
        return QRect(widget.mapToGlobal(QPoint(0, 0)), widget.size()).contains(global_pos)

    def _apply_expansion_state(self, expanded_keys):
        for group in self.current_groups:
            index = self.model.index_for_entry_id(group["id"])
            if not index.isValid():
                continue
            if self.filter_text or group["group_key"] in expanded_keys:
                self.tree.expand(index)
            else:
                self.tree.collapse(index)

    def _group_key_for_entry_id(self, entry_id):
        if entry_id is None:
            return None
        for group in self.current_groups:
            if group["id"] == entry_id:
                return group["group_key"]
            if any(child["id"] == entry_id for child in group["children"]):
                return group["group_key"]
        return None

    def _group_id_for_entry_id(self, entry_id):
        if entry_id is None:
            return None
        for group in self.current_groups:
            if group["id"] == entry_id:
                return group["id"]
            if any(child["id"] == entry_id for child in group["children"]):
                return group["id"]
        return None

    def _install_clear_filters(self, widget):
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)
