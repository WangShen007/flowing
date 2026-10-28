#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="${ROOT_DIR}/frontend"
BACKEND_DIR="${ROOT_DIR}/backend"
PYTHON_BIN="${BACKEND_DIR}/.venv/bin/python"
PID_FILE="${ROOT_DIR}/.flowing-backend.pid"
LOG_FILE="${ROOT_DIR}/flowing.log"
HOST="${FLOWING_HOST:-127.0.0.1}"
PORT="${FLOWING_PORT:-8000}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  printf 'Backend virtualenv not found: %s\n' "${PYTHON_BIN}" >&2
  printf 'Create it with: python3.12 -m venv backend/.venv\n' >&2
  exit 1
fi

if [[ -f "${PID_FILE}" ]]; then
  old_pid="$(<"${PID_FILE}")"
  if [[ "${old_pid}" =~ ^[0-9]+$ ]] && kill -0 "${old_pid}" 2>/dev/null; then
    kill "${old_pid}"
    for _ in {1..20}; do
      kill -0 "${old_pid}" 2>/dev/null || break
      sleep 0.25
    done
  fi
  rm -f "${PID_FILE}"
fi

npm --prefix "${FRONTEND_DIR}" ci
npm --prefix "${FRONTEND_DIR}" run build

(
  cd "${BACKEND_DIR}"
  exec "${PYTHON_BIN}" -m uvicorn app.main:app --host "${HOST}" --port "${PORT}"
) >>"${LOG_FILE}" 2>&1 &

printf '%s\n' "$!" >"${PID_FILE}"
printf 'Flowing restarted: http://%s:%s\n' "${HOST}" "${PORT}"
printf 'Log: %s\n' "${LOG_FILE}"
