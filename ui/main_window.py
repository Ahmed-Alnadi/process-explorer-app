from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QStackedWidget, QLineEdit, QLabel, QFrame
)
from ui.details_tab import DetailsTab
from ui.processes_tab import ProcessesTab
from ui.performance_tab import PerformanceTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Task Manager Clone")
        self.resize(1600, 880)
        self.setMinimumSize(1450, 820)

        main_widget = QWidget()
        main_widget.setObjectName("windowRoot")
        self.setCentralWidget(main_widget)

        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(18)
        main_widget.setLayout(main_layout)

        # Sidebar
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("navSidebar")
        self.sidebar.addItem("Processes")
        self.sidebar.addItem("Details")
        self.sidebar.addItem("Performance")
        self.sidebar.setFixedWidth(190)

        # Right side
        right_layout = QVBoxLayout()
        right_layout.setSpacing(14)

        self.content_panel = QFrame()
        self.content_panel.setObjectName("contentPanel")
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(14)
        self.content_panel.setLayout(content_layout)

        self.header_title = QLabel("Control Center")
        self.header_title.setObjectName("headerTitle")
        self.header_subtitle = QLabel("Live process control with protected app handling.")
        self.header_subtitle.setObjectName("headerSubtitle")

        # Search bar
        self.search = QLineEdit()
        self.search.setObjectName("searchField")
        self.search.setPlaceholderText("Type a name, publisher, or PID to search")
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(120)
        self.search_timer.timeout.connect(self._apply_search_text)

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

        self.stack.addWidget(self.processes_tab)
        self.stack.addWidget(self.details_host)
        self.stack.addWidget(self.performance_tab)

        content_layout.addWidget(self.header_title)
        content_layout.addWidget(self.header_subtitle)
        content_layout.addWidget(self.search)
        content_layout.addWidget(self.stack)

        right_layout.addWidget(self.content_panel)

        main_layout.addWidget(self.sidebar)
        main_layout.addLayout(right_layout)

        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.currentRowChanged.connect(self.update_page_context)
        self.search.textChanged.connect(self._schedule_search_update)
        self.search.installEventFilter(self)
        self.header_title.installEventFilter(self)
        self.header_subtitle.installEventFilter(self)
        self.content_panel.installEventFilter(self)
        self.sidebar.setCurrentRow(0)

    def update_page_context(self, index):
        if index == 1:
            self._ensure_details_tab()
            self.header_title.setText("Process Details")
            self.header_subtitle.setText("Per-process view with file actions and direct PID handling.")
            self.search.show()
            self.details_tab.set_filter_text(self.search.text())
            self.processes_tab.set_active(False)
            self.details_tab.set_active(True)
            self.performance_tab.set_active(False)
            return

        if index == 2:
            self.header_title.setText("Performance Lab")
            self.header_subtitle.setText("Live hardware and system throughput monitoring.")
            self.search.hide()
            self.processes_tab.set_active(False)
            if self.details_tab is not None:
                self.details_tab.set_active(False)
            self.performance_tab.set_active(True)
            return

        self.header_title.setText("Control Center")
        self.header_subtitle.setText("Live process control with protected app handling.")
        self.search.show()
        self.processes_tab.set_filter_text(self.search.text())
        if self.details_tab is not None:
            self.details_tab.set_active(False)
        self.performance_tab.set_active(False)
        self.processes_tab.set_active(True)

    def closeEvent(self, event: QCloseEvent):
        self.processes_tab.shutdown()
        if self.details_tab is not None:
            self.details_tab.shutdown()
        super().closeEvent(event)

    def moveEvent(self, event):
        self._pause_live_updates()
        super().moveEvent(event)

    def resizeEvent(self, event):
        self._pause_live_updates()
        super().resizeEvent(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress:
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

    def _schedule_search_update(self, _text):
        self.search_timer.start()

    def _apply_search_text(self):
        text = self.search.text()
        current_index = self.sidebar.currentRow()
        if current_index == 1:
            self._ensure_details_tab()
            self.details_tab.set_filter_text(text)
            return
        if current_index == 0:
            self.processes_tab.set_filter_text(text)

    def _pause_live_updates(self):
        widgets = [self.processes_tab, self.performance_tab]
        if self.details_tab is not None:
            widgets.append(self.details_tab)
        for widget in widgets:
            pause_refresh = getattr(widget, "pause_refresh_temporarily", None)
            if callable(pause_refresh):
                pause_refresh(500)

    def _ensure_details_tab(self):
        if self.details_tab is not None:
            return
        self.details_tab = DetailsTab()
        self.details_tab.set_filter_text(self.search.text())
        self.details_host_layout.addWidget(self.details_tab)
