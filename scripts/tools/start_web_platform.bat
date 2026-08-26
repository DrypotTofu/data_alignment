@echo off
setlocal EnableDelayedExpansion

title VLGP Web 平台 (T15 v0)

rem T13: paths derived from this script location (%~dp0).
set PROJECT_ROOT=%~dp0..\..
if exist "D:\miniconda3\envs\SurgDataColl\python.exe" (
    set PYTHON=D:\miniconda3\envs\SurgDataColl\python.exe
) else (
    set PYTHON=python
)
set LOG=%PROJECT_ROOT%\scripts\tools\web_platform.log

cd /d "%PROJECT_ROOT%"

echo ========================================
echo  VLGP Web 平台 (T15 v0) - FastAPI + JS
echo  URL: http://127.0.0.1:8765
echo ========================================
echo.
set /p PORT=Port (default 8765): 
if not defined PORT set PORT=8765

echo [%date% %time%] Starting web platform on port %PORT%... > "%LOG%"
"%PYTHON%" -m vlgp.web.app --host 127.0.0.1 --port !PORT! 2>> "%LOG%"
set EXIT_CODE=%errorlevel%
echo [%date% %time%] Exit code: %EXIT_CODE% >> "%LOG%"

echo.
echo 服务器已退出 (exit code %EXIT_CODE%)
echo Log: %LOG%
echo.
echo 按任意键关闭窗口...
pause >nul
