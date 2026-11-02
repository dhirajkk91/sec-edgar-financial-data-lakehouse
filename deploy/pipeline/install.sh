#!/bin/sh
# Install the weeknight job on the Oracle VM. Run from a checkout of the repo:
#   sudo sh deploy/pipeline/install.sh
# Safe to run again after `git pull`: it rebuilds the image and reloads the units,
# and never touches /var/lib/sec-edgar (the data) or an existing env file.
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo" >&2
    exit 1
fi
command -v docker >/dev/null || { echo "Docker is not installed" >&2; exit 1; }
cd "$(dirname "$0")/../.."

# Data lives outside the container; the image runs as uid 10001.
install -d -m 0750 -o 10001 /var/lib/sec-edgar
install -d -m 0700 /etc/sec-edgar
if [ ! -f /etc/sec-edgar/env ]; then
    install -m 0600 deploy/pipeline/env.example /etc/sec-edgar/env
    echo "Created /etc/sec-edgar/env: fill in SEC_USER_AGENT and the AWS values, then rerun."
    exit 0
fi
if grep -q '^AWS_ACCESS_KEY_ID=$' /etc/sec-edgar/env; then
    echo "/etc/sec-edgar/env still has no AWS key; fill it in first." >&2
    exit 1
fi

DOCKER_BUILDKIT=1 docker build --file deploy/pipeline/Dockerfile --tag sec-edgar-pipeline .
install -m 0644 deploy/pipeline/sec-edgar-nightly.service /etc/systemd/system/
install -m 0644 deploy/pipeline/sec-edgar-nightly.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now sec-edgar-nightly.timer
systemctl list-timers sec-edgar-nightly.timer --no-pager

cat <<'EOF'

Installed. The timer runs Monday to Friday at 11:30 p.m. Central.
Start the first run (the five-year backfill) now with:
    sudo systemctl start --no-block sec-edgar-nightly.service
Follow it with:
    journalctl -u sec-edgar-nightly.service -f
EOF
