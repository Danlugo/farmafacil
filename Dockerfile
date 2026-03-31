FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml .
COPY src/ src/
RUN pip install --prefix=/install --no-cache-dir .

FROM python:3.12-slim AS runtime
RUN groupadd -r farmafacil && useradd -r -g farmafacil farmafacil

COPY --from=builder /install /usr/local
COPY src/ /app/src/

WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Data directory for SQLite DB (mount as volume in production)
RUN mkdir -p /app/data && chown farmafacil:farmafacil /app/data
VOLUME /app/data

USER farmafacil

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "farmafacil.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
