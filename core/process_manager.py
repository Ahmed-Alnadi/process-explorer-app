import ctypes
import os
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
        self._last_disk_active_time_percent = 0.0
        self._last_disk_active_time_update = 0.0
        self._cpu_count = max(psutil.cpu_count() or 1, 1)
        self._memory_total = max(psutil.virtual_memory().total, 1)
        self._memory_cache = {}
        self._memory_cache_ttl = 3.5
        self._metadata_cache = {}
        self._window_scan_ttl = 5.0
        self._last_window_scan_update = 0.0
        self._cached_window_titles_by_pid = {}
        self._nvidia_smi_path = shutil.which("nvidia-smi") or "C:\\Windows\\System32\\nvidia-smi.exe"
        self._last_temperature_update = 0.0
        self._cached_gpu_temp_c = None

    def list_processes(self):
        current_time = time.time()
        interval = max(current_time - self._last_sample_time, 0.001)
        self._last_sample_time = current_time

        window_titles_by_pid = self._visible_windows_by_pid()
        process_entries, active_pids = self._collect_process_entries(
            interval,
            window_titles_by_pid,
        )
        grouped_processes = {}

        for child in process_entries:
            group = grouped_processes.get(child["group_key"])
            if group is None:
                group = {
                    "id": f"group:{child['group_key']}",
                    "group_key": child["group_key"],
                    "name": child["name"],
                    "publisher": child["publisher"],
                    "description": child["description"],
                    "product_name": child["product_name"],
                    "process_count": 0,
                    "pids": [],
                    "exe_path": child["exe_path"],
                    "cpu_percent": 0.0,
                    "memory_percent": 0.0,
                    "memory_mb": 0.0,
                    "disk_mb_per_sec": 0.0,
                    "window_titles": [],
                    "has_window": False,
                    "type_display": "Background process",
                    "is_protected": False,
                    "children": [],
                }
                grouped_processes[child["group_key"]] = group

            group["process_count"] += 1
            group["pids"].append(child["pid"])
            if not group["exe_path"] and child["exe_path"]:
                group["exe_path"] = child["exe_path"]
            group["publisher"] = self._merge_publishers(group["publisher"], child["publisher"])
            group["description"] = self._merge_metadata_text(
                group["description"],
                child["description"],
            )
            group["product_name"] = self._merge_metadata_text(
                group["product_name"],
                child["product_name"],
            )
            group["cpu_percent"] += child["cpu_percent"]
            group["memory_percent"] += child["memory_percent"]
            group["memory_mb"] += child["memory_mb"]
            group["disk_mb_per_sec"] += child["disk_rate_mb_per_sec"]
            group["window_titles"] = self._merge_window_titles(
                group["window_titles"],
                child["window_titles"],
            )
            group["has_window"] = group["has_window"] or child["has_window"]
            group["is_protected"] = group["is_protected"] or child["is_protected"]
            group["children"].append(child)

        self._prune_pid_caches(active_pids)

        groups = []
        for group in grouped_processes.values():
            group["pids"].sort()
            group["children"].sort(key=lambda child: (child["name"].lower(), child["pid"]))
            group["cpu_percent"] = min(group["cpu_percent"], 100.0)
            group["cpu_display"] = f"{group['cpu_percent']:.1f}%"
            group["memory_display"] = f"{group['memory_percent']:.1f}%"
            group["memory_value_display"] = self._format_memory_amount(group["memory_mb"])
            group["memory_tooltip"] = self._format_memory_usage(group["memory_mb"])
            group["disk_display"] = self._format_disk_rate(group["disk_mb_per_sec"])
            group["disk_tooltip"] = self._format_disk_usage(group["disk_mb_per_sec"])
            group["window_display"] = self._format_window_display(group["window_titles"])
            group["window_tooltip"] = self._format_window_tooltip(group["window_titles"])
            group["type_display"] = self._classify_process_type(
                exe_path=group["exe_path"],
                publisher=group["publisher"],
                has_window=group["has_window"],
            )
            groups.append(group)

        groups.sort(key=lambda group: group["name"].lower())
        return groups

    def list_process_details(self):
        current_time = time.time()
        interval = max(current_time - self._last_sample_time, 0.001)
        self._last_sample_time = current_time

        window_titles_by_pid = self._visible_windows_by_pid()
        process_entries, active_pids = self._collect_process_entries(
            interval,
            window_titles_by_pid,
        )

        self._prune_pid_caches(active_pids)

        process_entries.sort(key=lambda entry: (entry["name"].lower(), entry["pid"]))
        return process_entries

    def system_summary(self):
        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        disk_active_time_percent = self._disk_active_time_percent()
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

    def _collect_process_entries(self, interval, window_titles_by_pid):
        active_pids = set()
        process_entries = []
        sample_time = time.time()

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
                metadata = self._metadata_for_exe(exe_path)
                publisher = metadata["company"]
                is_protected = self.is_protected_process(raw_name)
                window_titles = window_titles_by_pid.get(pid, [])

                active_pids.add(pid)

                cpu_percent = self._sample_process_cpu_percent(proc, interval)

                memory_bytes = self._process_memory_bytes(proc, sample_time)
                memory_mb = memory_bytes / (1024 * 1024)
                memory_percent = (memory_bytes / self._memory_total) * 100

                disk_io = proc.info.get("io_counters")
                process_disk_bytes = self._sample_process_disk_bytes(pid, disk_io)
                process_disk_mb_per_sec = process_disk_bytes / (1024 * 1024) / interval

                process_entries.append(
                    {
                        "id": f"pid:{pid}",
                        "group_key": self._group_key(raw_name, exe_path),
                        "raw_name": raw_name,
                        "name": display_name,
                        "publisher": publisher,
                        "description": metadata["description"],
                        "product_name": metadata["product_name"],
                        "pid": pid,
                        "pids": [pid],
                        "exe_path": exe_path,
                        "cpu_percent": cpu_percent,
                        "cpu_display": f"{cpu_percent:.1f}%",
                        "memory_percent": memory_percent,
                        "memory_mb": memory_mb,
                        "memory_display": f"{memory_percent:.1f}%",
                        "memory_value_display": self._format_memory_amount(memory_mb),
                        "memory_tooltip": self._format_memory_usage(memory_mb),
                        "disk_rate_mb_per_sec": process_disk_mb_per_sec,
                        "disk_display": self._format_disk_rate(process_disk_mb_per_sec),
                        "disk_tooltip": self._format_disk_usage(process_disk_mb_per_sec),
                        "window_display": self._format_window_display(window_titles),
                        "window_tooltip": self._format_window_tooltip(window_titles),
                        "window_titles": list(window_titles),
                        "has_window": bool(window_titles),
                        "type_display": self._classify_process_type(
                            exe_path=exe_path,
                            publisher=publisher,
                            has_window=bool(window_titles),
                        ),
                        "is_protected": is_protected,
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        return process_entries, active_pids

    def _prune_pid_caches(self, active_pids):
        self._last_disk = {
            pid: counters for pid, counters in self._last_disk.items() if pid in active_pids
        }
        self._last_cpu = {
            pid: cpu_time for pid, cpu_time in self._last_cpu.items() if pid in active_pids
        }
        self._memory_cache = {
            pid: snapshot for pid, snapshot in self._memory_cache.items() if pid in active_pids
        }

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

    def _disk_active_time_percent(self):
        current_time = time.time()
        if current_time - self._last_disk_active_time_update < 5.0:
            return self._last_disk_active_time_percent

        self._last_disk_active_time_update = current_time
        self._last_disk_active_time_percent = self._read_disk_active_time_percent()
        return self._last_disk_active_time_percent

    def _read_disk_active_time_percent(self):
        command = (
            "Get-Counter '\\PhysicalDisk(_Total)\\% Idle Time' -ErrorAction SilentlyContinue | "
            "Select-Object -ExpandProperty CounterSamples | "
            "ForEach-Object { $_.CookedValue.ToString([System.Globalization.CultureInfo]::InvariantCulture) }"
        )
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=True,
            )
        except Exception:
            return self._last_disk_active_time_percent

        values = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                values.append(float(line))
            except ValueError:
                continue

        if not values:
            return self._last_disk_active_time_percent

        idle_percent = min(max(values[0], 0.0), 100.0)
        return max(0.0, 100.0 - idle_percent)

    def _metadata_for_exe(self, exe_path):
        normalized_path = (exe_path or "").strip().lower()
        if not normalized_path:
            return {
                "company": UNKNOWN_PUBLISHER,
                "description": "",
                "product_name": "",
            }

        cached_metadata = self._metadata_cache.get(normalized_path)
        if cached_metadata is not None:
            return cached_metadata

        version_strings = self._read_version_strings(
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
        self._metadata_cache[normalized_path] = metadata
        return metadata

    def _read_version_strings(self, exe_path, keys):
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

    def _format_memory_usage(self, memory_mb):
        if memory_mb >= 1024:
            return f"{memory_mb / 1024:.1f} GB in use"
        return f"{memory_mb:.1f} MB in use"

    def _format_memory_amount(self, memory_mb):
        if memory_mb >= 1024:
            return f"{memory_mb / 1024:.1f} GB"
        if memory_mb >= 10:
            return f"{memory_mb:.0f} MB"
        return f"{memory_mb:.1f} MB"

    def _format_disk_usage(self, disk_mb_per_sec):
        if disk_mb_per_sec > 0.1:
            return f"{disk_mb_per_sec:.1f} MB/s of disk traffic"
        return "No measurable disk traffic"

    def _format_disk_rate(self, disk_mb_per_sec):
        if disk_mb_per_sec >= 0.1:
            return f"{disk_mb_per_sec:.1f} MB/s"
        if disk_mb_per_sec > 0:
            return f"{disk_mb_per_sec * 1024:.0f} KB/s"
        return "0 MB/s"

    def _process_memory_bytes(self, proc, sample_time):
        pid = proc.info["pid"]
        cached_snapshot = self._memory_cache.get(pid)
        if cached_snapshot and sample_time - cached_snapshot["time"] < self._memory_cache_ttl:
            return cached_snapshot["bytes"]

        memory_bytes = 0
        try:
            full_info = proc.memory_full_info()
            unique_bytes = getattr(full_info, "uss", None)
            if unique_bytes is not None and unique_bytes > 0:
                memory_bytes = unique_bytes
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            pass

        if memory_bytes <= 0:
            memory_info = proc.info.get("memory_info")
            memory_bytes = memory_info.rss if memory_info else 0

        self._memory_cache[pid] = {
            "time": sample_time,
            "bytes": memory_bytes,
        }
        return memory_bytes

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

    def _merge_metadata_text(self, current_value, new_value):
        if current_value:
            return current_value
        return new_value or ""

    def _classify_process_type(self, exe_path, publisher, has_window):
        if has_window:
            return "App"

        normalized_path = (exe_path or "").strip().lower()
        normalized_publisher = (publisher or "").strip().lower()
        windows_root = (os.environ.get("SystemRoot") or "C:\\Windows").lower()
        if normalized_path.startswith(windows_root.lower()) or "microsoft" in normalized_publisher:
            return "Windows process"

        return "Background process"

    def _gpu_temperature(self):
        current_time = time.time()
        if current_time - self._last_temperature_update < 5.0:
            return self._cached_gpu_temp_c

        self._last_temperature_update = current_time
        self._cached_gpu_temp_c = self._read_nvidia_gpu_temperature()
        return self._cached_gpu_temp_c

    def _visible_windows_by_pid(self):
        current_time = time.time()
        if current_time - self._last_window_scan_update < self._window_scan_ttl:
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
