#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
CONFIG_PATH="${OPENCLAW_CONFIG_PATH:-$HOME/.openclaw/openclaw.json}"
PLUGIN_ID="openclaw-mem0-plugin"
MODE_VALUE="${MEM0_MODE:-platform}"
API_KEY_VALUE="${MEM0_API_KEY:-test-api-key}"
USER_ID_VALUE="${MEM0_USER_ID:-openclaw-user}"
HOST_VALUE="${MEM0_HOST:-https://api.mem0.ai}"
GATEWAY_PORT="${OPENCLAW_VERIFY_PORT:-18790}"
PACK_DIR="$(mktemp -d)"
BACKUP_PATH=""
GATEWAY_PID=""

cleanup() {
  if [[ -n "${GATEWAY_PID}" ]] && kill -0 "${GATEWAY_PID}" >/dev/null 2>&1; then
    kill "${GATEWAY_PID}" >/dev/null 2>&1 || true
    wait "${GATEWAY_PID}" >/dev/null 2>&1 || true
  fi

  if [[ -n "${BACKUP_PATH}" && -f "${BACKUP_PATH}" ]]; then
    cp "${BACKUP_PATH}" "${CONFIG_PATH}" >/dev/null 2>&1 || true
  fi

  rm -rf "${PACK_DIR}" >/dev/null 2>&1 || true
}

trap cleanup EXIT

if ! command -v "${OPENCLAW_BIN}" >/dev/null 2>&1; then
  echo "ERROR: openclaw binary not found. Set OPENCLAW_BIN or add openclaw to PATH." >&2
  exit 1
fi

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "ERROR: config file not found: ${CONFIG_PATH}" >&2
  exit 1
fi

BACKUP_PATH="${CONFIG_PATH}.mem0-env-verify-backup-$(date +%Y%m%d-%H%M%S)"
cp "${CONFIG_PATH}" "${BACKUP_PATH}"

pushd "${REPO_ROOT}" >/dev/null
TARBALL_NAME="$(npm pack --pack-destination "${PACK_DIR}" | tail -n 1)"
TARBALL_PATH="${PACK_DIR}/${TARBALL_NAME}"
popd >/dev/null

python3 - "${CONFIG_PATH}" "${PLUGIN_ID}" "${MODE_VALUE}" "${API_KEY_VALUE}" "${USER_ID_VALUE}" "${HOST_VALUE}" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
plugin_id = sys.argv[2]
mode_value = sys.argv[3]
api_key_value = sys.argv[4]
user_id_value = sys.argv[5]
host_value = sys.argv[6]

config = json.loads(config_path.read_text("utf-8") or "{}")

env_cfg = config.setdefault("env", {})
env_cfg["MEM0_MODE"] = mode_value
env_cfg["MEM0_API_KEY"] = api_key_value
env_cfg["MEM0_USER_ID"] = user_id_value
env_cfg["MEM0_HOST"] = host_value

plugins = config.setdefault("plugins", {})
allow = plugins.setdefault("allow", [])
if plugin_id not in allow:
    allow.append(plugin_id)

slots = plugins.setdefault("slots", {})
slots["memory"] = plugin_id

entries = plugins.setdefault("entries", {})
entry = entries.setdefault(plugin_id, {})
entry["enabled"] = True
entry.pop("config", None)

config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
print(config_path)
PY

echo "Installing plugin tarball: ${TARBALL_PATH}"
"${OPENCLAW_BIN}" plugins install --force "${TARBALL_PATH}"

echo
echo "--- config validate ---"
"${OPENCLAW_BIN}" config validate

echo
echo "--- plugins info ---"
PLUGIN_INFO="$("${OPENCLAW_BIN}" plugins info "${PLUGIN_ID}")"
printf '%s\n' "${PLUGIN_INFO}"

if ! grep -q "Status: loaded" <<<"${PLUGIN_INFO}"; then
  echo "ERROR: plugin did not reach loaded status." >&2
  exit 1
fi

echo
echo "--- gateway boot logs ---"
LOG_FILE="$(mktemp)"
"${OPENCLAW_BIN}" gateway --port "${GATEWAY_PORT}" --allow-unconfigured >"${LOG_FILE}" 2>&1 &
GATEWAY_PID="$!"
sleep 6
cat "${LOG_FILE}"

if ! grep -q "openclaw-mem0-plugin: registered (mode: ${MODE_VALUE}, user: ${USER_ID_VALUE}" "${LOG_FILE}" \
  && ! grep -q "openclaw-mem0-plugin: registered (mode: ${MODE_VALUE}, user: ${USER_ID_VALUE}" <<<"${PLUGIN_INFO}"; then
  echo "ERROR: expected registration log with env-backed values was not found." >&2
  rm -f "${LOG_FILE}"
  exit 1
fi

rm -f "${LOG_FILE}"
echo
echo "OK: env-backed plugin configuration verified via load status and registration logs."
