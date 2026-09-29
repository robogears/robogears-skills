#!/usr/bin/env bash
# Run every offline test suite. Each suite uses throwaway folders only.
# Usage:  bash tests/run_all.sh
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
failed=0
for t in "$HERE"/test_*.sh; do
  if ! bash "$t"; then failed=$((failed + 1)); fi
  echo
done
if [ "$failed" -eq 0 ]; then echo "ALL SUITES PASSED"; else echo "$failed SUITE(S) FAILED"; fi
[ "$failed" -eq 0 ]
