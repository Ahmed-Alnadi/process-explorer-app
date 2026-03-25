import subprocess
import time

import psutil

from core.cold_turkey import service_seed_match
from core.file_metadata import metadata_for_exe
from core.path_utils import query_process_image_path, resolve_command_path
from core.refresh_profiles import DEFAULT_REFRESH_PROFILE, REFRESH_PROFILES
from core.service_links import ServiceLinkResolver
from core.subprocess_utils import hidden_subprocess_kwargs
from core.windows_native import native_dependent_services


class ServiceActionBlockedError(RuntimeError):
    pass


class ServiceManager:
    def __init__(self):
        self._refresh_profile_name = DEFAULT_REFRESH_PROFILE
        self._low_overhead_mode = False
        self._service_cache = []
        self._last_service_cache_update = 0.0
        self._service_cache_ttl = 0.0
        self._service_path_cache = {}
        self._dependency_cache = {}
        self._service_link_resolver = ServiceLinkResolver()
        self._apply_runtime_budget()

    def list_services(self):
        current_time = time.time()
        if self._service_cache and current_time - self._last_service_cache_update < self._service_cache_ttl:
            return [dict(entry) for entry in self._service_cache]

        services = []
        snapshot = self._service_link_resolver.snapshot(force=True)
        seen_service_ids = set()
        for info in snapshot.services:
            name = info.get("name") or "Unknown"
            service_id = name.lower()
            if service_id in seen_service_ids:
                continue
            seen_service_ids.add(service_id)

            display_name = info.get("display_name") or name
            status = (info.get("status") or "unknown").replace("_", " ").title()
            start_type = (info.get("start_type") or "unknown").replace("_", " ").title()
            pid = info.get("pid") or 0
            username = info.get("username") or "LocalSystem"
            binpath = info.get("binpath") or ""
            description = info.get("description") or ""

            exe_path = self._resolve_service_exe_path(binpath, pid)
            metadata = metadata_for_exe(exe_path)
            dependents = self.dependent_services(name)
            dependency_depth = self.dependent_service_depth(name)
            services.append(
                {
                    "id": f"service:{name.lower()}",
                    "name": name,
                    "display_name": display_name,
                    "status": status,
                    "start_type": start_type,
                    "pid": pid,
                    "pid_display": str(pid) if pid else "-",
                    "username": username,
                    "binpath": binpath,
                    "exe_path": exe_path,
                    "publisher": metadata["company"],
                    "can_open_location": bool(exe_path),
                    "location_reason": self._service_location_reason(binpath, exe_path),
                    "description": description,
                    "dependent_count": len(dependents),
                    "dependent_depth": dependency_depth,
                    "linked_process_display": f"{pid}" if pid else "None",
                    "is_protected": self.is_protected_service(
                        name,
                        display_name=display_name,
                        description=description,
                        binpath=binpath,
                        exe_path=exe_path,
                        publisher=metadata["company"],
                    ),
                    "search_query": self._build_service_search_query(
                        name,
                        display_name,
                        description,
                        metadata["company"],
                    ),
                }
            )

        services.sort(key=lambda item: (item["display_name"].lower(), item["name"].lower()))
        self._service_cache = [dict(entry) for entry in services]
        self._last_service_cache_update = current_time
        return services

    def set_refresh_profile(self, profile_name):
        self._refresh_profile_name = (
            profile_name if profile_name in REFRESH_PROFILES else DEFAULT_REFRESH_PROFILE
        )
        self._apply_runtime_budget()

    def set_low_overhead_mode(self, enabled):
        self._low_overhead_mode = bool(enabled)
        self._apply_runtime_budget()

    def is_protected_service(
        self,
        name,
        *,
        display_name="",
        description="",
        binpath="",
        exe_path="",
        publisher="",
    ):
        return service_seed_match(
            name=name,
            display_name=display_name,
            description=description,
            binpath=binpath,
            exe_path=exe_path,
            publisher=publisher,
        )

    def start_service(self, name):
        self._invalidate_caches()
        self._run_sc_command("start", name)

    def stop_service(self, name):
        if self.is_protected_service(name):
            raise ServiceActionBlockedError(
                f"{name} is protected and cannot be stopped from this app."
            )
        self._invalidate_caches()
        self._run_sc_command("stop", name)

    def restart_service(self, name):
        if self.is_protected_service(name):
            raise ServiceActionBlockedError(
                f"{name} is protected and cannot be restarted from this app."
            )
        self._invalidate_caches()
        self._run_sc_command("stop", name)
        self._wait_for_service_state(name, {"Stopped", "Stop Pending"}, timeout_s=6.0)
        self._run_sc_command("start", name)

    def dependent_services(self, name):
        service_name = (name or "").strip()
        if not service_name:
            return []

        cache_key = service_name.lower()
        cached = self._dependency_cache.get(cache_key)
        if cached is not None:
            return [dict(entry) for entry in cached]

        dependents = native_dependent_services(service_name)
        self._dependency_cache[cache_key] = [dict(entry) for entry in dependents]
        return dependents

    def dependent_service_depth(self, name):
        return self._dependent_service_depth(name, set())

    def _extract_exe_path(self, binpath):
        return resolve_command_path(binpath)

    def _resolve_service_exe_path(self, binpath, pid):
        cache_key = (binpath or "", int(pid or 0))
        cached_path = self._service_path_cache.get(cache_key)
        if cached_path is not None:
            return cached_path

        exe_path = self._extract_exe_path(binpath)
        if exe_path:
            self._service_path_cache[cache_key] = exe_path
            return exe_path
        if pid:
            exe_path = query_process_image_path(pid)
            self._service_path_cache[cache_key] = exe_path or ""
            return exe_path
        self._service_path_cache[cache_key] = ""
        return ""

    def _service_location_reason(self, binpath, exe_path):
        if exe_path:
            return ""
        if not (binpath or "").strip():
            return "This service does not expose a binary path."
        return "This service command does not resolve to an accessible local executable."

    def _build_service_search_query(self, name, display_name, description, publisher):
        return (name or display_name or "").strip()

    def _run_sc_command(self, action, name):
        result = subprocess.run(
            ["sc.exe", action, name],
            capture_output=True,
            text=True,
            timeout=8.0,
            **hidden_subprocess_kwargs(),
        )
        if result.returncode != 0:
            error_text = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(error_text or f"sc.exe {action} failed for {name}.")
        return result

    def _wait_for_service_state(self, name, acceptable_states, timeout_s=5.0):
        deadline = time.time() + timeout_s
        normalized_states = {state.lower() for state in acceptable_states}
        while time.time() < deadline:
            try:
                current = psutil.win_service_get(name).as_dict().get("status", "")
            except Exception:
                current = ""
            if current.replace("_", " ").title().lower() in normalized_states or current.lower() in normalized_states:
                return
            time.sleep(0.35)

    def _invalidate_caches(self):
        self._last_service_cache_update = 0.0
        self._service_cache = []
        self._dependency_cache = {}

    def _apply_runtime_budget(self):
        config = REFRESH_PROFILES.get(
            self._refresh_profile_name,
            REFRESH_PROFILES[DEFAULT_REFRESH_PROFILE],
        )
        multiplier = 1.8 if self._low_overhead_mode else 1.0
        base_ttl = max(config["services_timer_ms"] / 1000.0 * 0.8, 2.0)
        self._service_cache_ttl = base_ttl * multiplier
        self._service_link_resolver.set_cache_ttl(self._service_cache_ttl)

    def _dependent_service_depth(self, name, visited):
        normalized_name = (name or "").strip().lower()
        if not normalized_name or normalized_name in visited:
            return 0
        visited = set(visited)
        visited.add(normalized_name)
        dependents = self.dependent_services(name)
        if not dependents:
            return 0
        return 1 + max(
            (
                self._dependent_service_depth(dependent.get("name", ""), visited)
                for dependent in dependents
            ),
            default=0,
        )
