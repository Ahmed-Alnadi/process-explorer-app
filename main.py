import ctypes
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from core.theme_manager import resource_path
from ui.main_window import MainWindow


def set_app_user_model_id():
    if sys.platform != "win32":
        return

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Nadzilla.PTM"
        )
    except Exception:
        pass

if __name__ == "__main__":
    set_app_user_model_id()
    app = QApplication(sys.argv)

    icon_path = resource_path("assets/task_manager_icon.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    with resource_path("styles/dark_theme.qss").open("r", encoding="utf-8") as f:
        app.setStyleSheet(f.read())

    window = MainWindow()
    if not app.windowIcon().isNull():
        window.setWindowIcon(app.windowIcon())
    window.show()

    sys.exit(app.exec())
