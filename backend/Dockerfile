# Build context: backend/
# Expected invocation from repo root: docker build -f backend/Dockerfile backend/
# (fly.toml sets [build] dockerfile = "backend/Dockerfile"; Fly uses repo root as
# context but passes the dockerfile path, so COPY paths are relative to backend/.)

FROM python:3.13-slim

# Create a non-root user
RUN addgroup --system app && adduser --system --ingroup app app

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY aidigest/ ./aidigest/
COPY scripts/ ./scripts/

# Switch to non-root user
USER app

EXPOSE 8000

CMD ["sh", "-c", "uvicorn aidigest.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
