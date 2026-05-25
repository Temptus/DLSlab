@echo off
REM =============================================
REM  DLSlab Build Script
REM  Genera DLSlab_Server.exe y DLSlab_Agent.exe
REM =============================================

echo [DLSlab] Verificando dependencias...
pip install pyinstaller --quiet
pip install -r requirements.txt --quiet

echo.
echo [DLSlab] Compilando DLSlab_Server.exe...
pyinstaller build\server.spec --distpath dist --workpath build\work --noconfirm
if errorlevel 1 (
    echo [ERROR] Fallo al compilar el servidor.
    pause
    exit /b 1
)

echo.
echo [DLSlab] Compilando DLSlab_Agent.exe...
pyinstaller build\agent.spec --distpath dist --workpath build\work --noconfirm
if errorlevel 1 (
    echo [ERROR] Fallo al compilar el agente.
    pause
    exit /b 1
)

echo.
echo =============================================
echo  BUILD EXITOSO
echo  Archivos generados en: dist\
echo    - dist\DLSlab_Server.exe
echo    - dist\DLSlab_Agent.exe
echo =============================================
pause
