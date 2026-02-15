from html import escape
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

app = FastAPI()
register_heif_opener()

templates = Jinja2Templates(directory="app/templates")

app.mount("/static", StaticFiles(directory="app/static"), name="static")

DOWNLOAD_CACHE: dict[str, tuple[str, bytes, str]] = {}


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("index.html", {"request": request})
    
    
@app.post("/convert", response_class=HTMLResponse)
async def convert(file: UploadFile = File(...), output: str = Form("png")) -> HTMLResponse:
    output = output.lower().strip()

    try:
        source_bytes = await file.read()
        with Image.open(BytesIO(source_bytes)) as img:
            img = ImageOps.exif_transpose(img)

            out = BytesIO()

            if output == "png":
                img.save(out, format="PNG", optimize=True, compress_level=9)
                out_bytes = out.getvalue()
                ext = "png"
                media_type = "image/png"
                label = "PNG"

            elif output == "jpg":
                if img.mode in ("RGBA", "LA"):
                    # JPEG has no alpha channel, so flatten onto white
                    alpha = img.getchannel("A")
                    base = Image.new("RGB", img.size, (255, 255, 255))
                    base.paste(img.convert("RGB"), mask=alpha)
                    img = base
                elif img.mode == "P":
                    img = img.convert("RGB")
                img.save(out, format="JPEG", quality=85, optimize=True, progressive=True)
                out_bytes = out.getvalue()
                ext = "jpg"
                media_type = "image/jpeg"
                label = "JPG"

            else:
                return HTMLResponse("<p>Unsupported output format.</p>", status_code=400)

    except Exception:
        return HTMLResponse("<p>Conversion failed. Please check the uploaded file.</p>", status_code=400)

    token = uuid4().hex
    output_name = f"{Path(file.filename or 'converted').stem}.{ext}"
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
