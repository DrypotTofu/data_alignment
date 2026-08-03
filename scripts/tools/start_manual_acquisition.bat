@echo off
setlocal EnableDelayedExpansion

title VLGP 手动数据采集

set PYTHON=C:\Users\11764\.conda\envs\SurgDataColl\python.exe
set PROJECT_ROOT=D:\AI4Surg\DataCollection\data_alignment
set SCRIPT=%PROJECT_ROOT%\scripts\tools\manual_acquisition.py
set CONFIG=%PROJECT_ROOT%\scripts\config\default.yaml
set LOG=%PROJECT_ROOT%\scripts\tools\start_manual_acquisition.log

cd /d "%PROJECT_ROOT%"

echo ========================================
echo  VLGP 手动数据采集启动器
echo ========================================
echo.
echo Python: %PYTHON%
echo Config: %CONFIG%
echo.

echo [%date% %time%] Starting manual acquisition... > "%LOG%"
"%PYTHON%" "%SCRIPT%" --config "%CONFIG%" 2>> "%LOG%"
set EXIT_CODE=%errorlevel%
echo [%date% %time%] Exit code: %EXIT_CODE% >> "%LOG%"

echo.
if %EXIT_CODE% neq 0 (
    echo [错误] 采集过程异常退出，退出码: %EXIT_CODE%
    echo 日志文件: %LOG%
) else (
    echo 采集已正常结束。
)
echo.
echo 按任意键关闭窗口...
pause >/dev/null
