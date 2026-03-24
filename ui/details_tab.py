import time

from PySide6.QtCore import QEvent, QFileInfo, QObject, QSettings, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileIconProvider,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from core.process_manager import ProcessManager, ProcessTerminationBlockedError
from ui.process_actions import open_file_location, search_online
from ui.processes_tab import (
    ClearableTreeWidget,
    ENTRY_ID_ROLE,
    ENTRY_KIND_ROLE,
    ENTRY_ROLE,
    SECONDARY_TEXT_ROLE,
    SORT_ROLE,
    SecondaryTextDelegate,
    SortableTreeWidgetItem,
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


class DetailsTab(QWidget):
    request_refresh = Signal()
    page_status_changed = Signal(str)

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

        self.tree = ClearableTreeWidget()
        self.tree.setObjectName("processTree")
        self.tree.setColumnCount(8)
        self._set_header_labels()
        self.tree.setRootIsDecorated(False)
        self.tree.setItemsExpandable(False)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
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
        layout.addWidget(self.tree)

        self.end_btn = QPushButton("End Task")
        self.end_btn.setObjectName("dangerButton")
        self.end_btn.clicked.connect(self.end_task)
        layout.addWidget(self.end_btn)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        layout.addWidget(self.status_label)

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
        self._tabular_font = self._build_tabular_font()
        self._column_labels = [
            "Name",
            "PID",
            "Type",
            "Publisher",
            "Window",
            "CPU %",
            "Memory %",
            "Disk",
        ]
        self._active = True
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self._resume_refresh)

        self.refresh_thread = QThread(self)
        self.refresh_worker = DetailRefreshWorker()
        self.refresh_worker.moveToThread(self.refresh_thread)
        self.request_refresh.connect(self.refresh_worker.refresh)
        self.refresh_worker.snapshot_ready.connect(self._handle_snapshot)
        self.refresh_thread.start()

        self.timer = QTimer()
        self.timer.timeout.connect(self.request_refresh.emit)
        self.timer.start(1250)

        self.tree.itemSelectionChanged.connect(self.on_select)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.header().sortIndicatorChanged.connect(self._save_sort_settings)
        self.tree.header().sectionResized.connect(self._save_header_state)
        self.tree.header().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.header().customContextMenuRequested.connect(self._show_column_chooser)
        self._restore_sort_settings()
        self._restore_header_state()
        self._restore_column_visibility()
        self._install_clear_filters(self)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.request_refresh.emit()

    @Slot(object, object, bool, float)
    def _handle_snapshot(self, details, summary, full_refresh, updated_at):
        self.latest_summary = summary
        self._apply_summary(summary, full_refresh, updated_at)
        if not full_refresh:
            return

        self.latest_details = details or []
        self._rebuild_tree()

    def _rebuild_tree(self):
        selected_entry_id = self._selected_entry_id()
        details = self._filter_details(self.latest_details)

        self.tree.setSortingEnabled(False)
        self.tree.setUpdatesEnabled(False)
        try:
            existing_items = self._top_level_items_by_id()
            visible_entry_ids = set()

            for entry in details:
                item = existing_items.get(entry["id"])
                if item is None:
                    item = SortableTreeWidgetItem()
                    self.tree.addTopLevelItem(item)
                self._update_detail_item(item, entry)
                visible_entry_ids.add(entry["id"])

            self._remove_missing_top_level_items(existing_items, visible_entry_ids)
        finally:
            self.tree.setUpdatesEnabled(True)
            self.tree.setSortingEnabled(True)

        self.tree.sortItems(self.tree.sortColumn(), self.tree.header().sortIndicatorOrder())
        self._restore_selection(selected_entry_id)
        self.on_select()
        self._emit_page_status()

    def on_select(self):
        entry = self._selected_entry()
        self.end_btn.setEnabled(entry is not None and not entry["is_protected"])

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

    def set_filter_text(self, text):
        self.filter_text = text.strip().lower()
        self._rebuild_tree()

    def clear_selection(self):
        self.tree.clearSelection()
        self.tree.setCurrentItem(None)
        self.on_select()

    def set_active(self, active):
        self._active = active
        if active:
            if not self.timer.isActive() and not self._resume_timer.isActive():
                self.timer.start(1250)
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

    def _filter_details(self, details):
        if not self.filter_text:
            return details

        filtered = []
        for entry in details:
            searchable = [
                entry["name"].lower(),
                entry["type_display"].lower(),
                entry["publisher"].lower(),
                entry.get("description", "").lower(),
                entry.get("product_name", "").lower(),
                entry["window_display"].lower(),
                entry["exe_path"].lower(),
                str(entry["pid"]),
            ]
            if any(self.filter_text in value for value in searchable):
                filtered.append(entry)
        return filtered

    def _update_detail_item(self, item, entry):
        self._set_item_text(item, 0, entry["name"])
        self._set_item_text(item, 1, str(entry["pid"]))
        self._set_item_text(item, 2, entry["type_display"])
        self._set_item_text(item, 3, entry["publisher"])
        self._set_item_text(item, 4, entry["window_display"])
        self._set_item_text(item, 5, entry["cpu_display"])
        self._set_item_text(item, 6, entry["memory_display"])
        self._set_item_text(item, 7, entry["disk_display"])
        self._set_item_data(item, 6, SECONDARY_TEXT_ROLE, entry["memory_value_display"])
        self._set_item_data(item, 0, ENTRY_ROLE, entry)
        self._set_item_data(item, 0, ENTRY_ID_ROLE, entry["id"])
        self._set_item_data(item, 0, ENTRY_KIND_ROLE, "detail")
        item.setIcon(0, self._icon_for_entry(entry))
        for column in (1, 5, 6, 7):
            item.setTextAlignment(column, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
            item.setFont(column, self._tabular_font)

        self._set_item_tooltip(item, 0, entry["exe_path"] or entry["name"])
        self._set_item_tooltip(item, 4, entry["window_tooltip"])
        self._set_item_tooltip(item, 6, entry["memory_tooltip"])
        self._set_item_tooltip(item, 7, entry["disk_tooltip"])

        for column, value in {
            0: entry["name"].lower(),
            1: entry["pid"],
            2: entry["type_display"].lower(),
            3: entry["publisher"].lower(),
            4: (0 if entry["has_window"] else 1, entry["window_display"].lower()),
            5: entry["cpu_percent"],
            6: entry["memory_percent"],
            7: entry["disk_rate_mb_per_sec"],
        }.items():
            self._set_item_data(item, column, SORT_ROLE, value)

    def _top_level_items_by_id(self):
        items = {}
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            items[item.data(0, ENTRY_ID_ROLE)] = item
        return items

    def _icon_for_entry(self, entry):
        exe_path = entry.get("exe_path") or ""
        cache_key = exe_path or entry["name"].lower()
        cached_icon = self.icon_cache.get(cache_key)
        if cached_icon is not None:
            return cached_icon

        icon = self.default_icon
        if exe_path:
            file_info = QFileInfo(exe_path)
            if file_info.exists():
                icon = self.icon_provider.icon(file_info)

        self.icon_cache[cache_key] = icon
        return icon

    def _set_item_text(self, item, column, value):
        if item.text(column) != value:
            item.setText(column, value)

    def _set_item_data(self, item, column, role, value):
        if item.data(column, role) != value:
            item.setData(column, role, value)

    def _set_item_tooltip(self, item, column, value):
        if item.toolTip(column) != value:
            item.setToolTip(column, value)

    def _remove_missing_top_level_items(self, existing_items, visible_entry_ids):
        for entry_id, item in existing_items.items():
            if entry_id in visible_entry_ids:
                continue
            index = self.tree.indexOfTopLevelItem(item)
            if index >= 0:
                self.tree.takeTopLevelItem(index)

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

    def _set_column_visible(self, column, visible):
        self.tree.setColumnHidden(column, not visible)
        self.settings.setValue(f"details/column_hidden_{column}", not visible)

    def _restore_column_visibility(self):
        for column in range(1, len(self._column_labels)):
            hidden = self.settings.value(f"details/column_hidden_{column}", False, type=bool)
            self.tree.setColumnHidden(column, hidden)

    def _save_header_state(self, *_args):
        self.settings.setValue("details/header_state", self.tree.header().saveState())

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

    def _emit_page_status(self):
        visible = self._filter_details(self.latest_details)
        if not visible:
            self.page_status_changed.emit("Details: no matching items")
            return
        self.page_status_changed.emit(f"Details: {len(visible)} processes visible")

    def _show_context_menu(self, position):
        item = self.tree.itemAt(position)
        if item is None:
            return

        self.tree.setCurrentItem(item)
        entry = item.data(0, ENTRY_ROLE)
        if entry is None:
            return

        menu = QMenu(self)
        end_task_action = menu.addAction("End Task")
        end_task_action.setEnabled(not entry["is_protected"])
        open_location_action = menu.addAction("Open File Location")
        open_location_action.setEnabled(bool(entry["exe_path"]))
        search_action = menu.addAction("Search Online")

        chosen_action = menu.exec(self.tree.viewport().mapToGlobal(position))
        if chosen_action == end_task_action:
            self.end_task()
        elif chosen_action == open_location_action:
            open_file_location(entry["exe_path"])
        elif chosen_action == search_action:
            search_online(f"{entry['name']} {entry['publisher']}")

    def _selected_entry(self):
        item = self.tree.currentItem()
        return item.data(0, ENTRY_ROLE) if item is not None else None

    def _selected_entry_id(self):
        entry = self._selected_entry()
        return entry["id"] if entry is not None else None

    def _restore_selection(self, entry_id):
        if entry_id is None:
            return
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.data(0, ENTRY_ID_ROLE) == entry_id:
                self.tree.setCurrentItem(item)
                return

    def _restore_sort_settings(self):
        column = int(self.settings.value("details/sort_column", 5))
        descending = self.settings.value("details/sort_descending", True, type=bool)
        order = Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder
        self.tree.sortItems(column, order)

    def _save_sort_settings(self, column, order):
        self.settings.setValue("details/sort_column", column)
        self.settings.setValue(
            "details/sort_descending",
            order == Qt.SortOrder.DescendingOrder,
        )

    def _set_header_labels(self, disk_active_percent=0.0):
        self.tree.setHeaderLabels(
            [
                "Name",
                "PID",
                "Type",
                "Publisher",
                "Window",
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
        self._update_disk_header(summary["disk_active_time_percent"])
        if full_refresh:
            self._set_label_text(
                self.last_updated_label,
                f"Details updated: {self._format_timestamp(updated_at)}",
            )

    def _resume_refresh(self):
        if not self._active:
            return
        if not self.timer.isActive():
            self.timer.start(1250)
        self.request_refresh.emit()

    def _format_timestamp(self, timestamp):
        if not timestamp:
            return "--"
        return time.strftime("%I:%M:%S %p", time.localtime(timestamp)).lstrip("0")

    def _build_tabular_font(self):
        font = QFont()
        try:
            font.setFamilies(["Cascadia Mono SemiLight", "Cascadia Mono", "Consolas"])
        except Exception:
            font.setFamily("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setFixedPitch(True)
        font.setBold(True)
        return font

    def _set_label_text(self, label, value):
        if label.text() != value:
            label.setText(value)

    def _update_disk_header(self, disk_active_percent):
        header_item = self.tree.headerItem()
        if header_item is None:
            return
        label = f"Disk ({disk_active_percent:.0f}%)"
        if header_item.text(7) != label:
            header_item.setText(7, label)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if watched is self.end_btn:
                return super().eventFilter(watched, event)
            if watched is not self.tree and not self.tree.isAncestorOf(watched):
                self.clear_selection()
        return super().eventFilter(watched, event)

    def _install_clear_filters(self, widget):
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)
