@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
rem ==========================================================================
rem  家庭保险整理工具 — 一键构建镜像并导出 tar 包（Windows 用）
rem --------------------------------------------------------------------------
rem  用法（在本项目根目录双击或命令行运行）：
rem    scripts\build-image.bat                        默认官方源
rem    先设置环境变量再运行即可国内换源：
rem      set BASE_IMAGE=docker.m.daocloud.io/library/python:3.13-slim
rem      set PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
rem      scripts\build-image.bat
rem
rem  产物：dist\family-insurance-manager-1.2.0.tar（用于导入极空间 NAS）
rem ==========================================================================

set "IMAGE_NAME=family-insurance-manager"
set "IMAGE_TAG=1.2.0"
set "IMAGE_REF=%IMAGE_NAME%:%IMAGE_TAG%"
set "TAR_NAME=%IMAGE_NAME%-%IMAGE_TAG%.tar"
set "PLATFORM=linux/amd64"

rem 国内可换源（默认官方源，保证可移植性）
if not defined BASE_IMAGE set "BASE_IMAGE=python:3.13-slim"
if not defined PIP_INDEX_URL set "PIP_INDEX_URL=https://pypi.org/simple"
if not defined TZ set "TZ=Asia/Shanghai"

rem 切到项目根目录（脚本位于 <root>\scripts\ 下）
cd /d "%~dp0.."

echo ============================================================
echo  家庭保险整理工具 — 构建并导出镜像
echo ============================================================
echo  项目目录 : %CD%
echo  镜像标签 : %IMAGE_REF%
echo  目标平台 : %PLATFORM%  (极空间等 x86 NAS 适用)
echo  基础镜像 : %BASE_IMAGE%
echo  pip 源   : %PIP_INDEX_URL%
echo ------------------------------------------------------------

rem ---- 1. 检查 docker 是否可用 ----
where docker >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 docker 命令。
    echo        请先安装 Docker Desktop，安装后重开一个终端再试。
    exit /b 1
)
docker info >nul 2>nul
if errorlevel 1 (
    echo [错误] docker 命令存在，但守护进程不可用（Docker Desktop 没启动？）。
    echo        请启动 Docker Desktop，等它状态变为运行中再试。
    exit /b 1
)
echo [1/3] Docker 环境检查通过。

rem ---- 2. 构建镜像（显式锁定 linux/amd64）----
echo [2/3] 正在构建镜像（首次构建需数分钟，请耐心等待）...
docker build --platform %PLATFORM% --build-arg BASE_IMAGE=%BASE_IMAGE% --build-arg PIP_INDEX_URL=%PIP_INDEX_URL% --build-arg TZ=%TZ% -t %IMAGE_REF% -f Dockerfile .
if errorlevel 1 (
    echo [错误] 镜像构建失败，请查看上方 Docker 输出的报错信息。
    exit /b 1
)

rem ---- 3. 导出 tar 包 ----
echo [3/3] 正在导出镜像为 tar 包...
if not exist dist mkdir dist
docker save -o "dist\%TAR_NAME%" %IMAGE_REF%
if errorlevel 1 (
    echo [错误] 导出 tar 包失败。
    exit /b 1
)

for %%F in ("dist\%TAR_NAME%") do set "TAR_ABS=%%~fF"
for %%F in ("dist\%TAR_NAME%") do set "TAR_SIZE=%%~zF"

echo ============================================================
echo  构建完成！
echo ------------------------------------------------------------
echo  镜像   : %IMAGE_REF%
echo  tar 包 : %TAR_ABS%
echo  大小   : %TAR_SIZE% 字节
echo ============================================================
echo  下一步：
echo    1) 把这个 tar 包上传到极空间 NAS（推荐用 SMB / 文件管理器）。
echo    2) 极空间 Docker - 镜像 - 导入，选择该 tar 包。
echo    3) 极空间 Docker - Compose - 新增项目，粘贴 docker-compose.yml 内容。
echo    详细步骤见 docs\DEPLOY-ZSPACE.md
echo ============================================================

endlocal
