#!/usr/bin/env bash
# Bundle the cloudpanel/ package into one runnable file: dist/cloudpanel.
# Uses Python's built-in zipapp, so the result needs only python3.
set -euo pipefail
cd "$(dirname "$0")"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

cp -r cloudpanel "$tmp/"
find "$tmp" -name __pycache__ -type d -prune -exec rm -rf {} +

mkdir -p dist
python3 -m zipapp "$tmp" \
    --output dist/cloudpanel \
    --python "/usr/bin/env python3" \
    --main "cloudpanel.cli:run" \
    --compress
chmod +x dist/cloudpanel

echo "Built dist/cloudpanel ($(du -h dist/cloudpanel | cut -f1))"
