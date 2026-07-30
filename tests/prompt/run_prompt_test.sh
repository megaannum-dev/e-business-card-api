#!/usr/bin/env bash
# Run image-enhancement prompt tests against a local/dev API.
#
# Usage:
#   mkdir -p tests/prompt/prompt_2_gemini_3.1_flash
#   ./tests/prompt/run_prompt_test.sh tests/prompt/prompt_2_gemini_3.1_flash
#   ./tests/prompt/run_prompt_test.sh tests/prompt/prompt_2_gemini_3.1_flash 3
#
# Env overrides:
#   API_BASE          default http://localhost:8000
#   FIREBASE_PLIST    path to GoogleService-Info.plist
#   FIREBASE_API_KEY  skip plist and use this key directly
#   EMAIL / PASSWORD  Firebase test account

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

OUTPUT_DIR="${1:-}"
RUNS_PER_IMAGE="${2:-3}"

if [[ -z "${OUTPUT_DIR}" ]]; then
  echo "Usage: $0 <output_dir> [runs_per_image]" >&2
  echo "Example: $0 tests/prompt/prompt_2_gemini_3.1_flash 3" >&2
  exit 1
fi

if ! [[ "${RUNS_PER_IMAGE}" =~ ^[1-9][0-9]*$ ]]; then
  echo "runs_per_image must be a positive integer (got: ${RUNS_PER_IMAGE})" >&2
  exit 1
fi

API_BASE="${API_BASE:-http://localhost:8000}"
EMAIL="${EMAIL:-test@ebusinesscard.com}"
PASSWORD="${PASSWORD:-Aa123456}"
FIREBASE_PLIST="${FIREBASE_PLIST:-/Users/mandes-mega/e-business-card-app/ios/GoogleService-Info.plist}"

INPUTS=(
  "tests/prompt/inputs/Mega.png|Mega|image/png"
  "tests/prompt/inputs/Bloomberg.jpeg|Bloomberg|image/jpeg"
  "tests/prompt/inputs/Mine_Wine.png|Mine_Wine|image/png"
)

OCR_TEXT='Alex Lee Megaannum Technology Limited Director alex@megaannum.ai +852 1234 5678'

for cmd in curl jq; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    echo "Missing required command: ${cmd}" >&2
    exit 1
  fi
done

mkdir -p "${OUTPUT_DIR}"

fetch_token() {
  local api_key=""
  if [[ -n "${FIREBASE_API_KEY:-}" ]]; then
    api_key="${FIREBASE_API_KEY}"
  else
    if [[ ! -f "${FIREBASE_PLIST}" ]]; then
      echo "Firebase plist not found: ${FIREBASE_PLIST}" >&2
      echo "Set FIREBASE_PLIST or FIREBASE_API_KEY." >&2
      exit 1
    fi
    if ! command -v /usr/libexec/PlistBuddy >/dev/null 2>&1; then
      echo "PlistBuddy not found; set FIREBASE_API_KEY instead." >&2
      exit 1
    fi
    api_key="$(/usr/libexec/PlistBuddy -c "Print :API_KEY" "${FIREBASE_PLIST}")"
  fi

  local response token
  response="$(
    curl -sS "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${api_key}" \
      -H "Content-Type: application/json" \
      --data "$(jq -nc \
        --arg email "${EMAIL}" \
        --arg password "${PASSWORD}" \
        '{email:$email,password:$password,returnSecureToken:true}')"
  )"

  token="$(jq -r '.idToken // empty' <<<"${response}")"
  if [[ -z "${token}" ]]; then
    echo "Failed to fetch Firebase ID token:" >&2
    jq . <<<"${response}" >&2 || echo "${response}" >&2
    exit 1
  fi
  printf '%s' "${token}"
}

file_sha256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

echo "API: ${API_BASE}"
echo "Output: ${OUTPUT_DIR}"
echo "Runs per image: ${RUNS_PER_IMAGE}"
echo "Fetching Firebase token for ${EMAIL}..."
TOKEN="$(fetch_token)"
echo "Token OK."
echo

for entry in "${INPUTS[@]}"; do
  IFS='|' read -r input_path company_name content_type <<<"${entry}"

  if [[ ! -f "${input_path}" ]]; then
    echo "Missing input: ${input_path}" >&2
    exit 1
  fi

  input_hash="$(file_sha256 "${input_path}")"

  for run in $(seq 1 "${RUNS_PER_IMAGE}"); do
    echo "=== ${company_name} run ${run}/${RUNS_PER_IMAGE} ==="

    start_ns="$(date +%s%N)"
    response="$(
      curl -sS -X POST "${API_BASE}/api/v1/user-cards/process" \
        -H "Authorization: Bearer ${TOKEN}" \
        -F "raw_ocr_text=${OCR_TEXT}" \
        -F "scan_image=@${input_path};type=${content_type}"
    )"
    end_ns="$(date +%s%N)"
    elapsed_s="$(( (end_ns - start_ns) / 1000000000 ))"
    if [[ "${elapsed_s}" -lt 1 ]]; then
      elapsed_s=1
    fi

    card_id="$(jq -r '._id // empty' <<<"${response}")"
    image_url="$(jq -r '.scan_image_front_pending_url // empty' <<<"${response}")"
    enhancement_status="$(jq -r '.scan_image_enhancement_status // empty' <<<"${response}")"

    if [[ -z "${card_id}" || -z "${image_url}" ]]; then
      echo "AI preview failed for ${company_name} run ${run} (status=${enhancement_status}):" >&2
      jq . <<<"${response}" >&2 || echo "${response}" >&2
      exit 1
    fi

    # Download the review candidate, not the canonical original.
    if [[ "${image_url}" == http* ]]; then
      download_url="${image_url}"
    else
      download_url="${API_BASE}${image_url}"
    fi

    out_file="${OUTPUT_DIR}/${elapsed_s}s_${company_name}_${run}.png"
    curl -sS -H "Authorization: Bearer ${TOKEN}" \
      "${download_url}" \
      --output "${out_file}"

    out_hash="$(file_sha256 "${out_file}")"
    bytes="$(wc -c <"${out_file}" | tr -d ' ')"

    echo "card_id=${card_id}"
    echo "elapsed=${elapsed_s}s"
    echo "saved=${out_file} (${bytes} bytes)"

    if [[ "${out_hash}" == "${input_hash}" ]]; then
      echo "WARNING: output is byte-identical to input → api-fallback-original (enhancement likely failed)."
      echo "         Check: docker compose logs api | grep -i 'Image enhancement'"
    elif [[ "${elapsed_s}" -lt 8 ]]; then
      echo "NOTE: unusually fast (${elapsed_s}s) but hash differs from input — inspect visually."
    fi
    echo
  done
done

echo "Done. Outputs in ${OUTPUT_DIR}"
