from html import escape
from io import BytesIO
from pathlib import Path
import zipfile
from uuid import uuid4

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps
import pypdfium2 as pdfium
from pillow_heif import register_heif_opener

app = FastAPI()
register_heif_opener()

templates = Jinja2Templates(directory="app/templates")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

DOWNLOAD_CACHE: dict[str, tuple[str, bytes, str]] = {}

SUPPORTED_COMPRESSION_SUFFIXES = {".heic", ".heif", ".png", ".jpg", ".jpeg", ".pdf"}
SUPPORTED_COMPRESSION_CONTENT_TYPES = {
    "image/heic",
    "image/heif",
    "image/png",
    "image/jpeg",
    "application/pdf",
}

UNIT_DEFINITIONS: dict[str, list[tuple[str, str, float]]] = {
    "powers_of_ten": [
        ("atto", "a", 1e-18),
        ("femto", "f", 1e-15),
        ("pico", "p", 1e-12),
        ("nano", "n", 1e-9),
        ("micro", "u", 1e-6),
        ("milli", "m", 1e-3),
        ("base", "base", 1.0),
        ("kilo", "k", 1e3),
        ("mega", "M", 1e6),
        ("giga", "G", 1e9),
        ("tera", "T", 1e12),
        ("peta", "P", 1e15),
        ("exa", "E", 1e18),
    ],
    "length": [
        ("nanometer", "nm", 1e-9),
        ("micrometer", "um", 1e-6),
        ("millimeter", "mm", 1e-3),
        ("meter", "m", 1.0),
        ("kilometer", "km", 1e3),
    ],
    "time": [
        ("nanosecond", "ns", 1e-9),
        ("microsecond", "us", 1e-6),
        ("millisecond", "ms", 1e-3),
        ("second", "s", 1.0),
        ("minute", "min", 60.0),
        ("hour", "h", 3600.0),
    ],
    "weight": [
        ("nanogram", "ng", 1e-9),
        ("microgram", "ug", 1e-6),
        ("milligram", "mg", 1e-3),
        ("gram", "g", 1.0),
        ("kilogram", "kg", 1e3),
        ("ton", "t", 1e6),
    ],
    "bit_byte": [
        ("bit", "b", 1.0),
        ("byte", "B", 8.0),
        ("kibibit", "Kib", 1024.0),
        ("kibibyte", "KiB", 8.0 * 1024.0),
        ("mebibit", "Mib", 1024.0**2),
        ("mebibyte", "MiB", 8.0 * (1024.0**2)),
        ("gibibit", "Gib", 1024.0**3),
        ("gibibyte", "GiB", 8.0 * (1024.0**3)),
        ("tebibit", "Tib", 1024.0**4),
        ("tebibyte", "TiB", 8.0 * (1024.0**4)),
    ],
}


