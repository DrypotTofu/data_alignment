@echo off
setlocal EnableDelayedExpansion

title VLGP 手动数据采集

rem T13: paths derived from this script location (%~dp0) so the launcher
rem works from any checkout; python falls back to conda on PATH.
set PROJECT_ROOT=%~dp0..\..
if exist D:\miniconda3\envs\SurgDataColl\python.exe (
    set PYTHON=D:\miniconda3\envs\SurgDataColl\python.exe
) else (
    set PYTHON=python
)
set LOG=%PROJECT_ROOT%\scripts\tools\start_manual_acquisition.log

cd /d %PROJECT_ROOT%

echo ========================================
echo  VLGP 手动数据采集
echo ========================================
echo.
echo Python: %PYTHON%
echo Module: vlgp.capture.manual_acquisition
echo.

echo [%date% %time%] Starting manual_acquisition... > %LOG%
%PYTHON% -m vlgp.capture.manual_acquisition --config %PROJECT_ROOT%\scripts\config\default.yaml 2>> %LOG%
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
