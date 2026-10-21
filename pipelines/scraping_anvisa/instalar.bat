@echo off
setlocal
cd /d "%~dp0"

py -m venv .venv
if errorlevel 1 (
    echo Nao foi possivel criar o ambiente virtual. Verifique a instalacao do Python.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo Instalacao concluida.
if not exist ".env" (
    echo Antes de executar, copie .env.example para .env e configure o PostgreSQL.
) else (
    echo Execute agora: executar_piloto.bat
)
pause
