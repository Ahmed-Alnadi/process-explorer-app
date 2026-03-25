import time

import psutil


class ServiceLinkSnapshot:
    def __init__(self, services):
        self.services = services
        self.name_to_service = {entry["name"].lower(): entry for entry in services if entry.get("name")}
        self.pid_to_services = {}
        for entry in services:
            pid = int(entry.get("pid") or 0)
            if pid <= 0:
                continue
            self.pid_to_services.setdefault(pid, []).append(entry)


def _call_service_attr(service, attr_name):
    attribute = getattr(service, attr_name, None)
    if attribute is None:
        return None
    try:
        return attribute() if callable(attribute) else attribute
    except Exception:
        return None


def _coalesce_service_value(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


class ServiceLinkResolver:
    def __init__(self):
        self._cache = None
        self._last_update = 0.0
        self._cache_ttl = 3.0

    def set_cache_ttl(self, seconds):
        self._cache_ttl = max(float(seconds), 0.5)

    def snapshot(self, force=False):
        current_time = time.time()
        if (
            not force
            and self._cache is not None
            and current_time - self._last_update < self._cache_ttl
        ):
            return self._cache

        services = []
        try:
            iterator = psutil.win_service_iter()
        except Exception:
            iterator = []

        seen_names = set()
        for service in iterator:
            info = {}
            try:
                raw = service.as_dict()
                if isinstance(raw, dict):
                    info.update(raw)
            except Exception:
                pass

            name = _coalesce_service_value(info.get("name"), _call_service_attr(service, "name"))
            if not name:
                continue

            normalized_name = name.lower()
            if normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)

            display_name = _coalesce_service_value(
                info.get("display_name"),
                _call_service_attr(service, "display_name"),
                name,
            )
            services.append(
                {
                    "name": name,
                    "display_name": display_name or name,
                    "status": _coalesce_service_value(
                        info.get("status"),
                        _call_service_attr(service, "status"),
                        "unknown",
                    ),
                    "start_type": _coalesce_service_value(
                        info.get("start_type"),
                        _call_service_attr(service, "start_type"),
                        "unknown",
                    ),
                    "pid": int(
                        _coalesce_service_value(
                            info.get("pid"),
                            _call_service_attr(service, "pid"),
                            0,
                        )
                        or 0
                    ),
                    "username": _coalesce_service_value(
                        info.get("username"),
                        _call_service_attr(service, "username"),
                        "LocalSystem",
                    ),
                    "binpath": _coalesce_service_value(
                        info.get("binpath"),
                        _call_service_attr(service, "binpath"),
                        "",
                    ),
                    "description": _coalesce_service_value(
                        info.get("description"),
                        _call_service_attr(service, "description"),
                        "",
                    ),
                }
            )

        self._cache = ServiceLinkSnapshot(services)
        self._last_update = current_time
        return self._cache
