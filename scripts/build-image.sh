#!/usr/bin/env bash
# ============================================================================
# 家庭保险整理工具 — 一键构建镜像并导出 tar 包（macOS / Linux 用）
# ----------------------------------------------------------------------------
# 用法：
#   ./scripts/build-image.sh                      # 默认官方源
#   BASE_IMAGE=docker.m.daocloud.io/library/python:3.13-slim \
#   PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
#   ./scripts/build-image.sh                       # 国内换源
#
# 产物：dist/family-insurance-manager-1.2.0.tar（用于导入极空间 NAS）
# ============================================================================
set -euo pipefail

IMAGE_NAME="family-insurance-manager"
IMAGE_TAG="1.2.0"
IMAGE_REF="${IMAGE_NAME}:${IMAGE_TAG}"
TAR_NAME="${IMAGE_NAME}-${IMAGE_TAG}.tar"
PLATFORM="linux/amd64"

# 国内可换源（默认官方源，保证可移植性）
BASE_IMAGE="${BASE_IMAGE:-python:3.13-slim}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}"
TZ="${TZ:-Asia/Shanghai}"

# 切到脚本所在项目的根目录（脚本位于 <root>/scripts/ 下）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

echo "============================================================"
echo " 家庭保险整理工具 — 构建并导出镜像"
echo "============================================================"
echo " 项目目录 : ${PROJECT_ROOT}"
echo " 镜像标签 : ${IMAGE_REF}"
echo " 目标平台 : ${PLATFORM}  (极空间等 x86 NAS 适用)"
echo " 基础镜像 : ${BASE_IMAGE}"
echo " pip 源   : ${PIP_INDEX_URL}"
echo "------------------------------------------------------------"

# ---- 1. 检查 docker 是否可用 ----
if ! command -v docker >/dev/null 2>&1; then
    echo "[错误] 未检测到 docker 命令。"
    echo "       请先安装 Docker Desktop（macOS/Windows）或 docker engine（Linux），"
    echo "       安装后重开一个终端再试。"
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "[错误] docker 命令存在，但守护进程不可用（Docker Desktop 没启动？）。"
    echo "       请启动 Docker Desktop，等它状态变为运行中再试。"
    exit 1
fi
echo "[1/3] Docker 环境检查通过。"

# ---- 2. 构建镜像（显式锁定 linux/amd64）----
echo "[2/3] 正在构建镜像（首次构建需数分钟，请耐心等待）..."
docker build \
    --platform "${PLATFORM}" \
    --build-arg "BASE_IMAGE=${BASE_IMAGE}" \
    --build-arg "PIP_INDEX_URL=${PIP_INDEX_URL}" \
    --build-arg "TZ=${TZ}" \
    -t "${IMAGE_REF}" \
    -f Dockerfile \
    .

# ---- 3. 导出 tar 包 ----
echo "[3/3] 正在导出镜像为 tar 包..."
mkdir -p dist
docker save -o "dist/${TAR_NAME}" "${IMAGE_REF}"

# ---- 打印结果与下一步指引 ----
TAR_ABS="$(cd dist && pwd)/${TAR_NAME}"
TAR_SIZE="$(du -h "dist/${TAR_NAME}" | cut -f1)"

echo "============================================================"
echo " 构建完成！"
echo "------------------------------------------------------------"
echo " 镜像   : ${IMAGE_REF}"
echo " tar 包 : ${TAR_ABS}"
echo " 大小   : ${TAR_SIZE}"
echo "============================================================"
echo " 下一步："
echo "   1) 把这个 tar 包上传到极空间 NAS（推荐用 SMB / 文件管理器）。"
echo "   2) 极空间 Docker → 镜像 → 导入，选择该 tar 包。"
echo "   3) 极空间 Docker → Compose → 新增项目，粘贴 docker-compose.yml 内容。"
echo "   详细步骤见 docs/DEPLOY-ZSPACE.md"
echo "============================================================"
