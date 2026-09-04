@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Ambiente virtual nao encontrado.
    echo Execute primeiro: instalar.bat
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
python main.py --inicio 0 --limite 5 --intervalo 5
set "CODIGO=%ERRORLEVEL%"

echo.
echo Execucao encerrada com codigo %CODIGO%.
pause
exit /b %CODIGO%
