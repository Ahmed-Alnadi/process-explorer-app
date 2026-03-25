import os
import subprocess
import webbrowser
from urllib.parse import quote_plus

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

from core.subprocess_utils import hidden_subprocess_kwargs


def open_file_location(path):
    if not path:
        return False, "No accessible executable path is available for this item."

    normalized_path = os.path.normpath(path)
    if os.path.isfile(normalized_path):
        try:
            subprocess.Popen(["explorer.exe", "/select,", normalized_path])
            return True, ""
        except Exception:
            parent_dir = os.path.dirname(normalized_path)
            if parent_dir:
                try:
                    os.startfile(parent_dir)
                    return True, ""
                except Exception as error:
                    return False, f"Could not open the parent folder: {error}"

    if os.path.isdir(normalized_path):
        try:
            os.startfile(normalized_path)
            return True, ""
        except Exception as error:
            return False, f"Could not open the folder: {error}"

    parent_dir = os.path.dirname(normalized_path)
    if parent_dir and os.path.isdir(parent_dir):
        try:
            os.startfile(parent_dir)
            return True, ""
        except Exception as error:
            return False, f"Could not open the parent folder: {error}"

    return False, "Windows did not expose an accessible local file location for this item."


def open_properties(path):
    if not path:
        return False

    normalized_path = os.path.normpath(path)
    try:
        os.startfile(normalized_path, "properties")
        return True
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
            ],
            **hidden_subprocess_kwargs(),
        )
        return True
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
        return True
    except Exception:
        open_file_location(normalized_path)
        return False


def search_online(query):
    if not query:
        return False

    exact_query = (query or "").strip()
    if not exact_query:
        return False
    if not (exact_query.startswith('"') and exact_query.endswith('"')):
        exact_query = f'"{exact_query}"'

    url = f"https://www.google.com/search?q={quote_plus(exact_query)}"
    if QDesktopServices.openUrl(QUrl(url)):
        return True

    try:
        os.startfile(url)
        return True
    except Exception:
        pass

    webbrowser.open_new_tab(url)
    return True


def copy_text_to_clipboard(text):
    clipboard = QApplication.clipboard()
    if clipboard is None:
        return False
    clipboard.setText(text or "")
    return True
