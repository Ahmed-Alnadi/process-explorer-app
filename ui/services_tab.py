import time

from PySide6.QtCore import QEvent, QFileInfo, QModelIndex, QObject, QPoint, QRect, QSettings, QSize, Qt, QThread, QTimer, Signal, Slot
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
    QStyle,
    QVBoxLayout,
    QWidget,
)

from core.refresh_profiles import DEFAULT_REFRESH_PROFILE, REFRESH_PROFILES
from core.service_manager import ServiceActionBlockedError, ServiceManager
from ui.export_utils import export_rows_to_csv
from ui.flat_entry_model import EntryFilterProxyModel, FlatEntryTableModel
from ui.heatmap_utils import protected_heat_brush, status_heat_brush
from ui.process_actions import copy_text_to_clipboard, open_file_location, search_online
from ui.process_tree_model import ClearableTreeView
from ui.processes_tab import ENTRY_ID_ROLE, ENTRY_KIND_ROLE, ENTRY_ROLE, SECONDARY_TEXT_ROLE, SORT_ROLE


class ServiceInfoPanel(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("sidePanel")
        self.setProperty("protectedState", False)
        self.current_entry = None
        self._value_labels = {}

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

        self.subtitle_label = QLabel("Pick a service to inspect it.")
        self.subtitle_label.setObjectName("sidePanelSubtitle")
        self.subtitle_label.setWordWrap(True)
        layout.addWidget(self.subtitle_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        layout.addLayout(grid)

        fields = [
            "Service",
            "Display Name",
            "Status",
            "Start Type",
            "PID",
            "Linked Process",
            "User",
            "Publisher",
            "Path",
            "Description",
            "Dependents",
            "Dependency Depth",
            "Protection",
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

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.open_button = QPushButton("Open File Location")
        self.open_button.setObjectName("secondaryButton")
        self.open_button.setToolTip("Open the folder that contains the service executable.")
        self.search_button = QPushButton("Search Online")
        self.search_button.setObjectName("secondaryButton")
        self.search_button.setToolTip("Search online for the selected Windows service.")
        self.process_button = QPushButton("Go to Process")
        self.process_button.setObjectName("secondaryButton")
        self.process_button.setToolTip("Jump to the running process when this service has one.")
        actions.addWidget(self.open_button)
        actions.addWidget(self.search_button)
        actions.addWidget(self.process_button)
        layout.addLayout(actions)
        layout.addStretch()

    def set_entry(self, entry, dependent_text="--", dependency_depth=None):
        self.current_entry = entry
        if entry is None:
            self.title_label.setText("Selection")
            self.badge_label.hide()
            self.subtitle_label.setText("Pick a service to inspect it.")
            for label in self._value_labels.values():
                label.setText("--")
            self._set_protected_state(False)
            self.open_button.setEnabled(False)
            self.search_button.setEnabled(False)
            self.process_button.setEnabled(False)
            self.open_button.setToolTip("")
            self.search_button.setToolTip("Search online for the selected Windows service.")
            self.process_button.setToolTip("Jump to the running process when this service has one.")
            return

        self.title_label.setText(entry["display_name"])
        if entry.get("is_protected"):
            self.badge_label.show()
            self.subtitle_label.setText("Protected service. Stop and restart are disabled in this app.")
        else:
            self.badge_label.hide()
            self.subtitle_label.setText("On-demand details for the selected service.")
        self._set_protected_state(bool(entry.get("is_protected")))
        values = {
            "Service": entry["name"],
            "Display Name": entry["display_name"],
            "Status": entry["status"],
            "Start Type": entry["start_type"],
            "PID": entry["pid_display"],
            "Linked Process": entry.get("linked_process_display") or "None",
            "User": entry["username"],
            "Publisher": entry.get("publisher") or "Unknown",
            "Path": entry.get("exe_path") or entry.get("binpath") or entry.get("location_reason") or "Unavailable",
            "Description": entry.get("description") or "Unknown",
            "Dependents": dependent_text,
            "Dependency Depth": str(entry.get("dependent_depth", 0) if dependency_depth is None else dependency_depth),
            "Protection": "Protected from stop/restart" if entry.get("is_protected") else "Normal",
        }
        for field, value in values.items():
            self._value_labels[field].setText(value)
        self.open_button.setEnabled(True)
        self.search_button.setEnabled(True)
        self.process_button.setEnabled(True)
        self.open_button.setToolTip(
            entry.get("location_reason") or "Open the folder that contains the service executable."
        )
        self.search_button.setToolTip("Search online for the selected Windows service.")
        self.process_button.setToolTip(
            "Open the linked running process."
            if entry.get("pid")
            else "This service is not linked to a running process right now."
        )

    def _set_protected_state(self, protected):
        protected = bool(protected)
        if self.property("protectedState") == protected:
            return
        self.setProperty("protectedState", protected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


class ServiceRefreshWorker(QObject):
    snapshot_ready = Signal(object, float)

    def __init__(self):
        super().__init__()
        self._manager = ServiceManager()
        self._busy = False

    @Slot()
    def refresh(self):
        if self._busy:
            return

        self._busy = True
        try:
            services = self._manager.list_services()
            self.snapshot_ready.emit(services, time.time())
        finally:
            self._busy = False

    def set_refresh_profile(self, profile_name):
        self._manager.set_refresh_profile(profile_name)

    def set_low_overhead_mode(self, enabled):
        self._manager.set_low_overhead_mode(enabled)


class ServicesTab(QWidget):
    request_refresh = Signal()
    page_status_changed = Signal(str)
    go_to_process_requested = Signal(int)

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout()
        layout.setSpacing(12)

        summary_layout = QHBoxLayout()
        summary_layout.setSpacing(10)
        self.running_label = QLabel("Running: 0")
        self.running_label.setObjectName("metricCard")
        self.stopped_label = QLabel("Stopped: 0")
        self.stopped_label.setObjectName("metricCard")
        self.last_updated_label = QLabel("Services updated: --")
        self.last_updated_label.setObjectName("statusLabel")
        summary_layout.addWidget(self.running_label)
        summary_layout.addWidget(self.stopped_label)
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
        self.tree.setRootIsDecorated(False)
        self.tree.setItemsExpandable(False)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setSortingEnabled(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.header().setMinimumSectionSize(72)
        self.content_splitter.addWidget(self.tree)

        self.info_scroll = QScrollArea()
        self.info_scroll.setObjectName("sidePanelScroll")
        self.info_scroll.setWidgetResizable(True)
        self.info_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.info_panel = ServiceInfoPanel()
        self.info_scroll.setMinimumWidth(320)
        self.info_scroll.setMaximumWidth(16777215)
        self.info_scroll.setWidget(self.info_panel)
        self.content_splitter.addWidget(self.info_scroll)
        self.content_splitter.setStretchFactor(0, 5)
        self.content_splitter.setStretchFactor(1, 2)
        self.content_splitter.setSizes([1240, 430])
        self.vertical_splitter.addWidget(self.content_splitter)

        bottom_panel = QWidget()
        bottom_layout = QVBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)
        bottom_panel.setLayout(bottom_layout)
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
        self.vertical_splitter.setSizes([760, 96])
        layout.addWidget(self.vertical_splitter)

        self.setLayout(layout)

        self.filter_text = ""
        self.latest_services = []
        self.service_manager = ServiceManager()
        self.settings = QSettings("CodexTaskManager", "TaskManagerClone")
        self.icon_provider = QFileIconProvider()
        self.default_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self.icon_cache = {}
        self._visible_entry_ids = set()
        self._visible_entries_cache = []
        self._runtime_paused = False
        self.dependency_cache = {}
        self._column_labels = [
            "Service",
            "Display Name",
            "Status",
            "Start Type",
            "PID",
            "User",
            "Binary Path",
        ]
        self._active = False
        self._refresh_profile_name = DEFAULT_REFRESH_PROFILE
        self._timer_interval_ms = REFRESH_PROFILES[DEFAULT_REFRESH_PROFILE]["services_timer_ms"]
        self._pending_focus_service = None
        self._low_overhead_mode = False
        self._compact_mode = False
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self._resume_refresh)
        self._icon_refresh_timer = QTimer(self)
        self._icon_refresh_timer.setSingleShot(True)
        self._icon_refresh_timer.timeout.connect(self._refresh_visible_icons)

        self.model = FlatEntryTableModel(
            headers=self._column_labels,
            roles={
                "sort": SORT_ROLE,
                "entry": ENTRY_ROLE,
                "entry_id": ENTRY_ID_ROLE,
                "entry_kind": ENTRY_KIND_ROLE,
                "secondary": SECONDARY_TEXT_ROLE,
            },
            display_resolver=self._display_value,
            sort_resolver=self._sort_value,
            filter_resolver=self._filter_text_for_entry,
            icon_resolver=self._icon_for_entry,
            tooltip_resolver=self._tooltip_value,
            background_resolver=self._background_brush,
            tabular_columns={4},
            alignment_columns={4},
            entry_kind="service",
            parent=self,
        )
        self.proxy_model = EntryFilterProxyModel(
            roles={
                "sort": SORT_ROLE,
                "entry": ENTRY_ROLE,
                "entry_id": ENTRY_ID_ROLE,
                "entry_kind": ENTRY_KIND_ROLE,
                "secondary": SECONDARY_TEXT_ROLE,
            },
            parent=self,
        )
        self.proxy_model.setSourceModel(self.model)
        self.tree.setModel(self.proxy_model)

        self.refresh_thread = QThread(self)
        self.refresh_worker = ServiceRefreshWorker()
        self.refresh_worker.moveToThread(self.refresh_thread)
        self.request_refresh.connect(self.refresh_worker.refresh)
        self.refresh_worker.snapshot_ready.connect(self._handle_snapshot)
        self.refresh_thread.start()

        self.timer = QTimer()
        self.timer.timeout.connect(self.request_refresh.emit)
        if self._timer_interval_ms > 0:
            self.timer.start(self._timer_interval_ms)
        self.timer.stop()

        self.tree.selectionModel().selectionChanged.connect(lambda *_: self.on_select())
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
        self.info_panel.process_button.pressed.connect(self._go_to_selected_process)
        self._restore_sort_settings()
        self._restore_header_state()
        self._restore_column_visibility()
        self._restore_splitter_state()
        self._restore_vertical_splitter_state()
        self._install_clear_filters(self)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.info_panel.set_entry(None, "--")

    @Slot(object, float)
    def _handle_snapshot(self, services, updated_at):
        self.latest_services = services
        self._set_label_text(
            self.last_updated_label,
            f"Services updated: {self._format_timestamp(updated_at)}",
        )
        self._sync_view()

    def _sync_view(self):
        selected_id = self._selected_entry_id()
        vertical_scroll = self.tree.verticalScrollBar().value()
        horizontal_scroll = self.tree.horizontalScrollBar().value()
        self._visible_entries_cache = []

        self.tree.setUpdatesEnabled(False)
        try:
            priority_ids = self._priority_entry_ids()
            self.model.sync_entries(
                self.latest_services,
                priority_ids=priority_ids,
                batch_size=max(len(priority_ids) + 24, 96),
            )
            self.proxy_model.set_filter_text(self.filter_text)
            self._restore_selection(selected_id)
        finally:
            self.tree.setUpdatesEnabled(True)

        self.tree.verticalScrollBar().setValue(vertical_scroll)
        self.tree.horizontalScrollBar().setValue(horizontal_scroll)
        self._apply_pending_focus()
        visible_services = self._visible_entries()
        self._visible_entries_cache = visible_services
        self._set_summary_labels(visible_services)
        self.on_select()
        self._update_notice_state(visible_services)
        self._emit_page_status(visible_services)
        self._schedule_visible_row_refresh()

    def on_select(self):
        entry = self._selected_entry()
        if entry is None:
            self.info_panel.set_entry(None, "--")
            return
        dependency_info = self._dependent_info(entry)
        self.info_panel.set_entry(entry, dependency_info["summary"], dependency_info["depth"])

    def set_filter_text(self, text):
        self.filter_text = text.strip().lower()
        self._sync_view()

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

    def pause_for_menu_open(self):
        if not self._active:
            return
        self.timer.stop()
        self._resume_timer.stop()

    def resume_after_menu_close(self, delay_ms=250):
        if not self._active or self._runtime_paused:
            return
        self._resume_timer.start(delay_ms)

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
        self._refresh_profile_name = (
            profile_name if profile_name in REFRESH_PROFILES else DEFAULT_REFRESH_PROFILE
        )
        self._timer_interval_ms = config["services_timer_ms"]
        self.refresh_worker.set_refresh_profile(self._refresh_profile_name)
        self.service_manager.set_refresh_profile(self._refresh_profile_name)
        self._resume_timer.stop()
        self.timer.stop()
        if self._active and not self._runtime_paused and self._timer_interval_ms > 0:
            self.timer.start(self._timer_interval_ms)
            self.request_refresh.emit()

    def set_low_overhead_mode(self, enabled):
        self._low_overhead_mode = bool(enabled)
        self.refresh_worker.set_low_overhead_mode(enabled)
        self.service_manager.set_low_overhead_mode(enabled)

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
        self.request_refresh.emit()

    def trigger_primary_action(self):
        entry = self._selected_entry()
        if entry is None:
            return
        if entry["status"] == "Running" and not entry.get("is_protected"):
            self._stop_service(entry)
            return
        if entry["status"] != "Running":
            self._start_service(entry)

    def visible_service_count(self):
        return len(self._visible_entries())

    def focus_service(self, service_name):
        normalized_name = (service_name or "").strip().lower()
        if not normalized_name:
            return
        self._pending_focus_service = normalized_name
        self._apply_pending_focus()

    def _apply_pending_focus(self):
        if not self._pending_focus_service:
            return
        for entry in self.latest_services:
            if entry["name"].lower() != self._pending_focus_service:
                continue
            source_index = self.model.index_for_entry_id(entry["id"])
            proxy_index = self.proxy_model.mapFromSource(source_index)
            if proxy_index.isValid():
                self.tree.setCurrentIndex(proxy_index)
                self.tree.scrollTo(proxy_index)
                self.on_select()
                self._pending_focus_service = None
            return

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

    def _show_context_menu(self, position):
        index = self.tree.indexAt(position)
        if not index.isValid():
            return

        self.tree.setCurrentIndex(index)
        entry = index.data(ENTRY_ROLE)
        if entry is None:
            return

        menu = QMenu(self)
        start_action = menu.addAction("Start")
        start_action.setEnabled(entry["status"] != "Running")
        stop_action = menu.addAction("Stop")
        stop_action.setEnabled(entry["status"] == "Running" and not entry.get("is_protected"))
        restart_action = menu.addAction("Restart")
        restart_action.setEnabled(entry["status"] == "Running" and not entry.get("is_protected"))
        menu.addSeparator()
        open_action = menu.addAction("Open File Location")
        search_action = menu.addAction("Search Online")
        menu.addSeparator()
        go_to_process_action = menu.addAction("Go to Process")
        go_to_process_action.setEnabled(bool(entry.get("pid")))
        menu.addSeparator()
        copy_details_action = menu.addAction("Copy Details")
        copy_path_action = menu.addAction("Copy Path")
        copy_pid_action = menu.addAction("Copy PID")

        chosen = self._exec_menu_with_refresh_pause(menu, self.tree.viewport().mapToGlobal(position))
        if chosen == start_action:
            self._start_service(entry)
        elif chosen == stop_action:
            self._stop_service(entry)
        elif chosen == restart_action:
            self._restart_service(entry)
        elif chosen == open_action:
            self._open_entry_location(entry)
        elif chosen == search_action:
            search_online(entry.get("search_query") or entry["name"])
        elif chosen == go_to_process_action:
            self.go_to_process_requested.emit(int(entry["pid"]))
        elif chosen == copy_details_action:
            self._copy_entry_details(entry)
        elif chosen == copy_path_action:
            self._copy_entry_path(entry)
        elif chosen == copy_pid_action:
            self._copy_entry_pid(entry)

    def _selected_entry_id(self):
        entry = self._selected_entry()
        return entry["id"] if entry else None

    def _selected_entry(self):
        index = self.tree.currentIndex()
        if not index.isValid():
            return None
        return index.data(ENTRY_ROLE)

    def _icon_for_entry(self, entry):
        exe_path = entry.get("exe_path") or ""
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
        return entry.get("exe_path") or entry["name"].lower()

    def _restore_selection(self, entry_id):
        if entry_id is None:
            return
        source_index = self.model.index_for_entry_id(entry_id)
        proxy_index = self.proxy_model.mapFromSource(source_index)
        if proxy_index.isValid():
            self.tree.setCurrentIndex(proxy_index)

    def _set_summary_labels(self, services):
        running = sum(1 for entry in services if entry["status"] == "Running")
        stopped = sum(1 for entry in services if entry["status"] != "Running")
        self._set_label_text(self.running_label, f"Running: {running}")
        self._set_label_text(self.stopped_label, f"Stopped: {stopped}")

    def _emit_page_status(self, services):
        if not services:
            self.page_status_changed.emit("Services: no matching items")
            return
        running = sum(1 for entry in services if entry["status"] == "Running")
        self.page_status_changed.emit(
            f"Services: {len(services)} visible | Running {running} | Stopped {len(services) - running}"
        )

    def _restore_sort_settings(self):
        column = int(self.settings.value("services/sort_column", 1))
        descending = self.settings.value("services/sort_descending", False, type=bool)
        order = Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder
        self.tree.sortByColumn(column, order)

    def _save_sort_settings(self, column, order):
        self.settings.setValue("services/sort_column", column)
        self.settings.setValue("services/sort_descending", order == Qt.SortOrder.DescendingOrder)

    def _set_column_visible(self, column, visible):
        self.tree.setColumnHidden(column, not visible)
        self.settings.setValue(f"services/column_hidden_{column}", not visible)

    def _restore_column_visibility(self):
        for column in range(1, len(self._column_labels)):
            hidden = self.settings.value(f"services/column_hidden_{column}", False, type=bool)
            self.tree.setColumnHidden(column, hidden)

    def _save_header_state(self, *_args):
        self.settings.setValue("services/header_state", self.tree.header().saveState())

    def _save_splitter_state(self, *_args):
        self.settings.setValue("services/content_splitter_state", self.content_splitter.saveState())

    def _restore_splitter_state(self):
        state = self.settings.value("services/content_splitter_state")
        if state and self.content_splitter.restoreState(state):
            return
        self.content_splitter.setSizes([1240, 430])

    def _save_vertical_splitter_state(self, *_args):
        self.settings.setValue("services/vertical_splitter_state", self.vertical_splitter.saveState())

    def _restore_vertical_splitter_state(self):
        state = self.settings.value("services/vertical_splitter_state")
        if state and self.vertical_splitter.restoreState(state):
            return
        self.vertical_splitter.setSizes([760, 96])

    def _restore_header_state(self):
        state = self.settings.value("services/header_state")
        if state and self.tree.header().restoreState(state):
            return
        header = self.tree.header()
        default_widths = {
            0: 170,
            1: 280,
            2: 120,
            3: 130,
            4: 90,
            5: 160,
            6: 340,
        }
        for column, width in default_widths.items():
            header.resizeSection(column, width)

    def _set_label_text(self, label, value):
        if label.text() != value:
            label.setText(value)

    def _open_entry_location(self, entry):
        success, message = open_file_location(entry.get("exe_path"))
        if not success:
            self.status_label.setText(message or "Could not open a file location for this item.")
            return
        self.status_label.setText("")

    def _open_selected_location(self):
        entry = self._selected_entry()
        if entry:
            self._open_entry_location(entry)

    def _search_selected_entry(self):
        entry = self._selected_entry()
        if entry:
            search_online(entry.get("search_query") or entry["name"])

    def _go_to_selected_process(self):
        entry = self.info_panel.current_entry or self._selected_entry()
        if not entry or not entry.get("pid"):
            self.status_label.setText("This service is not linked to a running process.")
            return
        self.go_to_process_requested.emit(int(entry["pid"]))

    def _start_service(self, entry):
        try:
            self.service_manager.start_service(entry["name"])
            self.dependency_cache.pop(entry["name"].lower(), None)
            self.status_label.setText(f"Requested start for {entry['display_name']}.")
            if self._refresh_profile_name != "Paused":
                self.request_refresh.emit()
        except Exception as error:
            self.status_label.setText(f"Could not start {entry['display_name']}: {error}")

    def _stop_service(self, entry):
        dependency_info = self._dependent_info(entry)
        dependents = dependency_info["dependents"]
        if dependents:
            dependent_names = ", ".join(item["display_name"] for item in dependents[:6])
            if len(dependents) > 6:
                dependent_names = f"{dependent_names}, and {len(dependents) - 6} more"
            response = QMessageBox.question(
                self,
                "Stop Service",
                (
                    f"{entry['display_name']} has {len(dependents)} dependent services"
                    f" across {dependency_info['depth']} level(s).\n\n"
                    f"Dependents: {dependent_names}\n\n"
                    "Stop this service anyway?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return
        try:
            self.service_manager.stop_service(entry["name"])
            self.dependency_cache.pop(entry["name"].lower(), None)
            self.status_label.setText(f"Requested stop for {entry['display_name']}.")
            if self._refresh_profile_name != "Paused":
                self.request_refresh.emit()
        except ServiceActionBlockedError as error:
            self.status_label.setText(str(error))
        except Exception as error:
            self.status_label.setText(f"Could not stop {entry['display_name']}: {error}")

    def _restart_service(self, entry):
        dependency_info = self._dependent_info(entry)
        dependents = dependency_info["dependents"]
        if dependents:
            dependent_names = ", ".join(item["display_name"] for item in dependents[:6])
            if len(dependents) > 6:
                dependent_names = f"{dependent_names}, and {len(dependents) - 6} more"
            response = QMessageBox.question(
                self,
                "Restart Service",
                (
                    f"{entry['display_name']} has {len(dependents)} dependent services"
                    f" across {dependency_info['depth']} level(s).\n\n"
                    f"Dependents: {dependent_names}\n\n"
                    "Restart this service anyway?"
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if response != QMessageBox.StandardButton.Yes:
                return
        try:
            self.service_manager.restart_service(entry["name"])
            self.dependency_cache.pop(entry["name"].lower(), None)
            self.status_label.setText(f"Requested restart for {entry['display_name']}.")
            if self._refresh_profile_name != "Paused":
                self.request_refresh.emit()
        except ServiceActionBlockedError as error:
            self.status_label.setText(str(error))
        except Exception as error:
            self.status_label.setText(f"Could not restart {entry['display_name']}: {error}")

    def _copy_entry_details(self, entry):
        lines = [
            f"Service: {entry['name']}",
            f"Display Name: {entry['display_name']}",
            f"Status: {entry['status']}",
            f"Start Type: {entry['start_type']}",
            f"PID: {entry['pid_display']}",
            f"Linked Process: {entry.get('linked_process_display') or 'None'}",
            f"User: {entry['username']}",
            f"Publisher: {entry.get('publisher') or 'Unknown'}",
            f"Binary Path: {entry.get('exe_path') or entry.get('binpath') or entry.get('location_reason') or 'Unavailable'}",
            f"Description: {entry.get('description') or 'Unknown'}",
            f"Dependents: {entry.get('dependent_count', 0)}",
            f"Dependency Depth: {entry.get('dependent_depth', 0)}",
        ]
        if copy_text_to_clipboard("\n".join(lines)):
            self.status_label.setText(f"Copied details for {entry['display_name']}.")

    def _copy_entry_path(self, entry):
        text = entry.get("exe_path") or entry.get("binpath") or entry.get("location_reason") or "Unavailable"
        if copy_text_to_clipboard(text):
            self.status_label.setText(f"Copied path for {entry['display_name']}.")

    def _copy_entry_pid(self, entry):
        if copy_text_to_clipboard(entry["pid_display"]):
            self.status_label.setText(f"Copied PID for {entry['display_name']}.")

    def export_csv(self):
        headers, rows = self._csv_rows()
        success, file_path = export_rows_to_csv(
            self,
            f"task-manager-services-{time.strftime('%Y%m%d-%H%M%S')}.csv",
            headers,
            rows,
        )
        if success:
            self.status_label.setText(f"Exported Services to {file_path}")
            return True
        return False

    def status_refresh_text(self):
        return self.last_updated_label.text()

    def _csv_rows(self):
        visible_columns = [
            column for column in range(len(self._column_labels)) if not self.tree.isColumnHidden(column)
        ]
        headers = [
            self.proxy_model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
            for column in visible_columns
        ]
        rows = []
        for entry in self._visible_entries():
            row = []
            for column in visible_columns:
                if column == 0:
                    row.append(entry["name"])
                elif column == 1:
                    row.append(entry["display_name"])
                elif column == 2:
                    row.append(entry["status"])
                elif column == 3:
                    row.append(entry["start_type"])
                elif column == 4:
                    row.append(entry["pid_display"])
                elif column == 5:
                    row.append(entry["username"])
                elif column == 6:
                    row.append(entry["binpath"] or "Unavailable")
            rows.append(row)
        return headers, rows

    def _visible_entries(self):
        if self.proxy_model.rowCount() == len(self._visible_entries_cache):
            return list(self._visible_entries_cache)
        entries = []
        for row in range(self.proxy_model.rowCount()):
            index = self.proxy_model.index(row, 0)
            entry = index.data(ENTRY_ROLE)
            if entry is not None:
                entries.append(entry)
        return entries

    def _update_notice_state(self, services):
        if not services:
            if self.filter_text:
                self.notice_label.setText("No matching services for the current filter.")
            else:
                self.notice_label.setText("No Windows services are currently available.")
            return

        limited_count = sum(
            1 for entry in services if not entry.get("exe_path") or entry.get("location_reason")
        )
        if limited_count:
            self.notice_label.setText(
                f"{limited_count} services have limited path metadata or inaccessible binaries."
            )
            return

        self.notice_label.setText("")

    def _resume_refresh(self):
        if not self._active:
            return
        if self._timer_interval_ms <= 0:
            return
        if not self.timer.isActive():
            self.timer.start(self._timer_interval_ms)
        self.request_refresh.emit()

    def _format_timestamp(self, timestamp):
        if not timestamp:
            return "--"
        return time.strftime("%I:%M:%S %p", time.localtime(timestamp)).lstrip("0")

    def _on_header_pressed(self, _section):
        self.pause_refresh_temporarily(1100)

    def _exec_menu_with_refresh_pause(self, menu, global_pos):
        self.pause_for_menu_open()
        try:
            return menu.exec(global_pos)
        finally:
            self.resume_after_menu_close()

    def _display_value(self, entry, column):
        if column == 0:
            if entry.get("is_protected"):
                return f"{entry['name']} [Protected]"
            return entry["name"]
        if column == 1:
            return entry["display_name"]
        if column == 2:
            return entry["status"]
        if column == 3:
            return entry["start_type"]
        if column == 4:
            return entry["pid_display"]
        if column == 5:
            return entry["username"]
        if column == 6:
            return entry["binpath"] or "Unavailable"
        return ""

    def _sort_value(self, entry, column):
        if column == 0:
            return entry["name"].lower()
        if column == 1:
            return entry["display_name"].lower()
        if column == 2:
            return entry["status"].lower()
        if column == 3:
            return entry["start_type"].lower()
        if column == 4:
            return entry["pid"] or -1
        if column == 5:
            return entry["username"].lower()
        if column == 6:
            return (entry["binpath"] or "").lower()
        return entry["display_name"].lower()

    def _filter_text_for_entry(self, entry):
        values = [
            entry["name"],
            entry["display_name"],
            entry["status"],
            entry["start_type"],
            entry["username"],
            entry["binpath"],
            entry.get("description") or "",
            entry.get("search_query") or "",
            str(entry["pid"]),
        ]
        return " ".join(value for value in values if value)

    def _tooltip_value(self, entry, column):
        if column == 0 and entry.get("is_protected"):
            return "Protected service: cannot be stopped or restarted from this app."
        if column == 1:
            return entry["description"] or entry["display_name"]
        if column == 6:
            return entry["exe_path"] or entry["binpath"] or entry.get("location_reason") or "Unavailable"
        return ""

    def _background_brush(self, entry, column):
        if column == 0 and entry.get("is_protected"):
            return protected_heat_brush()
        if column == 2:
            return status_heat_brush(entry["status"])
        return None

    def _dependent_services(self, entry):
        return self._dependent_info(entry)["dependents"]

    def _dependent_info(self, entry):
        cache_key = (entry.get("name") or "").lower()
        cached = self.dependency_cache.get(cache_key)
        if cached is not None:
            return cached
        dependents = self.service_manager.dependent_services(entry.get("name"))
        info = {
            "dependents": dependents,
            "depth": self.service_manager.dependent_service_depth(entry.get("name")),
            "summary": self._format_dependent_summary(dependents),
        }
        self.dependency_cache[cache_key] = info
        return info

    def _dependent_summary_text(self, entry):
        return self._dependent_info(entry)["summary"]

    def _format_dependent_summary(self, dependents):
        if not dependents:
            return "No dependent services"
        display_names = ", ".join(item["display_name"] for item in dependents[:4])
        if len(dependents) > 4:
            display_names = f"{display_names}, and {len(dependents) - 4} more"
        return display_names

    def _repolish_widget(self, widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _priority_entry_ids(self):
        priority_ids = set(self._compute_visible_entry_ids())
        selected_id = self._selected_entry_id()
        if selected_id:
            priority_ids.add(selected_id)
        if self._pending_focus_service:
            for entry in self.latest_services:
                if entry["name"].lower() == self._pending_focus_service:
                    priority_ids.add(entry["id"])
                    break
        return priority_ids

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
            source_index = self.model.index_for_entry_id(entry_id)
            if not source_index.isValid():
                continue
            entry = self.model.entry_for_row(source_index.row())
            if entry is None:
                continue
            if self._icon_cache_key(entry) in self.icon_cache:
                continue
            self._icon_for_entry(entry)
            proxy_index = self.proxy_model.mapFromSource(source_index)
            if proxy_index.isValid():
                self.proxy_model.dataChanged.emit(proxy_index, proxy_index, [Qt.ItemDataRole.DecorationRole])

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if self._preserves_selection_click(watched, event):
                return super().eventFilter(watched, event)
            if watched is not self.tree and not self.tree.isAncestorOf(watched):
                self.clear_selection()
        return super().eventFilter(watched, event)

    def _preserves_selection_click(self, watched, event=None):
        if watched in (
            self.info_panel.open_button,
            self.info_panel.search_button,
            self.info_panel.process_button,
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

    def _install_clear_filters(self, widget):
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)
