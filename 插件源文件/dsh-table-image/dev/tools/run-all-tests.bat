@echo off
rem ---------------------------------------------------------------------------
rem Run ALL harnesses for dsh-table-image, in dependency order.
rem
rem Content is pure ASCII on purpose: cmd.exe reads a .bat byte-by-byte in the
rem active code page, so non-ASCII here would garble and truncate commands.
rem (A Chinese FILE NAME is fine; Chinese CONTENT is not.)
rem
rem The workspace path is read from dev\tools\workspace.txt (one line, UTF-8,
rem BOM tolerated) so this file never needs a non-ASCII literal.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0..\.."

set WSFILE=dev\tools\workspace.txt
if not exist "%WSFILE%" goto nows
set /p WS=<"%WSFILE%"
if "%WS%"=="" goto nows
goto run

:nows
echo.
echo  [SKIP] %WSFILE% not found or empty.
echo         The "host smoke" step needs it. Create it with one line:
echo             C:\path\to\your\workspace
echo         (UTF-8, a BOM at the start is tolerated)
echo.
set WS=
set RC3=0

:run
echo ============================================================
echo  [1/5] client.js harness  (mock React, no browser needed)
echo ============================================================
node "dev\tools\test-harness.mjs"
set RC1=%ERRORLEVEL%

echo.
echo ============================================================
echo  [2/5] index.js harness   (stubbed dsh-tools, real spawn + HTTP)
echo ============================================================
node --import ./dev/tools/stub-loader.mjs "dev\tools\test-host.mjs"
set RC2=%ERRORLEVEL%

if "%WS%"=="" goto skip3
echo.
echo ============================================================
echo  [3/5] host smoke          (real workspace: %WS%)
echo ============================================================
node "dev\tools\host-harness.mjs" "%WS%"
set RC3=%ERRORLEVEL%
goto after3

:skip3
echo.
echo  [3/5] host smoke          SKIPPED (no workspace.txt)

:after3
echo.
echo ============================================================
echo  [4/5] real integration    (real dsh-tools + real python)
echo ============================================================
node "dev\tools\real-check.mjs"
set RC4=%ERRORLEVEL%

echo.
echo ============================================================
echo  [5/5] bracket balance
echo ============================================================
node "dev\tools\check-brackets.mjs"
set RC5=%ERRORLEVEL%

echo.
echo ============================================================
echo   [1] client harness     rc=%RC1%
echo   [2] host harness       rc=%RC2%
echo   [3] host smoke         rc=%RC3%
echo   [4] real integration   rc=%RC4%
echo   [5] bracket balance    rc=%RC5%
echo ============================================================
set ALLOK=1
for %%R in (%RC1% %RC2% %RC3% %RC4% %RC5%) do if not "%%R"=="0" set ALLOK=0
if "%ALLOK%"=="0" goto failed
echo  ALL GREEN
pause
exit /b 0

:failed
echo  SOMETHING FAILED - see the output above
pause
exit /b 1
