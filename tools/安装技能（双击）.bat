@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0.."
echo ============================================
echo   安装「图像数据识别转化」技能到本机所有 AI 工具
echo ============================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo [提示] 没找到 Python，无法自动安装。
  echo.
  echo 手动安装办法（任选一个工具）：
  echo   把工作区里的  技能源文件\image-data-recognition
  echo   整个文件夹复制到下面这个目录里：
  echo     Codex:     %%USERPROFILE%%\.codex\skills\
  echo     DSH:       %%USERPROFILE%%\.dsh\skills\
  echo     Cursor:    %%USERPROFILE%%\.cursor\skills\
  echo     Claude:    %%USERPROFILE%%\.claude\skills\
  echo     OpenCode:  %%USERPROFILE%%\.opencode\skills\
  echo     ZCode:     %%USERPROFILE%%\.zcode\skills\
  echo.
  pause
  exit /b 1
)
python "tools\初始化工作区.py" --quiet

python "tools\安装技能.py" %*
echo.
pause
