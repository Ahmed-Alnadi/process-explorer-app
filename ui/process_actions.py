import os
import subprocess
import webbrowser
from urllib.parse import quote_plus


def open_file_location(path):
    if not path:
        return

    normalized_path = os.path.normpath(path)
    if os.path.isfile(normalized_path):
        try:
            subprocess.Popen(["explorer", f"/select,{normalized_path}"])
            return
        except Exception:
            os.startfile(os.path.dirname(normalized_path))
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

    webbrowser.open(f"https://www.google.com/search?q={quote_plus(query)}")
