REM ========================================================================
REM  Para generar los instaladores necesitas tener instalado Inno Setup 6:
REM    https://jrsoftware.org/isdl.php
REM  El compilador ISCC.exe debe estar en el PATH, o ajusta ISCC_PATH abajo.
REM ========================================================================

@echo off
setlocal

REM --- Ruta al compilador de Inno Setup (ajusta si es necesario) ---
set ISCC_PATH=C:\Program Files (x86)\Inno Setup 6\

REM --- Compilar instaladores con Inno Setup ---
"%ISCC_PATH%\ISCC.exe" installer\setup_server.iss
if errorlevel 1 (
    echo [ERROR] Fallo al compilar el instalador del servidor.
    pause
    exit /b 1
)

"%ISCC_PATH%\ISCC.exe" installer\setup_agent.iss
if errorlevel 1 (
    echo [ERROR] Fallo al compilar el instalador del agente.
    pause
    exit /b 1
)
endlocal

echo.
echo ===============================================
echo  Instaladores COMPLETOS:
echo    dist\installers\DLSlab_Server_Setup_v1.0.exe
echo    dist\installers\DLSlab_Agent_Setup_v1.0.exe
echo ===============================================
pause