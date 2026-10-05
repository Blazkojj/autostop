@echo off
setlocal
rem AutoStop - wylacza komputer, gdy gry sie pobiora.
rem Mozna dopisac opcje, np.:  start.bat --test
pushd "%~dp0"

if not exist "autostop.py" (
    echo Nie widze pliku autostop.py obok start.bat.
    echo Rozpakuj CALY plik ZIP do zwyklego folderu i uruchom start.bat stamtad.
    goto :end
)

rem Szukamy Pythona: najpierw polecenia py/python, potem typowe foldery instalacji.
set "PYEXE="
set "PYARGS="
call :try py -3
if not defined PYEXE call :try python
if not defined PYEXE call :try python3
if not defined PYEXE call :search "%LOCALAPPDATA%\Programs\Python" Python3*
if not defined PYEXE call :search "%LOCALAPPDATA%\Python" *
if not defined PYEXE call :search "%ProgramFiles%" Python3*
if not defined PYEXE call :search "%ProgramFiles(x86)%" Python3*
if not defined PYEXE call :search "%SystemDrive%\" Python3*
if not defined PYEXE call :search "%USERPROFILE%" anaconda3
if not defined PYEXE call :search "%USERPROFILE%" miniconda3
if not defined PYEXE call :search "%ProgramData%" anaconda3
if not defined PYEXE call :search "%ProgramData%" miniconda3
if not defined PYEXE (
    echo Nie znaleziono Pythona 3.
    echo Zainstaluj go ze strony https://www.python.org/downloads/
    echo albo uruchom AutoStop recznie, podajac sciezke do python.exe:
    echo   "C:\sciezka\do\python.exe" autostop.py
    goto :end
)
"%PYEXE%" %PYARGS% -c "import sys; print('Python:', sys.executable)"

"%PYEXE%" %PYARGS% -c "import psutil" >nul 2>nul
if errorlevel 1 (
    echo Instaluje biblioteke psutil...
    "%PYEXE%" %PYARGS% -m pip install psutil
)
echo.
"%PYEXE%" %PYARGS% autostop.py %*

:end
popd
echo.
pause
exit /b

rem Uzycie: call :try PROGRAM [ARGUMENT] - zapamietuje program, jesli to Python 3.7+.
:try
"%~1" %~2 -c "import sys; sys.exit(sys.version_info < (3, 7))" >nul 2>nul
if errorlevel 1 exit /b 0
set "PYEXE=%~1"
set "PYARGS=%~2"
exit /b 0

rem Uzycie: call :search "FOLDER" WZORZEC - szuka python.exe w podfolderach FOLDER
rem pasujacych do WZORZEC (np. Python3*).
:search
if "%~1"=="" exit /b 0
if not exist "%~1\" exit /b 0
pushd "%~1"
for /d %%D in (%~2) do (
    if not defined PYEXE if exist "%%~fD\python.exe" call :try "%%~fD\python.exe"
)
popd
exit /b 0
