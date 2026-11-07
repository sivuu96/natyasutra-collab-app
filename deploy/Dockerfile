# Production image: builds the React app, then runs the API and serves the built UI from one origin.
# Build from the repository root:  docker build -f deploy/Dockerfile .

FROM node:22-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ENVIRONMENT=production \
    FRONTEND_DIST=/app/frontend/dist
WORKDIR /app/backend
RUN useradd --create-home --uid 10001 hub
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=frontend /src/frontend/dist /app/frontend/dist
USER hub
EXPOSE 8000
# Migrations run before the server starts. --proxy-headers trusts X-Forwarded-* from the platform's proxy,
# which is what makes per-client rate limits and the public origin correct behind TLS termination.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --no-server-header"]
