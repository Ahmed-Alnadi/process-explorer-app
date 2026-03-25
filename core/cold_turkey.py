import os


PROTECTED_PROCESS_NAMES = {
    "servicehub.power",
    "servicehub.power.exe",
    "servicehub.helper",
    "servicehub.helper.exe",
}

PROTECTED_SERVICE_NAMES = {
    "power_a17007",
}

PROTECTED_REASON_DEFAULT = "Protected Cold Turkey component."


def normalize_text(value):
    return (value or "").strip().lower()


def normalized_path(path):
    candidate = (path or "").strip().strip('"').strip("'")
    if not candidate:
        return ""
    try:
        return os.path.normcase(os.path.normpath(candidate))
    except Exception:
        return candidate.lower()


def process_seed_match(*, raw_name="", exe_path="", publisher="", command_line="", service_names=None):
    normalized_name = normalize_text(raw_name)
    if normalized_name in PROTECTED_PROCESS_NAMES:
        return True, "Protected by process name."
    normalized_exe = normalized_path(exe_path)
    if normalized_exe.endswith("\\servicehub.power") or normalized_exe.endswith("\\servicehub.power.exe"):
        return True, "Protected by executable path."
    if normalized_exe.endswith("\\servicehub.helper") or normalized_exe.endswith("\\servicehub.helper.exe"):
        return True, "Protected by executable path."

    return False, ""


def service_seed_match(
    *,
    name="",
    display_name="",
    description="",
    binpath="",
    exe_path="",
    publisher="",
):
    normalized_name = normalize_text(name)
    if normalized_name in PROTECTED_SERVICE_NAMES:
        return True
    normalized_exe = normalized_path(exe_path)
    if normalized_exe.endswith("\\power_a17007") or normalized_exe.endswith("\\power_a17007.exe"):
        return True
    normalized_binpath = normalized_path(binpath)
    if normalized_binpath.endswith("\\power_a17007") or normalized_binpath.endswith("\\power_a17007.exe"):
        return True
    return False


def startup_impact_label(cpu_percent, memory_mb, disk_mb_per_sec):
    if cpu_percent >= 10.0 or memory_mb >= 500.0 or disk_mb_per_sec >= 1.0:
        return "High"
    if cpu_percent >= 4.0 or memory_mb >= 180.0 or disk_mb_per_sec >= 0.25:
        return "Medium"
    if cpu_percent > 0.0 or memory_mb > 0.0 or disk_mb_per_sec > 0.0:
        return "Low"
    return "Low"
