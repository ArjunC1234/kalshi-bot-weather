#!/usr/bin/env bash
# Install this additive release only; never copy the entire dirty deployable tree.
set -euo pipefail
root=/opt/kalshi-weather-next-gen
release="$root/releases/timing-20260913-v1"
service=kalshi-weather-timing-v1.service
timer=kalshi-weather-timing-v1.timer
test "$(id -u)" -eq 0
test ! -e "$root/collector/timing_collector.py"
test ! -e "/etc/systemd/system/$service"
test ! -e "/etc/systemd/system/$timer"
test -d "$root/collector_spool_v3"
cd "$root"
sha256sum "$root"/collector/*.py "$root/live_strategy.py" \
  /etc/systemd/system/kalshi-weather-collector-v3.service \
  /etc/systemd/system/kalshi-weather-collector-v3.timer > "$release/existing-files.sha256"
systemd-analyze verify "$release/systemd/$service" "$release/systemd/$timer"
"$root/.venv/bin/python" "$release/verify_timing.py" \
  --data-dir "$release/smoke_spool" --local-only --min-runs 1
trap 'systemctl disable --now "$timer" || true; systemctl stop "$service" || true' ERR
install -m 0644 "$release/collector/timing_collector.py" "$root/collector/timing_collector.py"
install -m 0644 "$release/collector/TIMING_CAPTURE.md" "$root/collector/TIMING_CAPTURE.md"
install -m 0644 "$release/systemd/$service" "/etc/systemd/system/$service"
install -m 0644 "$release/systemd/$timer" "/etc/systemd/system/$timer"
systemctl daemon-reload
systemctl start "$service"
"$root/.venv/bin/python" "$release/verify_timing.py" --min-runs 1
sha256sum --check "$release/existing-files.sha256"
systemctl enable --now "$timer"
systemctl list-timers "$timer" --no-pager
trap - ERR
