@echo off
rem pyauto launcher. Keep this file ASCII-only: cmd.exe reads .cmd in the OEM codepage.
rem Order: PYAUTO_PYTHON env var -> bundled DSH runtime -> python on PATH.
rem No arguments -> little command prompt (type gui / web / doctor ...).
rem Set PYAUTO_NO_PAUSE=1 to skip the "press any key" prompt (for scripts).
setlocal
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
rem Make "import pyauto" work even when this is called from another folder.
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
set "PYEXE=%PYAUTO_PYTHON%"
if not defined PYEXE set "PYEXE=C:\Users\cfgxd\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not exist "%PYEXE%" set "PYEXE=python"

if "%~1"=="" goto :interactive

"%PYEXE%" -m pyauto %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto :failed
goto :done

:interactive
"%PYEXE%" -m pyauto
set "RC=%ERRORLEVEL%"
goto :done

:failed
echo.
echo   [pyauto] failed with exit code %RC%
if not defined PYAUTO_NO_PAUSE pause

:done
endlocal & exit /b %RC%
