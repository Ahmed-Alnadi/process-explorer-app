import time
from collections import deque

import psutil
from PySide6.QtCore import QEvent, QFileInfo, QModelIndex, QObject, QPoint, QRect, QSettings, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileIconProvider,
    QFrame,
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

from core.process_manager import ProcessManager, ProcessTerminationBlockedError
from core.refresh_profiles import DEFAULT_REFRESH_PROFILE, REFRESH_PROFILES
from ui.export_utils import export_rows_to_csv
from ui.flat_entry_model import EntryFilterProxyModel, FlatEntryTableModel
from ui.heatmap_utils import disk_intensity_from_rate, protected_heat_brush, resource_heat_brush
from ui.process_actions import copy_text_to_clipboard, open_file_location, search_online
from ui.process_tree_model import ClearableTreeView
from ui.processes_tab import (
    ENTRY_ID_ROLE,
    ENTRY_KIND_ROLE,
    ENTRY_ROLE,
    SECONDARY_TEXT_ROLE,
    SORT_ROLE,
    SecondaryTextDelegate,
    SelectionInfoPanel,
)


class DetailRefreshWorker(QObject):
    snapshot_ready = Signal(object, object, bool, float)

    def __init__(self):
        super().__init__()
        self._manager = ProcessManager()
        self._busy = False
        self._cached_details = []
        self._last_full_refresh = 0.0
        self._full_refresh_interval_s = 4.5

    @Slot()
    def refresh(self):
        if self._busy:
            return

        self._busy = True
        try:
            current_time = time.time()
            summary = self._manager.system_summary()
            full_refresh = (
                not self._cached_details
                or current_time - self._last_full_refresh >= self._full_refresh_interval_s
            )
            details = None
            if full_refresh:
                self._cached_details = self._manager.list_process_details()
                self._last_full_refresh = current_time
                details = self._cached_details
            self.snapshot_ready.emit(details, summary, full_refresh, current_time)
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


