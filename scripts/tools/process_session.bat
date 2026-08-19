@echo off
setlocal EnableDelayedExpansion

title VLGP Session Pipeline

rem T13: paths derived from this script location (%~dp0).
set PROJECT_ROOT=%~dp0..\..
if exist D:\miniconda3\envs\SurgDataColl\python.exe (
    set PYTHON=D:\miniconda3\envs\SurgDataColl\python.exe
) else (
    set PYTHON=python
)
set LOG=%PROJECT_ROOT%\scripts\tools\process_session.log

cd /d %PROJECT_ROOT%

echo ========================================
echo  VLGP Session Pipeline
echo ========================================
echo.
set /p SESSION_NAME=Session name (e.g. test_20260818_215014):
set /p WITH_META=Fill session metadata first? [y/N]:

set ARGS=--session sessions\%SESSION_NAME%
if /i %WITH_META%==y set ARGS=%ARGS% --meta

echo [%date% %time%] Pipeline start: %SESSION_NAME% >> %LOG%
%PYTHON% -m vlgp.review.process_session %ARGS% 2>&1 | %SystemRoot%\System32\findstr.exe /v ^$
set EXIT_CODE=%errorlevel%
echo [%date% %time%] Exit code: %EXIT_CODE% >> %LOG%

echo.
if %EXIT_CODE% neq 0 (
    echo [FAILED] Pipeline aborted, exit code: %EXIT_CODE%
    echo Log file: %LOG%
) else (
    echo Pipeline finished.
)
echo.
pause
