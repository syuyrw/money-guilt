#!/bin/bash
# Dependency audit + static analysis. Run from anywhere: ./security_check.sh
#
# Fails (exit 1) on any known-vulnerable dependency, or any HIGH-severity
# finding in the code. Lower-severity findings are printed for review but do
# not fail the run: bandit cannot tell a safe "?"-placeholder query built with
# an f-string from an injectable one, and failing on triaged false positives
# teaches people to ignore the script.
cd "$(dirname "$0")" || exit 1
BIN=.venv/bin
status=0

echo "== dependencies (pip-audit) =="
$BIN/pip-audit || status=1

echo
echo "== private data: nothing from your real transactions in any file or commit =="
$BIN/python check_private_data.py --tracked && $BIN/python check_private_data.py --history \
  && echo "clean" || status=1

EXCLUDE='./.venv,./MoneyGuilt.app,./__pycache__,./test_*.py'

echo
echo "== code, all severities, for review (bandit) =="
$BIN/bandit -r . -c bandit.yaml -x "$EXCLUDE" -q -f txt 2>/dev/null \
  | grep -E "^>> Issue|Severity|Location" | paste - - - | sed 's/  */ /g'

echo
echo "== code, HIGH severity only (fails the run) =="
if $BIN/bandit -r . -c bandit.yaml -x "$EXCLUDE" -q -lll >/dev/null 2>&1; then
  echo "none"
else
  $BIN/bandit -r . -c bandit.yaml -x "$EXCLUDE" -q -lll 2>/dev/null
  status=1
fi

echo
[ "$status" -eq 0 ] && echo "PASS" || echo "FAIL"
exit $status
