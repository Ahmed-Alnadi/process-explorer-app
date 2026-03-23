import os

import psutil


class ServiceManager:
    def list_services(self):
        services = []
        try:
            iterator = psutil.win_service_iter()
        except Exception:
            return services

        for service in iterator:
            try:
                info = service.as_dict()
            except Exception:
                continue

            name = info.get("name") or "Unknown"
            display_name = info.get("display_name") or name
            status = (info.get("status") or "unknown").replace("_", " ").title()
            start_type = (info.get("start_type") or "unknown").replace("_", " ").title()
            pid = info.get("pid") or 0
            username = info.get("username") or "LocalSystem"
            binpath = info.get("binpath") or ""
            description = info.get("description") or ""

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
                    "exe_path": self._extract_exe_path(binpath),
                    "description": description,
                }
            )

        services.sort(key=lambda item: (item["display_name"].lower(), item["name"].lower()))
        return services

    def _extract_exe_path(self, binpath):
        normalized = (binpath or "").strip()
        if not normalized:
            return ""

        candidate = normalized
        if normalized.startswith('"'):
            end_quote = normalized.find('"', 1)
            if end_quote > 1:
                candidate = normalized[1:end_quote]
        else:
            lowered = normalized.lower()
            for extension in (".exe", ".com", ".bat", ".cmd", ".msc"):
                position = lowered.find(extension)
                if position >= 0:
                    candidate = normalized[: position + len(extension)]
                    break

        candidate = os.path.expandvars(candidate.strip().strip('"'))
        return os.path.normpath(candidate) if candidate else ""
