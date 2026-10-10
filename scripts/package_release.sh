#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
DIST_DIR="$REPO_ROOT/dist"
STAMP=$(date +"%Y%m%d-%H%M%S")
ARCHIVE_NAME="gold-monitor-${STAMP}.tar.gz"
ARCHIVE_PATH="$DIST_DIR/$ARCHIVE_NAME"

mkdir -p "$DIST_DIR"

tar \
  --exclude=".git" \
  --exclude=".venv" \
  --exclude=".env" \
  --exclude=".env.*" \
  --exclude="__pycache__" \
  --exclude=".pycache" \
  --exclude=".DS_Store" \
  --exclude=".playwright-mcp" \
  --exclude=".tmp_jd" \
  --exclude="screenlog.0" \
  --exclude="dist" \
  --exclude="data/*.db" \
  --exclude="logs/*" \
  -czf "$ARCHIVE_PATH" \
  -C "$REPO_ROOT" \
  .

echo "Package created: $ARCHIVE_PATH"
