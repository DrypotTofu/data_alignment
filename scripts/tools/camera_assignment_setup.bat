@echo off
setlocal EnableDelayedExpansion

title VLGP 摄像头分配设置 (GUI)

set PYTHON=D:\miniconda3\envs\SurgDataColl\python.exe
set PROJECT_ROOT=D:\AI4Surg\DataCollection\data_alignment
set SCRIPT=%PROJECT_ROOT%\scripts\tools\camera_assignment_gui.py
set CONFIG=%PROJECT_ROOT%\scripts\config\default.yaml
set OUT_DIR=%PROJECT_ROOT%\var\camera_test_frames
set LOG=%PROJECT_ROOT%\scripts\tools\camera_assignment_setup.log

cd /d "%PROJECT_ROOT%"

echo ========================================
echo  VLGP 摄像头分配设置 (GUI)
echo ========================================
echo.
echo Python: %PYTHON%
echo Config: %CONFIG%
echo Preview: %OUT_DIR%
echo.

echo [%date% %time%] Starting camera assignment setup... > "%LOG%"
"%PYTHON%" "%SCRIPT%" --config "%CONFIG%" --out-dir "%OUT_DIR%" 2>> "%LOG%"
set EXIT_CODE=%errorlevel%
echo [%date% %time%] Exit code: %EXIT_CODE% >> "%LOG%"

echo.
if %EXIT_CODE% neq 0 (
    echo [错误] 设置过程异常退出，退出码: %EXIT_CODE%
    echo 日志文件: %LOG%
) else (
    echo 设置已正常结束。
)
echo.
echo 按任意键关闭窗口...
pause >nul
