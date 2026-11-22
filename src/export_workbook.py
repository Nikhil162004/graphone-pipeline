"""Deliverable export: one workbook, six tabs, plus a CSV mirror.

Column headers are the brief's schema paths (source.name,
content.entityName, ...), so the sheet reads as the canonical JSON
flattened, and every row keeps its source.url — the anti-hallucination
rule is that each record must trace to a live source page.
"""
from __future__ import annotations

import csv
import os

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

COLUMNS: dict[str, list[str]] = {
    "Startups": [
        "schemaVersion", "recordType", "source.name", "source.url",
        "content.entityName", "content.data.employeeCount", "collectedAt",
    ],
    "Products": [
        "schemaVersion", "recordType", "source.name", "source.url",
        "content.startupName", "content.pricingModel", "content.data.productName",
        "content.data.likes", "collectedAt",
    ],
    "Research Papers": [
        "schemaVersion", "recordType", "source.name", "source.url", "content.title",
        "content.authors", "content.paper_url", "content.github_url",
        "content.github_stars", "content.published_date", "collectedAt",
    ],
    "Jobs": [
        "schemaVersion", "recordType", "source.name", "source.url", "content.title",
        "content.company", "content.date", "content.is_remote", "content.role_family",
        "content.fullText", "collectedAt",
    ],
    "News": [
        "schemaVersion", "recordType", "source.name", "source.url", "content.title",
        "content.publishedAt", "content.freshnessBasis", "content.summary",
        "content.fullText", "content.entities", "content.extractionTier", "collectedAt",
    ],
    "Entity Mapping Log": [
        "rawName", "canonicalName", "method", "score", "vertical", "occurrences",
    ],
}

TAB_FOR_TYPE = {
    "STARTUP": "Startups",
    "PRODUCT": "Products",
    "RESEARCH_PAPER": "Research Papers",
    "JOB": "Jobs",
    "NEWS": "News",
}


def _cell(record: dict, path: str):
    value = record
    for part in path.split("."):
        if not isinstance(value, dict):
            return ""
        value = value.get(part)
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


def rows_for(tab: str, records: list[dict]) -> list[list]:
    columns = COLUMNS[tab]
    return [[_cell(r, c) for c in columns] for r in records]


def write_outputs(records_by_tab: dict[str, list[dict]], mapping_rows: list[dict], out_dir: str = "output") -> str:
    os.makedirs(os.path.join(out_dir, "csv"), exist_ok=True)
    workbook = Workbook()
    workbook.remove(workbook.active)
    for tab in COLUMNS:
        rows = mapping_rows if tab == "Entity Mapping Log" else records_by_tab.get(tab, [])
        sheet = workbook.create_sheet(tab)
        sheet.append(COLUMNS[tab])
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        body = [[row.get(c, "") if isinstance(row, dict) else "" for c in COLUMNS[tab]] for row in rows] \
            if tab == "Entity Mapping Log" else rows_for(tab, rows)
        for row in body:
            sheet.append(row)
        for idx, column in enumerate(COLUMNS[tab], start=1):
            width = 60 if "fullText" in column or "summary" in column else 22
            if column in ("source.url", "content.github_url", "content.paper_url"):
                width = 42
            sheet.column_dimensions[get_column_letter(idx)].width = width
        with open(os.path.join(out_dir, "csv", f"{tab}.csv"), "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(COLUMNS[tab])
            writer.writerows(body)
    path = os.path.join(out_dir, "workbook.xlsx")
    workbook.save(path)
    return path
