REFRESH_PROFILES = {
    "High": {
        "processes_timer_ms": 450,
        "processes_full_s": 1.0,
        "details_timer_ms": 450,
        "details_full_s": 1.25,
        "performance_timer_ms": 750,
        "services_timer_ms": 1500,
    },
    "Normal": {
        "processes_timer_ms": 700,
        "processes_full_s": 1.75,
        "details_timer_ms": 700,
        "details_full_s": 2.0,
        "performance_timer_ms": 1000,
        "services_timer_ms": 2500,
    },
    "Low": {
        "processes_timer_ms": 1200,
        "processes_full_s": 3.5,
        "details_timer_ms": 1200,
        "details_full_s": 4.0,
        "performance_timer_ms": 1800,
        "services_timer_ms": 4500,
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

DEFAULT_REFRESH_PROFILE = "Low"
