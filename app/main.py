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


def _render_pdf_pages(source_bytes: bytes) -> list[Image.Image]:
    pdf = pdfium.PdfDocument(source_bytes)
    page_count = len(pdf)
    if page_count < 1:
        raise ValueError("PDF has no pages.")

    pages: list[Image.Image] = []
    for page_index in range(page_count):
        page = pdf[page_index]
        rendered = page.render(scale=2)
        pil_image = rendered.to_pil().convert("RGBA")
        pages.append(pil_image)
        rendered.close()
        page.close()

    pdf.close()
    return pages


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



@app.get("/download/{token}")
def download(token: str) -> Response:
    file_info = DOWNLOAD_CACHE.get(token)
    if not file_info:
        return Response(content="File not found.", status_code=404)

    filename, content, media_type = file_info
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return Response(content=content, media_type=media_type, headers=headers)
