import ctypes
import shutil
import subprocess
import time
from ctypes import wintypes

import psutil


PROTECTED_PROCESS_NAMES = {"servicehub.power"}
UNKNOWN_PUBLISHER = "Unknown"


class ProcessTerminationBlockedError(RuntimeError):
    pass


class ProcessManager:
    def __init__(self):
        self._last_disk = {}
        self._last_cpu = {}
        self._last_sample_time = time.time()
        self._last_system_disk = None
        self._last_disk_active_time_percent = 0.0
        self._cpu_count = max(psutil.cpu_count() or 1, 1)
        self._memory_total = max(psutil.virtual_memory().total, 1)
        self._publisher_cache = {}
        self._last_window_scan_update = 0.0
        self._cached_window_titles_by_pid = {}
        self._nvidia_smi_path = shutil.which("nvidia-smi") or "C:\\Windows\\System32\\nvidia-smi.exe"
        self._last_temperature_update = 0.0
        self._cached_gpu_temp_c = None

    def list_processes(self):
        current_time = time.time()
        interval = max(current_time - self._last_sample_time, 0.001)
        self._last_sample_time = current_time

        total_disk_bytes = self._sample_system_disk(interval)
        window_titles_by_pid = self._visible_windows_by_pid()
        active_pids = set()
        grouped_processes = {}

        for proc in psutil.process_iter(
            ["pid", "name", "memory_info", "io_counters", "exe", "cpu_times"]
        ):
            try:
                pid = proc.info["pid"]
                raw_name = proc.info.get("name") or "Unknown"
                if raw_name.lower() == "system idle process":
                    continue

                exe_path = proc.info.get("exe") or ""
                display_name = self._display_name(raw_name)
                publisher = self._publisher_for_exe(exe_path)
                is_protected = self.is_protected_process(raw_name)
                window_titles = window_titles_by_pid.get(pid, [])

                active_pids.add(pid)

                cpu_percent = self._sample_process_cpu_percent(proc, interval)

                memory_info = proc.info.get("memory_info")
                memory_bytes = memory_info.rss if memory_info else 0
                memory_mb = memory_bytes / (1024 * 1024)
                memory_percent = (memory_bytes / self._memory_total) * 100

                disk_io = proc.info.get("io_counters")
                process_disk_bytes = self._sample_process_disk_bytes(pid, disk_io)
                process_disk_mb_per_sec = process_disk_bytes / (1024 * 1024) / interval
                if total_disk_bytes > 0:
                    disk_percent = min((process_disk_bytes / total_disk_bytes) * 100, 100.0)
                else:
                    disk_percent = 0.0

                child = {
                    "id": f"pid:{pid}",
                    "group_key": self._group_key(raw_name, exe_path),
                    "raw_name": raw_name,
                    "name": display_name,
                    "publisher": publisher,
                    "pid": pid,
                    "pids": [pid],
                    "exe_path": exe_path,
                    "cpu_percent": cpu_percent,
                    "cpu_display": f"{cpu_percent:.1f}%",
                    "memory_percent": memory_percent,
                    "memory_display": f"{memory_percent:.1f}%",
                    "memory_tooltip": self._format_memory_usage(memory_mb),
                    "disk_percent": disk_percent,
                    "disk_display": f"{disk_percent:.1f}%",
                    "disk_tooltip": self._format_disk_usage(process_disk_mb_per_sec),
                    "window_display": self._format_window_display(window_titles),
                    "window_tooltip": self._format_window_tooltip(window_titles),
                    "has_window": bool(window_titles),
                    "is_protected": is_protected,
                }

                group = grouped_processes.get(child["group_key"])
                if group is None:
                    group = {
                        "id": f"group:{child['group_key']}",
                        "group_key": child["group_key"],
                        "name": display_name,
                        "publisher": publisher,
                        "process_count": 0,
                        "pids": [],
                        "exe_path": exe_path,
                        "cpu_percent": 0.0,
                        "memory_percent": 0.0,
                        "memory_mb": 0.0,
                        "disk_percent": 0.0,
                        "disk_mb_per_sec": 0.0,
                        "window_titles": [],
                        "has_window": False,
                        "is_protected": False,
                        "children": [],
                    }
                    grouped_processes[child["group_key"]] = group

                group["process_count"] += 1
                group["pids"].append(pid)
                if not group["exe_path"] and exe_path:
                    group["exe_path"] = exe_path
                group["publisher"] = self._merge_publishers(group["publisher"], publisher)
                group["cpu_percent"] += cpu_percent
                group["memory_percent"] += memory_percent
                group["memory_mb"] += memory_mb
                group["disk_percent"] += disk_percent
                group["disk_mb_per_sec"] += process_disk_mb_per_sec
                group["window_titles"] = self._merge_window_titles(group["window_titles"], window_titles)
                group["has_window"] = group["has_window"] or bool(window_titles)
                group["is_protected"] = group["is_protected"] or is_protected
                group["children"].append(child)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        self._last_disk = {
            pid: counters for pid, counters in self._last_disk.items() if pid in active_pids
        }
        self._last_cpu = {
            pid: cpu_time for pid, cpu_time in self._last_cpu.items() if pid in active_pids
        }

        groups = []
        for group in grouped_processes.values():
            group["pids"].sort()
            group["children"].sort(key=lambda child: (child["name"].lower(), child["pid"]))
            group["cpu_percent"] = min(group["cpu_percent"], 100.0)
            group["disk_percent"] = min(group["disk_percent"], 100.0)
            group["cpu_display"] = f"{group['cpu_percent']:.1f}%"
            group["memory_display"] = f"{group['memory_percent']:.1f}%"
            group["memory_tooltip"] = self._format_memory_usage(group["memory_mb"])
            group["disk_display"] = f"{group['disk_percent']:.1f}%"
            group["disk_tooltip"] = self._format_disk_usage(group["disk_mb_per_sec"])
            group["window_display"] = self._format_window_display(group["window_titles"])
            group["window_tooltip"] = self._format_window_tooltip(group["window_titles"])
            groups.append(group)

        groups.sort(key=lambda group: group["name"].lower())
        return groups

    def system_summary(self):
        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        disk_active_time_percent = self._last_disk_active_time_percent
        gpu_temp_c = self._gpu_temperature()
        return {
            "cpu_percent": cpu_percent,
            "cpu_display": f"CPU: {cpu_percent:.1f}%",
            "memory_percent": memory.percent,
            "memory_display": f"Physical Memory: {memory.percent:.1f}%",
            "disk_active_time_percent": disk_active_time_percent,
            "disk_active_time_display": f"Disk Active Time: {disk_active_time_percent:.1f}%",
            "gpu_temp_c": gpu_temp_c,
            "gpu_temp_display": self._format_temperature_display("GPU Temp", gpu_temp_c),
        }

    def is_protected_process(self, name):
        normalized_name = (name or "").strip().lower()
        return normalized_name in PROTECTED_PROCESS_NAMES

    def terminate_processes(self, pids):
        protected_names = []
        processes = []
        for pid in pids:
            try:
                process = psutil.Process(pid)
                name = process.name()
                if self.is_protected_process(name):
                    protected_names.append(name)
                    continue
                processes.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if protected_names:
            raise ProcessTerminationBlockedError(
                f"{protected_names[0]} is protected and cannot be ended from this app."
            )

        for process in processes:
            process.terminate()

    def _sample_process_cpu_percent(self, proc, interval):
        cpu_times = proc.info.get("cpu_times")
        if not cpu_times:
            return 0.0

        pid = proc.info["pid"]
        total_cpu_time = cpu_times.user + cpu_times.system
        previous_cpu_time = self._last_cpu.get(pid)
        self._last_cpu[pid] = total_cpu_time

        if previous_cpu_time is None:
            return 0.0

        elapsed_cpu_time = max(total_cpu_time - previous_cpu_time, 0.0)
        cpu_percent = (elapsed_cpu_time / (interval * self._cpu_count)) * 100
        return min(max(cpu_percent, 0.0), 100.0)

    def _sample_process_disk_bytes(self, pid, io_counters):
        if not io_counters:
            return 0

        previous_read, previous_write = self._last_disk.get(pid, (0, 0))
        current_read = io_counters.read_bytes
        current_write = io_counters.write_bytes
        self._last_disk[pid] = (current_read, current_write)

        return max((current_read - previous_read) + (current_write - previous_write), 0)

    def _sample_system_disk(self, interval):
        disk_counters = psutil.disk_io_counters()
        if disk_counters is None:
            self._last_system_disk = None
            self._last_disk_active_time_percent = 0.0
            return 0

        current_snapshot = {
            "read_bytes": disk_counters.read_bytes,
            "write_bytes": disk_counters.write_bytes,
            "read_time": getattr(disk_counters, "read_time", 0),
            "write_time": getattr(disk_counters, "write_time", 0),
        }
        if self._last_system_disk is None:
            self._last_system_disk = current_snapshot
            self._last_disk_active_time_percent = 0.0
            return 0

        total_bytes = max(
            (current_snapshot["read_bytes"] - self._last_system_disk["read_bytes"])
            + (current_snapshot["write_bytes"] - self._last_system_disk["write_bytes"]),
            0,
        )
        busy_time_ms = max(
            (current_snapshot["read_time"] - self._last_system_disk["read_time"])
            + (current_snapshot["write_time"] - self._last_system_disk["write_time"]),
            0,
        )
        self._last_system_disk = current_snapshot
        self._last_disk_active_time_percent = min(
            (busy_time_ms / max(interval * 1000.0, 1.0)) * 100.0,
            100.0,
        )
        return total_bytes

    def _publisher_for_exe(self, exe_path):
        normalized_path = (exe_path or "").strip().lower()
        if not normalized_path:
            return UNKNOWN_PUBLISHER

        cached_publisher = self._publisher_cache.get(normalized_path)
        if cached_publisher is not None:
            return cached_publisher

        publisher = self._read_company_name(exe_path) or UNKNOWN_PUBLISHER
        self._publisher_cache[normalized_path] = publisher
        return publisher

    def _read_company_name(self, exe_path):
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
                return None

            buffer = ctypes.create_string_buffer(size)
            if not GetFileVersionInfoW(exe_path, 0, size, buffer):
                return None

            translation_ptr = ctypes.c_void_p()
            translation_len = wintypes.UINT(0)
            if not VerQueryValueW(
                buffer,
                "\\VarFileInfo\\Translation",
                ctypes.byref(translation_ptr),
                ctypes.byref(translation_len),
            ):
                return None

            translation = ctypes.cast(
                translation_ptr, ctypes.POINTER(ctypes.c_ushort)
            )
            language = translation[0]
            code_page = translation[1]
            query = f"\\StringFileInfo\\{language:04x}{code_page:04x}\\CompanyName"

            value_ptr = ctypes.c_void_p()
            value_len = wintypes.UINT(0)
            if not VerQueryValueW(
                buffer,
                query,
                ctypes.byref(value_ptr),
                ctypes.byref(value_len),
            ):
                return None

            if not value_ptr.value:
                return None

            return ctypes.wstring_at(value_ptr.value).strip() or None
        except Exception:
            return None

    def _format_memory_usage(self, memory_mb):
        if memory_mb >= 1024:
            return f"{memory_mb / 1024:.1f} GB in use"
        return f"{memory_mb:.1f} MB in use"

    def _format_disk_usage(self, disk_mb_per_sec):
        if disk_mb_per_sec > 0.1:
            return f"{disk_mb_per_sec:.1f} MB/s of disk traffic"
        return "No measurable disk traffic"

    def _group_key(self, name, exe_path):
        normalized_name = (name or "").strip().lower()
        normalized_path = (exe_path or "").strip().lower()
        return normalized_path or normalized_name

    def _display_name(self, name):
        normalized_name = (name or "Unknown").strip()
        if normalized_name.lower().endswith(".exe"):
            return normalized_name[:-4]
        return normalized_name

    def _merge_publishers(self, current_publisher, new_publisher):
        if current_publisher == new_publisher:
            return current_publisher
        if current_publisher == UNKNOWN_PUBLISHER:
            return new_publisher
        if new_publisher == UNKNOWN_PUBLISHER:
            return current_publisher
        return "Multiple"

    def _gpu_temperature(self):
        current_time = time.time()
        if current_time - self._last_temperature_update < 5.0:
            return self._cached_gpu_temp_c

        self._last_temperature_update = current_time
        self._cached_gpu_temp_c = self._read_nvidia_gpu_temperature()
        return self._cached_gpu_temp_c

    def _visible_windows_by_pid(self):
        current_time = time.time()
        if current_time - self._last_window_scan_update < 3.0:
            return self._cached_window_titles_by_pid

        user32 = ctypes.windll.user32
        titles_by_pid = {}

        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        GetWindowTextW = user32.GetWindowTextW
        IsWindowVisible = user32.IsWindowVisible
        GetWindowThreadProcessId = user32.GetWindowThreadProcessId

        def callback(hwnd, lparam):
            if not IsWindowVisible(hwnd):
                return True

            length = GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True

            buffer = ctypes.create_unicode_buffer(length + 1)
            GetWindowTextW(hwnd, buffer, len(buffer))
            title = buffer.value.strip()
            if not title:
                return True

            pid = wintypes.DWORD()
            GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            titles = titles_by_pid.setdefault(pid.value, [])
            if title not in titles:
                titles.append(title)
            return True

        user32.EnumWindows(EnumWindowsProc(callback), 0)
        self._last_window_scan_update = current_time
        self._cached_window_titles_by_pid = titles_by_pid
        return titles_by_pid

    def _format_window_display(self, window_titles):
        if not window_titles:
            return "Background"
        if len(window_titles) == 1:
            return window_titles[0]
        return f"{len(window_titles)} windows"

    def _format_window_tooltip(self, window_titles):
        if not window_titles:
            return "No visible top-level window"
        return "\n".join(window_titles)

    def _merge_window_titles(self, existing_titles, new_titles):
        merged = list(existing_titles)
        for title in new_titles:
            if title not in merged:
                merged.append(title)
        return merged

    def _read_nvidia_gpu_temperature(self):
        if not self._nvidia_smi_path:
            return None

        try:
            result = subprocess.run(
                [
                    self._nvidia_smi_path,
                    "--query-gpu=temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=1.0,
                check=True,
            )
        except Exception:
            return None

        temperatures = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                temperatures.append(float(line))
            except ValueError:
                continue

        if not temperatures:
            return None

        return max(temperatures)

    def _format_temperature_display(self, label, temperature_c):
        if temperature_c is None:
            return f"{label}: N/A"
        return f"{label}: {temperature_c:.0f} C"
