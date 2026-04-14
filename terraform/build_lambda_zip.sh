#!/usr/bin/env bash
# data.external: 打包 Lambda zip，输出 {"path": "...", "hash": "..."}
set -euo pipefail

SERVICE_NAME="$1"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${ROOT_DIR}/terraform/.build"
mkdir -p "$OUT_DIR"

ZIP_FILE="${OUT_DIR}/${SERVICE_NAME}.zip"
rm -f "$ZIP_FILE"

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

cp -r "${ROOT_DIR}/services/${SERVICE_NAME}/src/"* "$TMPDIR/"
cp -r "${ROOT_DIR}/packages/common/src/"* "$TMPDIR/"

cd "$TMPDIR"
zip -qr "$ZIP_FILE" .

HASH=$(openssl dgst -sha256 -binary "$ZIP_FILE" | openssl base64)

echo "{\"path\": \"${ZIP_FILE}\", \"hash\": \"${HASH}\"}"
