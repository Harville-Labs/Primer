#!/usr/bin/env bash
# End-to-end check against a running Compose stack: upload a document, wait
# for it to be indexed, ask a question, and confirm the answer is cited.
#
#   deploy/compose/scripts/smoke.sh
#
# The question needs Chat, which Compose does not publish, and a chat model,
# which CI does not have. Set PRIMER_CHAT_URL to a Chat you can reach to ask
# it; left unset, the check ends once the document is indexed, which is
# every stage of ingestion and the part most likely to strand.
#
# Prints request ids so a failure can be traced in the logs. Never prints
# document text, answer text, or any credential - a smoke test that dumped
# the response would put a user's content in CI output.
set -euo pipefail

control="${PRIMER_CONTROL_URL:-http://localhost:8000}"
chat="${PRIMER_CHAT_URL:-}"
timeout_seconds="${SMOKE_TIMEOUT:-300}"
library_id=""

cleanup() {
  if [ -n "$library_id" ]; then
    curl -sS -X DELETE "$control/api/v1/libraries/$library_id" >/dev/null || true
    echo "cleaned up library $library_id"
  fi
}
trap cleanup EXIT

say() { printf '==> %s\n' "$1"; }

wait_for() {
  local url="$1" name="$2" deadline=$((SECONDS + 120))
  say "waiting for $name"
  until curl -fsS "$url" >/dev/null 2>&1; do
    [ "$SECONDS" -lt "$deadline" ] || { echo "$name never became ready"; exit 1; }
    sleep 2
  done
}

wait_for "$control/health/ready" "control"

say "creating a library"
library_id=$(curl -fsS -X POST "$control/api/v1/libraries" \
  -H 'Content-Type: application/json' \
  -d '{"name":"Smoke test"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
echo "    library $library_id"

say "uploading a document"
fixture=$(mktemp -t primer-smoke.XXXXXX.txt)
trap 'rm -f "$fixture"; cleanup' EXIT
cat > "$fixture" <<'TXT'
Retrieval Augmented Generation

Grounding answers in cited sources measurably reduces unsupported claims.
Recall at rank ten was the decisive metric for this corpus.
TXT
document_id=$(curl -fsS -X POST "$control/api/v1/libraries/$library_id/documents" \
  -F "file=@$fixture;filename=smoke.txt" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
echo "    document $document_id"

say "waiting for it to be indexed"
deadline=$((SECONDS + timeout_seconds))
while true; do
  status=$(curl -fsS "$control/api/v1/libraries/$library_id/documents/$document_id" \
    | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"])')
  case "$status" in
    ready) echo "    ready"; break ;;
    failed|unsupported)
      echo "document ended in '$status'; check the worker logs for document $document_id"
      exit 1 ;;
  esac
  [ "$SECONDS" -lt "$deadline" ] || { echo "still '$status' after ${timeout_seconds}s"; exit 1; }
  sleep 3
done

if [ -z "$chat" ]; then
  say "smoke test passed (indexed; PRIMER_CHAT_URL unset, so nothing was asked)"
  exit 0
fi

say "asking a question"
citations=$(curl -fsS -N -X POST "$chat/api/v1/conversations" \
  -H 'Content-Type: application/json' \
  -d "{\"library_id\":\"$library_id\",\"message\":\"What was the decisive metric?\"}" \
  | grep -c '^event: citation' || true)

if [ "$citations" -lt 1 ]; then
  echo "the answer carried no citations"
  exit 1
fi
echo "    answer cited $citations passage(s)"

say "smoke test passed"
