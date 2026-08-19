@echo off
setlocal EnableDelayedExpansion

title VLGP 多相机预览

rem T13: paths derived from this script location (%~dp0).
set PROJECT_ROOT=%~dp0..\..
if exist "D:\miniconda3\envs\SurgDataColl\python.exe" (
    set PYTHON=D:\miniconda3\envs\SurgDataColl\python.exe
) else (
    set PYTHON=python
)
set LOG=%PROJECT_ROOT%\scripts\tools\preview_connected_cameras.log

cd /d "%PROJECT_ROOT%"

echo ========================================
echo  VLGP 多相机预览
echo ========================================
echo.
echo [%date% %time%] Starting vlgp.capture.preview_connected_cameras... > "%LOG%"
"%PYTHON%" -m vlgp.capture.preview_connected_cameras  2>> "%LOG%"
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
