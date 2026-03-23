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

from core.startup_manager import StartupManager
from ui.process_actions import open_file_location, search_online
from ui.processes_tab import ClearableTreeWidget, ENTRY_ID_ROLE, ENTRY_ROLE, SORT_ROLE, SortableTreeWidgetItem


class StartupRefreshWorker(QObject):
    snapshot_ready = Signal(object, float)

    def __init__(self):
        super().__init__()
        self._manager = StartupManager()
        self._busy = False

    @Slot()
    def refresh(self):
        if self._busy:
            return

        self._busy = True
        try:
            entries = self._manager.list_startup_apps()
            self.snapshot_ready.emit(entries, time.time())
        finally:
            self._busy = False


class StartupTab(QWidget):
    request_refresh = Signal()
    page_status_changed = Signal(str)

    def __init__(self):
        super().__init__()

        layout = QVBoxLayout()
        layout.setSpacing(12)

        summary_layout = QHBoxLayout()
        summary_layout.setSpacing(10)
        self.configured_label = QLabel("Configured: 0")
        self.configured_label.setObjectName("metricCard")
        self.enabled_label = QLabel("Enabled: 0")
        self.enabled_label.setObjectName("metricCard")
        self.last_updated_label = QLabel("Startup apps scanned: --")
        self.last_updated_label.setObjectName("statusLabel")
        summary_layout.addWidget(self.configured_label)
        summary_layout.addWidget(self.enabled_label)
        summary_layout.addStretch()
        summary_layout.addWidget(self.last_updated_label, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(summary_layout)

        self.tree = ClearableTreeWidget()
        self.tree.setObjectName("processTree")
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(
            ["Name", "Status", "Publisher", "Location", "Command", "Target"]
        )
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setUniformRowHeights(True)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setSortingEnabled(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.tree)

        self.setLayout(layout)

        self.filter_text = ""
        self.latest_entries = []
        self.settings = QSettings("CodexTaskManager", "TaskManagerClone")
        self._column_labels = ["Name", "Status", "Publisher", "Location", "Command", "Target"]
        self._active = False
        self._last_refresh_time = 0.0
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self._resume_refresh)

        self.refresh_thread = QThread(self)
        self.refresh_worker = StartupRefreshWorker()
        self.refresh_worker.moveToThread(self.refresh_thread)
        self.request_refresh.connect(self.refresh_worker.refresh)
        self.refresh_worker.snapshot_ready.connect(self._handle_snapshot)
        self.refresh_thread.start()

        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.header().sortIndicatorChanged.connect(self._save_sort_settings)
        self.tree.header().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.header().customContextMenuRequested.connect(self._show_column_chooser)
        self._restore_sort_settings()
        self._restore_column_visibility()
        self._install_clear_filters(self)
        QApplication.instance().aboutToQuit.connect(self.shutdown)

    @Slot(object, float)
    def _handle_snapshot(self, entries, updated_at):
        self.latest_entries = entries
        self._last_refresh_time = updated_at
        self._set_label_text(
            self.last_updated_label,
            f"Startup apps scanned: {self._format_timestamp(updated_at)}",
        )
        self._rebuild_tree()

    def _rebuild_tree(self):
        selected_id = self._selected_entry_id()
        entries = self._filtered_entries()
        self._set_summary_labels(entries)

        self.tree.setSortingEnabled(False)
        self.tree.clear()
        for entry in entries:
            item = SortableTreeWidgetItem()
            item.setText(0, entry["name"])
            item.setText(1, entry["status"])
            item.setText(2, entry["publisher"])
            item.setText(3, entry["location"])
            item.setText(4, entry["command"])
            item.setText(5, entry["target_path"] or "Unavailable")
            item.setData(0, ENTRY_ROLE, entry)
            item.setData(0, ENTRY_ID_ROLE, entry["id"])
            item.setToolTip(4, entry["command"])
            item.setToolTip(5, entry["target_path"] or entry["description"] or "Unavailable")
            for column, value in {
                0: entry["name"].lower(),
                1: entry["status"].lower(),
                2: entry["publisher"].lower(),
                3: entry["location"].lower(),
                4: entry["command"].lower(),
                5: entry["target_path"].lower(),
            }.items():
                item.setData(column, SORT_ROLE, value)
            self.tree.addTopLevelItem(item)

        self.tree.setSortingEnabled(True)
        self.tree.sortItems(self.tree.sortColumn(), self.tree.header().sortIndicatorOrder())
        self._restore_selection(selected_id)
        self._emit_page_status(entries)

    def set_filter_text(self, text):
        self.filter_text = text.strip().lower()
        self._rebuild_tree()

    def clear_selection(self):
        self.tree.clearSelection()
        self.tree.setCurrentItem(None)

    def set_active(self, active):
        self._active = active
        if active:
            if not self.latest_entries or time.time() - self._last_refresh_time > 20.0:
                self.request_refresh.emit()
            return
        self._resume_timer.stop()

    def pause_refresh_temporarily(self, duration_ms=450):
        if not self._active:
            return
        self._resume_timer.start(duration_ms)

    def shutdown(self):
        self._resume_timer.stop()
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
        open_action.setEnabled(bool(entry["target_path"]))
        search_action = menu.addAction("Search Online")

        chosen = menu.exec(self.tree.viewport().mapToGlobal(position))
        if chosen == open_action:
            open_file_location(entry["target_path"])
        elif chosen == search_action:
            search_online(f"{entry['name']} startup app {entry['publisher']}")

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

    def _filtered_entries(self):
        if not self.filter_text:
            return self.latest_entries
        filtered = []
        for entry in self.latest_entries:
            values = [
                entry["name"].lower(),
                entry["status"].lower(),
                entry["publisher"].lower(),
                entry["location"].lower(),
                entry["command"].lower(),
                entry["target_path"].lower(),
                entry["description"].lower(),
            ]
            if any(self.filter_text in value for value in values):
                filtered.append(entry)
        return filtered

    def _set_summary_labels(self, entries):
        self._set_label_text(self.configured_label, f"Configured: {len(entries)}")
        enabled = sum(1 for entry in entries if entry["status"] == "Enabled")
        self._set_label_text(self.enabled_label, f"Enabled: {enabled}")

    def _emit_page_status(self, entries):
        if not entries:
            self.page_status_changed.emit("Startup Apps: no matching items")
            return
        self.page_status_changed.emit(f"Startup Apps: {len(entries)} visible")

    def _restore_sort_settings(self):
        column = int(self.settings.value("startup/sort_column", 0))
        descending = self.settings.value("startup/sort_descending", False, type=bool)
        order = Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder
        self.tree.sortItems(column, order)

    def _save_sort_settings(self, column, order):
        self.settings.setValue("startup/sort_column", column)
        self.settings.setValue("startup/sort_descending", order == Qt.SortOrder.DescendingOrder)

    def _set_column_visible(self, column, visible):
        self.tree.setColumnHidden(column, not visible)
        self.settings.setValue(f"startup/column_hidden_{column}", not visible)

    def _restore_column_visibility(self):
        for column in range(1, len(self._column_labels)):
            hidden = self.settings.value(f"startup/column_hidden_{column}", False, type=bool)
            self.tree.setColumnHidden(column, hidden)

    def _set_label_text(self, label, value):
        if label.text() != value:
            label.setText(value)

    def _resume_refresh(self):
        if not self._active:
            return
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
