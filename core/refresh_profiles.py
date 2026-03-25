REFRESH_PROFILES = {
    "High": {
        "processes_timer_ms": 750,
        "processes_full_s": 2.0,
        "details_timer_ms": 750,
        "details_full_s": 2.5,
        "performance_timer_ms": 1000,
        "services_timer_ms": 3000,
    },
    "Normal": {
        "processes_timer_ms": 1250,
        "processes_full_s": 4.0,
        "details_timer_ms": 1250,
        "details_full_s": 4.5,
        "performance_timer_ms": 2000,
        "services_timer_ms": 5000,
    },
    "Low": {
        "processes_timer_ms": 2000,
        "processes_full_s": 6.0,
        "details_timer_ms": 2000,
        "details_full_s": 6.5,
        "performance_timer_ms": 3500,
        "services_timer_ms": 8000,
    },
    "Paused": {
        "processes_timer_ms": 0,
        "processes_full_s": 60.0,
        "details_timer_ms": 0,
        "details_full_s": 60.0,
        "performance_timer_ms": 0,
        "services_timer_ms": 0,
    },
}

DEFAULT_REFRESH_PROFILE = "Normal"
