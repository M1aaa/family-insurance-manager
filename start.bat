@echo off
setlocal enabledelayedexpansion
cd /d %~dp0

set PORT=8000
set PYEXE=python

echo ============================================
echo   家庭保险整理工具 - 本地一键启动 (Windows)
echo ============================================

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 创建虚拟环境 .venv ...
    %PYEXE% -m venv .venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败，请确认已安装 Python 3.12+。
        exit /b 1
    )
) else (
    echo [1/3] 检测到已存在的虚拟环境 .venv，跳过创建。
)

echo [2/3] 安装依赖 ...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 依赖安装失败。
    exit /b 1
)

echo [3/3] 启动服务： http://127.0.0.1:%PORT%
echo        （按 Ctrl+C 停止）
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%

endlocal
