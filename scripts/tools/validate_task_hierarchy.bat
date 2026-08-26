@echo off
setlocal EnableDelayedExpansion

title VLGP T15 词表校验 validate_task_hierarchy

rem T13: paths derived from this script location (%~dp0).
set PROJECT_ROOT=%~dp0..\..
if exist "D:\miniconda3\envs\SurgDataColl\python.exe" (
    set PYTHON=D:\miniconda3\envs\SurgDataColl\python.exe
) else (
    set PYTHON=python
)
set LOG=%PROJECT_ROOT%\scripts\tools\validate_task_hierarchy.log

cd /d "%PROJECT_ROOT%"

echo ========================================
echo  VLGP T15 词表校验 validate_task_hierarchy
echo ========================================
echo.
echo 词表: scripts\config\task_hierarchy.yaml
echo.
echo [%date% %time%] Starting vlgp.annotations.task_hierarchy... > "%LOG%"
"%PYTHON%" -m vlgp.annotations.task_hierarchy 2>> "%LOG%"
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
