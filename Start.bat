@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\python\pythonw.exe" (
    echo Local runtime missing. See README.md.
    pause
    exit /b 1
)
set PYTHONHOME=
set PYTHONPATH=
set PYTHONNOUSERSITE=1
set "TCL_LIBRARY=%~dp0runtime\python\tcl\tcl8.6"
set "TK_LIBRARY=%~dp0runtime\python\tcl\tk8.6"
set "PATH=%~dp0runtime\cuda;%PATH%"
if not exist "logs" mkdir "logs"
start "" /b "runtime\python\pythonw.exe" "app\launch.py" %*
endlocal
