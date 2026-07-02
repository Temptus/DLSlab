; =============================================================================
;  DLSlab Server — Inno Setup Installer Script
;  Instala DLSlab_Server.exe en la PC del profesor.
;
;  Acciones de instalación:
;    1. Copia DLSlab_Server.exe a %ProgramFiles%\DLSlab\
;    2. Crea acceso directo en el Escritorio y en el Menú de Inicio.
;    3. Agrega una regla de Firewall de Windows (TCP entrada, puerto 9000).
;
;  Acciones de desinstalación:
;    1. Elimina la regla de Firewall.
;    2. Elimina los archivos y accesos directos instalados.
;
;  Requisitos:
;    - Compilar con Inno Setup 6.x  (https://jrsoftware.org/isinfo.php)
;    - Tener dist\DLSlab_Server.exe generado por build\build.bat
;    - Opcionalmente: convertir icon.png a icon.ico y descomentar las líneas de icono.
; =============================================================================

#define AppName      "DLSlab Server"
#define AppVersion   "1.0"
#define AppPublisher "DLSlab"
#define AppExeName   "DLSlab_Server.exe"
#define FirewallRule "DLSlab Server (TCP 9000)"

[Setup]
; IMPORTANTE: Genera un GUID único para tu proyecto con Tools > Generate GUID en Inno Setup IDE
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\DLSlab
DefaultGroupName=DLSlab
DisableProgramGroupPage=yes
OutputDir=..\dist\installers
OutputBaseFilename=DLSlab_Server_Setup_v{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
; Descomenta si tienes un archivo .ico:
; SetupIconFile=..\icon.ico

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Messages]
; Mensajes personalizados en español
WelcomeLabel1=Bienvenido al instalador de [name]
WelcomeLabel2=Este asistente instalará [name/ver] en su equipo.%n%nSe configurará automáticamente una regla de Firewall para permitir la comunicación con los agentes de los estudiantes.%n%nCierre todas las aplicaciones antes de continuar.

[Files]
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Menú de Inicio
Name: "{group}\{#AppName}";         Filename: "{app}\{#AppExeName}"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
; Escritorio
Name: "{commondesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el &Escritorio"; GroupDescription: "Opciones adicionales:"

[Run]
; --- Regla de Firewall de Windows ---
; Elimina primero si ya existe (actualización limpia), luego crea.
Filename: "powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -NonInteractive -Command ""Remove-NetFirewallRule -DisplayName '{#FirewallRule}' -ErrorAction SilentlyContinue"""; \
    Flags: runhidden; \
    StatusMsg: "Configurando Firewall de Windows..."

Filename: "powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -NonInteractive -Command ""New-NetFirewallRule -DisplayName '{#FirewallRule}' -Direction Inbound -Protocol TCP -LocalPort 9000 -Action Allow -Profile Any -Description 'Permite que los agentes DLSlab de los estudiantes se conecten al servidor.'"""; \
    Flags: runhidden; \
    StatusMsg: "Configurando Firewall de Windows..."

; --- Opción de iniciar el servidor al finalizar ---
Filename: "{app}\{#AppExeName}"; \
    Description: "Iniciar {#AppName} ahora"; \
    Flags: nowait postinstall skipifsilent

[UninstallRun]
; Elimina la regla de Firewall al desinstalar
Filename: "powershell.exe"; \
    Parameters: "-ExecutionPolicy Bypass -NonInteractive -Command ""Remove-NetFirewallRule -DisplayName '{#FirewallRule}' -ErrorAction SilentlyContinue"""; \
    Flags: runhidden

[Code]
// ---------------------------------------------------------------------------
// Verificar que el sistema operativo soporte New-NetFirewallRule (Win 8+)
// ---------------------------------------------------------------------------
function InitializeSetup(): Boolean;
var
  Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);
  // Windows 8 = major 6, minor 2
  if (Version.Major < 6) or ((Version.Major = 6) and (Version.Minor < 2)) then
  begin
    MsgBox(
      'DLSlab Server requiere Windows 8 o superior.' + #13#10 +
      'La instalación no puede continuar.',
      mbError, MB_OK
    );
    Result := False;
  end
  else
    Result := True;
end;
