import ctypes
import os
import re
from ctypes import wintypes

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only module
    winreg = None


UNKNOWN_PUBLISHER = "Unknown"


class StartupManager:
    def __init__(self):
        self._metadata_cache = {}

    def list_startup_apps(self):
        entries = []
        seen_ids = set()

        for entry in self._registry_startup_entries():
            if entry["id"] in seen_ids:
                continue
            seen_ids.add(entry["id"])
            entries.append(entry)

        for entry in self._folder_startup_entries():
            if entry["id"] in seen_ids:
                continue
            seen_ids.add(entry["id"])
            entries.append(entry)

        entries.sort(key=lambda item: (item["name"].lower(), item["location"].lower()))
        return entries

    def _registry_startup_entries(self):
        if winreg is None:
            return []

        run_keys = [
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "Current User Run"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "Machine Run"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "Machine Run (32-bit)"),
        ]

        entries = []
        for hive, key_path, location in run_keys:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    index = 0
                    while True:
                        name, value, _ = winreg.EnumValue(key, index)
                        command = str(value).strip()
                        target_path = self._extract_target_path(command)
                        metadata = self._metadata_for_path(target_path)
                        entries.append(
                            {
                                "id": f"startup:registry:{location}:{name.lower()}",
                                "name": name,
                                "status": "Enabled",
                                "publisher": metadata["company"],
                                "location": location,
                                "command": command or "Unavailable",
                                "target_path": target_path,
                                "description": metadata["description"],
                            }
                        )
                        index += 1
            except OSError:
                continue

        return entries

    def _folder_startup_entries(self):
        entries = []
        folders = [
            (os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup"), "User Startup Folder"),
            (os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs\StartUp"), "Common Startup Folder"),
        ]

        for folder, location in folders:
            if not folder or not os.path.isdir(folder):
                continue

            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if not os.path.isfile(path):
                    continue

                metadata = self._metadata_for_path(path)
                entries.append(
                    {
                        "id": f"startup:folder:{location}:{name.lower()}",
                        "name": os.path.splitext(name)[0],
                        "status": "Enabled",
                        "publisher": metadata["company"],
                        "location": location,
                        "command": path,
                        "target_path": path,
                        "description": metadata["description"],
                    }
                )

        return entries

    def _extract_target_path(self, command):
        normalized = os.path.expandvars((command or "").strip())
        if not normalized:
            return ""

        if normalized.startswith('"'):
            end_quote = normalized.find('"', 1)
            if end_quote > 1:
                return os.path.normpath(normalized[1:end_quote])

        match = re.match(r"([^\s]+?\.(?:exe|com|bat|cmd|msc|lnk))", normalized, re.IGNORECASE)
        if match:
            return os.path.normpath(match.group(1))

        first_token = normalized.split(" ", 1)[0]
        return os.path.normpath(first_token) if first_token else ""

    def _metadata_for_path(self, path):
        normalized_path = (path or "").strip().lower()
        if not normalized_path:
            return {
                "company": UNKNOWN_PUBLISHER,
                "description": "",
            }

        cached = self._metadata_cache.get(normalized_path)
        if cached is not None:
            return cached

        metadata = self._read_version_strings(path, ["CompanyName", "FileDescription"])
        result = {
            "company": metadata.get("CompanyName") or UNKNOWN_PUBLISHER,
            "description": metadata.get("FileDescription") or "",
        }
        self._metadata_cache[normalized_path] = result
        return result

    def _read_version_strings(self, path, keys):
        try:
            version = ctypes.WinDLL("version", use_last_error=True)
            get_size = version.GetFileVersionInfoSizeW
            get_size.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
            get_size.restype = wintypes.DWORD

            get_info = version.GetFileVersionInfoW
            get_info.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
            get_info.restype = wintypes.BOOL

            query_value = version.VerQueryValueW
            query_value.argtypes = [
                wintypes.LPCVOID,
                wintypes.LPCWSTR,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(wintypes.UINT),
            ]
            query_value.restype = wintypes.BOOL

            handle = wintypes.DWORD(0)
            size = get_size(path, ctypes.byref(handle))
            if size == 0:
                return {}

            buffer = ctypes.create_string_buffer(size)
            if not get_info(path, 0, size, buffer):
                return {}

            translation_ptr = ctypes.c_void_p()
            translation_len = wintypes.UINT(0)
            translations = []
            if query_value(
                buffer,
                "\\VarFileInfo\\Translation",
                ctypes.byref(translation_ptr),
                ctypes.byref(translation_len),
            ) and translation_ptr.value and translation_len.value >= 4:
                raw = ctypes.cast(translation_ptr, ctypes.POINTER(ctypes.c_ushort))
                count = translation_len.value // 4
                for index in range(count):
                    base = index * 2
                    translations.append((raw[base], raw[base + 1]))

            if not translations:
                translations.append((0x0409, 0x04B0))

            values = {}
            for key in keys:
                for language, code_page in translations:
                    block = f"\\StringFileInfo\\{language:04x}{code_page:04x}\\{key}"
                    value_ptr = ctypes.c_void_p()
                    value_len = wintypes.UINT(0)
                    if not query_value(buffer, block, ctypes.byref(value_ptr), ctypes.byref(value_len)):
                        continue
                    if not value_ptr.value:
                        continue
                    value = ctypes.wstring_at(value_ptr.value).strip()
                    if value:
                        values[key] = value
                        break
            return values
        except Exception:
            return {}
