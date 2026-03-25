from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QFont


class FlatEntryTableModel(QAbstractTableModel):
    def __init__(
        self,
        *,
        headers,
        roles,
        display_resolver,
        sort_resolver,
        filter_resolver,
        icon_resolver=None,
        tooltip_resolver=None,
        background_resolver=None,
        secondary_resolver=None,
        tabular_columns=None,
        alignment_columns=None,
        entry_kind="row",
        parent=None,
    ):
        super().__init__(parent)
        self._headers = list(headers)
        self._roles = roles
        self._display_resolver = display_resolver
        self._sort_resolver = sort_resolver
        self._filter_resolver = filter_resolver
        self._icon_resolver = icon_resolver
        self._tooltip_resolver = tooltip_resolver
        self._background_resolver = background_resolver
        self._secondary_resolver = secondary_resolver
        self._tabular_columns = set(tabular_columns or set())
        self._alignment_columns = set(alignment_columns or set())
        self._entry_kind = entry_kind
        self._entries = []
        self._tabular_font = self._build_tabular_font()

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._entries)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        entry = self._entries[index.row()]
        column = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_resolver(entry, column)
        if role == Qt.ItemDataRole.DecorationRole and column == 0 and self._icon_resolver:
            return self._icon_resolver(entry)
        if role == Qt.ItemDataRole.ToolTipRole and self._tooltip_resolver:
            return self._tooltip_resolver(entry, column)
        if role == Qt.ItemDataRole.BackgroundRole and self._background_resolver:
            return self._background_resolver(entry, column)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column in self._alignment_columns:
                return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.FontRole and column in self._tabular_columns:
            return self._tabular_font
        if role == self._roles["sort"]:
            return self._sort_resolver(entry, column)
        if role == self._roles["entry"]:
            return entry
        if role == self._roles["entry_id"]:
            return entry["id"]
        if role == self._roles["entry_kind"]:
            return self._entry_kind
        if role == self._roles["secondary"] and self._secondary_resolver:
            return self._secondary_resolver(entry, column)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._headers[section]
        return super().headerData(section, orientation, role)

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def set_header_label(self, column, label):
        if 0 <= column < len(self._headers) and self._headers[column] != label:
            self._headers[column] = label
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, column, column)

    def sync_entries(self, entries):
        incoming_entries = list(entries)
        incoming_by_id = {entry["id"]: entry for entry in incoming_entries}

        for row in range(len(self._entries) - 1, -1, -1):
            if self._entries[row]["id"] in incoming_by_id:
                continue
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._entries[row]
            self.endRemoveRows()

        existing_ids = {entry["id"] for entry in self._entries}
        for row, entry in enumerate(list(self._entries)):
            updated_entry = incoming_by_id.get(entry["id"])
            if updated_entry is None:
                continue
            if entry == updated_entry:
                continue
            self._entries[row] = updated_entry
            self.dataChanged.emit(
                self.index(row, 0),
                self.index(row, len(self._headers) - 1),
            )

        new_entries = [entry for entry in incoming_entries if entry["id"] not in existing_ids]
        if new_entries:
            start_row = len(self._entries)
            end_row = start_row + len(new_entries) - 1
            self.beginInsertRows(QModelIndex(), start_row, end_row)
            self._entries.extend(new_entries)
            self.endInsertRows()

        desired_order = [entry["id"] for entry in incoming_entries]
        current_order = [entry["id"] for entry in self._entries]
        if desired_order != current_order:
            self.layoutAboutToBeChanged.emit()
            self._entries = [incoming_by_id[entry_id] for entry_id in desired_order]
            self.layoutChanged.emit()

    def clear_entries(self):
        if not self._entries:
            return
        self.beginResetModel()
        self._entries = []
        self.endResetModel()

    def index_for_entry_id(self, entry_id, column=0):
        for row, entry in enumerate(self._entries):
            if entry["id"] == entry_id:
                return self.index(row, column)
        return QModelIndex()

    def entry_for_row(self, row):
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None

    def filter_text_for_entry(self, row):
        entry = self.entry_for_row(row)
        if entry is None:
            return ""
        return self._filter_resolver(entry)

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


class EntryFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, *, roles, parent=None):
        super().__init__(parent)
        self._roles = roles
        self._filter_text = ""
        self.setDynamicSortFilter(True)
        self.setSortRole(roles["sort"])

    def set_filter_text(self, text):
        normalized_text = (text or "").strip().lower()
        if normalized_text == self._filter_text:
            return
        self._filter_text = normalized_text
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self._filter_text:
            return True

        source_model = self.sourceModel()
        if source_model is None:
            return True

        filter_text = source_model.filter_text_for_entry(source_row)
        return self._filter_text in (filter_text or "").lower()
