@echo off
setlocal
chcp 65001 >nul
set "JARVIS_ROOT=%~dp0"
set "JARVIS_PYTHON=%JARVIS_ROOT%.venv\Scripts\python.exe"
if not exist "%JARVIS_PYTHON%" (
    >&2 echo [ERRO] Ambiente virtual nao encontrado em "%JARVIS_ROOT%.venv".
    exit /b 1
)
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONPATH=%JARVIS_ROOT%src;%PYTHONPATH%"
set "JARVIS_DOUBLE_CLICK=0"
if "%~1"=="" (
    set "JARVIS_DOUBLE_CLICK=1"
    "%JARVIS_PYTHON%" -m jarvis --runtime-root "%JARVIS_ROOT%." --voice --ui
) else (
    "%JARVIS_PYTHON%" -m jarvis --runtime-root "%JARVIS_ROOT%." %*
)
set "JARVIS_EXIT=%ERRORLEVEL%"
if not "%JARVIS_EXIT%"=="0" if "%JARVIS_DOUBLE_CLICK%"=="1" pause
exit /b %JARVIS_EXIT%
