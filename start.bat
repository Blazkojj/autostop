@echo off
rem AutoStop - wylacza komputer, gdy gry sie pobiora.
rem Mozna dopisac opcje, np.:  start.bat --test
cd /d "%~dp0"

set "PY="
where py >/dev/null 2>/dev/null && set "PY=py -3"
if not defined PY where python >/dev/null 2>/dev/null && set "PY=python"
if not defined PY (
    echo Nie znaleziono Pythona. Zainstaluj go ze strony https://www.python.org/downloads/
    echo Przy instalacji zaznacz "Add python.exe to PATH".
    pause
    exit /b 1
)

%PY% -c "import psutil" >/dev/null 2>/dev/null || %PY% -m pip install --user psutil
%PY% autostop.py %*
pause
