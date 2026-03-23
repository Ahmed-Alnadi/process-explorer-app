import time

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from core.service_manager import ServiceManager
from ui.process_actions import open_file_location, search_online
from ui.processes_tab import ClearableTreeWidget, ENTRY_ID_ROLE, ENTRY_ROLE, SORT_ROLE, SortableTreeWidgetItem


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


class ServicesTab(QWidget):
    request_refresh = Signal()
    page_status_changed = Signal(str)

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

        self.tree = ClearableTreeWidget()
        self.tree.setObjectName("processTree")
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels(
            ["Service", "Display Name", "Status", "Start Type", "PID", "User", "Binary Path"]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setSortingEnabled(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.tree)

        self.setLayout(layout)

        self.filter_text = ""
        self.latest_services = []
        self.settings = QSettings("CodexTaskManager", "TaskManagerClone")
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
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self._resume_refresh)

        self.refresh_thread = QThread(self)
        self.refresh_worker = ServiceRefreshWorker()
        self.refresh_worker.moveToThread(self.refresh_thread)
        self.request_refresh.connect(self.refresh_worker.refresh)
        self.refresh_worker.snapshot_ready.connect(self._handle_snapshot)
        self.refresh_thread.start()

        self.timer = QTimer()
        self.timer.timeout.connect(self.request_refresh.emit)
        self.timer.start(5000)
        self.timer.stop()

        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.header().sortIndicatorChanged.connect(self._save_sort_settings)
        self.tree.header().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.header().customContextMenuRequested.connect(self._show_column_chooser)
        self._restore_sort_settings()
        self._restore_column_visibility()
        self._install_clear_filters(self)
        QApplication.instance().aboutToQuit.connect(self.shutdown)

    @Slot(object, float)
    def _handle_snapshot(self, services, updated_at):
        self.latest_services = services
        self._set_label_text(
            self.last_updated_label,
            f"Services updated: {self._format_timestamp(updated_at)}",
        )
        self._rebuild_tree()

    def _rebuild_tree(self):
        selected_id = self._selected_entry_id()
        services = self._filtered_services()
        self._set_summary_labels(services)

        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for entry in services:
            item = SortableTreeWidgetItem()
            item.setText(0, entry["name"])
            item.setText(1, entry["display_name"])
            item.setText(2, entry["status"])
            item.setText(3, entry["start_type"])
            item.setText(4, entry["pid_display"])
            item.setText(5, entry["username"])
            item.setText(6, entry["binpath"] or "Unavailable")
            item.setData(0, ENTRY_ROLE, entry)
            item.setData(0, ENTRY_ID_ROLE, entry["id"])
            item.setToolTip(1, entry["description"] or entry["display_name"])
            item.setToolTip(6, entry["binpath"] or "Unavailable")
            for column, value in {
                0: entry["name"].lower(),
                1: entry["display_name"].lower(),
                2: entry["status"].lower(),
                3: entry["start_type"].lower(),
                4: entry["pid"] or -1,
                5: entry["username"].lower(),
                6: entry["binpath"].lower(),
            }.items():
                item.setData(column, SORT_ROLE, value)
            self.tree.addTopLevelItem(item)

        self.tree.setSortingEnabled(True)
        self.tree.sortItems(self.tree.sortColumn(), self.tree.header().sortIndicatorOrder())
        self._restore_selection(selected_id)
        self._emit_page_status(services)

    def set_filter_text(self, text):
        self.filter_text = text.strip().lower()
        self._rebuild_tree()

    def clear_selection(self):
        self.tree.clearSelection()
        self.tree.setCurrentItem(None)

    def set_active(self, active):
        self._active = active
        if active:
            if not self.timer.isActive() and not self._resume_timer.isActive():
                self.timer.start(5000)
            self.request_refresh.emit()
            return
        self._resume_timer.stop()
        self.timer.stop()

    def pause_refresh_temporarily(self, duration_ms=450):
        if not self._active:
            return
        self.timer.stop()
        self._resume_timer.start(duration_ms)

    def shutdown(self):
        self._resume_timer.stop()
        self.timer.stop()
        if self.refresh_thread.isRunning():
            self.refresh_thread.quit()
            self.refresh_thread.wait(1000)

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
        menu.exec(global_pos)

    def _show_context_menu(self, position):
        item = self.tree.itemAt(position)
        if item is None:
            return

        self.tree.setCurrentItem(item)
        entry = item.data(0, ENTRY_ROLE)
        if entry is None:
            return

        menu = QMenu(self)
        open_action = menu.addAction("Open File Location")
        open_action.setEnabled(bool(entry["exe_path"]))
        search_action = menu.addAction("Search Online")

        chosen = menu.exec(self.tree.viewport().mapToGlobal(position))
        if chosen == open_action:
            open_file_location(entry["exe_path"])
        elif chosen == search_action:
            search_online(f"{entry['display_name']} Windows service")

    def _selected_entry_id(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        entry = item.data(0, ENTRY_ROLE)
        return entry["id"] if entry else None

    def _restore_selection(self, entry_id):
        if entry_id is None:
            return
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.data(0, ENTRY_ID_ROLE) == entry_id:
                self.tree.setCurrentItem(item)
                return

    def _filtered_services(self):
        if not self.filter_text:
            return self.latest_services
        filtered = []
        for entry in self.latest_services:
            values = [
                entry["name"].lower(),
                entry["display_name"].lower(),
                entry["status"].lower(),
                entry["start_type"].lower(),
                entry["username"].lower(),
                entry["binpath"].lower(),
                entry["description"].lower(),
                str(entry["pid"]),
            ]
            if any(self.filter_text in value for value in values):
                filtered.append(entry)
        return filtered

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
        self.tree.sortItems(column, order)

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

    def _set_label_text(self, label, value):
        if label.text() != value:
            label.setText(value)

    def _resume_refresh(self):
        if not self._active:
            return
        if not self.timer.isActive():
            self.timer.start(5000)
        self.request_refresh.emit()

    def _format_timestamp(self, timestamp):
        if not timestamp:
            return "--"
        return time.strftime("%I:%M:%S %p", time.localtime(timestamp)).lstrip("0")

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if watched is not self.tree and not self.tree.isAncestorOf(watched):
                self.clear_selection()
        return super().eventFilter(watched, event)

    def _install_clear_filters(self, widget):
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)
