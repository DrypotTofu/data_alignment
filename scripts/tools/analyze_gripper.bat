@echo off
setlocal EnableDelayedExpansion

title VLGP Gripper 量程分析

rem T13: paths derived from this script location (%~dp0).
set PROJECT_ROOT=%~dp0..\..
if exist "D:\miniconda3\envs\SurgDataColl\python.exe" (
    set PYTHON=D:\miniconda3\envs\SurgDataColl\python.exe
) else (
    set PYTHON=python
)
set LOG=%PROJECT_ROOT%\scripts\tools\analyze_gripper.log

cd /d "%PROJECT_ROOT%"

echo ========================================
echo  VLGP Gripper 量程分析
echo ========================================
echo.
set /p SESSION_NAME=Session name (e.g. test_20260818_215014):

set ARGS=--session "sessions\%SESSION_NAME%"
echo [%date% %time%] Starting vlgp.analysis.analyze_gripper... > "%LOG%"
"%PYTHON%" -m vlgp.analysis.analyze_gripper %ARGS% 2>> "%LOG%"
set EXIT_CODE=%errorlevel%
echo [%date% %time%] Exit code: %EXIT_CODE% >> "%LOG%"

echo.
if %EXIT_CODE% neq 0 (
    echo [FAILED] Process aborted, exit code: %EXIT_CODE%
    echo Log file: %LOG%
) else (
    echo Done.
)
echo.
echo 按任意键关闭窗口...
pause >nul
