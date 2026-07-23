#!/usr/bin/env bash
# agent_core staging shakedown — the pre-prod gate.
#
# Builds the staging image for a candidate version, boots it as an isolated
# container, and asserts the daemon comes up clean. Exits 0 = candidate is
# clear to promote to the live daemon; non-zero = do NOT promote.
#
# Usage:
#   ./shakedown.sh                          # pypi, version 0.8.2 (default)
#   INSTALL_MODE=pypi VERSION=0.8.2 ./shakedown.sh
#   INSTALL_MODE=source REF=main ./shakedown.sh
#
# Nothing here touches the host bus/ports/vault — the container is the wall.
set -uo pipefail

INSTALL_MODE="${INSTALL_MODE:-pypi}"
VERSION="${VERSION:-0.8.2}"
REF="${REF:-main}"
IMAGE="agent-core-staging:${INSTALL_MODE}-${VERSION}"
NAME="agent-core-canary"
HOST_PORT="${HOST_PORT:-18789}"   # deliberately NOT 8789 (the live daemon's port)
# pwd -W emits the Windows-style path (E:/…) that Windows Docker needs for the
# build context; plain pwd emits the MSYS /e/… form Docker can't resolve.
HERE="$(cd "$(dirname "$0")" && { pwd -W 2>/dev/null || pwd; })"

pass() { echo "  ✅ $1"; }
fail() { echo "  ❌ $1"; FAILED=1; }
FAILED=0

echo "=== agent_core staging shakedown =="
echo "mode=$INSTALL_MODE version=$VERSION ref=$REF image=$IMAGE port=$HOST_PORT"

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

echo "--- build ---"
if ! docker build \
      --build-arg "INSTALL_MODE=$INSTALL_MODE" \
      --build-arg "VERSION=$VERSION" \
      --build-arg "REF=$REF" \
      -t "$IMAGE" "$HERE"; then
  echo "RESULT: FAIL — image build failed (candidate does not even install)"
  exit 1
fi

echo "--- boot (fully isolated container; daemon binds loopback, inspected via exec) ---"
docker run -d --name "$NAME" "$IMAGE" >/dev/null

# Wait for the daemon to come up (bus status returns cleanly), up to ~40s.
booted=0
for i in $(seq 1 20); do
  if docker exec "$NAME" ac bus status --config /config/staging.yaml >/tmp/canary_status.txt 2>/tmp/canary_status.err; then
    booted=1; break
  fi
  sleep 2
done

echo "--- assertions ---"
installed_ver="$(docker exec "$NAME" cat /agent-core-version.txt 2>/dev/null | tr -d '\r')"
[ -n "$installed_ver" ] && pass "installed agent-core-bus $installed_ver" || fail "could not read installed version"

if [ "$booted" = "1" ]; then
  pass "daemon booted — 'bus status' responds"
else
  fail "daemon did NOT boot within timeout"
fi

# Endpoints registered?
status="$(cat /tmp/canary_status.txt 2>/dev/null)"
echo "$status" | grep -qiE "\bstub\b"      && pass "endpoint 'stub' registered"      || fail "endpoint 'stub' missing"
echo "$status" | grep -qiE "scheduler"     && pass "endpoint 'scheduler' registered" || fail "endpoint 'scheduler' missing"

# No tracebacks / fatal errors in the boot logs?
logs="$(docker logs "$NAME" 2>&1)"
if echo "$logs" | grep -qiE "Traceback|CRITICAL|Fatal|Error: "; then
  fail "boot logs contain errors:"
  echo "$logs" | grep -iE "Traceback|CRITICAL|Fatal|Error: " | head -5 | sed 's/^/      /'
else
  pass "boot logs clean (no tracebacks)"
fi

# HTTP surface (informational — only binds when the config has HTTP-mounted
# endpoints; the minimal Phase-1 config has none, so absence is expected).
code="$(docker exec "$NAME" curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8789/" 2>/dev/null)"
if [ -n "$code" ] && [ "$code" != "000" ]; then
  pass "http surface up (HTTP $code, loopback)"
else
  echo "  ℹ️  http surface not bound — expected (no HTTP-mounted endpoints in Phase-1 config)"
fi

echo "--- daemon status snapshot ---"
echo "$status" | sed 's/^/      /' | head -20

echo ""
if [ "$FAILED" = "0" ]; then
  echo "RESULT: PASS — candidate $installed_ver booted clean in isolation. Cleared to promote."
  exit 0
else
  echo "RESULT: FAIL — do NOT promote. See failures above."
  echo "  (logs: docker logs $NAME  |  the container is torn down on exit — comment out cleanup to inspect)"
  exit 1
fi
