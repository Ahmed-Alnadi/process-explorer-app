import ctypes
import sys
import time

import psutil
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, QSettings, Qt, QTimer
from PySide6.QtGui import QActionGroup, QColor, QCloseEvent, QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QListWidgetItem, QStackedWidget, QLineEdit, QLabel, QFrame, QPushButton, QSplitter, QSplitterHandle, QMenu,
    QGraphicsDropShadowEffect, QStyle
)
from core.refresh_profiles import DEFAULT_REFRESH_PROFILE, REFRESH_PROFILES
from core.windows_native import native_uptime_seconds
from ui.details_tab import DetailsTab
from ui.processes_tab import ProcessesTab
from ui.performance_tab import PerformanceTab
from ui.services_tab import ServicesTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("CodexTaskManager", "TaskManagerClone")
        self._base_window_title = "Nadzilla PTM"
        self._is_admin = self._detect_admin_state()
        self._refresh_profile_name = self.settings.value(
            "main/refresh_profile",
            DEFAULT_REFRESH_PROFILE,
            type=str,
        )
        self._low_overhead_mode = self.settings.value("main/low_overhead_mode", False, type=bool)
        self._compact_mode = self.settings.value("main/compact_mode", False, type=bool)
        self._runtime_paused = False
        self._drag_active = False
        self._drag_offset = QPoint()
        self._resize_margin = 8
        self._resize_edges = set()
        self._resize_start_pos = QPoint()
        self._resize_start_geometry = QRect()
        if self._refresh_profile_name not in REFRESH_PROFILES:
            self._refresh_profile_name = DEFAULT_REFRESH_PROFILE

        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self._apply_window_title()
        self.resize(1720, 940)
        self.setMinimumSize(1540, 860)

        main_widget = QWidget()
        main_widget.setObjectName("windowRoot")
        self.setCentralWidget(main_widget)

        self.backdrop_orb_a = QFrame(main_widget)
        self.backdrop_orb_a.setObjectName("backdropOrbA")
        self.backdrop_orb_a.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.backdrop_orb_b = QFrame(main_widget)
        self.backdrop_orb_b.setObjectName("backdropOrbB")
        self.backdrop_orb_b.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(10)
        main_widget.setLayout(root_layout)

        self.title_bar = QFrame()
        self.title_bar.setObjectName("titleBar")
        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(16, 10, 10, 10)
        title_layout.setSpacing(10)
        self.title_bar.setLayout(title_layout)

        self.title_badge = QLabel("N")
        self.title_badge.setObjectName("titleBadge")
        self.title_badge.setFixedSize(QSize(28, 28))

        title_text_layout = QVBoxLayout()
        title_text_layout.setContentsMargins(0, 0, 0, 0)
        title_text_layout.setSpacing(0)
        self.window_title_label = QLabel(self._base_window_title)
        self.window_title_label.setObjectName("windowTitleLabel")
        self.window_status_label = QLabel("Process Task Manager")
        self.window_status_label.setObjectName("windowStatusLabel")
        title_text_layout.addWidget(self.window_title_label)
        title_text_layout.addWidget(self.window_status_label)

        self.minimize_button = QPushButton("-")
        self.minimize_button.setObjectName("titleBarButton")
        self.minimize_button.setToolTip("Minimize")
        self.minimize_button.clicked.connect(self.showMinimized)
        self.maximize_button = QPushButton("[]")
        self.maximize_button.setObjectName("titleBarButton")
        self.maximize_button.setToolTip("Maximize")
        self.maximize_button.clicked.connect(self._toggle_maximize_restore)
        self.close_button = QPushButton("X")
        self.close_button.setObjectName("titleBarCloseButton")
        self.close_button.setToolTip("Close")
        self.close_button.clicked.connect(self.close)

        title_layout.addWidget(self.title_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        title_layout.addLayout(title_text_layout)
        title_layout.addStretch()
        title_layout.addWidget(self.minimize_button)
        title_layout.addWidget(self.maximize_button)
        title_layout.addWidget(self.close_button)
        root_layout.addWidget(self.title_bar)

        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(10)
        root_layout.addLayout(main_layout, 1)

        # Sidebar
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("navSidebar")
        self._add_sidebar_item("Processes", QStyle.StandardPixmap.SP_ComputerIcon)
        self._add_sidebar_item("Details", QStyle.StandardPixmap.SP_FileDialogDetailedView)
        self._add_sidebar_item("Performance", QStyle.StandardPixmap.SP_DesktopIcon)
        self._add_sidebar_item("Services", QStyle.StandardPixmap.SP_FileDialogListView)
        self.sidebar.setMinimumWidth(120)
        self.sidebar.setMaximumWidth(320)

        # Right side
        right_layout = QVBoxLayout()
        right_layout.setSpacing(8)

        self.content_panel = QFrame()
        self.content_panel.setObjectName("contentPanel")
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(14, 14, 14, 14)
        content_layout.setSpacing(10)
        self.content_panel.setLayout(content_layout)

        self.header_title = QLabel("Control Center")
        self.header_title.setObjectName("headerTitle")
        self.header_subtitle = QLabel("Live process control with protected app handling.")
        self.header_subtitle.setObjectName("headerSubtitle")

        # Search bar
        self.search = QLineEdit()
        self.search.setObjectName("searchField")
        self.search.setPlaceholderText("Type a name, publisher, or PID to search")
        self.search.setClearButtonEnabled(True)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(120)
        self.search_timer.timeout.connect(self._apply_search_text)
        self.columns_button = QPushButton("Columns")
        self.columns_button.setObjectName("secondaryButton")
        self.columns_button.setToolTip("Choose which columns are visible on the current page.")
        self.columns_button.clicked.connect(self._show_columns_for_current_page)
        self.refresh_now_button = QPushButton("Refresh Now")
        self.refresh_now_button.setObjectName("secondaryButton")
        self.refresh_now_button.setProperty("accentAction", True)
        self.refresh_now_button.setProperty("flashActive", False)
        self.refresh_now_button.setToolTip("Immediately refresh the current page.")
        self.refresh_now_button.clicked.connect(self._manual_refresh_current_view)
        self.compact_button = QPushButton("Compact")
        self.compact_button.setObjectName("secondaryButton")
        self.compact_button.setProperty("toggleControl", True)
        self.compact_button.setCheckable(True)
        self.compact_button.setToolTip("Reduce row and sidebar spacing for a denser layout.")
        self.compact_button.toggled.connect(self._apply_compact_mode)
        self.low_overhead_button = QPushButton("Low Overhead")
        self.low_overhead_button.setObjectName("secondaryButton")
        self.low_overhead_button.setProperty("toggleControl", True)
        self.low_overhead_button.setCheckable(True)
        self.low_overhead_button.setToolTip("Use longer caches and lighter refresh work to reduce app overhead.")
        self.low_overhead_button.toggled.connect(self._apply_low_overhead_mode)
        self.refresh_button = QPushButton()
        self.refresh_button.setObjectName("secondaryButton")
        self.refresh_button.setProperty("accentAction", True)
        self.refresh_button.setProperty("menuControl", True)
        self.refresh_button.setProperty("flashActive", False)
        self.export_button = QPushButton("Export CSV")
        self.export_button.setObjectName("secondaryButton")
        self.export_button.setToolTip("Export the current page to a CSV file.")
        self.export_button.clicked.connect(self._export_current_view)
        self._build_refresh_menu()
        top_controls = QHBoxLayout()
        top_controls.setSpacing(10)
        top_controls.addWidget(self.search, 1)
        top_controls.addWidget(self.columns_button)
        top_controls.addWidget(self.refresh_now_button)
        top_controls.addWidget(self.compact_button)
        top_controls.addWidget(self.low_overhead_button)
        top_controls.addWidget(self.refresh_button)
        top_controls.addWidget(self.export_button)

        # Pages
        self.stack = QStackedWidget()
        self.stack.setObjectName("pageStack")
        self.processes_tab = ProcessesTab()
        self.details_tab = None
        self.details_host = QWidget()
        self.details_host_layout = QVBoxLayout()
        self.details_host_layout.setContentsMargins(0, 0, 0, 0)
        self.details_host_layout.setSpacing(0)
        self.details_host.setLayout(self.details_host_layout)
        self.performance_tab = PerformanceTab()
        self.services_tab = ServicesTab()
        self.page_status_cache = {}

        self.stack.addWidget(self.processes_tab)
        self.stack.addWidget(self.details_host)
        self.stack.addWidget(self.performance_tab)
        self.stack.addWidget(self.services_tab)

        content_layout.addWidget(self.header_title)
        content_layout.addWidget(self.header_subtitle)
        content_layout.addLayout(top_controls)
        content_layout.addWidget(self.stack)
        right_layout.addWidget(self.content_panel)

        right_container = QWidget()
        right_container.setLayout(right_layout)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(10)
        self.main_splitter.addWidget(self.sidebar)
        self.main_splitter.addWidget(right_container)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        main_layout.addWidget(self.main_splitter)

        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.currentRowChanged.connect(self.update_page_context)
        self.sidebar.currentRowChanged.connect(
            lambda row: self.settings.setValue("main/current_page", row)
        )
        self.search.textChanged.connect(self._schedule_search_update)
        self.search.installEventFilter(self)
        self.header_title.installEventFilter(self)
        self.header_subtitle.installEventFilter(self)
        self.content_panel.installEventFilter(self)
        for widget in (self.title_bar, self.title_badge, self.window_title_label, self.window_status_label):
            widget.installEventFilter(self)
        self.main_splitter.splitterMoved.connect(self._save_main_splitter_state)
        self._connect_page_status("Processes", self.processes_tab)
        self._connect_page_status("Services", self.services_tab)
        self.processes_tab.go_to_details_requested.connect(self._handle_go_to_details)
        self.processes_tab.go_to_service_requested.connect(self._handle_go_to_service)
        self.services_tab.go_to_process_requested.connect(self._handle_go_to_process)
        self.admin_status_label = QLabel("Administrator")
        self.admin_status_label.setObjectName("adminStatusBadge")
        self.admin_status_label.setToolTip("Running with elevated administrator privileges.")
        self.status_value_label = QLabel("Ready")
        self.status_value_label.setObjectName("statusBarLabel")
        self.counts_label = QLabel("Apps 0 | Background 0 | Windows 0 | Services 0")
        self.counts_label.setObjectName("statusBarLabel")
        self.last_refresh_label = QLabel("Last refresh: --")
        self.last_refresh_label.setObjectName("statusBarLabel")
        self.uptime_label = QLabel("Uptime: --")
        self.uptime_label.setObjectName("statusBarLabel")
        self.statusBar().setSizeGripEnabled(False)
        if self._is_admin:
            self.statusBar().addPermanentWidget(self.admin_status_label)
        self.statusBar().addPermanentWidget(self.status_value_label, 1)
        self.statusBar().addPermanentWidget(self.counts_label)
        self.statusBar().addPermanentWidget(self.last_refresh_label)
        self.statusBar().addPermanentWidget(self.uptime_label)
        self.refresh_shortcut = QShortcut(QKeySequence("F5"), self)
        self.refresh_shortcut.activated.connect(self._manual_refresh_current_view)
        self.find_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.find_shortcut.activated.connect(self._focus_search)
        self.delete_shortcut = QShortcut(QKeySequence("Del"), self)
        self.delete_shortcut.activated.connect(self._trigger_primary_action)
        self.footer_timer = QTimer(self)
        self.footer_timer.timeout.connect(self._update_footer_metrics)
        self.footer_timer.start(1000)
        QApplication.instance().applicationStateChanged.connect(
            lambda *_: QTimer.singleShot(0, self._update_runtime_pause_state)
        )
        self._restore_window_geometry()
        self._restore_main_splitter_state()
        self._apply_refresh_profile(self._refresh_profile_name)
        self._apply_compact_mode(self._compact_mode)
        self._apply_low_overhead_mode(self._low_overhead_mode)
        self.services_tab.refresh_now()
        self.sidebar.setCurrentRow(int(self.settings.value("main/current_page", 0)))
        self._update_footer_metrics()
        self._update_runtime_pause_state()
        self._apply_depth_effects()
        self._position_backdrop_orbs()
        self._update_title_bar_state()

    def update_page_context(self, index):
        if index == 1:
            self._ensure_details_tab()
            self.header_title.setText("Process Details")
            self.header_subtitle.setText("Per-process view with file actions and direct PID handling.")
            self.search.show()
            self.columns_button.show()
            self.details_tab.set_filter_text(self.search.text())
            self.processes_tab.set_active(False)
            self.details_tab.set_active(True)
            self.performance_tab.set_active(False)
            self.services_tab.set_active(False)
            self._update_status_bar("Process Details")
            return

        if index == 2:
            self.header_title.setText("Performance Lab")
            self.header_subtitle.setText("Live hardware and system throughput monitoring.")
            self.search.hide()
            self.columns_button.hide()
            self.processes_tab.set_active(False)
            if self.details_tab is not None:
                self.details_tab.set_active(False)
            self.performance_tab.set_active(True)
            self.services_tab.set_active(False)
            self._update_status_bar("Performance")
            return

        if index == 3:
            self.header_title.setText("Services")
            self.header_subtitle.setText("Live Windows service inventory with startup and status details.")
            self.search.show()
            self.columns_button.show()
            self.services_tab.set_filter_text(self.search.text())
            self.processes_tab.set_active(False)
            if self.details_tab is not None:
                self.details_tab.set_active(False)
            self.performance_tab.set_active(False)
            self.services_tab.set_active(True)
            self._update_status_bar("Services")
            return

        self.header_title.setText("Control Center")
        self.header_subtitle.setText("Live process control with protected app handling.")
        self.search.show()
        self.columns_button.show()
        self.processes_tab.set_filter_text(self.search.text())
        if self.details_tab is not None:
            self.details_tab.set_active(False)
        self.performance_tab.set_active(False)
        self.services_tab.set_active(False)
        self.processes_tab.set_active(True)
        self._update_status_bar("Processes")

    def closeEvent(self, event: QCloseEvent):
        self._save_window_geometry()
        self._save_main_splitter_state()
        self.processes_tab.shutdown()
        if self.details_tab is not None:
            self.details_tab.shutdown()
        self.performance_tab.shutdown()
        self.services_tab.shutdown()
        super().closeEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            self._update_title_bar_state()
        if event.type() in (
            QEvent.Type.WindowStateChange,
            QEvent.Type.ActivationChange,
            QEvent.Type.Show,
            QEvent.Type.Hide,
        ):
            QTimer.singleShot(0, self._update_runtime_pause_state)
        return super().event(event)

    def moveEvent(self, event):
        self._pause_live_updates()
        super().moveEvent(event)

    def resizeEvent(self, event):
        self._pause_live_updates()
        super().resizeEvent(event)
        self._position_backdrop_orbs()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.isMaximized():
            edges = self._resize_edges_for_pos(event.position().toPoint())
            if edges:
                self._resize_edges = edges
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_geometry = self.geometry()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_edges and event.buttons() & Qt.MouseButton.LeftButton:
            self._resize_from_global_pos(event.globalPosition().toPoint())
            event.accept()
            return
        self._update_resize_cursor(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_edges = set()
        self.unsetCursor()
        super().mouseReleaseEvent(event)

    def eventFilter(self, watched, event):
        if watched in (self.title_bar, self.title_badge, self.window_title_label, self.window_status_label):
            return self._handle_title_bar_event(watched, event)
        if event.type() == QEvent.Type.MouseButtonPress:
            if self._preserves_current_selection(watched, event):
                return super().eventFilter(watched, event)
            self._clear_current_selection()
        return super().eventFilter(watched, event)

    def _clear_current_selection(self):
        current_widget = self.stack.currentWidget()
        if current_widget is self.details_host and self.details_tab is not None:
            clear_selection = self.details_tab.clear_selection
        else:
            clear_selection = getattr(current_widget, "clear_selection", None)
        if callable(clear_selection):
            clear_selection()

    def _preserves_current_selection(self, watched, event=None):
        if isinstance(watched, QSplitterHandle):
            return True

        current_widget = self._current_page_widget()
        info_scroll = getattr(current_widget, "info_scroll", None)
        content_splitter = getattr(current_widget, "content_splitter", None)

        if info_scroll is not None:
            if watched in (info_scroll, getattr(current_widget, "info_panel", None)):
                return True
            if info_scroll.isAncestorOf(watched):
                return True

        if content_splitter is not None:
            if watched is content_splitter:
                return True
            if content_splitter.isAncestorOf(watched):
                return True
        if watched is self.content_panel and event is not None:
            click_pos = event.position().toPoint() if hasattr(event, "position") else QPoint()
            for candidate in (info_scroll, getattr(current_widget, "info_panel", None), content_splitter):
                if candidate is None or not candidate.isVisible():
                    continue
                top_left = candidate.mapTo(self.content_panel, QPoint(0, 0))
                if QRect(top_left, candidate.size()).contains(click_pos):
                    return True

        return False

    def _schedule_search_update(self, _text):
        self.search_timer.start()

    def _apply_search_text(self):
        text = self.search.text()
        current_index = self.sidebar.currentRow()
        if current_index == 1:
            self._ensure_details_tab()
            self.details_tab.set_filter_text(text)
            return
        if current_index == 3:
            self.services_tab.set_filter_text(text)
            return
        if current_index == 0:
            self.processes_tab.set_filter_text(text)

    def _pause_live_updates(self, duration_ms=220):
        widgets = [self.processes_tab, self.performance_tab, self.services_tab]
        if self.details_tab is not None:
            widgets.append(self.details_tab)
        for widget in widgets:
            pause_refresh = getattr(widget, "pause_refresh_temporarily", None)
            if callable(pause_refresh):
                pause_refresh(duration_ms)

    def _update_runtime_pause_state(self):
        paused = bool(self.isMinimized() or not self.isVisible())
        if paused == self._runtime_paused:
            return
        self._runtime_paused = paused
        widgets = [self.processes_tab, self.performance_tab, self.services_tab]
        if self.details_tab is not None:
            widgets.append(self.details_tab)
        for widget in widgets:
            set_runtime_paused = getattr(widget, "set_runtime_paused", None)
            if callable(set_runtime_paused):
                set_runtime_paused(paused)

    def _ensure_details_tab(self):
        if self.details_tab is not None:
            return
        self.details_tab = DetailsTab()
        self.details_tab.set_refresh_profile(self._refresh_profile_name)
        self.details_tab.set_compact_mode(self._compact_mode)
        self.details_tab.set_low_overhead_mode(self._low_overhead_mode)
        self.details_tab.set_filter_text(self.search.text())
        self.details_host_layout.addWidget(self.details_tab)
        self._connect_page_status("Process Details", self.details_tab)
        self.details_tab.go_to_service_requested.connect(self._handle_go_to_service)

    def _show_columns_for_current_page(self):
        widget = self._current_page_widget()
        show_columns = getattr(widget, "show_column_chooser", None)
        if callable(show_columns):
            show_columns(self.columns_button.mapToGlobal(self.columns_button.rect().bottomLeft()))

    def _current_page_widget(self):
        current_index = self.sidebar.currentRow()
        if current_index == 1:
            self._ensure_details_tab()
            return self.details_tab
        if current_index == 2:
            return self.performance_tab
        if current_index == 3:
            return self.services_tab
        return self.processes_tab

    def _connect_page_status(self, page_name, widget):
        widget.page_status_changed.connect(
            lambda text, name=page_name: self._handle_page_status(name, text)
        )

    def _handle_page_status(self, page_name, text):
        self.page_status_cache[page_name] = text
        current_page = self._page_name_for_index(self.sidebar.currentRow())
        if current_page == page_name:
            self.status_value_label.setText(text)
        self._update_footer_metrics()

    def _update_status_bar(self, page_name):
        self.status_value_label.setText(self.page_status_cache.get(page_name, page_name))
        self._update_footer_metrics()

    def _page_name_for_index(self, index):
        return {
            0: "Processes",
            1: "Process Details",
            2: "Performance",
            3: "Services",
        }.get(index, "Ready")

    def _save_main_splitter_state(self, *_args):
        self.settings.setValue("main/sidebar_splitter_state", self.main_splitter.saveState())

    def _restore_main_splitter_state(self):
        state = self.settings.value("main/sidebar_splitter_state")
        if state and self.main_splitter.restoreState(state):
            sizes = self.main_splitter.sizes()
            if len(sizes) == 2 and sizes[1] >= 600:
                return
        self.main_splitter.setSizes([136, 1560])

    def _save_window_geometry(self):
        self.settings.setValue("main/window_geometry", self.saveGeometry())
        self.settings.setValue("main/current_page", self.sidebar.currentRow())

    def _restore_window_geometry(self):
        geometry = self.settings.value("main/window_geometry")
        if geometry:
            self.restoreGeometry(geometry)

    def _add_sidebar_item(self, label, icon_kind):
        item = QListWidgetItem(self.style().standardIcon(icon_kind), label)
        self.sidebar.addItem(item)

    def _handle_title_bar_event(self, _watched, event):
        if event.type() == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            self._toggle_maximize_restore()
            return True
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_active = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            return True
        if event.type() == QEvent.Type.MouseMove and self._drag_active and event.buttons() & Qt.MouseButton.LeftButton:
            if self.isMaximized():
                return True
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            return True
        if event.type() == QEvent.Type.MouseButtonRelease:
            self._drag_active = False
            return True
        return False

    def _toggle_maximize_restore(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        self._update_title_bar_state()

    def _update_title_bar_state(self):
        maximized = self.isMaximized()
        self.maximize_button.setText("O" if maximized else "[]")
        self.maximize_button.setToolTip("Restore Down" if maximized else "Maximize")
        status_text = "Process Task Manager"
        if self._is_admin:
            status_text = f"{status_text}  |  Administrator"
        self.window_status_label.setText(status_text)

    def _resize_edges_for_pos(self, pos):
        rect = self.rect()
        margin = self._resize_margin
        if pos.x() < 0 or pos.y() < 0 or pos.x() > rect.width() or pos.y() > rect.height():
            return set()
        edges = set()
        if pos.x() <= margin:
            edges.add("left")
        elif pos.x() >= rect.width() - margin:
            edges.add("right")
        if pos.y() <= margin:
            edges.add("top")
        elif pos.y() >= rect.height() - margin:
            edges.add("bottom")
        return edges

    def _update_resize_cursor(self, pos):
        if self.isMaximized():
            self.unsetCursor()
            return
        edges = self._resize_edges_for_pos(pos)
        if not edges:
            self.unsetCursor()
            return
        if edges in ({"left", "top"}, {"right", "bottom"}):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif edges in ({"right", "top"}, {"left", "bottom"}):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif "left" in edges or "right" in edges:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeVerCursor)

    def _resize_from_global_pos(self, global_pos):
        geometry = QRect(self._resize_start_geometry)
        delta = global_pos - self._resize_start_pos
        minimum_width = self.minimumWidth()
        minimum_height = self.minimumHeight()

        if "left" in self._resize_edges:
            new_left = geometry.left() + delta.x()
            max_left = geometry.right() - minimum_width
            geometry.setLeft(min(new_left, max_left))
        if "right" in self._resize_edges:
            geometry.setRight(max(geometry.left() + minimum_width, geometry.right() + delta.x()))
        if "top" in self._resize_edges:
            new_top = geometry.top() + delta.y()
            max_top = geometry.bottom() - minimum_height
            geometry.setTop(min(new_top, max_top))
        if "bottom" in self._resize_edges:
            geometry.setBottom(max(geometry.top() + minimum_height, geometry.bottom() + delta.y()))

        self.setGeometry(geometry)

    def _apply_depth_effects(self):
        for widget, blur_radius, offset_y, alpha in (
            (self.sidebar, 34, 12, 72),
            (self.content_panel, 48, 18, 92),
        ):
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(blur_radius)
            shadow.setOffset(0, offset_y)
            shadow.setColor(QColor(3, 8, 16, alpha))
            widget.setGraphicsEffect(shadow)

    def _position_backdrop_orbs(self):
        root = self.centralWidget()
        if root is None:
            return
        root_rect = root.rect()
        orb_a_size = 360
        orb_b_size = 280
        self.backdrop_orb_a.setGeometry(
            root_rect.right() - orb_a_size - 92,
            34,
            orb_a_size,
            orb_a_size,
        )
        self.backdrop_orb_b.setGeometry(
            116,
            max(root_rect.bottom() - orb_b_size - 150, 40),
            orb_b_size,
            orb_b_size,
        )
        self.backdrop_orb_a.lower()
        self.backdrop_orb_b.lower()

    def _detect_admin_state(self):
        if sys.platform != "win32":
            return False
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def _apply_window_title(self):
        title = self._base_window_title
        if self._is_admin:
            title = f"{title} (Administrator)"
        self.setWindowTitle(title)
        if hasattr(self, "window_title_label"):
            self.window_title_label.setText(self._base_window_title)

    def _build_refresh_menu(self):
        self.refresh_menu = QMenu(self)
        self.refresh_menu.aboutToShow.connect(lambda: self._pause_live_updates(60000))
        self.refresh_menu.aboutToHide.connect(lambda: self._pause_live_updates(120))
        action_group = QActionGroup(self)
        action_group.setExclusive(True)
        for profile_name in REFRESH_PROFILES:
            action = self.refresh_menu.addAction(profile_name)
            action.setCheckable(True)
            action.setChecked(profile_name == self._refresh_profile_name)
            action.triggered.connect(
                lambda checked=False, name=profile_name: self._apply_refresh_profile(name)
            )
            action_group.addAction(action)
        self.refresh_button.setMenu(self.refresh_menu)
        self._update_refresh_button_label()

    def _update_refresh_button_label(self):
        self.refresh_button.setText(f"Refresh: {self._refresh_profile_name}")
        self.refresh_button.setProperty("refreshProfile", self._refresh_profile_name.lower())
        self.refresh_button.setToolTip(self._refresh_profile_tooltip(self._refresh_profile_name))
        self._repolish_widget(self.refresh_button)

    def _apply_compact_mode(self, enabled):
        enabled = bool(enabled)
        self._compact_mode = enabled
        self.settings.setValue("main/compact_mode", enabled)
        self.compact_button.blockSignals(True)
        self.compact_button.setChecked(enabled)
        self.compact_button.blockSignals(False)
        self.compact_button.setText("Compact On" if enabled else "Compact")
        self.sidebar.setProperty("compactMode", enabled)
        self._repolish_widget(self.sidebar)
        self.processes_tab.set_compact_mode(enabled)
        self.performance_tab.set_compact_mode(enabled)
        self.services_tab.set_compact_mode(enabled)
        if self.details_tab is not None:
            self.details_tab.set_compact_mode(enabled)

    def _apply_low_overhead_mode(self, enabled):
        enabled = bool(enabled)
        self._low_overhead_mode = enabled
        self.settings.setValue("main/low_overhead_mode", enabled)
        self.low_overhead_button.blockSignals(True)
        self.low_overhead_button.setChecked(enabled)
        self.low_overhead_button.blockSignals(False)
        self.low_overhead_button.setText("Low Overhead On" if enabled else "Low Overhead")
        self.processes_tab.set_low_overhead_mode(enabled)
        self.performance_tab.set_low_overhead_mode(enabled)
        self.services_tab.set_low_overhead_mode(enabled)
        if self.details_tab is not None:
            self.details_tab.set_low_overhead_mode(enabled)

    def _apply_refresh_profile(self, profile_name):
        if profile_name not in REFRESH_PROFILES:
            profile_name = DEFAULT_REFRESH_PROFILE
        self._refresh_profile_name = profile_name
        self.settings.setValue("main/refresh_profile", profile_name)
        self._update_refresh_button_label()
        self._flash_secondary_button(self.refresh_button)
        self.processes_tab.set_refresh_profile(profile_name)
        self.performance_tab.set_refresh_profile(profile_name)
        self.services_tab.set_refresh_profile(profile_name)
        if self.details_tab is not None:
            self.details_tab.set_refresh_profile(profile_name)

    def _export_current_view(self):
        widget = self._current_page_widget()
        export_csv = getattr(widget, "export_csv", None)
        if callable(export_csv):
            if export_csv():
                self.status_value_label.setText("Export completed.")
                self._update_footer_metrics()

    def _manual_refresh_current_view(self):
        widget = self._current_page_widget()
        refresh_now = getattr(widget, "refresh_now", None)
        if callable(refresh_now):
            refresh_now()
            self._flash_secondary_button(self.refresh_now_button)
            self.status_value_label.setText("Manual refresh requested.")
            self._update_footer_metrics()

    def _focus_search(self):
        if self.search.isVisible():
            self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
            self.search.selectAll()

    def _trigger_primary_action(self):
        if self.search.hasFocus():
            return
        widget = self._current_page_widget()
        trigger_action = getattr(widget, "trigger_primary_action", None)
        if callable(trigger_action):
            trigger_action()

    def _handle_go_to_details(self, entry):
        self._ensure_details_tab()
        target_pid = entry.get("pid") or (entry.get("pids") or [None])[0]
        search_text = str(target_pid) if target_pid and len(entry.get("pids", [])) <= 1 else entry["name"]
        self.search.setText(search_text)
        self.sidebar.setCurrentRow(1)
        if target_pid is not None:
            self.details_tab.focus_pid(target_pid)

    def _handle_go_to_service(self, service_name):
        if not service_name:
            return
        self.search.setText(service_name)
        self.sidebar.setCurrentRow(3)
        self.services_tab.focus_service(service_name)

    def _handle_go_to_process(self, pid):
        if not pid:
            return
        self._ensure_details_tab()
        self.search.setText(str(pid))
        self.sidebar.setCurrentRow(1)
        self.details_tab.focus_pid(pid)

    def _update_footer_metrics(self):
        current_widget = self._current_page_widget()
        refresh_text_getter = getattr(current_widget, "status_refresh_text", None)
        if callable(refresh_text_getter):
            self.last_refresh_label.setText(refresh_text_getter())
        else:
            self.last_refresh_label.setText("Last refresh: --")
        process_counts = self.processes_tab.footer_counts()
        service_count = self.services_tab.visible_service_count()
        self.counts_label.setText(
            f"Apps {process_counts['apps']} | Background {process_counts['background']} | Windows {process_counts['windows']} | Services {service_count}"
        )
        self.uptime_label.setText(f"Uptime: {self._format_uptime()}")

    def _format_uptime(self):
        uptime_seconds = native_uptime_seconds()
        if uptime_seconds is None:
            uptime_seconds = max(time.time() - psutil.boot_time(), 0)
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        if days > 0:
            return f"{days}d {hours}h {minutes}m"
        return f"{hours}h {minutes}m"

    def _repolish_widget(self, widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _flash_secondary_button(self, button, duration_ms=650):
        button.setProperty("flashActive", True)
        self._repolish_widget(button)
        QTimer.singleShot(duration_ms, lambda btn=button: self._clear_button_flash(btn))

    def _clear_button_flash(self, button):
        if button is None:
            return
        button.setProperty("flashActive", False)
        self._repolish_widget(button)

    def _refresh_profile_tooltip(self, profile_name):
        descriptions = {
            "High": "Update more aggressively for the live-est view.",
            "Normal": "Balanced refresh speed for everyday use.",
            "Low": "Refresh less often to reduce UI churn.",
            "Paused": "Stop live refreshing until you refresh manually or choose another speed.",
        }
        return descriptions.get(profile_name, "Choose how often the app refreshes live data.")
