import csv
from pathlib import Path

from PySide6.QtWidgets import QFileDialog


def export_rows_to_csv(parent, suggested_name, headers, rows):
    default_path = str(Path.home() / suggested_name)
    file_path, _ = QFileDialog.getSaveFileName(
        parent,
        "Export CSV",
        default_path,
        "CSV Files (*.csv)",
    )
    if not file_path:
        return False, ""

    if not file_path.lower().endswith(".csv"):
        file_path += ".csv"

    with open(file_path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)

    return True, file_path
