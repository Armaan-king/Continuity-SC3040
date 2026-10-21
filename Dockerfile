FROM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
COPY backend/continuity/api/walkthrough.jsonl /build/backend/continuity/api/walkthrough.jsonl
RUN npm run build

FROM python:3.13-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CONTINUITY_FRONTEND_DIR=/app/frontend/dist
WORKDIR /app
COPY backend/requirements.lock /app/backend/requirements.lock
RUN pip install --no-cache-dir -r /app/backend/requirements.lock
COPY backend/ /app/backend/
RUN pip install --no-cache-dir --no-deps -e /app/backend && useradd --create-home continuity \
    && chown -R continuity:continuity /app
COPY --from=frontend /build/frontend/dist /app/frontend/dist
USER continuity
EXPOSE 8000
CMD ["sh", "-c", "uvicorn continuity.server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers"]
