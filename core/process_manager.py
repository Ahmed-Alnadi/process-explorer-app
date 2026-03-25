import ctypes
import os
import shutil
import subprocess
import time
from ctypes import wintypes

import psutil

from core.cold_turkey import (
    PROTECTED_REASON_DEFAULT,
    process_seed_match,
    startup_impact_label,
)
from core.file_metadata import UNKNOWN_PUBLISHER, metadata_for_exe
from core.path_utils import query_process_image_path, resolve_command_path, resolve_existing_path
from core.refresh_profiles import DEFAULT_REFRESH_PROFILE, REFRESH_PROFILES
from core.service_links import ServiceLinkResolver
from core.startup_manager import StartupManager
from core.subprocess_utils import hidden_subprocess_kwargs
from core.windows_native import (
    NativeDiskActivityMonitor,
    native_memory_status,
    native_process_private_bytes,
)


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
        self._native_memory_total = 0
        self._memory_cache = {}
        self._identity_cache = {}
        self._service_links = ServiceLinkResolver()
        self._startup_manager = StartupManager()
        self._startup_cache = {}
        self._last_startup_cache_update = 0.0
        self._startup_cache_ttl = 20.0
        self._last_window_scan_update = 0.0
        self._cached_window_titles_by_pid = {}
        self._nvidia_smi_path = shutil.which("nvidia-smi") or "C:\\Windows\\System32\\nvidia-smi.exe"
        self._last_temperature_update = 0.0
        self._cached_gpu_temp_c = None
        self._disk_monitor = NativeDiskActivityMonitor()
        self._refresh_profile_name = DEFAULT_REFRESH_PROFILE
        self._low_overhead_mode = False
        self._memory_cache_ttl = 3.5
        self._identity_cache_ttl = 8.0
        self._window_scan_ttl = 5.0
        self._disk_active_time_ttl = 5.0
        self._temperature_cache_ttl = 5.0
        self._apply_runtime_budget()

    def set_refresh_profile(self, profile_name):
        self._refresh_profile_name = (
            profile_name if profile_name in REFRESH_PROFILES else DEFAULT_REFRESH_PROFILE
        )
        self._apply_runtime_budget()

    def set_low_overhead_mode(self, enabled):
        self._low_overhead_mode = bool(enabled)
        self._apply_runtime_budget()

    def list_processes(self):
        current_time = time.time()
        interval = max(current_time - self._last_sample_time, 0.001)
        self._last_sample_time = current_time

        window_titles_by_pid = self._visible_windows_by_pid()
        service_snapshot = self._service_links.snapshot()
        process_entries, active_pids = self._collect_process_entries(
            interval,
            window_titles_by_pid,
            service_snapshot,
            None,
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
                    "can_open_location": child["can_open_location"],
                    "location_reason": child["location_reason"],
                    "cpu_percent": 0.0,
                    "memory_percent": 0.0,
                    "memory_mb": 0.0,
                    "disk_mb_per_sec": 0.0,
                    "window_titles": [],
                    "has_window": False,
                    "type_display": "Background process",
                    "is_protected": False,
                    "protection_reason": "",
                    "service_names": [],
                    "service_display_names": [],
                    "primary_service_name": "",
                    "primary_service_display_name": "",
                    "startup_display": "",
                    "search_query": "",
                    "children": [],
                }
                grouped_processes[child["group_key"]] = group

            group["process_count"] += 1
            group["pids"].append(child["pid"])
            if not group["exe_path"] and child["exe_path"]:
                group["exe_path"] = child["exe_path"]
                group["can_open_location"] = True
                group["location_reason"] = ""
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
            if group["is_protected"] and not group["protection_reason"]:
                group["protection_reason"] = child.get("protection_reason") or PROTECTED_REASON_DEFAULT
            group["service_names"] = self._merge_unique_text(group["service_names"], child["service_names"])
            group["service_display_names"] = self._merge_unique_text(
                group["service_display_names"],
                child["service_display_names"],
            )
            if not group["primary_service_name"] and child.get("primary_service_name"):
                group["primary_service_name"] = child["primary_service_name"]
                group["primary_service_display_name"] = child["primary_service_display_name"]
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
            if not group["exe_path"]:
                group["location_reason"] = self._group_location_reason(group)
            group["service_display"] = self._format_service_display(
                group["service_display_names"],
                group["service_names"],
            )
            group["search_query"] = self._build_group_search_query(group)
            groups.append(group)

        groups.sort(key=lambda group: group["name"].lower())
        return groups

    def list_process_details(self):
        current_time = time.time()
        interval = max(current_time - self._last_sample_time, 0.001)
        self._last_sample_time = current_time

        window_titles_by_pid = self._visible_windows_by_pid()
        service_snapshot = self._service_links.snapshot()
        process_entries, active_pids = self._collect_process_entries(
            interval,
            window_titles_by_pid,
            service_snapshot,
            None,
        )

        self._prune_pid_caches(active_pids)

        process_entries.sort(key=lambda entry: (entry["name"].lower(), entry["pid"]))
        return process_entries

    def system_summary(self):
        cpu_percent = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        native_memory = native_memory_status()
        memory_percent = native_memory["memory_load_percent"] if native_memory else memory.percent
        if native_memory and native_memory.get("total_phys"):
            self._native_memory_total = int(native_memory["total_phys"])
            self._memory_total = max(self._native_memory_total, 1)
        disk_active_time_percent = self._disk_active_time_percent()
        gpu_temp_c = self._gpu_temperature()
        return {
            "cpu_percent": cpu_percent,
            "cpu_display": f"CPU: {cpu_percent:.1f}%",
            "memory_percent": memory_percent,
            "memory_display": f"Physical Memory: {memory_percent:.1f}%",
            "disk_active_time_percent": disk_active_time_percent,
            "disk_active_time_display": f"Disk Active Time: {disk_active_time_percent:.1f}%",
            "gpu_temp_c": gpu_temp_c,
            "gpu_temp_display": self._format_temperature_display("GPU Temp", gpu_temp_c),
        }

    def startup_display_for_entry(self, entry):
        if not entry:
            return "Not listed"

        if "children" in entry:
            startup_display = "Not listed"
            for child in entry.get("children", []):
                startup_display = self._merge_startup_display(
                    startup_display,
                    self.startup_display_for_entry(child),
                )
            return startup_display

        startup_match = self._startup_match_for_path(
            entry.get("exe_path", ""),
            self._startup_entries(),
        )
        if not startup_match:
            return "Not listed"
        return (
            f"Enabled ({startup_impact_label(entry.get('cpu_percent', 0.0), entry.get('memory_mb', 0.0), entry.get('disk_rate_mb_per_sec', 0.0))})"
        )

    def is_protected_process(self, name, **kwargs):
        return self._process_protection_result(name, **kwargs)["is_protected"]

    def terminate_processes(self, pids):
        protected_names = []
        processes = []
        process_entries = []
        for pid in pids:
            try:
                process = psutil.Process(pid)
                name = process.name()
                exe_path = self._resolve_process_exe_path(process)
                metadata = metadata_for_exe(exe_path)
                try:
                    command_line = " ".join(process.cmdline()).strip()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    command_line = ""
                process_entries.append(
                    {
                        "pid": process.pid,
                        "ppid": process.ppid(),
                        "raw_name": name,
                        "exe_path": exe_path,
                        "publisher": metadata["company"],
                        "command_line": command_line,
                        "service_names": [],
                    }
                )
                processes.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        protected_pids = self._resolve_protected_pids(process_entries)
        for process, entry in list(zip(processes, process_entries)):
            if self.is_protected_process(
                entry["raw_name"],
                exe_path=entry["exe_path"],
                publisher=entry["publisher"],
                command_line=entry["command_line"],
                pid=entry["pid"],
                ppid=entry["ppid"],
                protected_pids=protected_pids,
            ):
                protected_names.append(entry["raw_name"])
                continue
        processes = [
            process
            for process, entry in zip(processes, process_entries)
            if not self.is_protected_process(
                entry["raw_name"],
                exe_path=entry["exe_path"],
                publisher=entry["publisher"],
                command_line=entry["command_line"],
                pid=entry["pid"],
                ppid=entry["ppid"],
                protected_pids=protected_pids,
            )
        ]

        if protected_names:
            raise ProcessTerminationBlockedError(
                f"{protected_names[0]} is protected and cannot be ended from this app."
            )

        for process in processes:
            process.terminate()

    def terminate_process_tree(self, root_pids):
        protected_names = []
        processes_by_pid = {}
        process_entries = []

        for pid in root_pids:
            try:
                root_process = psutil.Process(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

            for process in [root_process, *root_process.children(recursive=True)]:
                try:
                    name = process.name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

                exe_path = self._resolve_process_exe_path(process)
                metadata = metadata_for_exe(exe_path)
                try:
                    command_line = " ".join(process.cmdline()).strip()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    command_line = ""
                process_entries.append(
                    {
                        "pid": process.pid,
                        "ppid": process.ppid(),
                        "raw_name": name,
                        "exe_path": exe_path,
                        "publisher": metadata["company"],
                        "command_line": command_line,
                        "service_names": [],
                    }
                )
                processes_by_pid[process.pid] = process

        protected_pids = self._resolve_protected_pids(process_entries)
        for entry in process_entries:
            if self.is_protected_process(
                entry["raw_name"],
                exe_path=entry["exe_path"],
                publisher=entry["publisher"],
                command_line=entry["command_line"],
                pid=entry["pid"],
                ppid=entry["ppid"],
                protected_pids=protected_pids,
            ):
                protected_names.append(entry["raw_name"])
                processes_by_pid.pop(entry["pid"], None)

        if protected_names:
            raise ProcessTerminationBlockedError(
                f"{protected_names[0]} is protected and cannot be ended from this app."
            )

        for process in reversed(list(processes_by_pid.values())):
            try:
                process.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _collect_process_entries(self, interval, window_titles_by_pid, service_snapshot, startup_entries):
        active_pids = set()
        process_entries = []
        sample_time = time.time()
        process_contexts = []

        for proc in psutil.process_iter(
            ["pid", "name", "memory_info", "io_counters", "exe", "cpu_times", "ppid"],
            ad_value=None,
        ):
            try:
                pid = getattr(proc, "pid", None)
                if pid is None:
                    continue

                identity = self._process_identity(proc, sample_time)
                exe_path = identity["exe_path"]
                raw_name = identity["raw_name"]
                if raw_name.lower() == "system idle process":
                    continue

                display_name = identity["display_name"]
                publisher = identity["publisher"]
                window_titles = window_titles_by_pid.get(pid, [])
                location_reason = identity["location_reason"]
                linked_services = service_snapshot.pid_to_services.get(pid, [])
                service_names = [service["name"] for service in linked_services if service.get("name")]
                service_display_names = [
                    service.get("display_name") or service.get("name") or ""
                    for service in linked_services
                    if service.get("name")
                ]

                active_pids.add(pid)

                cpu_percent = self._sample_process_cpu_percent(proc, interval)

                memory_bytes = self._process_memory_bytes(proc, sample_time)
                memory_mb = memory_bytes / (1024 * 1024)
                memory_percent = (memory_bytes / self._memory_total) * 100

                disk_io = proc.info.get("io_counters")
                process_disk_bytes = self._sample_process_disk_bytes(pid, disk_io)
                process_disk_mb_per_sec = process_disk_bytes / (1024 * 1024) / interval
                entry = {
                    "id": f"pid:{pid}",
                    "group_key": self._group_key(raw_name, exe_path),
                    "raw_name": raw_name,
                    "name": display_name,
                    "publisher": publisher,
                    "description": identity["description"],
                    "product_name": identity["product_name"],
                    "pid": pid,
                    "ppid": int(proc.info.get("ppid") or 0),
                    "pids": [pid],
                    "exe_path": exe_path,
                    "command_line": identity["command_line"],
                    "can_open_location": bool(exe_path),
                    "location_reason": location_reason,
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
                    "service_names": service_names,
                    "service_display_names": service_display_names,
                    "primary_service_name": service_names[0] if service_names else "",
                    "primary_service_display_name": service_display_names[0] if service_display_names else "",
                    "service_display": self._format_service_display(service_display_names, service_names),
                    "startup_display": "",
                    "search_query": "",
                    "is_protected": False,
                    "protection_reason": "",
                }
                process_entries.append(entry)
                process_contexts.append((proc, entry))
            except psutil.NoSuchProcess:
                continue

        protected_pids = self._resolve_protected_pids(process_entries)
        for proc, entry in process_contexts:
            protection = self._process_protection_result(
                entry["raw_name"],
                exe_path=entry["exe_path"],
                publisher=entry["publisher"],
                command_line=entry.get("command_line", ""),
                service_names=entry.get("service_names", []),
                pid=entry["pid"],
                ppid=entry.get("ppid"),
                protected_pids=protected_pids,
            )
            entry["is_protected"] = protection["is_protected"]
            entry["protection_reason"] = protection["reason"]
            entry["search_query"] = self._build_process_search_query(
                entry["name"],
                entry["raw_name"],
                entry["publisher"],
                entry["product_name"],
                entry["description"],
                entry["pid"],
                entry["service_display"],
                entry["startup_display"],
            )
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
        self._identity_cache = {
            pid: snapshot for pid, snapshot in self._identity_cache.items() if pid in active_pids
        }

    def _resolve_protected_pids(self, process_entries):
        protected_pids = set()
        for entry in process_entries:
            matched, _reason = process_seed_match(
                raw_name=entry["raw_name"],
                exe_path=entry.get("exe_path", ""),
                publisher=entry.get("publisher", ""),
                command_line=entry.get("command_line", ""),
                service_names=entry.get("service_names", []),
            )
            if matched:
                protected_pids.add(entry["pid"])
        return protected_pids

    def _process_protection_result(
        self,
        name,
        *,
        exe_path="",
        publisher="",
        command_line="",
        service_names=None,
        pid=None,
        ppid=None,
        protected_pids=None,
    ):
        seed_match, reason = process_seed_match(
            raw_name=name,
            exe_path=exe_path,
            publisher=publisher,
            command_line=command_line,
            service_names=service_names,
        )
        if seed_match:
            return {"is_protected": True, "reason": reason or PROTECTED_REASON_DEFAULT}

        protected_pids = protected_pids or set()
        if pid in protected_pids:
            return {"is_protected": True, "reason": PROTECTED_REASON_DEFAULT}

        return {"is_protected": False, "reason": ""}

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
        if current_time - self._last_disk_active_time_update < self._disk_active_time_ttl:
            return self._last_disk_active_time_percent

        self._last_disk_active_time_update = current_time
        self._last_disk_active_time_percent = self._read_disk_active_time_percent()
        return self._last_disk_active_time_percent

    def _read_disk_active_time_percent(self):
        native_value = self._disk_monitor.read_active_percent()
        if native_value is not None:
            return native_value

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
                **hidden_subprocess_kwargs(),
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

    def _resolve_process_exe_path(self, proc):
        try:
            info = getattr(proc, "info", {}) or {}
        except Exception:
            info = {}

        exe_path = resolve_existing_path(info.get("exe") or "")
        if exe_path:
            return exe_path

        pid = getattr(proc, "pid", None)
        if pid is None:
            return ""

        exe_path = query_process_image_path(pid)
        if exe_path:
            return exe_path

        try:
            command_line = proc.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            command_line = []

        if command_line:
            return resolve_command_path(command_line[0])

        return ""

    def _resolve_process_name(self, proc, exe_path):
        try:
            info = getattr(proc, "info", {}) or {}
        except Exception:
            info = {}

        raw_name = (info.get("name") or "").strip()
        if raw_name:
            return raw_name

        try:
            raw_name = (proc.name() or "").strip()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            raw_name = ""

        if raw_name:
            return raw_name

        if exe_path:
            return os.path.basename(exe_path)

        pid = getattr(proc, "pid", None)
        if pid is not None:
            return f"PID {pid}"
        return "Unknown"

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
            memory_bytes = native_process_private_bytes(pid) or 0

        if memory_bytes <= 0:
            memory_info = proc.info.get("memory_info")
            memory_bytes = memory_info.rss if memory_info else 0

        self._memory_cache[pid] = {
            "time": sample_time,
            "bytes": memory_bytes,
        }
        return memory_bytes

    def _process_identity(self, proc, sample_time):
        pid = proc.info["pid"]
        cached_snapshot = self._identity_cache.get(pid)
        if cached_snapshot and sample_time - cached_snapshot["time"] < self._identity_cache_ttl:
            return cached_snapshot["data"]

        exe_path = self._resolve_process_exe_path(proc)
        raw_name = self._resolve_process_name(proc, exe_path)
        metadata = metadata_for_exe(exe_path)
        try:
            command_line = " ".join(proc.cmdline()).strip()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            command_line = ""
        identity = {
            "exe_path": exe_path,
            "raw_name": raw_name,
            "display_name": self._display_name(raw_name),
            "publisher": metadata["company"],
            "description": metadata["description"],
            "product_name": metadata["product_name"],
            "command_line": command_line,
            "location_reason": self._process_location_reason(raw_name, exe_path),
        }
        self._identity_cache[pid] = {
            "time": sample_time,
            "data": identity,
        }
        return identity

    def _group_key(self, name, exe_path):
        normalized_name = (name or "").strip().lower()
        normalized_path = (exe_path or "").strip().lower()
        return normalized_path or normalized_name

    def _display_name(self, name):
        normalized_name = (name or "Unknown").strip()
        if normalized_name.lower().endswith(".exe"):
            return normalized_name[:-4]
        return normalized_name

    def _build_process_search_query(
        self,
        display_name,
        raw_name,
        publisher,
        product_name,
        description,
        pid,
        service_display,
        startup_display,
    ):
        return (display_name or raw_name or "").strip()

    def _build_group_search_query(self, group):
        return (group.get("name") or "").strip()

    def _process_location_reason(self, raw_name, exe_path):
        if exe_path:
            return ""

        normalized_name = (raw_name or "").strip().lower()
        if normalized_name in {"system", "registry", "memory compression", "system interrupts"}:
            return "Windows does not expose a file location for this system process."
        return "Windows did not expose an accessible executable path for this process."

    def _group_location_reason(self, group):
        child_reasons = {
            child.get("location_reason", "")
            for child in group["children"]
            if child.get("location_reason")
        }
        if len(child_reasons) == 1:
            return child_reasons.pop()
        if child_reasons:
            return "Windows did not expose a usable file location for this process group."
        return "No accessible executable path is available for this process group."

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

    def _merge_unique_text(self, existing_values, new_values):
        merged = list(existing_values or [])
        for value in new_values or []:
            if value and value not in merged:
                merged.append(value)
        return merged

    def _merge_startup_display(self, current_value, new_value):
        priority = {
            "Not listed": 0,
            "Enabled (Low)": 1,
            "Enabled (Medium)": 2,
            "Enabled (High)": 3,
        }
        current_value = current_value or "Not listed"
        new_value = new_value or "Not listed"
        return new_value if priority.get(new_value, 0) > priority.get(current_value, 0) else current_value

    def _format_service_display(self, display_names, service_names):
        if display_names:
            if len(display_names) == 1:
                return display_names[0]
            return f"{display_names[0]} +{len(display_names) - 1}"
        if service_names:
            if len(service_names) == 1:
                return service_names[0]
            return f"{service_names[0]} +{len(service_names) - 1}"
        return "None"

    def _startup_entries(self):
        current_time = time.time()
        if current_time - self._last_startup_cache_update < self._startup_cache_ttl:
            return self._startup_cache

        entries_by_path = {}
        try:
            for entry in self._startup_manager.list_startup_apps():
                target_path = (entry.get("target_path") or "").strip().lower()
                if not target_path:
                    continue
                entries_by_path[target_path] = entry
        except Exception:
            entries_by_path = {}

        self._startup_cache = entries_by_path
        self._last_startup_cache_update = current_time
        return entries_by_path

    def _startup_match_for_path(self, exe_path, startup_entries):
        normalized_path = (exe_path or "").strip().lower()
        if not normalized_path:
            return None
        return startup_entries.get(normalized_path)

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
        if current_time - self._last_temperature_update < self._temperature_cache_ttl:
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

    def _apply_runtime_budget(self):
        config = REFRESH_PROFILES.get(
            self._refresh_profile_name,
            REFRESH_PROFILES[DEFAULT_REFRESH_PROFILE],
        )
        multiplier = 1.8 if self._low_overhead_mode else 1.0
        self._memory_cache_ttl = max(config["processes_full_s"] * 0.9, 2.5) * multiplier
        self._identity_cache_ttl = max(config["processes_full_s"] * 2.0, 6.0) * multiplier
        self._window_scan_ttl = max(config["processes_full_s"] * 1.5, 4.0) * multiplier
        self._disk_active_time_ttl = max(config["processes_timer_ms"] / 1000.0 * 3.0, 4.0) * multiplier
        self._temperature_cache_ttl = max(config["performance_timer_ms"] / 1000.0 * 2.5, 5.0) * multiplier
        self._startup_cache_ttl = max(config["processes_full_s"] * 4.0, 18.0) * multiplier
        self._service_links.set_cache_ttl(max(config["processes_full_s"] * 0.9, 2.5) * multiplier)

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
                **hidden_subprocess_kwargs(),
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
        return f"{label}: {temperature_c:.0f}°C"
