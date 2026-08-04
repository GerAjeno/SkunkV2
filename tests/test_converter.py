from pathlib import Path

import pytest
from PIL import Image

from skunk_pc.converter import (
    ConversionError,
    LABEL_HEIGHT,
    LABEL_WIDTH,
    convert_document,
    normalize_image,
    trim_to_content,
)


def test_normalize_image_is_exact_4x6() -> None:
    source = Image.new("RGB", (1600, 900), "white")
    result = normalize_image(source, fit_mode="contain", orientation="auto")
    assert result.size == (LABEL_WIDTH, LABEL_HEIGHT)
    assert result.mode == "1"


def test_convert_image(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (400, 600), "white").save(source)
    outputs = convert_document(source, tmp_path / "output")
    assert len(outputs) == 1
    with Image.open(outputs[0]) as converted:
        assert converted.size == (LABEL_WIDTH, LABEL_HEIGHT)


def test_trim_to_content_crops_to_visible_area() -> None:
    # Etiqueta de 300x400 ubicada en la esquina superior izquierda de una
    # hoja mucho más grande, como una guía de despacho con margen enorme.
    canvas = Image.new("RGB", (2000, 1500), "white")
    label = Image.new("RGB", (300, 400), "black")
    canvas.paste(label, (50, 60))

    trimmed = trim_to_content(canvas)

    assert trimmed.size != canvas.size
    assert abs(trimmed.width - 300) <= 30
    assert abs(trimmed.height - 400) <= 30


def test_trim_to_content_leaves_full_bleed_image_unchanged() -> None:
    canvas = Image.new("RGB", (800, 1200), "black")
    trimmed = trim_to_content(canvas)
    assert trimmed.size == canvas.size


def test_trim_to_content_handles_blank_page() -> None:
    canvas = Image.new("RGB", (800, 1200), "white")
    trimmed = trim_to_content(canvas)
    assert trimmed.size == canvas.size


def test_normalize_image_trim_fit_mode_fills_more_of_the_label() -> None:
    canvas = Image.new("RGB", (2000, 1500), "white")
    label = Image.new("RGB", (300, 400), "black")
    canvas.paste(label, (50, 60))

    contained = normalize_image(canvas, fit_mode="contain", orientation="auto")
    trimmed = normalize_image(canvas, fit_mode="trim", orientation="auto")

    assert trimmed.size == (LABEL_WIDTH, LABEL_HEIGHT) == contained.size
    contained_dark_pixels = sum(1 for pixel in contained.getdata() if pixel == 0)
    trimmed_dark_pixels = sum(1 for pixel in trimmed.getdata() if pixel == 0)
    assert trimmed_dark_pixels > contained_dark_pixels


def test_convert_document_rejects_page_below_one(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    Image.new("RGB", (400, 600), "white").save(source)
    with pytest.raises(ConversionError, match="1 o mayor"):
        convert_document(source, tmp_path / "output", page=0)


def test_convert_document_selects_a_single_pdf_page(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    first = Image.new("RGB", (400, 600), "white")
    second = Image.new("RGB", (400, 600), "black")
    first.save(source, save_all=True, append_images=[second])

    outputs = convert_document(source, tmp_path / "output", page=2)

    assert len(outputs) == 1
    with Image.open(outputs[0]) as converted:
        # La página 2 es negra: al convertir a blanco y negro debe
        # predominar el negro, a diferencia de la página 1 (blanca).
        black_pixels = sum(1 for pixel in converted.getdata() if pixel == 0)
        assert black_pixels > converted.width * converted.height * 0.9


def test_convert_document_missing_pdf_page_raises_clear_error(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    Image.new("RGB", (400, 600), "white").save(source)

    with pytest.raises(ConversionError, match="no tiene una página 5"):
        convert_document(source, tmp_path / "output", page=5)
