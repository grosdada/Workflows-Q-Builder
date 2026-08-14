@echo off
setlocal
cd /d "%~dp0"

rem Cherche un Python utilisable : un chemin ecrit a la main dans
rem python_path.txt (utile si seul le Python embarque de ComfyUI est installe
rem sur cette machine), puis le lanceur py, puis le PATH.
rem
rem On ne teste jamais la simple presence d'un python.exe : sous Windows 10 et
rem 11 un faux python.exe (le raccourci vers le Microsoft Store) est place dans
rem le PATH par defaut. `where python` le trouve et repond 0, mais l'executer
rem n'affiche qu'un message renvoyant vers le Store. Chaque candidat est donc
rem lance pour de vrai, et n'est retenu que s'il repond en Python 3.

set "PY="
set "PYARG="

if not exist "python_path.txt" goto :try_py

set "CAND="
set /p CAND=<python_path.txt
if not defined CAND goto :try_py

call :probe "%CAND%"
if defined PY goto :found
echo.
echo Le chemin indique dans python_path.txt ne repond pas en Python 3 :
echo   %CAND%
echo On continue la recherche.

:try_py
call :probe "py" "-3"
if defined PY goto :found

call :probe "python"
if defined PY goto :found

echo.
echo Python 3 est introuvable sur cet ordinateur.
echo.
echo Si vous venez de voir un message parlant du Microsoft Store, c'est le
echo raccourci Windows : un faux python.exe qui ne fait qu'ouvrir le Store.
echo Python n'est pas installe.
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

:probe
rem %~1 = executable a tester, %~2 = argument optionnel (pour "py -3").
rem Retient le candidat dans PY seulement si l'appel aboutit vraiment : le
rem raccourci du Store, un chemin mort ou un Python 2 echouent tous ici.
"%~1" %~2 -c "import sys; assert sys.version_info[0] == 3" >nul 2>&1
if errorlevel 1 exit /b 1
set "PY=%~1"
set "PYARG=%~2"
exit /b 0

:found
"%PY%" %PYARG% workflows_q_builder_server.py
if %errorlevel% neq 0 (
  echo.
  echo Le serveur s'est arrete avec une erreur.
)
pause