class DetailsTab(QWidget):
    request_refresh = Signal()
    request_force_refresh = Signal()
    page_status_changed = Signal(str)
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
        self.last_updated_label = QLabel("Details updated: --")
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
        self.tree.setRootIsDecorated(False)
        self.tree.setItemsExpandable(False)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.header().setMinimumSectionSize(72)
        self.tree.header().setStretchLastSection(False)
        self.content_splitter.addWidget(self.tree)

        self.info_scroll = QScrollArea()
        self.info_scroll.setObjectName("sidePanelScroll")
        self.info_scroll.setWidgetResizable(True)
        self.info_scroll.setFrameShape(QFrame.Shape.NoFrame)

        self.info_panel = SelectionInfoPanel()
        self.info_scroll.setMinimumWidth(320)
        self.info_scroll.setMaximumWidth(16777215)
        self.info_scroll.setWidget(self.info_panel)
        self.content_splitter.addWidget(self.info_scroll)
        self.content_splitter.setStretchFactor(0, 5)
        self.content_splitter.setStretchFactor(1, 2)
        self.content_splitter.setSizes([1260, 430])
        self.vertical_splitter.addWidget(self.content_splitter)

        bottom_panel = QWidget()
        bottom_layout = QVBoxLayout()
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(8)
        bottom_panel.setLayout(bottom_layout)
        self.end_btn = QPushButton("End Task")
        self.end_btn.setObjectName("dangerButton")
        self.end_btn.setToolTip("Terminate the selected process.")
        self.end_btn.clicked.connect(self.end_task)
        bottom_layout.addWidget(self.end_btn)

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
        self.vertical_splitter.setSizes([760, 108])
        layout.addWidget(self.vertical_splitter)

        self.setLayout(layout)

        self.process_manager = ProcessManager()
        self.filter_text = ""
        self.latest_details = []
        self.latest_summary = {
            "cpu_display": "CPU: 0.0%",
            "memory_display": "Physical Memory: 0.0%",
            "disk_active_time_display": "Disk Active Time: 0.0%",
            "disk_active_time_percent": 0.0,
            "gpu_temp_display": "GPU Temp: N/A",
        }
        self.settings = QSettings("CodexTaskManager", "TaskManagerClone")
        self.icon_provider = QFileIconProvider()
        self.default_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self.icon_cache = {}
        self._visible_entry_ids = set()
        self._visible_entries_cache = []
        self._runtime_paused = False
        self.entry_detail_cache = {}
        self.entry_detail_cache_ttl = 8.0
        self.entry_history = {}
        self._column_labels = [
            "Name",
            "PID",
            "Type",
            "Publisher",
            "Window",
            "CPU %",
            "Memory %",
            "Disk (0%)",
        ]
        self._active = True
        self._refresh_profile_name = DEFAULT_REFRESH_PROFILE
        self._timer_interval_ms = REFRESH_PROFILES[DEFAULT_REFRESH_PROFILE]["details_timer_ms"]
        self._pending_focus_pid = None
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
            secondary_resolver=self._secondary_value,
            tabular_columns={1, 5, 6, 7},
            alignment_columns={1, 5, 6, 7},
            entry_kind="detail",
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
        self.tree.setItemDelegateForColumn(6, SecondaryTextDelegate(self.tree))

        self.refresh_thread = QThread(self)
        self.refresh_worker = DetailRefreshWorker()
        self.refresh_worker.moveToThread(self.refresh_thread)
        self.request_refresh.connect(self.refresh_worker.refresh)
        self.request_force_refresh.connect(self.refresh_worker.force_refresh)
        self.refresh_worker.snapshot_ready.connect(self._handle_snapshot)
        self.refresh_thread.start()
        self.refresh_worker._full_refresh_interval_s = REFRESH_PROFILES[DEFAULT_REFRESH_PROFILE][
            "details_full_s"
        ]

        self.timer = QTimer()
        self.timer.timeout.connect(self.request_refresh.emit)
        if self._timer_interval_ms > 0:
            self.timer.start(self._timer_interval_ms)

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

    @Slot(object, object, bool, float)
    def _handle_snapshot(self, details, summary, full_refresh, updated_at):
        self.latest_summary = summary
        self._apply_summary(summary, full_refresh, updated_at)
        if full_refresh:
            self.latest_details = details or []
            self._update_entry_histories(self.latest_details)
        self._sync_view()

    def _sync_view(self):
        selected_entry_id = self._selected_entry_id()
        vertical_scroll = self.tree.verticalScrollBar().value()
        horizontal_scroll = self.tree.horizontalScrollBar().value()
        self._visible_entries_cache = []

        self.tree.setUpdatesEnabled(False)
        try:
            priority_ids = self._priority_entry_ids()
            self.model.sync_entries(
                self.latest_details,
                priority_ids=priority_ids,
                batch_size=max(len(priority_ids) + 24, 96),
            )
            self.proxy_model.set_filter_text(self.filter_text)
            self._restore_selection(selected_entry_id)
        finally:
            self.tree.setUpdatesEnabled(True)

        self.tree.verticalScrollBar().setValue(vertical_scroll)
        self.tree.horizontalScrollBar().setValue(horizontal_scroll)
        self.on_select()
        visible_details = self._visible_entries()
        self._visible_entries_cache = visible_details
        self._update_notice_state(visible_details)
        self._emit_page_status(visible_details)
        self._schedule_visible_row_refresh()

    def on_select(self):
        entry = self._selected_entry()
        self.end_btn.setEnabled(entry is not None and not entry["is_protected"])
        if entry is None:
            self.info_panel.set_entry(None, self._blank_extra_details(), None)
            return
        self.info_panel.set_entry(
            entry,
            self._entry_additional_details(entry),
            self.entry_history.get(entry["id"]),
        )

    def end_task(self):
        entry = self._selected_entry()
        if entry is None or entry["is_protected"]:
            return

        response = QMessageBox.question(
            self,
            "End Task",
            f"End task for {entry['name']} (PID {entry['pid']})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if response != QMessageBox.StandardButton.Yes:
            return

        try:
            self.process_manager.terminate_processes(entry["pids"])
            self.status_label.setText(f"Sent terminate signal to {entry['name']}.")
        except ProcessTerminationBlockedError as error:
            self.status_label.setText(str(error))
        except Exception as error:
            self.status_label.setText(f"Error ending {entry['name']}: {error}")

    def end_process_tree(self):
        entry = self._selected_entry()
        if entry is None or entry["is_protected"]:
            return

        response = QMessageBox.question(
            self,
            "End Process Tree",
            f"End process tree for {entry['name']} (PID {entry['pid']})?",
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
        self._timer_interval_ms = config["details_timer_ms"]
        self.refresh_worker._full_refresh_interval_s = config["details_full_s"]
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
        end_task_action = menu.addAction("End Task")
        end_task_action.setEnabled(not entry["is_protected"])
        end_tree_action = menu.addAction("End Process Tree")
        end_tree_action.setEnabled(not entry["is_protected"])
        menu.addSeparator()
        open_location_action = menu.addAction("Open File Location")
        search_action = menu.addAction("Search Online")
        menu.addSeparator()
        go_to_service_action = menu.addAction("Go to Service")
        go_to_service_action.setEnabled(bool(entry.get("primary_service_name")))
        menu.addSeparator()
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
        elif chosen_action == go_to_service_action:
            self.go_to_service_requested.emit(entry["primary_service_name"])
        elif chosen_action == copy_details_action:
            self._copy_entry_details(entry)
        elif chosen_action == copy_path_action:
            self._copy_entry_path(entry)
        elif chosen_action == copy_pid_action:
            self._copy_entry_pid(entry)

    def _selected_entry(self):
        index = self.tree.currentIndex()
        if not index.isValid():
            return None
        return index.data(ENTRY_ROLE)

    def _selected_entry_id(self):
        entry = self._selected_entry()
        return entry["id"] if entry is not None else None

    def _restore_selection(self, entry_id):
        if self._pending_focus_pid is not None:
            for entry in self.latest_details:
                if entry.get("pid") != self._pending_focus_pid:
                    continue
                source_index = self.model.index_for_entry_id(entry["id"])
                proxy_index = self.proxy_model.mapFromSource(source_index)
                if proxy_index.isValid():
                    self.tree.setCurrentIndex(proxy_index)
                    self.tree.scrollTo(proxy_index)
                    self._pending_focus_pid = None
                    return
        if entry_id is None:
            return
        source_index = self.model.index_for_entry_id(entry_id)
        proxy_index = self.proxy_model.mapFromSource(source_index)
        if proxy_index.isValid():
            self.tree.setCurrentIndex(proxy_index)

    def _restore_sort_settings(self):
        column = int(self.settings.value("details/sort_column", 5))
        descending = self.settings.value("details/sort_descending", True, type=bool)
        order = Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder
        self.tree.sortByColumn(column, order)

    def _save_sort_settings(self, column, order):
        self.settings.setValue("details/sort_column", column)
        self.settings.setValue("details/sort_descending", order == Qt.SortOrder.DescendingOrder)

    def _set_column_visible(self, column, visible):
        self.tree.setColumnHidden(column, not visible)
        self.settings.setValue(f"details/column_hidden_{column}", not visible)

    def _restore_column_visibility(self):
        for column in range(1, len(self._column_labels)):
            hidden = self.settings.value(f"details/column_hidden_{column}", False, type=bool)
            self.tree.setColumnHidden(column, hidden)

    def _save_header_state(self, *_args):
        self.settings.setValue("details/header_state", self.tree.header().saveState())

    def _save_splitter_state(self, *_args):
        self.settings.setValue("details/content_splitter_state", self.content_splitter.saveState())

    def _restore_splitter_state(self):
        state = self.settings.value("details/content_splitter_state")
        if state and self.content_splitter.restoreState(state):
            return
        self.content_splitter.setSizes([1260, 430])

    def _save_vertical_splitter_state(self, *_args):
        self.settings.setValue("details/vertical_splitter_state", self.vertical_splitter.saveState())

    def _restore_vertical_splitter_state(self):
        state = self.settings.value("details/vertical_splitter_state")
        if state and self.vertical_splitter.restoreState(state):
            return
        self.vertical_splitter.setSizes([760, 108])

    def _restore_header_state(self):
        state = self.settings.value("details/header_state")
        if state and self.tree.header().restoreState(state):
            return
        header = self.tree.header()
        default_widths = {
            0: 280,
            1: 90,
            2: 145,
            3: 170,
            4: 250,
            5: 95,
            6: 120,
            7: 110,
        }
        for column, width in default_widths.items():
            header.resizeSection(column, width)

    def _emit_page_status(self, visible_entries):
        if not visible_entries:
            self.page_status_changed.emit("Details: no matching items")
            return
        self.page_status_changed.emit(f"Details: {len(visible_entries)} processes visible")

    def _apply_summary(self, summary, full_refresh, updated_at):
        self._set_label_text(self.cpu_summary_label, summary["cpu_display"])
        self._set_label_text(self.memory_summary_label, summary["memory_display"])
        self._set_label_text(self.disk_summary_label, summary["disk_active_time_display"])
        self._set_label_text(self.gpu_summary_label, summary["gpu_temp_display"])
        self.model.set_header_label(7, f"Disk ({summary['disk_active_time_percent']:.0f}%)")
        if full_refresh:
            self._set_label_text(
                self.last_updated_label,
                f"Details updated: {self._format_timestamp(updated_at)}",
            )

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

    def _go_to_selected_service(self):
        entry = self.info_panel.current_entry or self._selected_entry()
        if not entry or not entry.get("primary_service_name"):
            self.status_label.setText("No linked Windows service is available for this selection.")
            return
        self.go_to_service_requested.emit(entry["primary_service_name"])

    def _copy_entry_details(self, entry):
        lines = [
            f"Name: {entry['name']}",
            f"PID: {entry['pid']}",
            f"Type: {entry['type_display']}",
            f"Protection: {entry.get('protection_reason') or ('Protected' if entry.get('is_protected') else 'Normal')}",
            f"Service: {entry.get('service_display') or 'None'}",
            f"Startup: {entry.get('startup_display') or 'Not listed'}",
            f"Publisher: {entry['publisher']}",
            f"Path: {entry.get('exe_path') or entry.get('location_reason') or 'Unavailable'}",
            f"Window: {entry['window_display']}",
            f"CPU: {entry['cpu_display']}",
            f"Memory: {entry['memory_display']} {entry['memory_value_display']}",
            f"Disk: {entry['disk_display']}",
            f"Product: {entry.get('product_name') or 'Unknown'}",
            f"Description: {entry.get('description') or 'Unknown'}",
        ]
        if copy_text_to_clipboard("\n".join(lines)):
            self.status_label.setText(f"Copied details for {entry['name']}.")

    def _copy_entry_path(self, entry):
        text = entry.get("exe_path") or entry.get("location_reason") or "Unavailable"
        if copy_text_to_clipboard(text):
            self.status_label.setText(f"Copied path for {entry['name']}.")

    def _copy_entry_pid(self, entry):
        if copy_text_to_clipboard(str(entry["pid"])):
            self.status_label.setText(f"Copied PID for {entry['name']}.")

    def _entry_additional_details(self, entry):
        cached = self.entry_detail_cache.get(entry["id"])
        if cached is not None and time.time() - cached["time"] < self.entry_detail_cache_ttl:
            return dict(cached["data"])
        extra = self._blank_extra_details()
        try:
            process = psutil.Process(entry["pid"])
        except Exception:
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

        self.entry_detail_cache[entry["id"]] = {"time": time.time(), "data": dict(extra)}
        return extra

    def _blank_extra_details(self):
        return {
            "user": "--",
            "started": "--",
            "threads": "--",
        }

    def focus_pid(self, pid):
        self._pending_focus_pid = int(pid)
        if self.latest_details:
            self._sync_view()
        elif self._refresh_profile_name != "Paused":
            self.request_refresh.emit()

    def export_csv(self):
        headers, rows = self._csv_rows()
        success, file_path = export_rows_to_csv(
            self,
            f"task-manager-details-{time.strftime('%Y%m%d-%H%M%S')}.csv",
            headers,
            rows,
        )
        if success:
            self.status_label.setText(f"Exported Details to {file_path}")
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
                    row.append(str(entry["pid"]))
                elif column == 2:
                    row.append(entry["type_display"])
                elif column == 3:
                    row.append(entry["publisher"])
                elif column == 4:
                    row.append(entry["window_display"])
                elif column == 5:
                    row.append(entry["cpu_display"])
                elif column == 6:
                    row.append(f"{entry['memory_display']} {entry['memory_value_display']}")
                elif column == 7:
                    row.append(entry["disk_display"])
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

    def _update_notice_state(self, details):
        if not details:
            if self.filter_text:
                self.notice_label.setText("No matching processes for the current filter.")
            else:
                self.notice_label.setText("No process details are currently available.")
            return

        limited_count = sum(
            1 for entry in details if not entry.get("exe_path") or entry.get("location_reason")
        )
        if limited_count:
            self.notice_label.setText(
                f"{limited_count} processes have limited metadata or file-path access due to Windows restrictions."
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

    def _set_label_text(self, label, value):
        if label.text() != value:
            label.setText(value)

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
            return str(entry["pid"])
        if column == 2:
            return entry["type_display"]
        if column == 3:
            return entry["publisher"]
        if column == 4:
            return entry["window_display"]
        if column == 5:
            return entry["cpu_display"]
        if column == 6:
            return entry["memory_display"]
        if column == 7:
            return entry["disk_display"]
        return ""

    def _sort_value(self, entry, column):
        if column == 0:
            return entry["name"].lower()
        if column == 1:
            return entry["pid"]
        if column == 2:
            return entry["type_display"].lower()
        if column == 3:
            return entry["publisher"].lower()
        if column == 4:
            return (0 if entry["has_window"] else 1, entry["window_display"].lower())
        if column == 5:
            return entry["cpu_percent"]
        if column == 6:
            return entry["memory_percent"]
        if column == 7:
            return entry["disk_rate_mb_per_sec"]
        return entry["name"].lower()

    def _filter_text_for_entry(self, entry):
        values = [
            entry["name"],
            entry["type_display"],
            entry["publisher"],
            entry.get("description") or "",
            entry.get("product_name") or "",
            entry["window_display"],
            entry["exe_path"],
            entry.get("search_query") or "",
            str(entry["pid"]),
        ]
        return " ".join(value for value in values if value)

    def _tooltip_value(self, entry, column):
        if column == 0:
            parts = []
            if entry.get("is_protected"):
                parts.append("Protected process: cannot be ended from this app.")
            parts.append(entry["exe_path"] or entry.get("location_reason") or entry["name"])
            return "\n".join(part for part in parts if part)
        if column == 4:
            return entry["window_tooltip"]
        if column == 6:
            return entry["memory_tooltip"]
        if column == 7:
            return entry["disk_tooltip"]
        return ""

    def _background_brush(self, entry, column):
        if column == 0 and entry.get("is_protected"):
            return protected_heat_brush()
        if column == 5:
            return resource_heat_brush(entry["cpu_percent"] / 100.0)
        if column == 6:
            return resource_heat_brush(entry["memory_percent"] / 100.0)
        if column == 7:
            return resource_heat_brush(disk_intensity_from_rate(entry["disk_rate_mb_per_sec"]))
        return None

    def _secondary_value(self, entry, column):
        if column == 6:
            return entry["memory_value_display"]
        return ""

    def _update_entry_histories(self, entries):
        active_ids = set()
        for entry in entries:
            active_ids.add(entry["id"])
            history = self._history_bucket(entry["id"])
            history["cpu"].append(entry["cpu_percent"])
            history["memory"].append(entry["memory_percent"])
            history["disk"].append(disk_intensity_from_rate(entry["disk_rate_mb_per_sec"]) * 100.0)

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

    def _repolish_widget(self, widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _priority_entry_ids(self):
        priority_ids = set(self._compute_visible_entry_ids())
        selected_id = self._selected_entry_id()
        if selected_id:
            priority_ids.add(selected_id)
        if self._pending_focus_pid is not None:
            for entry in self.latest_details:
                if entry.get("pid") == self._pending_focus_pid:
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
            self.end_btn,
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

    def _install_clear_filters(self, widget):
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)
