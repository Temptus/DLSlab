# DLSlab Build Script (PowerShell)
# Uso: .\build\build.ps1

param(
    [switch]$ServerOnly,
    [switch]$AgentOnly,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "  DLSlab Build System" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan

# Limpiar builds anteriores
if ($Clean) {
    Write-Host "`n[*] Limpiando builds anteriores..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force build\work -ErrorAction SilentlyContinue
}

# Verificar Python
Write-Host "`n[*] Verificando Python..." -ForegroundColor Yellow
python --version

# Instalar dependencias
Write-Host "`n[*] Instalando dependencias..." -ForegroundColor Yellow
pip install pyinstaller --quiet
pip install -r requirements.txt --quiet

# Compilar servidor
if (-not $AgentOnly) {
    Write-Host "`n[*] Compilando DLSlab_Server.exe..." -ForegroundColor Green
    pyinstaller build\server.spec --distpath dist --workpath build\work --noconfirm
    Write-Host "[OK] DLSlab_Server.exe generado" -ForegroundColor Green
}

# Compilar agente
if (-not $ServerOnly) {
    Write-Host "`n[*] Compilando DLSlab_Agent.exe..." -ForegroundColor Green
    pyinstaller build\agent.spec --distpath dist --workpath build\work --noconfirm
    Write-Host "[OK] DLSlab_Agent.exe generado" -ForegroundColor Green
}

# Resumen
Write-Host "`n======================================" -ForegroundColor Cyan
Write-Host "  BUILD COMPLETADO" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
$distFiles = Get-ChildItem dist -Filter "*.exe" -ErrorAction SilentlyContinue
foreach ($f in $distFiles) {
    $size = [math]::Round($f.Length / 1MB, 1)
    Write-Host "  $($f.Name) ($size MB)" -ForegroundColor White
}
