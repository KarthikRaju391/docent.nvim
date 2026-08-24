#!/usr/bin/env bash
# Single entrypoint for the docent.nvim test suite. Run from the repo root:
#   tests/run.sh
# Exits nonzero on any failure. Set KEEP_TMP=1 to keep the temp dir around.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRATCH_BASE="/private/tmp/claude-501/-Users-karthik-code-autosched/20e3914b-2795-43b4-9e79-7da312323399/scratchpad"

if [ -d "$SCRATCH_BASE" ]; then
  TMP="$(mktemp -d "$SCRATCH_BASE/docent-tests.XXXXXX")"
else
  TMP="$(mktemp -d)"
fi

cleanup() {
  if [ "${KEEP_TMP:-0}" = "1" ]; then
    echo "KEEP_TMP=1: leaving temp dir at $TMP"
  else
    rm -rf "$TMP"
  fi
}
trap cleanup EXIT

echo "docent.nvim tests — temp dir: $TMP"
python3 "$ROOT/tests/driver.py" "$TMP"
exit $?
