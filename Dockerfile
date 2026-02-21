FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# libheif helps HEIC/HEIF support at runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libheif1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.docker.txt ./
RUN pip install --no-cache-dir -r requirements.docker.txt

COPY app ./app
COPY README.md TODO.md ./

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
