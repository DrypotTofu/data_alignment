@echo off
setlocal EnableDelayedExpansion

title VLGP 相机参数实时调参

rem T13: paths derived from this script location (%~dp0).
set PROJECT_ROOT=%~dp0..\..
if exist "D:\miniconda3\envs\SurgDataColl\python.exe" (
    set PYTHON=D:\miniconda3\envs\SurgDataColl\python.exe
) else (
    set PYTHON=python
)
set LOG=%PROJECT_ROOT%\scripts\tools\camera_param_tuner.log

cd /d "%PROJECT_ROOT%"

echo ========================================
echo  VLGP 相机参数实时调参
echo ========================================
echo.
set /p CAM_INDEX=Camera index (arm_1=1, arm_2=0, arm_3=3):

set ARGS=--index %CAM_INDEX%
echo [%date% %time%] Starting vlgp.calibration.camera_param_tuner... > "%LOG%"
"%PYTHON%" -m vlgp.calibration.camera_param_tuner %ARGS% 2>> "%LOG%"
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
