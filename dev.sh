#!/usr/bin/env bash
# Start the FastAPI backend (:8000) and Vite frontend together. Ctrl+C stops both.
set -e
cd "$(dirname "$0")"

trap 'kill 0' EXIT

(cd server && .venv/bin/uvicorn app.main:app --reload --port 8000) &
(cd frontend && npm run dev) &
wait
