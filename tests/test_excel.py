import io
import zipfile

from skunk_pc.excel import build_history_xlsx


def test_build_history_xlsx_contains_filtered_history_data() -> None:
    workbook = build_history_xlsx(
        [
            {
                "original_name": "etiqueta.pdf",
                "source_device": "IP 192.168.1.55",
                "printer_name": "Zima",
                "status": "completed",
                "pages": 1,
                "created_at": "2026-07-29T15:30:00+00:00",
            }
        ]
    )

    assert workbook.startswith(b"PK")
    with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
        sheet = archive.read("xl/worksheets/sheet1.xml").decode()
    assert "etiqueta.pdf" in sheet
    assert "IP 192.168.1.55" in sheet
    assert "Completado" in sheet
    assert '<c r="E2"><v>1</v></c>' in sheet
