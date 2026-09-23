@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0.."
echo ============================================
echo   表格识别系统 - 环境安装/检查
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [问题] 这台电脑没有找到 Python。
  echo.
  echo   请先安装 Python 3.10 或更高版本：
  echo   https://www.python.org/downloads/windows/
  echo   安装时务必勾选 "Add python.exe to PATH"，装完再运行本脚本。
  echo.
  pause
  exit /b 1
)

python -c "import sys;print('  Python 版本:',sys.version.split()[0])"

echo [检查] 补齐工作区文件夹（inbox / runs 等）...
python "tools\初始化工作区.py"
echo.

python -c "import openpyxl" >nul 2>nul
if not errorlevel 1 (
  echo [OK] openpyxl 已安装，环境正常。
  echo.
  echo 可以直接对 Codex 说：处理今天的图
  echo.
  pause
  exit /b 0
)

echo [安装] 正在安装 openpyxl ...
if exist "vendor\wheels" (
  python -m pip install --no-index --find-links "vendor\wheels" openpyxl
) else (
  python -m pip install openpyxl -i https://pypi.tuna.tsinghua.edu.cn/simple
)

python -c "import openpyxl;print('  openpyxl 安装成功:',openpyxl.__version__)" 2>nul
if errorlevel 1 (
  echo [失败] 安装没有成功，请把上面的报错发给管理员。
) else (
  echo.
  echo [完成] 环境已就绪。
)
echo.
pause
