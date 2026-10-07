# Multi-stage build: Node builds the frontend, Python runs the API and serves it (single process).
# Real Recovery Mode / Recovery Shield are disabled by RECOVERYLENS_PUBLIC_DEMO=true (see app/main.py) --
# they act on the server's own local drives/folders, which is meaningless on a shared public host.

FROM node:24-slim AS frontend-build
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM python:3.12-slim AS backend
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .
COPY --from=frontend-build /src/frontend/dist /app/frontend/dist

ENV RECOVERYLENS_PUBLIC_DEMO=true
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
