import ctypes
import os
import re
import time
from ctypes import wintypes

try:
    import winreg
except ImportError:  # pragma: no cover - Windows-only module
    winreg = None


UNKNOWN_PUBLISHER = "Unknown"
APPROVAL_BASE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
APPROVAL_ENABLED = 0x02
APPROVAL_DISABLED = 0x03
APPROVAL_ENABLED_DELAYED = 0x06


def _filetime_now_bytes():
    filetime = int((time.time() + 11644473600) * 10000000)
    return filetime.to_bytes(8, "little", signed=False)


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
            (
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                "Current User Run",
                "HKCU",
                "Run",
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                "Machine Run",
                "HKLM",
                "Run",
            ),
            (
                winreg.HKEY_LOCAL_MACHINE,
                r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
                "Machine Run (32-bit)",
                "HKLM",
                "Run32",
            ),
        ]

        entries = []
        for hive, key_path, location, hive_name, approval_section in run_keys:
            try:
                with winreg.OpenKey(hive, key_path) as key:
                    index = 0
                    while True:
                        name, value, _ = winreg.EnumValue(key, index)
                        command = str(value).strip()
                        target_path = self._extract_target_path(command)
                        metadata = self._metadata_for_path(target_path)
                        status, enabled = self._approval_status(hive_name, approval_section, name)
                        entries.append(
                            {
                                "id": f"startup:registry:{location}:{name.lower()}",
                                "name": name,
                                "status": status,
                                "enabled": enabled,
                                "publisher": metadata["company"],
                                "location": location,
                                "command": command or "Unavailable",
                                "target_path": target_path,
                                "description": metadata["description"],
                                "source_type": "registry",
                                "approval_hive": hive_name,
                                "approval_section": approval_section,
                                "approval_name": name,
                                "supports_toggle": True,
                            }
                        )
                        index += 1
            except OSError:
                continue

        return entries

    def _folder_startup_entries(self):
        entries = []
        folders = [
            (
                os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup"),
                "User Startup Folder",
                "HKCU",
            ),
            (
                os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs\StartUp"),
                "Common Startup Folder",
                "HKLM",
            ),
        ]

        for folder, location, hive_name in folders:
            if not folder or not os.path.isdir(folder):
                continue

            for name in os.listdir(folder):
                path = os.path.join(folder, name)
                if not os.path.isfile(path):
                    continue

                metadata = self._metadata_for_path(path)
                status, enabled = self._approval_status(hive_name, "StartupFolder", name)
                entries.append(
                    {
                        "id": f"startup:folder:{location}:{name.lower()}",
                        "name": os.path.splitext(name)[0],
                        "status": status,
                        "enabled": enabled,
                        "publisher": metadata["company"],
                        "location": location,
                        "command": path,
                        "target_path": path,
                        "description": metadata["description"],
                        "source_type": "folder",
                        "approval_hive": hive_name,
                        "approval_section": "StartupFolder",
                        "approval_name": name,
                        "supports_toggle": True,
                    }
                )

        return entries

    def set_entry_enabled(self, entry, enabled):
        if winreg is None:
            return False, "Startup management is only available on Windows."
        if not entry or not entry.get("supports_toggle"):
            return False, "This startup entry cannot be changed."

        hive_name = entry.get("approval_hive")
        section = entry.get("approval_section")
        value_name = entry.get("approval_name")
        if not hive_name or not section or not value_name:
            return False, "Windows did not expose a writable startup control for this item."

        try:
            self._write_approval_value(hive_name, section, value_name, bool(enabled))
        except PermissionError:
            return False, "Administrator rights are required to change this startup entry."
        except OSError as error:
            return False, f"Windows rejected the startup change: {error}"

        return True, ""

    def _approval_status(self, hive_name, section, value_name):
        raw = self._approval_value(hive_name, section, value_name)
        if not raw:
            return "Enabled", True

        state = raw[0]
        if state == APPROVAL_DISABLED:
            return "Disabled", False
        if state == APPROVAL_ENABLED_DELAYED:
            return "Enabled", True
        return "Enabled", True

    def _approval_value(self, hive_name, section, value_name):
        hive = self._hive_from_name(hive_name)
        if hive is None or winreg is None:
            return None
        key_path = f"{APPROVAL_BASE_KEY}\\{section}"
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
                return bytes(value) if isinstance(value, (bytes, bytearray)) else None
        except OSError:
            return None

    def _write_approval_value(self, hive_name, section, value_name, enabled):
        hive = self._hive_from_name(hive_name)
        if hive is None or winreg is None:
            raise OSError("Unsupported startup approval hive.")

        key_path = f"{APPROVAL_BASE_KEY}\\{section}"
        access = getattr(winreg, "KEY_SET_VALUE", 0) | getattr(winreg, "KEY_QUERY_VALUE", 0)
        with winreg.CreateKeyEx(hive, key_path, 0, access) as key:
            state = APPROVAL_ENABLED if enabled else APPROVAL_DISABLED
            data = bytes([state, 0, 0, 0]) + _filetime_now_bytes()
            winreg.SetValueEx(key, value_name, 0, winreg.REG_BINARY, data)

    def _hive_from_name(self, hive_name):
        if winreg is None:
            return None
        return {
            "HKCU": winreg.HKEY_CURRENT_USER,
            "HKLM": winreg.HKEY_LOCAL_MACHINE,
        }.get((hive_name or "").upper())

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
