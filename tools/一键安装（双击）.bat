@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0.."
echo ============================================
echo   表格识别系统 - 一键安装
echo ============================================
echo.
echo 安装位置（也就是工作区）：
echo   %CD%
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [问题] 这台电脑没有找到 Python，无法继续。
  echo.
  echo   请先安装 Python 3.10 或更高版本：
  echo   https://www.python.org/downloads/windows/
  echo   安装时务必勾选 "Add python.exe to PATH"，装完再双击本脚本。
  echo.
  pause
  exit /b 1
)

echo [第 1 步 / 共 3 步] 检查依赖库 openpyxl ...
python -c "import openpyxl" >nul 2>nul
if errorlevel 1 (
  echo   没装，正在安装（离线，不需要联网）...
  if exist "vendor\wheels" (
    python -m pip install --no-index --find-links "vendor\wheels" openpyxl
  ) else (
    python -m pip install openpyxl -i https://pypi.tuna.tsinghua.edu.cn/simple
  )
  python -c "import openpyxl" >nul 2>nul
  if errorlevel 1 (
    echo   [失败] openpyxl 没装上，请把上面的报错发给管理员。
    pause
    exit /b 1
  )
) else (
  echo   已就绪。
)
echo.

echo [第 2 步 / 共 3 步] 补齐工作区文件夹（inbox / runs 等）...
python "tools\初始化工作区.py"
echo.

echo [第 3 步 / 共 3 步] 把技能装进本机所有 AI 工具 ...
python "tools\安装技能.py"
echo.

echo ============================================
echo   安装完成。接下来怎么用：
echo.
echo   1. 用你常用的 AI 工具（Codex / DSH / Cursor 等）
echo      打开这个文件夹：
echo      %CD%
echo   2. 把单据照片丢进 inbox 文件夹
echo   3. 对 AI 说一句：处理今天的图
echo ============================================
echo.
pause