# === ANVESHA Dockerfile ===
# Optimized for Render free tier: 512MB RAM / 0.1 CPU
# Single-stage build to minimize image size

FROM python:3.11-slim

# Install system dependencies
# tesseract-ocr: PDF/image OCR
# ghostscript: PDF processing support
# libgl1: OpenCV headless dependency
# ffmpeg: Audio processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    ghostscript \
    libgl1-mesa-glx \
    libglib2.0-0 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first (Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create uploads directory (temporary file storage)
RUN mkdir -p /tmp/anvesha_uploads

# Environment
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV APP_ENV=production

# Expose port
EXPOSE 8000

# Health check (Use 127.0.0.1 instead of localhost to avoid IPv6 routing issues)
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD sh -c "python -c \"import httpx; r = httpx.get('http://127.0.0.1:${PORT:-8000}/health'); r.raise_for_status()\"" || exit 1

# Start with uvicorn — single worker to stay within RAM limits
# Use shell form to allow environment variable expansion (Render sets PORT)
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --timeout-keep-alive 65
