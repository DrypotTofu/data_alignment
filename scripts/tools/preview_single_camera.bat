@echo off
setlocal EnableDelayedExpansion

title VLGP 单摄像头实时预览

set PYTHON=C:\Users\11764\.conda\envs\SurgDataColl\python.exe
set PROJECT_ROOT=D:\AI4Surg\DataCollection\data_alignment
set SCRIPT=%PROJECT_ROOT%\scripts\tools\preview_single_camera.py
set LOG=%PROJECT_ROOT%\scripts\tools\preview_single_camera.log

cd /d "%PROJECT_ROOT%"

echo ========================================
echo  VLGP 单摄像头实时预览
echo ========================================
echo.
echo 脚本会自动探测摄像头并让你选择要预览的索引。
echo 预览窗口中：q/ESC 退出，s 保存快照。
echo.
echo [%date% %time%] Starting single camera preview... > "%LOG%"
"%PYTHON%" "%SCRIPT%" 2>> "%LOG%"
set EXIT_CODE=%errorlevel%
echo [%date% %time%] Exit code: %EXIT_CODE% >> "%LOG%"

echo.
if %EXIT_CODE% neq 0 (
    echo [错误] 预览异常退出，退出码: %EXIT_CODE%
    echo 日志文件: %LOG%
) else (
    echo 预览已结束。
)
echo.
echo 按任意键关闭窗口...
pause >nul
