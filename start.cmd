@echo off
setlocal
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "PYTHON_EXE=%USERPROFILE%\miniconda3\envs\WXbot\python.exe"
if not exist "%PYTHON_EXE%" (
    echo WXbot Conda environment not found: %PYTHON_EXE%
    pause
    exit /b 1
)
"%PYTHON_EXE%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=3).close()" >nul 2>&1
if errorlevel 1 (
    where ollama >nul 2>&1
    if errorlevel 1 (
        echo Ollama is not running and ollama.exe was not found in PATH.
        pause
        exit /b 1
    )
    echo Starting Ollama...
    start "" /b ollama serve
    for /l %%i in (1,1,10) do (
        timeout /t 2 /nobreak >nul
        "%PYTHON_EXE%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11434/api/tags', timeout=2).close()" >nul 2>&1
        if not errorlevel 1 goto run_bot
    )
    echo Ollama did not start. Please check Ollama and try again.
    pause
    exit /b 1
)
:run_bot
"%PYTHON_EXE%" main.py
 echo.
echo Bot stopped. Press any key to close.
pause >nul
