#!/usr/bin/env bash
# data.external: 打包 Lambda zip，输出 {"path": "...", "hash": "..."}
# 内容：业务代码（service + common）+ 跨平台 wheel 依赖（linux/x86_64/py3.12）
set -euo pipefail

SERVICE_NAME="$1"
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="${ROOT_DIR}/terraform/.build"
mkdir -p "$OUT_DIR"

ZIP_FILE="${OUT_DIR}/${SERVICE_NAME}.zip"
rm -f "$ZIP_FILE"

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# 1. 从 pyproject.toml 拿 package 名（dir 名和 package 名有时不同，比如 get_photo_ids → get-photo-ids）
PACKAGE_NAME=$(awk -F'"' '/^name = /{print $2; exit}' \
  "${ROOT_DIR}/services/${SERVICE_NAME}/pyproject.toml")

# 2. 导出该 package 的 transitive 依赖（排除自身和 dev group）
cd "${ROOT_DIR}"
uv export \
  --package "${PACKAGE_NAME}" \
  --no-dev \
  --no-emit-project \
  --no-emit-workspace \
  --no-hashes \
  --format requirements-txt \
  2>/dev/null > "$TMPDIR/requirements.txt"

# 3. 跨平台安装（Lambda 环境：linux x86_64 / Python 3.12）
uv pip install \
  --target "$TMPDIR" \
  --python-version 3.12 \
  --python-platform x86_64-manylinux2014 \
  --only-binary :all: \
  -r "$TMPDIR/requirements.txt" \
  >/dev/null

rm "$TMPDIR/requirements.txt"

# 4. 业务代码（service + common 共享库）
cp -r "${ROOT_DIR}/services/${SERVICE_NAME}/src/"* "$TMPDIR/"
cp -r "${ROOT_DIR}/packages/common/src/"* "$TMPDIR/"

# 5. 清理减小 zip —— 多条最佳实践：
#    - __pycache__：跨架构可能不兼容（AWS 文档明确建议排除）
#    - *.dist-info：包元数据，运行时不用
#    - tests/：包测试目录，生产不用
#    - boto3/botocore：Lambda runtime 已自带（省 ~20MB；可能版本略异，若遇兼容问题再加回）
#    - *.so 的 debug 符号太大时可 strip（暂不做，避免二进制风险）
find "$TMPDIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
# NOTE: 保留 *.dist-info！opentelemetry / langfuse 靠 entry_points 插件发现，
# 删 dist-info 会让 runtime 抛 StopIteration
find "$TMPDIR" -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
rm -rf "$TMPDIR/boto3" "$TMPDIR/botocore" 2>/dev/null || true

cd "$TMPDIR"
zip -qr "$ZIP_FILE" .

HASH=$(openssl dgst -sha256 -binary "$ZIP_FILE" | openssl base64)

echo "{\"path\": \"${ZIP_FILE}\", \"hash\": \"${HASH}\"}"
