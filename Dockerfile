FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 1. Install CPU-only PyTorch first (Drops image size from ~5GB to ~200MB, eliminates nvidia-* packages)
RUN pip install --upgrade pip && \
    pip install torch --index-url https://download.pytorch.org/whl/cpu

# 2. Copy dependency files first to maximize Docker layer caching
COPY pyproject.toml requirements.txt ./

# 3. Install remaining dependencies using PyTorch CPU wheel repo
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt

# 4. Copy source code and install project package
COPY . .
RUN pip install --no-deps -e .

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]