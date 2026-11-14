FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/opt/bot/.venv/bin:$PATH"

WORKDIR /opt/bot
COPY pyproject.toml uv.lock README.md ./
COPY app ./app
RUN pip install uv==0.9.8 \
    && uv sync --frozen --no-dev --no-editable --no-cache \
    && useradd --uid 10001 --no-create-home bot \
    && mkdir -p /opt/bot/data \
    && chown 10001:10001 /opt/bot/data \
    && chmod 700 /opt/bot/data
USER 10001:10001
CMD ["python", "-m", "app.main"]
