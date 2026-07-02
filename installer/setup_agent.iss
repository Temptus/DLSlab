; =============================================================================
;  DLSlab Agent — Inno Setup Installer Script
;  Instala DLSlab_Agent.exe en las PCs de los estudiantes.
;
;  Acciones de instalación:
;    1. Solicita al administrador el nombre/IP del servidor DLSlab.
;    2. Copia DLSlab_Agent.exe a %ProgramFiles%\DLSlab\
;    3. Crea %ProgramData%\DLSlab\config.ini con la dirección del servidor.
;    4. Registra una Tarea Programada (Task Scheduler) que ejecuta el agente
;       automáticamente al inicio de sesión de cualquier usuario, con los
;       máximos privilegios disponibles (HighestAvailable).
;    5. NO crea accesos directos visibles — el agente es transparente para
;       el estudiante (solo aparece el ícono de estado en la bandeja del sistema).
;
;  Acciones de desinstalación:
;    1. Elimina la Tarea Programada.
;    2. Elimina config.ini y el directorio %ProgramData%\DLSlab\ (si está vacío).
;    3. Elimina los archivos instalados.
;
;  Requisitos:
;    - Compilar con Inno Setup 6.x  (https://jrsoftware.org/isinfo.php)
;    - Tener dist\DLSlab_Agent.exe generado por build\build.bat
;    - Ejecutar como administrador en la PC destino.
;
;  Nota sobre privilegios del agente:
;    La tarea usa RunLevel "HighestAvailable", lo que significa que el agente
;    se ejecuta elevado si el usuario logueado es administrador local.
;    En laboratorios donde los estudiantes son usuarios estándar, se recomienda
;    configurar las cuentas de lab como administradores locales (práctica común
;    en entornos controlados donde las políticas de dominio limitan el acceso).
; =============================================================================

#define AppName      "DLSlab Agent"
#define AppVersion   "1.0"
#define AppPublisher "DLSlab"
#define AppExeName   "DLSlab_Agent.exe"
#define TaskName     "DLSlab Agent"
#define ConfigDir    "{commonappdata}\DLSlab"

[Setup]
; IMPORTANTE: Genera un GUID único con Tools > Generate GUID en Inno Setup IDE
AppId={{B2C3D4E5-F6A7-8901-BCDE-F12345678901}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\DLSlab
DefaultGroupName=DLSlab
; No crear carpeta en el Menú de Inicio (agente oculto para estudiantes)
CreateUninstallRegKey=yes
DisableProgramGroupPage=yes
; Sin acceso directo en el escritorio por defecto
OutputDir=..\dist\installers
OutputBaseFilename=DLSlab_Agent_Setup_v{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
; Descomenta si tienes un archivo .ico:
SetupIconFile=..\icon.ico

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Messages]
WelcomeLabel1=Bienvenido al instalador de [name]
WelcomeLabel2=Este asistente instalará el agente [name/ver] en este equipo.%n%nEl agente se ejecutará automáticamente al iniciar sesión y aparecerá solo como un pequeño ícono en la bandeja del sistema.%n%nCierre todas las aplicaciones antes de continuar.

[Files]
Source: "..\dist\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Run]
; --- Opción de iniciar el agente al finalizar con permisos elevados (UAC) ---
; runascurrentuser: baja al contexto del usuario logueado
; shellexec + Verb:runas: dispara el diálogo UAC para re-elevar
Filename: "{app}\{#AppExeName}"; \
    Description: "Iniciar {#AppName} ahora"; \
    Flags: nowait postinstall skipifsilent shellexec runascurrentuser; \
    Verb: runas

[UninstallDelete]
; Eliminar config.ini al desinstalar
Type: files;     Name: "{commonappdata}\DLSlab\config.ini"
; Eliminar el directorio si queda vacío
Type: dirifempty; Name: "{commonappdata}\DLSlab"

[Code]
// ---------------------------------------------------------------------------
// Variables globales
// ---------------------------------------------------------------------------
var
  ServerPage: TInputQueryWizardPage;

// ---------------------------------------------------------------------------
// Página personalizada: solicitar servidor DLSlab
// ---------------------------------------------------------------------------
procedure InitializeWizard;
begin
  ServerPage := CreateInputQueryPage(
    wpSelectDir,
    'Configuración del Servidor DLSlab',
    'Dirección del servidor (PC del profesor)',
    'Ingrese el nombre de host o la dirección IP de la PC donde se ejecuta ' +
    'DLSlab Server.' + #13#10 + #13#10 +
    'Ejemplo con nombre de host:  DLSLAB-PROF' + #13#10 +
    'Ejemplo con dirección IP:    192.168.1.100'
  );
  ServerPage.Add('Servidor DLSlab:', False);
  // Valor por defecto — el administrador puede cambiarlo durante la instalación
  ServerPage.Values[0] := 'DLSLAB-PROF';
end;

// ---------------------------------------------------------------------------
// Validar que el campo servidor no esté vacío antes de avanzar
// ---------------------------------------------------------------------------
function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = ServerPage.ID then
  begin
    if Trim(ServerPage.Values[0]) = '' then
    begin
      MsgBox(
        'Por favor ingrese el nombre de host o la dirección IP del servidor DLSlab.',
        mbError, MB_OK
      );
      Result := False;
    end;
  end;
end;

// ---------------------------------------------------------------------------
// Escribir config.ini y registrar la Tarea Programada tras la instalación
// ---------------------------------------------------------------------------
procedure CurStepChanged(CurStep: TSetupStep);
var
  ServerHost  : String;
  ConfigDir   : String;
  ConfigFile  : String;
  TaskXML     : String;
  TaskXMLPath : String;
  AppPath     : String;
  Lines       : TArrayOfString;
  ResultCode  : Integer;
  TaskOK      : Boolean;
begin
  if CurStep <> ssPostInstall then Exit;

  // --- 1. Obtener valores ---
  ServerHost := Trim(ServerPage.Values[0]);
  if ServerHost = '' then ServerHost := 'DLSLAB-PROF';

  AppPath    := ExpandConstant('{app}\{#AppExeName}');
  ConfigDir  := ExpandConstant('{commonappdata}\DLSlab');
  ConfigFile := ConfigDir + '\config.ini';

  // --- 2. Crear %ProgramData%\DLSlab\ si no existe ---
  if not DirExists(ConfigDir) then
    ForceDirectories(ConfigDir);

  // --- 3. Escribir config.ini ---
  SetArrayLength(Lines, 4);
  Lines[0] := '[server]';
  Lines[1] := 'host = ' + ServerHost;
  Lines[2] := 'port = 9000';
  SaveStringsToUTF8FileWithoutBOM(ConfigFile, Lines, False);

  // --- 4. Generar XML de la Tarea Programada ---
  //
  // GroupId = BUILTIN\Users  → la tarea se activa para TODOS los usuarios
  // RunLevel = HighestAvailable → elevada para admins, normal para usuarios estándar
  // MultipleInstancesPolicy = IgnoreNew → evita duplicados si ya corre
  // ExecutionTimeLimit = PT0S  → sin límite de tiempo (el agente corre indefinidamente)
  // encoding="UTF-8" → coincide con la codificación real del archivo guardado por Inno Setup
  //
  TaskXML :=
    '<?xml version="1.0" encoding="UTF-8"?>' + #13#10 +
    '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">' + #13#10 +
    '  <RegistrationInfo>' + #13#10 +
    '    <Description>DLSlab Student Agent — monitor de laboratorio</Description>' + #13#10 +
    '  </RegistrationInfo>' + #13#10 +
    '  <Triggers>' + #13#10 +
    '    <LogonTrigger>' + #13#10 +
    '      <Enabled>true</Enabled>' + #13#10 +
    '    </LogonTrigger>' + #13#10 +
    '  </Triggers>' + #13#10 +
    '  <Principals>' + #13#10 +
    '    <Principal id="Author">' + #13#10 +
    '      <GroupId>BUILTIN\Users</GroupId>' + #13#10 +
    '      <RunLevel>HighestAvailable</RunLevel>' + #13#10 +
    '    </Principal>' + #13#10 +
    '  </Principals>' + #13#10 +
    '  <Settings>' + #13#10 +
    '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>' + #13#10 +
    '    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>' + #13#10 +
    '    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>' + #13#10 +
    '    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>' + #13#10 +
    '    <Priority>7</Priority>' + #13#10 +
    '  </Settings>' + #13#10 +
    '  <Actions Context="Author">' + #13#10 +
    '    <Exec>' + #13#10 +
    '      <Command>' + AppPath + '</Command>' + #13#10 +
    '    </Exec>' + #13#10 +
    '  </Actions>' + #13#10 +
    '</Task>';

  // --- 5. Guardar XML en archivo temporal ---
  TaskXMLPath := ExpandConstant('{tmp}\dlslab_agent_task.xml');
  SaveStringToFile(TaskXMLPath, TaskXML, False);

  // --- 6. Registrar la tarea con schtasks via XML ---
  // /F sobreescribe si ya existe (útil en actualizaciones)
  Exec(
    ExpandConstant('{sys}\schtasks.exe'),
    '/Create /TN "{#TaskName}" /XML "' + TaskXMLPath + '" /F',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  );
  TaskOK := (ResultCode = 0);

  // --- 7. Fallback: clave de Registro si schtasks falló (ej: PC en dominio con GPO restrictiva) ---
  if not TaskOK then
  begin
    TaskOK := RegWriteStringValue(
      HKEY_LOCAL_MACHINE,
      'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
      'DLSlab Agent',
      '"' + AppPath + '"'
    );
  end;

  // --- 8. Informar resultado ---
  if not TaskOK then
    MsgBox(
      'Advertencia: no se pudo registrar el inicio automático del agente.' + #13#10 +
      'El agente puede iniciarse manualmente desde:' + #13#10 + AppPath + #13#10 + #13#10 +
      'Código de error schtasks: ' + IntToStr(ResultCode),
      mbInformation, MB_OK
    );
end;

// ---------------------------------------------------------------------------
// Eliminar la Tarea Programada y la clave de Registro al desinstalar
// ---------------------------------------------------------------------------
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // Eliminar tarea programada
    Exec(
      ExpandConstant('{sys}\schtasks.exe'),
      '/Delete /TN "{#TaskName}" /F',
      '',
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    );
    // Eliminar también el fallback de Registry (si existe)
    RegDeleteValue(
      HKEY_LOCAL_MACHINE,
      'SOFTWARE\Microsoft\Windows\CurrentVersion\Run',
      'DLSlab Agent'
    );
  end;
end;

// ---------------------------------------------------------------------------
// Verificar sistema operativo mínimo (Windows 8+)
// ---------------------------------------------------------------------------
function InitializeSetup(): Boolean;
var
  Version: TWindowsVersion;
begin
  GetWindowsVersionEx(Version);
  if (Version.Major < 6) or ((Version.Major = 6) and (Version.Minor < 2)) then
  begin
    MsgBox(
      'DLSlab Agent requiere Windows 8 o superior.' + #13#10 +
      'La instalación no puede continuar.',
      mbError, MB_OK
    );
    Result := False;
  end
  else
    Result := True;
end;
