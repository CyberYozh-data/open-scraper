FROM mcr.microsoft.com/playwright/python:v1.57.0-jammy

ENV PATH=/app/.venv/bin:$PATH \
    \
    # Python
    PYTHONPATH=/app:$PYTHONPATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    \
    # Pip
    PIP_NO_CACHE_DIR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100 \
    \
    # Ruff
    RUFF_CACHE_DIR=/tmp

WORKDIR /app

COPY pyproject.toml requirements.txt ./

RUN pip install -r requirements.txt

COPY src /app/src
COPY scripts /app/scripts

ENV HOST=0.0.0.0
ENV PORT=8000

CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
