from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError


LABEL_WIDTH = 812
LABEL_HEIGHT = 1218
LABEL_DPI = 203
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
SUPPORTED_TEXT_SUFFIXES = {".txt", ".csv"}
SUPPORTED_SUFFIXES = SUPPORTED_IMAGE_SUFFIXES | SUPPORTED_TEXT_SUFFIXES | {".pdf"}


class ConversionError(RuntimeError):
    pass


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size=size)
    return ImageFont.load_default()


def _rotation_for_fit(image: Image.Image, orientation: str) -> int:
    if orientation == "portrait":
        return 90 if image.width > image.height else 0
    if orientation == "landscape":
        return 90 if image.height > image.width else 0
    normal_scale = min(LABEL_WIDTH / image.width, LABEL_HEIGHT / image.height)
    rotated_scale = min(LABEL_WIDTH / image.height, LABEL_HEIGHT / image.width)
    return 90 if rotated_scale > normal_scale * 1.08 else 0


def trim_to_content(image: Image.Image, *, threshold: int = 245, padding: int = 12) -> Image.Image:
    """Recorta los márgenes casi blancos, dejando solo el área con contenido real.

    Sirve para etiquetas incrustadas en una hoja más grande (por ejemplo, guías
    de despacho que ubican la etiqueta 4x6 en una esquina de una hoja A4). Si
    no se detecta un margen recortable, devuelve la imagen sin cambios.
    """
    grayscale = image.convert("L")
    mask = grayscale.point(lambda p: 255 if p <= threshold else 0)
    bbox = mask.getbbox()
    if bbox is None:
        return image
    left, top, right, bottom = bbox
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(image.width, right + padding)
    bottom = min(image.height, bottom + padding)
    if (right - left) >= image.width * 0.98 and (bottom - top) >= image.height * 0.98:
        return image
    return image.crop((left, top, right, bottom))


def normalize_image(
    image: Image.Image,
    *,
    fit_mode: str,
    orientation: str,
) -> Image.Image:
    if fit_mode not in {"contain", "cover", "trim"}:
        raise ConversionError("Modo de ajuste inválido")
    if orientation not in {"auto", "portrait", "landscape"}:
        raise ConversionError("Orientación inválida")

    working = image.convert("RGB")
    if fit_mode == "trim":
        working = trim_to_content(working)
    if _rotation_for_fit(working, orientation):
        working = working.rotate(90, expand=True)

    if fit_mode == "cover":
        scale = max(LABEL_WIDTH / working.width, LABEL_HEIGHT / working.height)
    else:
        scale = min(LABEL_WIDTH / working.width, LABEL_HEIGHT / working.height)

    resized = working.resize(
        (
            max(1, round(working.width * scale)),
            max(1, round(working.height * scale)),
        ),
        Image.Resampling.LANCZOS,
    )

    canvas = Image.new("L", (LABEL_WIDTH, LABEL_HEIGHT), 255)
    grayscale = resized.convert("L")
    left = (LABEL_WIDTH - grayscale.width) // 2
    top = (LABEL_HEIGHT - grayscale.height) // 2
    canvas.paste(grayscale, (left, top))

    # La trama Floyd-Steinberg conserva degradados y bordes en impresoras térmicas.
    return canvas.convert("1", dither=Image.Dither.FLOYDSTEINBERG)


def _render_text(path: Path) -> Image.Image:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="latin-1")
    text = text.replace("\t", "    ").strip()
    if not text:
        raise ConversionError("El archivo de texto está vacío")

    canvas = Image.new("RGB", (LABEL_WIDTH, LABEL_HEIGHT), "white")
    draw = ImageDraw.Draw(canvas)
    font = _font(30)
    margin = 42
    line_height = 40

    # Un cálculo conservador mantiene el texto dentro de 4 pulgadas.
    average_width = max(1, draw.textlength("ABCDEFGHIJKLMNOPQRSTUVWXYZ", font=font) / 26)
    chars_per_line = max(12, int((LABEL_WIDTH - margin * 2) / average_width))
    lines: list[str] = []
    for paragraph in text.splitlines():
        lines.extend(textwrap.wrap(paragraph, width=chars_per_line) or [""])

    max_lines = (LABEL_HEIGHT - margin * 2) // line_height
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + ["…"]
    draw.multiline_text(
        (margin, margin),
        "\n".join(lines),
        fill="black",
        font=font,
        spacing=10,
    )
    return canvas


def _render_pdf(path: Path, work_dir: Path, *, page: int | None = None) -> list[Path]:
    if path.read_bytes()[:5] != b"%PDF-":
        raise ConversionError("El archivo no contiene un PDF válido")
    if not shutil.which("pdftoppm"):
        raise ConversionError("pdftoppm no está instalado")

    prefix = work_dir / "pdf-page"
    args = ["pdftoppm", "-png", "-r", str(LABEL_DPI)]
    if page is not None:
        args += ["-f", str(page), "-l", str(page)]
    args += [str(path), str(prefix)]
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    pages = sorted(work_dir.glob("pdf-page-*.png"))
    if result.returncode != 0 or not pages:
        if page is not None:
            raise ConversionError(f"El PDF no tiene una página {page}")
        raise ConversionError(result.stderr.strip() or "No fue posible renderizar el PDF")
    if len(pages) > 50:
        raise ConversionError("El PDF excede el máximo de 50 páginas")
    return pages


def convert_document(
    source: Path,
    output_dir: Path,
    *,
    fit_mode: str = "contain",
    orientation: str = "auto",
    page: int | None = None,
) -> list[Path]:
    suffix = source.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ConversionError(
            "Formato no compatible. Usa PDF, PNG, JPEG, WebP, BMP, TIFF, TXT o CSV"
        )
    if page is not None and page < 1:
        raise ConversionError("El número de página debe ser 1 o mayor")

    output_dir.mkdir(parents=True, exist_ok=True)
    rendered: list[Image.Image] = []
    temporary_pages: list[Path] = []

    try:
        if suffix == ".pdf":
            temporary_pages = _render_pdf(source, output_dir, page=page)
            for page in temporary_pages:
                with Image.open(page) as image:
                    rendered.append(image.copy())
        elif suffix in SUPPORTED_TEXT_SUFFIXES:
            rendered.append(_render_text(source))
        else:
            try:
                with Image.open(source) as image:
                    image.verify()
                with Image.open(source) as image:
                    rendered.append(image.copy())
            except (UnidentifiedImageError, OSError) as exc:
                raise ConversionError("La imagen está dañada o no es compatible") from exc

        outputs: list[Path] = []
        for index, image in enumerate(rendered, start=1):
            normalized = normalize_image(
                image,
                fit_mode=fit_mode,
                orientation=orientation,
            )
            destination = output_dir / f"page-{index:03d}.png"
            normalized.save(destination, format="PNG", dpi=(LABEL_DPI, LABEL_DPI), optimize=True)
            outputs.append(destination)
        return outputs
    finally:
        for page in temporary_pages:
            page.unlink(missing_ok=True)

