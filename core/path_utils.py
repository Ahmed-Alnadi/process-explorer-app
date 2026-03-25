import ctypes
import os
import shutil
from ctypes import wintypes


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
KNOWN_EXECUTABLE_EXTENSIONS = (
    ".exe",
    ".com",
    ".bat",
    ".cmd",
    ".msc",
    ".ps1",
    ".vbs",
    ".js",
)


def normalize_windows_path(path):
    candidate = (path or "").strip().strip('"').strip("'")
    if not candidate:
        return ""

    system_root = os.environ.get("SystemRoot") or "C:\\Windows"
    lowered = candidate.lower()
    if lowered.startswith("\\??\\"):
        candidate = candidate[4:]
        lowered = candidate.lower()
    if lowered.startswith("\\systemroot\\"):
        candidate = os.path.join(system_root, candidate[len("\\systemroot\\") :].lstrip("\\/"))
    elif lowered.startswith("system32\\"):
        candidate = os.path.join(system_root, candidate)

    candidate = os.path.expandvars(candidate)
    return os.path.normpath(candidate)


def resolve_command_path(command_line):
    command = (command_line or "").strip()
    if not command:
        return ""

    candidate = command
    if command[0] in ('"', "'"):
        end_quote = command.find(command[0], 1)
        if end_quote > 1:
            candidate = command[1:end_quote]
    else:
        lowered = command.lower()
        end_position = -1
        for extension in KNOWN_EXECUTABLE_EXTENSIONS:
            position = lowered.find(extension)
            if position >= 0:
                end_position = position + len(extension)
                break
        if end_position > 0:
            candidate = command[:end_position]
        else:
            candidate = command.split(None, 1)[0]

    return resolve_existing_path(candidate)


def resolve_existing_path(path):
    candidate = normalize_windows_path(path)
    if not candidate:
        return ""

    if os.path.exists(candidate):
        return candidate

    if not os.path.isabs(candidate):
        resolved = shutil.which(candidate)
        if resolved:
            return os.path.normpath(resolved)

        system_root = os.environ.get("SystemRoot") or "C:\\Windows"
        for parent in (system_root, os.path.join(system_root, "System32")):
            joined = os.path.normpath(os.path.join(parent, candidate))
            if os.path.exists(joined):
                return joined

    return candidate if os.path.isabs(candidate) else ""


def query_process_image_path(pid):
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        OpenProcess = kernel32.OpenProcess
        OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        OpenProcess.restype = wintypes.HANDLE

        QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
        QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        QueryFullProcessImageNameW.restype = wintypes.BOOL

        CloseHandle = kernel32.CloseHandle
        CloseHandle.argtypes = [wintypes.HANDLE]
        CloseHandle.restype = wintypes.BOOL

        handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return ""

        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return ""
            return resolve_existing_path(buffer.value)
        finally:
            CloseHandle(handle)
    except Exception:
        return ""
