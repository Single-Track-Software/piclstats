FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
COPY alembic.ini .
COPY alembic/ alembic/

RUN pip install --no-cache-dir .

EXPOSE ${PORT:-8080}

COPY start.sh .

# Run as an unprivileged user: uvicorn binds 8080, nothing needs root.
RUN useradd --system --uid 10001 --no-create-home app \
    && chown -R app:app /app
USER app

CMD ["./start.sh"]
