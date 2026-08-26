@echo off
setlocal EnableDelayedExpansion

title VLGP T16 运动学验证 validate_kinematics

rem T13: paths derived from this script location (%~dp0).
set PROJECT_ROOT=%~dp0..\..
if exist "D:\miniconda3\envs\SurgDataColl\python.exe" (
    set PYTHON=D:\miniconda3\envs\SurgDataColl\python.exe
) else (
    set PYTHON=python
)
set LOG=%PROJECT_ROOT%\scripts\tools\validate_kinematics.log

cd /d "%PROJECT_ROOT%"

echo ========================================
echo  VLGP T16 运动学验证 validate_kinematics
echo ========================================
echo.
set /p SESSION_NAME=Session name (e.g. 20260821_123455):

set ARGS=--session "sessions\%SESSION_NAME%"
echo [%date% %time%] Starting vlgp.kinematics.validate... > "%LOG%"
"%PYTHON%" -m vlgp.kinematics.validate %ARGS% 2>> "%LOG%"
set EXIT_CODE=%errorlevel%
echo [%date% %time%] Exit code: %EXIT_CODE% >> "%LOG%"

echo.
if %EXIT_CODE% neq 0 (
    echo [FAILED] Process aborted, exit code: %EXIT_CODE%
    echo Log file: %LOG%
) else (
    echo Done. Report: sessions\%SESSION_NAME%\t16_validation.json
)
echo.
echo 按任意键关闭窗口...
pause >nul
