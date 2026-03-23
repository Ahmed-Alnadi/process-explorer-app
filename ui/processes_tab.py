from PySide6.QtCore import QFileInfo, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileIconProvider,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.process_manager import ProcessManager, ProcessTerminationBlockedError


SORT_ROLE = int(Qt.ItemDataRole.UserRole)
ENTRY_ROLE = SORT_ROLE + 1
ENTRY_ID_ROLE = SORT_ROLE + 2
ENTRY_KIND_ROLE = SORT_ROLE + 3


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


class ProcessesTab(QWidget):
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
        summary_layout.addWidget(self.cpu_summary_label)
        summary_layout.addWidget(self.memory_summary_label)
        summary_layout.addWidget(self.disk_summary_label)
        summary_layout.addWidget(self.gpu_summary_label)
        summary_layout.addStretch()
        layout.addLayout(summary_layout)

        self.tree = ClearableTreeWidget()
        self.tree.setObjectName("processTree")
        self.tree.setColumnCount(8)
        self.tree.setHeaderLabels(
            ["Name", "Type", "Publisher", "Window", "PID / Count", "CPU %", "Memory %", "Disk"]
        )
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setRootIsDecorated(True)
        self.tree.setItemsExpandable(True)
        self.tree.setAllColumnsShowFocus(True)
        self.tree.setSortingEnabled(True)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
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
        self.current_groups = []
        self.icon_provider = QFileIconProvider()
        self.default_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self.icon_cache = {}

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_tree)
        self.timer.start(1500)

        self.tree.itemSelectionChanged.connect(self.on_select)
        self.tree.sortItems(5, Qt.SortOrder.DescendingOrder)
        self.update_tree()

    def mousePressEvent(self, event):
        self.tree.clearSelection()
        self.tree.setCurrentItem(None)
        self.on_select()
        super().mousePressEvent(event)

    def update_tree(self):
        selected_entry_id = self._selected_entry_id()
        expanded_keys = self._expanded_group_keys()

        groups = self.process_manager.list_processes()
        self.current_groups = self._filter_groups(groups)

        summary = self.process_manager.system_summary()
        self.cpu_summary_label.setText(summary["cpu_display"])
        self.memory_summary_label.setText(summary["memory_display"])
        self.disk_summary_label.setText(summary["disk_active_time_display"])
        self.gpu_summary_label.setText(summary["gpu_temp_display"])

        self.tree.setSortingEnabled(False)
        self.tree.clear()

        for group in self.current_groups:
            group_item = self._build_group_item(group)
            self.tree.addTopLevelItem(group_item)

            for child in group["children"]:
                child_item = self._build_child_item(child)
                group_item.addChild(child_item)

            if self.filter_text or group["group_key"] in expanded_keys:
                group_item.setExpanded(True)

        self.tree.setSortingEnabled(True)
        self.tree.sortItems(self.tree.sortColumn(), self.tree.header().sortIndicatorOrder())
        self._restore_selection(selected_entry_id)
        self.on_select()

    def on_select(self):
        entry = self._selected_entry()
        if entry is None:
            self.end_btn.setEnabled(False)
            return

        self.end_btn.setEnabled(not entry["is_protected"])

    def end_task(self):
        entry = self._selected_entry()
        if entry is None:
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
        self.update_tree()

    def _build_group_item(self, group):
        item = SortableTreeWidgetItem()
        display_name = group["name"]
        if group["process_count"] > 1:
            display_name = f"{display_name} ({group['process_count']})"

        item.setText(0, display_name)
        item.setText(1, group["type_display"])
        item.setText(2, group["publisher"])
        item.setText(3, group["window_display"])
        item.setText(4, str(group["process_count"]))
        item.setText(5, group["cpu_display"])
        item.setText(6, group["memory_display"])
        item.setText(7, group["disk_display"])
        item.setIcon(0, self._icon_for_entry(group))

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
        item.setData(0, ENTRY_ROLE, group)
        item.setData(0, ENTRY_ID_ROLE, group["id"])
        item.setData(0, ENTRY_KIND_ROLE, "group")

        name_tooltips = []
        if group["is_protected"]:
            name_tooltips.append("Protected process group: cannot be ended from this app.")
        if group["exe_path"]:
            name_tooltips.append(group["exe_path"])
        if name_tooltips:
            item.setToolTip(0, "\n".join(name_tooltips))
        item.setToolTip(3, group["window_tooltip"])
        item.setToolTip(6, group["memory_tooltip"])
        item.setToolTip(7, group["disk_tooltip"])
        return item

    def _build_child_item(self, child):
        item = SortableTreeWidgetItem()
        item.setText(0, child["name"])
        item.setText(1, child["type_display"])
        item.setText(2, child["publisher"])
        item.setText(3, child["window_display"])
        item.setText(4, str(child["pid"]))
        item.setText(5, child["cpu_display"])
        item.setText(6, child["memory_display"])
        item.setText(7, child["disk_display"])
        item.setIcon(0, self._icon_for_entry(child))

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
        item.setData(0, ENTRY_ROLE, child)
        item.setData(0, ENTRY_ID_ROLE, child["id"])
        item.setData(0, ENTRY_KIND_ROLE, "child")

        name_tooltips = []
        if child["is_protected"]:
            name_tooltips.append("Protected process: cannot be ended from this app.")
        if child["exe_path"]:
            name_tooltips.append(child["exe_path"])
        if name_tooltips:
            item.setToolTip(0, "\n".join(name_tooltips))
        item.setToolTip(3, child["window_tooltip"])
        item.setToolTip(6, child["memory_tooltip"])
        item.setToolTip(7, child["disk_tooltip"])
        return item

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
            entry["window_display"].lower(),
            entry["exe_path"].lower(),
        ]
        if "pid" in entry:
            searchable.append(str(entry["pid"]))
        if "pids" in entry:
            searchable.append(" ".join(str(pid) for pid in entry["pids"]))
        return any(self.filter_text in value for value in searchable)

    def _selected_entry(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        return item.data(0, ENTRY_ROLE)

    def _selected_entry_id(self):
        entry = self._selected_entry()
        if entry is None:
            return None
        return entry["id"]

    def _restore_selection(self, entry_id):
        if entry_id is None:
            return

        for index in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(index)
            if group_item.data(0, ENTRY_ID_ROLE) == entry_id:
                self.tree.setCurrentItem(group_item)
                return

            for child_index in range(group_item.childCount()):
                child_item = group_item.child(child_index)
                if child_item.data(0, ENTRY_ID_ROLE) == entry_id:
                    group_item.setExpanded(True)
                    self.tree.setCurrentItem(child_item)
                    return

    def _expanded_group_keys(self):
        expanded_keys = set()
        for index in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(index)
            entry = group_item.data(0, ENTRY_ROLE)
            if entry and group_item.isExpanded():
                expanded_keys.add(entry["group_key"])
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
            item.setData(column, SORT_ROLE, value)
