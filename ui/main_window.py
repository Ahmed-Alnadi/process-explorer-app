from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QListWidget, QStackedWidget, QLineEdit, QLabel, QFrame
)
from ui.processes_tab import ProcessesTab
from ui.performance_tab import PerformanceTab


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Task Manager Clone")
        self.resize(1100, 700)

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

        # Pages
        self.stack = QStackedWidget()
        self.stack.setObjectName("pageStack")
        self.processes_tab = ProcessesTab()
        self.performance_tab = PerformanceTab()

        self.stack.addWidget(self.processes_tab)
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
        self.search.textChanged.connect(self.processes_tab.set_filter_text)
        self.sidebar.setCurrentRow(0)

    def update_page_context(self, index):
        if index == 1:
            self.header_title.setText("Performance Lab")
            self.header_subtitle.setText("Live hardware and system throughput monitoring.")
            self.search.hide()
            return

        self.header_title.setText("Control Center")
        self.header_subtitle.setText("Live process control with protected app handling.")
        self.search.show()
