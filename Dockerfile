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

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir .

COPY backend/app/ ./app/
COPY --from=frontend-build /build/frontend/dist/ /frontend/dist/

ENV THEMIS_STATIC_DIR=/frontend/dist

RUN mkdir -p /data

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
