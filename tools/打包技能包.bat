@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0.."
echo ============================================
echo   表格识别系统 - 打包技能包
echo ============================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo [问题] 没有找到 Python，无法打包。
  pause
  exit /b 1
)
python "tools\导出技能包.py"
echo.
echo 打包好的文件在 发布 文件夹里，可用 U盘/微信 拷到别的电脑。
pause
