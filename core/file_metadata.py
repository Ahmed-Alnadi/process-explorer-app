import ctypes
from ctypes import wintypes


UNKNOWN_PUBLISHER = "Unknown"
_METADATA_CACHE = {}


def metadata_for_exe(exe_path):
    normalized_path = (exe_path or "").strip().lower()
    if not normalized_path:
        return {
            "company": UNKNOWN_PUBLISHER,
            "description": "",
            "product_name": "",
        }

    cached_metadata = _METADATA_CACHE.get(normalized_path)
    if cached_metadata is not None:
        return cached_metadata

    version_strings = read_version_strings(
        exe_path,
        ["CompanyName", "FileDescription", "ProductName"],
    ) or {}
    if not hasattr(version_strings, "get"):
        version_strings = {}
    metadata = {
        "company": version_strings.get("CompanyName") or UNKNOWN_PUBLISHER,
        "description": version_strings.get("FileDescription") or "",
        "product_name": version_strings.get("ProductName") or "",
    }
    _METADATA_CACHE[normalized_path] = metadata
    return metadata


def read_version_strings(exe_path, keys):
    try:
        version = ctypes.WinDLL("version", use_last_error=True)
        GetFileVersionInfoSizeW = version.GetFileVersionInfoSizeW
        GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        GetFileVersionInfoSizeW.restype = wintypes.DWORD

        GetFileVersionInfoW = version.GetFileVersionInfoW
        GetFileVersionInfoW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        GetFileVersionInfoW.restype = wintypes.BOOL

        VerQueryValueW = version.VerQueryValueW
        VerQueryValueW.argtypes = [
            wintypes.LPCVOID,
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.UINT),
        ]
        VerQueryValueW.restype = wintypes.BOOL

        handle = wintypes.DWORD(0)
        size = GetFileVersionInfoSizeW(exe_path, ctypes.byref(handle))
        if size == 0:
            return {}

        buffer = ctypes.create_string_buffer(size)
        if not GetFileVersionInfoW(exe_path, 0, size, buffer):
            return {}

        translation_ptr = ctypes.c_void_p()
        translation_len = wintypes.UINT(0)
        translations = []
        if VerQueryValueW(
            buffer,
            "\\VarFileInfo\\Translation",
            ctypes.byref(translation_ptr),
            ctypes.byref(translation_len),
        ) and translation_ptr.value and translation_len.value >= 4:
            raw_translations = ctypes.cast(
                translation_ptr, ctypes.POINTER(ctypes.c_ushort)
            )
            translation_count = translation_len.value // 4
            for index in range(translation_count):
                base = index * 2
                translations.append((raw_translations[base], raw_translations[base + 1]))

        if not translations:
            translations.append((0x0409, 0x04B0))

        values = {}
        for key in keys:
            for language, code_page in translations:
                query = f"\\StringFileInfo\\{language:04x}{code_page:04x}\\{key}"
                value_ptr = ctypes.c_void_p()
                value_len = wintypes.UINT(0)
                if not VerQueryValueW(
                    buffer,
                    query,
                    ctypes.byref(value_ptr),
                    ctypes.byref(value_len),
                ):
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
