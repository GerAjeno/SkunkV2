from pathlib import Path

from PIL import Image

from skunk_pc.converter import LABEL_HEIGHT, LABEL_WIDTH, convert_document, normalize_image


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
