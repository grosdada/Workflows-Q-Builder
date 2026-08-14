@echo off
setlocal
cd /d "%~dp0"

rem Cherche un Python utilisable : PATH, puis le lanceur py, puis un chemin
rem ecrit a la main dans python_path.txt (utile si seul le Python embarque de
rem ComfyUI est installe sur cette machine).
set "PY="

if exist "python_path.txt" (
  set /p PY=<python_path.txt
  goto :found
)

where python >nul 2>&1
if %errorlevel%==0 (
  set "PY=python"
  goto :found
)

where py >nul 2>&1
if %errorlevel%==0 (
  set "PY=py -3"
  goto :found
)

echo.
echo Python 3 est introuvable sur cet ordinateur.
echo.
echo Deux solutions :
echo   1. Installer Python 3 depuis https://www.python.org/downloads/
echo      en cochant "Add python.exe to PATH" pendant l'installation.
echo   2. Ou creer un fichier python_path.txt a cote de ce .bat, contenant
echo      le chemin complet d'un python.exe deja present, par exemple celui
echo      de ComfyUI portable :
echo      E:\ComfyUI_windows_portable\python_embeded\python.exe
echo.
pause
exit /b 1

:found
%PY% workflows_q_builder_server.py
if %errorlevel% neq 0 (
  echo.
  echo Le serveur s'est arrete avec une erreur.
)
pause
