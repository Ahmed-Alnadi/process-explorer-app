import time

import psutil
from PySide6.QtCore import QEvent, QFileInfo, QModelIndex, QObject, QSettings, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QFont
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
    QSplitter,
    QStyledItemDelegate,
    QStyle,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.process_manager import ProcessManager, ProcessTerminationBlockedError
from ui.process_actions import open_file_location, search_online
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
        painter.drawText(
            text_rect,
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            primary_text,
        )

        if secondary_text:
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


class SelectionInfoPanel(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("sidePanel")
        self._value_labels = {}
        self.current_entry = None

        layout = QVBoxLayout()
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.setLayout(layout)

        self.title_label = QLabel("Selection")
        self.title_label.setObjectName("sidePanelTitle")
        layout.addWidget(self.title_label)

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
            "Command",
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
        self.search_button = QPushButton("Search Online")
        self.search_button.setObjectName("secondaryButton")
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addWidget(self.open_button)
        actions.addWidget(self.search_button)
        layout.addLayout(actions)
        layout.addStretch()

    def set_entry(self, entry, extra_details):
        self.current_entry = entry
        if entry is None:
            self.title_label.setText("Selection")
            self.subtitle_label.setText("Pick an app or process to inspect it.")
            for label in self._value_labels.values():
                label.setText("--")
            self.open_button.setEnabled(False)
            self.search_button.setEnabled(False)
            return

        self.title_label.setText(entry["name"])
        self.subtitle_label.setText(
            "On-demand details for the selected item."
        )

        field_values = {
            "Type": entry["type_display"],
            "Publisher": entry["publisher"],
            "Path": entry["exe_path"] or "Unavailable",
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
            "Command": extra_details["command"],
        }

        for field, value in field_values.items():
            self._value_labels[field].setText(value)

        self.open_button.setEnabled(bool(entry["exe_path"]))
        self.search_button.setEnabled(True)


class ProcessesTab(QWidget):
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
        self.last_updated_label = QLabel("List updated: --")
        self.last_updated_label.setObjectName("statusLabel")
        summary_layout.addWidget(self.cpu_summary_label)
        summary_layout.addWidget(self.memory_summary_label)
        summary_layout.addWidget(self.disk_summary_label)
        summary_layout.addWidget(self.gpu_summary_label)
        summary_layout.addStretch()
        summary_layout.addWidget(self.last_updated_label, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(summary_layout)

        content_splitter = QSplitter(Qt.Orientation.Horizontal)
        content_splitter.setChildrenCollapsible(False)
        content_splitter.setHandleWidth(10)

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
        self.tree.setSortingEnabled(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.setItemDelegateForColumn(6, SecondaryTextDelegate(self.tree))
        content_splitter.addWidget(self.tree)

        self.info_panel = SelectionInfoPanel()
        self.info_panel.setMinimumWidth(320)
        self.info_panel.setMaximumWidth(420)
        content_splitter.addWidget(self.info_panel)
        content_splitter.setStretchFactor(0, 4)
        content_splitter.setStretchFactor(1, 0)
        content_splitter.setSizes([1120, 360])
        layout.addWidget(content_splitter)

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
        self._resume_timer = QTimer(self)
        self._resume_timer.setSingleShot(True)
        self._resume_timer.timeout.connect(self._resume_refresh)

        self.refresh_thread = QThread(self)
        self.refresh_worker = ProcessRefreshWorker()
        self.refresh_worker.moveToThread(self.refresh_thread)
        self.request_refresh.connect(self.refresh_worker.refresh)
        self.refresh_worker.snapshot_ready.connect(self._handle_snapshot)
        self.refresh_thread.start()

        self.timer = QTimer()
        self.timer.timeout.connect(self.request_refresh.emit)
        self.timer.start(1250)

        self.tree.selectionModel().selectionChanged.connect(lambda *_: self.on_select())
        self.tree.expanded.connect(self._on_item_expanded)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.header().sortIndicatorChanged.connect(self._save_sort_settings)
        self.tree.header().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.header().customContextMenuRequested.connect(self._show_column_chooser)
        self.info_panel.open_button.clicked.connect(self._open_selected_location)
        self.info_panel.search_button.clicked.connect(self._search_selected_entry)
        self._restore_sort_settings()
        self._restore_column_visibility()
        self._install_clear_filters(self)
        QApplication.instance().aboutToQuit.connect(self.shutdown)
        self.info_panel.set_entry(None, self._blank_extra_details())
        self.request_refresh.emit()

    def mousePressEvent(self, event):
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
        self._rebuild_tree()

    def _rebuild_tree(self):
        selected_entry_id = self._selected_entry_id()
        expanded_keys = self._expanded_group_keys()
        selected_group_key = self._group_key_for_entry_id(selected_entry_id)
        if selected_group_key:
            expanded_keys.add(selected_group_key)
        self.current_groups = self._filter_groups(self.latest_groups)
        load_children_keys = set(expanded_keys)
        if self.filter_text:
            load_children_keys.update(group["group_key"] for group in self.current_groups)
        self.model.set_groups(
            self.current_groups,
            expanded_group_keys=load_children_keys,
            load_all_children=bool(self.filter_text),
        )
        self.model.sort(
            self.tree.header().sortIndicatorSection(),
            self.tree.header().sortIndicatorOrder(),
        )
        self._apply_expansion_state(expanded_keys)
        self._restore_selection(selected_entry_id)
        self.on_select()
        self._emit_page_status()

    def on_select(self):
        entry = self._selected_entry()
        if entry is None:
            self.end_btn.setEnabled(False)
            self.info_panel.set_entry(None, self._blank_extra_details())
            return

        self.end_btn.setEnabled(not entry["is_protected"])
        self.info_panel.set_entry(entry, self._entry_additional_details(entry))

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
                self.model.ensure_group_children_loaded(group_id)
                group_index = self.model.index_for_entry_id(group_id)
                if group_index.isValid():
                    self.tree.expand(group_index)
                index = self.model.index_for_entry_id(entry_id)
        if index.isValid():
            self.tree.setCurrentIndex(index)
            self.tree.scrollTo(index)

    def _expanded_group_keys(self):
        expanded_keys = set()
        for group in self.current_groups:
            index = self.model.index_for_entry_id(group["id"])
            if index.isValid() and self.tree.isExpanded(index):
                expanded_keys.add(group["group_key"])
        return expanded_keys

    def _icon_for_entry(self, entry):
        exe_path = entry["exe_path"]
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
        if not self.timer.isActive():
            self.timer.start(1250)
        self.request_refresh.emit()

    def _on_item_expanded(self, index):
        if index.data(ENTRY_KIND_ROLE) != "group":
            return
        group = index.data(ENTRY_ROLE)
        if not group:
            return
        self.model.ensure_group_children_loaded(group["id"])

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
        self.settings.setValue(f"processes/column_hidden_{column}", not visible)

    def _restore_column_visibility(self):
        for column in range(1, len(self._column_labels)):
            hidden = self.settings.value(f"processes/column_hidden_{column}", False, type=bool)
            self.tree.setColumnHidden(column, hidden)

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

    def _entry_additional_details(self, entry):
        cached = self.entry_detail_cache.get(entry["id"])
        if cached is not None:
            return cached

        extra = self._blank_extra_details()
        primary_pid = None
        if "pid" in entry:
            primary_pid = entry["pid"]
        elif entry.get("pids"):
            primary_pid = entry["pids"][0]

        if primary_pid is None:
            self.entry_detail_cache[entry["id"]] = extra
            return extra

        try:
            process = psutil.Process(primary_pid)
        except Exception:
            self.entry_detail_cache[entry["id"]] = extra
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

        try:
            command = " ".join(process.cmdline()).strip()
            extra["command"] = command or "Unavailable"
        except Exception:
            extra["command"] = "Unavailable"

        self.entry_detail_cache[entry["id"]] = extra
        return extra

    def _blank_extra_details(self):
        return {
            "user": "--",
            "started": "--",
            "threads": "--",
            "command": "--",
        }

    def _open_selected_location(self):
        entry = self.info_panel.current_entry or self._selected_entry()
        if entry:
            open_file_location(entry["exe_path"])

    def _search_selected_entry(self):
        entry = self.info_panel.current_entry or self._selected_entry()
        if entry:
            search_online(f"{entry['name']} {entry['publisher']}")

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
            if watched in (
                self.end_btn,
                self.info_panel.open_button,
                self.info_panel.search_button,
            ):
                return super().eventFilter(watched, event)
            if watched is not self.tree and not self.tree.isAncestorOf(watched):
                self.clear_selection()
        return super().eventFilter(watched, event)

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
