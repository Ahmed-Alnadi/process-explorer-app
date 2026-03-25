from PySide6.QtGui import QBrush, QColor


def resource_heat_brush(intensity):
    normalized = max(0.0, min(float(intensity), 1.0))
    if normalized <= 0.0:
        return QBrush()

    color = QColor(247, 196, 79)
    color.setAlpha(int(28 + (normalized * 150)))
    return QBrush(color)


def status_heat_brush(status_text):
    normalized = (status_text or "").strip().lower()
    if normalized == "running":
        color = QColor(77, 193, 122, 70)
        return QBrush(color)
    if normalized:
        color = QColor(198, 120, 92, 60)
        return QBrush(color)
    return QBrush()


def protected_heat_brush():
    color = QColor(206, 92, 108, 78)
    return QBrush(color)


def disk_intensity_from_rate(disk_rate_mb_per_sec):
    return min(max(float(disk_rate_mb_per_sec) / 10.0, 0.0), 1.0)