def _as_rgb_without_alpha(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA"):
        alpha = img.getchannel("A")
        base = Image.new("RGB", img.size, (255, 255, 255))
        base.paste(img.convert("RGB"), mask=alpha)
        return base
    if img.mode == "P":
        return img.convert("RGB")
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _save_png_bytes(img: Image.Image) -> bytes:
    out = BytesIO()
    img.save(out, format="PNG", optimize=True, compress_level=9)
    return out.getvalue()


def _compress_png(img: Image.Image, quality: int) -> bytes:
    # Try multiple PNG encodings and keep the smallest result.
    candidates: list[bytes] = []
    candidates.append(_save_png_bytes(img))

    base_colors = int(32 + ((quality - 20) / 75) * 224)
    base_colors = max(32, min(256, base_colors))
    palette_sizes = sorted(
        {base_colors, max(16, base_colors // 2), max(16, base_colors // 4)},
        reverse=True,
    )

    for colors in palette_sizes:
        work = img
        if work.mode not in ("RGB", "RGBA"):
            work = work.convert("RGBA")

        if "A" in work.getbands():
            quantized = work.quantize(
                colors=colors,
                method=Image.Quantize.FASTOCTREE,
                dither=Image.Dither.NONE,
            )
        else:
            quantized = work.convert("RGB").quantize(
                colors=colors,
                method=Image.Quantize.MEDIANCUT,
                dither=Image.Dither.NONE,
            )

        candidates.append(_save_png_bytes(quantized))

    return min(candidates, key=len)


def _encode_image(img: Image.Image, output: str) -> tuple[bytes, str, str, str]:
    out = BytesIO()

    if output == "png":
        img.save(out, format="PNG", optimize=True, compress_level=9)
        return out.getvalue(), "png", "image/png", "PNG"

    if output == "jpg":
        img = _as_rgb_without_alpha(img)

        img.save(out, format="JPEG", quality=85, optimize=True, progressive=True)
        return out.getvalue(), "jpg", "image/jpeg", "JPG"

    raise ValueError("Unsupported output format.")


def _encode_pdf(img: Image.Image) -> tuple[bytes, str, str, str]:
    out = BytesIO()
    pdf_ready = _as_rgb_without_alpha(img)
    pdf_ready.save(out, format="PDF")
    return out.getvalue(), "pdf", "application/pdf", "PDF"


def _render_pdf_pages(source_bytes: bytes, scale: float = 2.0) -> list[Image.Image]:
    pdf = pdfium.PdfDocument(source_bytes)
    page_count = len(pdf)
    if page_count < 1:
        raise ValueError("PDF has no pages.")

    pages: list[Image.Image] = []
    for page_index in range(page_count):
        page = pdf[page_index]
        rendered = page.render(scale=scale)
        pil_image = rendered.to_pil().convert("RGBA")
        pages.append(pil_image)
        rendered.close()
        page.close()

    pdf.close()
    return pages


def _encode_pdf_pages(pages: list[Image.Image], quality: int) -> bytes:
    if not pages:
        raise ValueError("No pages to encode.")

    pdf_pages = [_as_rgb_without_alpha(page) for page in pages]
    out = BytesIO()
    pdf_pages[0].save(
        out,
        format="PDF",
        save_all=True,
        append_images=pdf_pages[1:],
        optimize=True,
        quality=quality,
    )
    return out.getvalue()


def _format_number(value: float) -> str:
    if value == 0:
        return "0"
    abs_value = abs(value)
    if abs_value >= 1e6 or abs_value < 1e-4:
        return f"{value:.8e}"
    return f"{value:.8f}".rstrip("0").rstrip(".")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})
    
    
@app.post("/convert", response_class=HTMLResponse)
async def convert(file: UploadFile = File(...), output: str = Form("png")) -> HTMLResponse:
    try:
        source_name = file.filename or "converted"
        source_bytes = await file.read()
        suffix = Path(source_name).suffix.lower()
        content_type = (file.content_type or "").lower()
        is_pdf = suffix == ".pdf" or content_type == "application/pdf"

        if is_pdf:
            if output == "pdf":
                return HTMLResponse("<p>PDF to PDF is not supported.</p>", status_code=400)

            pages = _render_pdf_pages(source_bytes)

            if len(pages) == 1:
                out_bytes, ext, media_type, label = _encode_image(pages[0], output)
                output_name = f"{Path(source_name).stem}.{ext}"
            else:
                archive = BytesIO()
                with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
                    for index, page in enumerate(pages, start=1):
                        page_bytes, ext, _, _ = _encode_image(page, output)
                        page_name = f"{Path(source_name).stem}_page_{index:03d}.{ext}"
                        zip_file.writestr(page_name, page_bytes)

                out_bytes = archive.getvalue()
                media_type = "application/zip"
                output_name = f"{Path(source_name).stem}_{output}_pages.zip"
                label = "ZIP"

            for page in pages:
                page.close()
        else:
            with Image.open(BytesIO(source_bytes)) as img:
                img = ImageOps.exif_transpose(img)
                if output == "pdf":
                    out_bytes, ext, media_type, label = _encode_pdf(img)
                else:
                    out_bytes, ext, media_type, label = _encode_image(img, output)
                output_name = f"{Path(source_name).stem}.{ext}"

    except Exception:
        return HTMLResponse("<p>Conversion failed. Please check the uploaded file.</p>", status_code=400)

    token = uuid4().hex
    DOWNLOAD_CACHE[token] = (output_name, out_bytes, media_type)

    safe_name = escape(output_name)
    return HTMLResponse(
        f"<p>Successfully converted.</p>"
        f'<a href="/download/{token}" download="{safe_name}">Download {label}</a>'
    )


@app.post("/compress", response_class=HTMLResponse)
async def compress(file: UploadFile = File(...), quality: int = Form(75)) -> HTMLResponse:
    quality = max(20, min(95, quality))

    try:
        source_name = file.filename or "compressed"
        source_bytes = await file.read()
        suffix = Path(source_name).suffix.lower()
        content_type = (file.content_type or "").lower()
        is_pdf = suffix == ".pdf" or content_type == "application/pdf"

        if (
            suffix not in SUPPORTED_COMPRESSION_SUFFIXES
            and content_type not in SUPPORTED_COMPRESSION_CONTENT_TYPES
        ):
            return HTMLResponse(
                "<p>Unsupported file type for compression.</p>",
                status_code=400,
            )

        if is_pdf:
            # Lower quality uses lower raster scale and stronger PDF image compression.
            scale = 0.9 + ((quality - 20) / 75) * 1.1
            pages = _render_pdf_pages(source_bytes, scale=scale)
            out_bytes = _encode_pdf_pages(pages, quality=quality)
            ext = "pdf"
            media_type = "application/pdf"
            label = "PDF"
            for page in pages:
                page.close()
        else:
            with Image.open(BytesIO(source_bytes)) as img:
                img = ImageOps.exif_transpose(img)

                if suffix == ".png" or content_type == "image/png":
                    out_bytes = _compress_png(img, quality)
                    ext = "png"
                    media_type = "image/png"
                    label = "PNG"
                else:
                    out = BytesIO()
                    img = _as_rgb_without_alpha(img)
                    img.save(
                        out,
                        format="JPEG",
                        quality=quality,
                        optimize=True,
                        progressive=True,
                    )
                    ext = "jpg"
                    media_type = "image/jpeg"
                    label = "JPG"
                    out_bytes = out.getvalue()

    except Exception:
        return HTMLResponse(
            "<p>Compression failed. Please check the uploaded file.</p>",
            status_code=400,
        )

    token = uuid4().hex
    output_name = f"{Path(source_name).stem}_compressed.{ext}"
    DOWNLOAD_CACHE[token] = (output_name, out_bytes, media_type)

    safe_name = escape(output_name)
    original_size = len(source_bytes)
    compressed_size = len(out_bytes)
    savings = original_size - compressed_size
    percent = 0.0
    if original_size > 0:
        percent = (savings / original_size) * 100

    if savings >= 0:
        status = (
            f"<p>Successfully compressed ({label}). Saved {savings} bytes "
            f"({percent:.1f}%).</p>"
        )
    else:
        status = (
            f"<p>Compression finished ({label}), but file grew by {abs(savings)} bytes "
            f"({abs(percent):.1f}%).</p>"
        )

    return HTMLResponse(
        status
        + f'<a href="/download/{token}" download="{safe_name}">Download compressed file</a>'
    )


@app.post("/unit-convert", response_class=HTMLResponse)
async def unit_convert(
    category: str = Form(...),
    unit: str = Form(...),
    value: float = Form(...),
) -> HTMLResponse:
    category = category.lower().strip()
    unit = unit.strip()

    units = UNIT_DEFINITIONS.get(category)
    if not units:
        return HTMLResponse("<p>Unsupported unit category.</p>", status_code=400)

    unit_map = {symbol: (name, factor) for name, symbol, factor in units}
    selected = unit_map.get(unit)
    if selected is None:
        return HTMLResponse("<p>Unsupported input unit.</p>", status_code=400)

    _, input_factor = selected
    base_value = value * input_factor

    rows: list[str] = []
    for name, symbol, factor in units:
        converted = base_value / factor
        rows.append(
            "<tr>"
            f"<td>{escape(name)}</td>"
            f"<td>{escape(symbol)}</td>"
            f"<td>{_format_number(converted)}</td>"
            "</tr>"
        )

    category_label = escape(category.replace("_", " ").title())
    input_label = escape(unit)
    input_value = _format_number(value)
    table = (
        "<table class='unit-table'>"
        "<thead><tr><th>Unit</th><th>Symbol</th><th>Value</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )
    return HTMLResponse(
        f"<p><strong>{category_label}</strong> conversion for {input_value} {input_label}:</p>"
        + table
    )



@app.get("/download/{token}")
def download(token: str) -> Response:
    file_info = DOWNLOAD_CACHE.get(token)
    if not file_info:
        return Response(content="File not found.", status_code=404)

    filename, content, media_type = file_info
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=content, media_type=media_type, headers=headers)
