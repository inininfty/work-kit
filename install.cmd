@echo off
setlocal
cd /d "%~dp0"
set "WK_PY="
py -3.11 -c "import sys; sys.exit(sys.version_info < (3,11))" >nul 2>nul
if not errorlevel 1 set "WK_PY=py -3.11"
if defined WK_PY goto run
py -3 -c "import sys; sys.exit(sys.version_info < (3,11))" >nul 2>nul
if not errorlevel 1 set "WK_PY=py -3"
if defined WK_PY goto run
python -c "import sys; sys.exit(sys.version_info < (3,11))" >nul 2>nul
if not errorlevel 1 set "WK_PY=python"
if defined WK_PY goto run
echo [STOP] Python 3.11+ was not found. Give this message to DSH.
pause
exit /b 2
:run
%WK_PY% "%~dp0bin\start.py"
set "WK_RC=%ERRORLEVEL%"
if not "%WK_RC%"=="3" goto done
echo.
set "WK_CONFIRM="
set /p "WK_CONFIRM=Are ALL listed candidate folders read-only? Type Y to confirm: "
if /i not "%WK_CONFIRM%"=="Y" goto done
%WK_PY% "%~dp0bin\start.py" --confirm-readonly
set "WK_RC=%ERRORLEVEL%"
:done
echo.
pause
exit /b %WK_RC%
