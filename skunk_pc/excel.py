from __future__ import annotations

import io
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo


HEADERS = ("Trabajo", "Origen / IP", "Impresora", "Resultado", "Páginas", "Hora")
STATUS_LABELS = {
    "queued": "En cola",
    "processing": "Procesando",
    "submitted": "Enviado",
    "completed": "Completado",
    "failed": "Error",
    "cancelled": "Cancelado",
}
LOCAL_TIMEZONE = ZoneInfo("America/Santiago")


def _cell(reference: str, value: object, *, numeric: bool = False) -> str:
    if numeric:
        return f'<c r="{reference}"><v>{int(value or 0)}</v></c>'
    text = escape(str(value or ""))
    return f'<c r="{reference}" t="inlineStr"><is><t>{text}</t></is></c>'


def _local_time(value: object) -> str:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(LOCAL_TIMEZONE)
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value or "")


def build_history_xlsx(jobs: list[dict]) -> bytes:
    rows = []
    values = [HEADERS]
    for job in jobs:
        result = STATUS_LABELS.get(job.get("status"), job.get("status", ""))
        if job.get("error"):
            result = f"{result}: {job['error']}"
        values.append(
            (
                job.get("original_name", ""),
                job.get("source_device", ""),
                job.get("printer_name", ""),
                result,
                job.get("pages", 0),
                _local_time(job.get("created_at")),
            )
        )
    for row_number, row in enumerate(values, 1):
        cells = "".join(
            _cell(
                f"{chr(65 + column)}{row_number}",
                value,
                numeric=row_number > 1 and column == 4,
            )
            for column, value in enumerate(row)
        )
        rows.append(f'<row r="{row_number}">{cells}</row>')

    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<cols><col min="1" max="1" width="38" customWidth="1"/>'
        '<col min="2" max="2" width="28" customWidth="1"/>'
        '<col min="3" max="3" width="22" customWidth="1"/>'
        '<col min="4" max="4" width="42" customWidth="1"/>'
        '<col min="5" max="5" width="10" customWidth="1"/>'
        '<col min="6" max="6" width="22" customWidth="1"/></cols>'
        f'<sheetData>{"".join(rows)}</sheetData>'
        f'<autoFilter ref="A1:F{max(len(values), 1)}"/>'
        '</worksheet>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Historial" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return output.getvalue()
