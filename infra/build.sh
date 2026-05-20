#!/usr/bin/env bash
# Lambda zip build script.
#
# Mac でも Linux 用 wheel だけ pull する (--python-platform x86_64-manylinux2014)。
# 出力: infra/build/  (Terraform の archive_file が zip 化する)
#
# Usage:
#   cd infra && ./build.sh
#
# 依存: uv (`brew install uv`)。venv が uv 製で pip を持たないため uv pip を使う

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
REQ_FILE="${SCRIPT_DIR}/requirements-lambda.txt"

if ! command -v uv >/dev/null 2>&1; then
  echo "[build] error: uv not found. install with 'brew install uv'" >&2
  exit 1
fi

echo "[build] cleaning ${BUILD_DIR}"
rm -rf "${BUILD_DIR}"
mkdir -p "${BUILD_DIR}"

echo "[build] installing Lambda dependencies (x86_64-manylinux2014, py3.12)"
uv pip install \
  --quiet \
  --python-platform x86_64-manylinux2014 \
  --python-version 3.12 \
  --only-binary=:all: \
  --target "${BUILD_DIR}" \
  -r "${REQ_FILE}"

echo "[build] copying src/news_prism"
cp -r "${REPO_ROOT}/src/news_prism" "${BUILD_DIR}/"

echo "[build] stripping __pycache__ and .pyc"
find "${BUILD_DIR}" -type d -name __pycache__ -prune -exec rm -rf {} +
find "${BUILD_DIR}" -type f -name '*.pyc' -delete
find "${BUILD_DIR}" -type f -name '*.pyo' -delete

# dist-info は実行に不要だがメタ情報なので残す (~数 KB)。サイズが気になれば削除可

SIZE_HUMAN="$(du -sh "${BUILD_DIR}" | cut -f1)"
echo "[build] done: ${BUILD_DIR} (${SIZE_HUMAN})"
