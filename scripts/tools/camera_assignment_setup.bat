@echo off
setlocal EnableDelayedExpansion

title VLGP 摄像头分配设置 (GUI)

rem T13: paths derived from this script location (%~dp0) so the launcher
rem works from any checkout; python falls back to conda on PATH.
set PROJECT_ROOT=%~dp0..\..
if exist D:\miniconda3\envs\SurgDataColl\python.exe (
    set PYTHON=D:\miniconda3\envs\SurgDataColl\python.exe
) else (
    set PYTHON=python
)
set LOG=%PROJECT_ROOT%\scripts\tools\camera_assignment_setup.log

cd /d %PROJECT_ROOT%

echo ========================================
echo  VLGP 摄像头分配设置 (GUI)
echo ========================================
echo.
echo Python: %PYTHON%
echo Module: vlgp.capture.camera_assignment_gui
echo.

echo [%date% %time%] Starting camera_assignment_gui... > %LOG%
%PYTHON% -m vlgp.capture.camera_assignment_gui --config %PROJECT_ROOT%\scripts\config\default.yaml --out-dir %PROJECT_ROOT%\var\camera_test_frames 2>> %LOG%
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
