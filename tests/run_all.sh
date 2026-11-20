#!/bin/bash
# Every check, without root. Needs a display for the window tests (WSLg provides one).
# Firmware binaries in tests/fixtures are not in git: see tests/fixtures/README.md.
cd "$(dirname "$0")"
PY=/opt/q2-venv/bin/python3; [ -x $PY ] || PY=python3
export DISPLAY=${DISPLAY:-:0}
total_pass=0; total_fail=0
for t in test_engine test_prebuilt_shim test_start_script test_stop test_watchdog test_window_states test_window_remembers test_video_timing test_captures test_agent_door; do
  out=$(cd /tmp && timeout 300 $PY -I -B "$OLDPWD/$t.py" 2>&1)
  p=$(echo "$out" | grep -c '^PASS'); f=$(echo "$out" | grep -c '^FAIL')
  total_pass=$((total_pass + p)); total_fail=$((total_fail + f))
  printf '%-24s %3d pass %2d fail\n' $t $p $f
  echo "$out" | grep -E '^FAIL|Traceback' -A2
done
echo "TOTAL: $total_pass pass, $total_fail fail"
