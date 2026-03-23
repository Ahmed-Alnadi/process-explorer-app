import os
import subprocess
import webbrowser
from urllib.parse import quote_plus

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def open_file_location(path):
    if not path:
        return

    normalized_path = os.path.normpath(path)
    if os.path.isfile(normalized_path):
        try:
            subprocess.Popen(["explorer.exe", "/select,", normalized_path])
            return
        except Exception:
            parent_dir = os.path.dirname(normalized_path)
            if parent_dir:
                os.startfile(parent_dir)
                return

    if os.path.isdir(normalized_path):
        os.startfile(normalized_path)


def open_properties(path):
    if not path:
        return

    normalized_path = os.path.normpath(path)
    try:
        os.startfile(normalized_path, "properties")
        return
    except Exception:
        pass

    try:
        escaped_path = normalized_path.replace("'", "''")
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"Start-Process -FilePath '{escaped_path}' -Verb Properties",
            ]
        )
        return
    except Exception:
        pass

    try:
        import ctypes

        ctypes.windll.shell32.ShellExecuteW(
            None,
            "properties",
            normalized_path,
            None,
            None,
            1,
        )
        return
    except Exception:
        open_file_location(normalized_path)


def search_online(query):
    if not query:
        return

    url = f"https://www.google.com/search?q={quote_plus(query)}"
    if QDesktopServices.openUrl(QUrl(url)):
        return

    try:
        os.startfile(url)
        return
    except Exception:
        pass

    webbrowser.open_new_tab(url)
