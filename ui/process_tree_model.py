from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PySide6.QtWidgets import QTreeView


class ProcessNode:
    def __init__(self, entry=None, kind="root", parent=None):
        self.entry = entry
        self.kind = kind
        self.parent = parent
        self.children = []
        self.children_loaded = False

    def child(self, row):
        if 0 <= row < len(self.children):
            return self.children[row]
        return None

    def row(self):
        if self.parent is None:
            return 0
        return self.parent.children.index(self)


class ClearableTreeView(QTreeView):
    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if not index.isValid():
            self.clearSelection()
            self.setCurrentIndex(QModelIndex())
            event.accept()
            return
        super().mousePressEvent(event)


class ProcessTreeModel(QAbstractItemModel):
    def __init__(self, icon_resolver, roles, parent=None):
        super().__init__(parent)
        self._icon_resolver = icon_resolver
        self._roles = roles
        self._headers = [
            "Name",
            "Type",
            "Publisher",
            "Window",
            "PID / Count",
            "CPU %",
            "Memory %",
            "Disk (0%)",
        ]
        self._root = ProcessNode()
        self._node_by_id = {}
        self._sort_column = 5
        self._sort_order = Qt.SortOrder.DescendingOrder

    def set_groups(self, groups, expanded_group_keys=None, load_all_children=False):
        expanded_group_keys = expanded_group_keys or set()

        self.beginResetModel()
        self._root.children = []
        self._node_by_id = {}

        for group in groups:
            group_node = ProcessNode(group, "group", self._root)
            self._node_by_id[group["id"]] = group_node
            should_load_children = load_all_children or group["group_key"] in expanded_group_keys
            if should_load_children:
                self._populate_group_children(group_node)
            self._root.children.append(group_node)

        self._sort_children(self._root)
        self.endResetModel()

    def ensure_group_children_loaded(self, group_id):
        group_node = self._node_by_id.get(group_id)
        if group_node is None or group_node.kind != "group" or group_node.children_loaded:
            return

        children = self._sorted_entries(group_node.entry["children"])
        if not children:
            group_node.children_loaded = True
            return

        parent_index = self.index_for_entry_id(group_id)
        self.beginInsertRows(parent_index, 0, len(children) - 1)
        for child in children:
            child_node = ProcessNode(child, "child", group_node)
            group_node.children.append(child_node)
            self._node_by_id[child["id"]] = child_node
        group_node.children_loaded = True
        self.endInsertRows()

    def index_for_entry_id(self, entry_id, column=0):
        node = self._node_by_id.get(entry_id)
        return self.index_for_node(node, column)

    def index_for_node(self, node, column=0):
        if node is None or node is self._root:
            return QModelIndex()
        return self.createIndex(node.row(), column, node)

    def set_disk_header_percent(self, disk_active_percent):
        label = f"Disk ({disk_active_percent:.0f}%)"
        if self._headers[7] == label:
            return
        self._headers[7] = label
        self.headerDataChanged.emit(Qt.Orientation.Horizontal, 7, 7)

    def rowCount(self, parent=QModelIndex()):
        node = self.node_from_index(parent)
        if node.kind == "group" and not node.children_loaded:
            return 0
        return len(node.children)

    def columnCount(self, parent=QModelIndex()):
        return len(self._headers)

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()

        parent_node = self.node_from_index(parent)
        child_node = parent_node.child(row)
        if child_node is None:
            return QModelIndex()
        return self.createIndex(row, column, child_node)

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()

        node = self.node_from_index(index)
        parent_node = node.parent
        if parent_node is None or parent_node is self._root:
            return QModelIndex()
        return self.createIndex(parent_node.row(), 0, parent_node)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        node = self.node_from_index(index)
        entry = node.entry
        column = index.column()
        roles = self._roles

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_value(entry, node.kind, column)
        if role == Qt.ItemDataRole.DecorationRole and column == 0:
            return self._icon_resolver(entry)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip_value(entry, column)
        if role == roles["sort"]:
            return self._sort_value(entry, node.kind, column)
        if role == roles["entry"]:
            return entry
        if role == roles["entry_id"]:
            return entry["id"]
        if role == roles["entry_kind"]:
            return node.kind
        if role == roles["secondary"] and column == 6:
            return entry["memory_value_display"]
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._headers[section]
        return super().headerData(section, orientation, role)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def hasChildren(self, parent=QModelIndex()):
        node = self.node_from_index(parent)
        if node.kind == "group":
            return bool(node.entry["children"])
        return super().hasChildren(parent)

    def sort(self, column, order=Qt.SortOrder.AscendingOrder):
        self._sort_column = column
        self._sort_order = order
        self.layoutAboutToBeChanged.emit()
        self._sort_children(self._root)
        self.layoutChanged.emit()

    def node_from_index(self, index):
        if index.isValid():
            return index.internalPointer()
        return self._root

    def _populate_group_children(self, group_node):
        group_node.children = []
        for child in self._sorted_entries(group_node.entry["children"]):
            child_node = ProcessNode(child, "child", group_node)
            group_node.children.append(child_node)
            self._node_by_id[child["id"]] = child_node
        group_node.children_loaded = True

    def _sorted_entries(self, entries):
        reverse = self._sort_order == Qt.SortOrder.DescendingOrder
        return sorted(
            entries,
            key=lambda entry: self._sort_value(entry, "child", self._sort_column),
            reverse=reverse,
        )

    def _sort_children(self, parent_node):
        reverse = self._sort_order == Qt.SortOrder.DescendingOrder
        parent_node.children.sort(
            key=lambda node: self._sort_value(node.entry, node.kind, self._sort_column),
            reverse=reverse,
        )
        for child in parent_node.children:
            if child.children_loaded and child.children:
                self._sort_children(child)

    def _display_value(self, entry, kind, column):
        if kind == "group":
            if column == 0:
                if entry["process_count"] > 1:
                    return f"{entry['name']} ({entry['process_count']})"
                return entry["name"]
            if column == 1:
                return entry["type_display"]
            if column == 2:
                return entry["publisher"]
            if column == 3:
                return entry["window_display"]
            if column == 4:
                return str(entry["process_count"])
            if column == 5:
                return entry["cpu_display"]
            if column == 6:
                return entry["memory_display"]
            if column == 7:
                return entry["disk_display"]
            return ""

        if column == 0:
            return entry["name"]
        if column == 1:
            return entry["type_display"]
        if column == 2:
            return entry["publisher"]
        if column == 3:
            return entry["window_display"]
        if column == 4:
            return str(entry["pid"])
        if column == 5:
            return entry["cpu_display"]
        if column == 6:
            return entry["memory_display"]
        if column == 7:
            return entry["disk_display"]
        return ""

    def _tooltip_value(self, entry, column):
        if column == 0:
            parts = []
            if entry["is_protected"]:
                if "process_count" in entry:
                    parts.append("Protected process group: cannot be ended from this app.")
                else:
                    parts.append("Protected process: cannot be ended from this app.")
            if entry["exe_path"]:
                parts.append(entry["exe_path"])
            return "\n".join(parts)
        if column == 3:
            return entry["window_tooltip"]
        if column == 6:
            return entry["memory_tooltip"]
        if column == 7:
            return entry["disk_tooltip"]
        return ""

    def _sort_value(self, entry, kind, column):
        if column == 0:
            return entry["name"].lower()
        if column == 1:
            return entry["type_display"].lower()
        if column == 2:
            return entry["publisher"].lower()
        if column == 3:
            return (0 if entry["has_window"] else 1, entry["window_display"].lower())
        if column == 4:
            return entry["process_count"] if kind == "group" else entry["pid"]
        if column == 5:
            return entry["cpu_percent"]
        if column == 6:
            return entry["memory_percent"]
        if column == 7:
            if kind == "group":
                return entry["disk_mb_per_sec"]
            return entry["disk_rate_mb_per_sec"]
        return entry["name"].lower()
