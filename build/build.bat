@echo off
REM ====================================================================
REM  DLSlab Build Script
REM  Fase 1: Genera DLSlab_Server.exe y DLSlab_Agent.exe con PyInstaller
REM  Fase 2: (Opcional) Genera los instaladores .exe con Inno Setup
REM
REM ====================================================================

REM --- Ruta al compilador de Inno Setup (ajusta si es necesario) ---
set ISCC_PATH=C:\Program Files (x86)\Inno Setup 6\

echo [DLSlab] Verificando dependencias...
pip install pyinstaller --quiet
pip install -r requirements.txt --quiet

echo.
echo [DLSlab] Compilando DLSlab_Server.exe...
pyinstaller server.spec --distpath dist --workpath \work --noconfirm --clean
if errorlevel 1 (
    echo [ERROR] Fallo al compilar el servidor.
    pause
    exit /b 1
)

echo.
echo [DLSlab] Compilando DLSlab_Agent.exe...
pyinstaller agent.spec --distpath dist --workpath \work --noconfirm --clean
if errorlevel 1 (
    echo [ERROR] Fallo al compilar el agente.
    pause
    exit /b 1
)

echo.
echo =============================================
echo  BUILD EXITOSO - EXEs generados en dist\
echo    - dist\DLSlab_Server.exe
echo    - dist\DLSlab_Agent.exe
echo =============================================
pause
