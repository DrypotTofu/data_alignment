@echo off
setlocal EnableDelayedExpansion

title VLGP 单摄像头预览

rem T13: paths derived from this script location (%~dp0) so the launcher
rem works from any checkout; python falls back to conda on PATH.
set PROJECT_ROOT=%~dp0..\..
if exist D:\miniconda3\envs\SurgDataColl\python.exe (
    set PYTHON=D:\miniconda3\envs\SurgDataColl\python.exe
) else (
    set PYTHON=python
)
set LOG=%PROJECT_ROOT%\scripts\tools\preview_single_camera.log

cd /d %PROJECT_ROOT%

echo ========================================
echo  VLGP 单摄像头预览
echo ========================================
echo.
echo Python: %PYTHON%
echo Module: vlgp.capture.preview_single_camera
echo.

echo [%date% %time%] Starting preview_single_camera... > %LOG%
%PYTHON% -m vlgp.capture.preview_single_camera  2>> %LOG%
set EXIT_CODE=%errorlevel%
echo [%date% %time%] Exit code: %EXIT_CODE% >> %LOG%

echo.
if %EXIT_CODE% neq 0 (
    echo [错误] 过程异常退出，退出码: %EXIT_CODE%
    echo 日志文件: %LOG%
) else (
    echo 已正常结束。
)
echo.
echo 按任意键关闭窗口...
pause >nul
