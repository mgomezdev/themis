# Stage 1: build React app
FROM node:20-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python runtime
FROM python:3.11-slim AS runtime
WORKDIR /app

# Install system deps (ffmpeg and OrcaSlicer added in a later plan)
RUN apt-get update \
    && rm -rf /var/lib/apt/lists/*

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir ".[dev]" || pip install --no-cache-dir .

COPY backend/app/ ./app/

# Copy frontend dist to /frontend/dist so that main.py's STATIC_DIR
# (Path(__file__).parent.parent.parent / "frontend" / "dist") resolves correctly.
# With main.py at /app/app/main.py: .parent.parent.parent = / → /frontend/dist
COPY --from=frontend-build /build/frontend/dist/ /frontend/dist/

RUN mkdir -p /data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
