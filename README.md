# DLSlab 🖥️

**DLSlab** es un sistema de gestión de laboratorios informáticos diseñado para **profesores y técnicos de TI** en entornos educativos. Permite supervisar en tiempo real las pantallas de todos los estudiantes conectados a la red local, tomar control remoto de cualquier equipo, transmitir la pantalla del profesor, aplicar restricciones de aplicaciones y navegación web, gestionar el encendido y apagado del laboratorio, y enviar archivos — todo desde un único punto de control.

---

## Arquitectura

```
┌─────────────────────────────────────┐
│     CONSOLA DEL PROFESOR            │  ← PyQt6  (ui/)
│     DLSlab_Server.exe               │  ← asyncio server  (server/)
└──────────────┬──────────────────────┘
               │  TCP · puerto 9000 · JSON por líneas
    ┌──────────┼──────────┐
    ▼          ▼          ▼
┌─────────┐ ┌─────────┐ ┌─────────┐
│ Agente 1│ │ Agente 2│ │ Agente N│  ← DLSlab_Agent.exe  (client/)
│ PC-LAB-1│ │ PC-LAB-2│ │ PC-LAB-N│     bandeja del sistema
└─────────┘ └─────────┘ └─────────┘
```

---

## Requisitos del sistema

| Componente | Requisito |
|---|---|
| Sistema operativo | Windows 10 / 11 (64-bit) |
| Python (desarrollo) | 3.11 o superior |
| Red | Todos los equipos en la misma LAN |
| Internet | No requerido para operar |
| Permisos (agente) | Administrador local (para políticas web) |

---

## Funcionalidades

| Función | Descripción |
|---|---|
| 📸 **Monitor en tiempo real** | Miniaturas de pantalla 320×180 px, actualizadas cada 2 s |
| 🔒 **Bloqueo de pantallas** | Superposición negra con mensaje personalizable |
| 📡 **Transmisión del profesor** | Pantalla del docente en los equipos estudiantes (configurable FPS/calidad) |
| 🎤 **Presentación de alumno** | Muestra la pantalla de un estudiante a toda la clase |
| 🖱️ **Control remoto** | Teclado y ratón completo vía `pynput` |
| 🛡️ **Políticas de aplicaciones** | Lista blanca o negra con cierre automático de procesos (`psutil`) |
| 🌐 **Políticas web** | Bloqueo total o lista blanca de URLs vía archivo `hosts` |
| ⚡ **Control de energía** | Apagado, reinicio, bloqueo de estación y cierre de sesión con delay |
| 🌐 **Ejecución remota** | Abrir URL o lanzar aplicación en los equipos de los estudiantes |
| 📡 **Wake-on-LAN** | Encender equipos apagados; MACs persistidas en `ui/macs.json` |
| 📤 **Envío de archivos** | Envía documentos al Escritorio de los estudiantes (máx. 35 MB) |
| 📋 **Log de violaciones** | Registro de intentos de acceso a apps/webs bloqueadas |
| 🔄 **Reconexión automática** | El agente reintenta con espera exponencial ante desconexiones |

---

## Instalación (desarrollo)

```bash
pip install -r requirements.txt
```

### Dependencias principales

| Paquete | Uso |
|---|---|
| `PyQt6` | Interfaz gráfica de la consola del profesor |
| `mss` | Captura de pantalla multiplataforma |
| `Pillow` | Compresión JPEG de capturas |
| `pynput` | Inyección de eventos de teclado y ratón |
| `psutil` | Monitoreo y cierre de procesos (políticas de apps) |
| `wakeonlan` | Envío de paquetes Magic Packet (WoL) |
| `qt-material` | Temas visuales para la interfaz |
| `qtawesome` | Íconos vectoriales |
| `pyinstaller` | Compilación a ejecutables `.exe` |

---

## Ejecución en desarrollo

### Consola del Profesor

```bash
python -m ui.main_window
```

### Agente del Estudiante

```bash
python -m client.agent --server-ip <IP_DEL_PROFESOR>
```

| Parámetro | Por defecto | Descripción |
|---|---|---|
| `--server-ip` | `127.0.0.1` | IP o hostname del PC del profesor |
| `--server-port` | `9000` | Puerto TCP del servidor |
| `--client-id` | Automático | Identificador manual del cliente |

---

## Build y Distribución

### Compilar los ejecutables

**Opción 1 — Script batch:**
```bat
build\build.bat
```

**Opción 2 — PowerShell:**
```powershell
# Compilar todo
.\build\build.ps1

# Solo el servidor
.\build\build.ps1 -ServerOnly

# Solo el agente
.\build\build.ps1 -AgentOnly

# Limpiar y recompilar
.\build\build.ps1 -Clean
```

Los ejecutables quedan en la carpeta `build\dist\`:

| Archivo | Destino | Admin requerido |
|---|---|---|
| `DLSlab_Server.exe` | PC del Profesor | No |
| `DLSlab_Agent.exe` | PCs de Alumnos | Sí (para políticas web) |

### Instaladores (Inno Setup)

Los scripts en `installer\` generan instaladores `.exe` listos para distribuir:

| Script | Instalador generado |
|---|---|
| `installer\setup_server.iss` | `DLSlab_Server_Setup_v1.0.exe` |
| `installer\setup_agent.iss` | `DLSlab_Agent_Setup_v1.0.exe` |

El instalador del agente solicita la IP del servidor, crea `C:\ProgramData\DLSlab\config.ini` y registra una **Tarea Programada** de Windows para el inicio automático.

### Despliegue masivo (PowerShell)

```powershell
$computers = @("PC-LAB-01", "PC-LAB-02", "PC-LAB-03")
foreach ($pc in $computers) {
    Copy-Item "DLSlab_Agent.exe" "\\$pc\C$\Program Files\DLSlab\"
    $config = "[server]`nhost = 192.168.1.100`nport = 9000"
    New-Item -Path "\\$pc\C$\ProgramData\DLSlab\" -ItemType Directory -Force
    Set-Content "\\$pc\C$\ProgramData\DLSlab\config.ini" $config
}
```

---

## Configuración del Agente

El agente lee su configuración desde `C:\ProgramData\DLSlab\config.ini`:

```ini
; DLSlab Agent — configuración
[server]
host = 192.168.1.100
port = 9000
```

Los parámetros de línea de comandos tienen prioridad sobre el archivo de configuración.

---

## Protocolo de mensajes

Todos los mensajes son **JSON en UTF-8 terminados en nueva línea** con la siguiente estructura:

```json
{
  "type":      "REGISTER",
  "client_id": "DESKTOP-ABC-1a2b3c4d",
  "payload":   { "hostname": "DESKTOP-ABC", "ip": "192.168.1.42" }
}
```

| Tipo | Dirección | Descripción |
|---|---|---|
| `REGISTER` | cliente → servidor | Registro con hostname, IP y MAC |
| `SCREENSHOT` | cliente → servidor | Miniatura JPEG en Base64 (cada 2 s) |
| `REMOTE_INPUT` | servidor → cliente | Eventos de ratón / teclado |
| `PING` / `PONG` | ambos | Heartbeat (cada 5 s) |
| `BLANK_SCREEN` | servidor → cliente | Bloquear pantalla con mensaje |
| `UNBLANK_SCREEN` | servidor → cliente | Desbloquear pantalla |
| `START_SHOW_TEACHER` | servidor → cliente | Iniciar recepción de pantalla del profesor |
| `STOP_SHOW_TEACHER` | servidor → cliente | Detener transmisión del profesor |
| `TEACHER_FRAME` | servidor → cliente | Frame JPEG de la pantalla del profesor |
| `START_SHOW_STUDENT` | servidor → cliente | Iniciar presentación de un compañero |
| `STOP_SHOW_STUDENT` | servidor → cliente | Detener presentación de compañero |
| `STUDENT_FRAME` | servidor → cliente | Frame JPEG de la pantalla del alumno presentador |
| `REQUEST_HIRES_SCREENSHOT` | servidor → cliente | Solicitar capturas en alta resolución (control remoto) |
| `STOP_HIRES_SCREENSHOT` | servidor → cliente | Detener capturas en alta resolución |
| `SET_APP_POLICY` | servidor → cliente | Aplicar política de apps (whitelist / blacklist) |
| `CLEAR_APP_POLICY` | servidor → cliente | Eliminar política de apps |
| `SET_WEB_POLICY` | servidor → cliente | Aplicar política web (bloqueo total / whitelist) |
| `CLEAR_WEB_POLICY` | servidor → cliente | Eliminar política web |
| `POLICY_VIOLATION` | cliente → servidor | Reporte de violación de política |
| `SHUTDOWN` | servidor → cliente | Apagar equipo con delay configurable |
| `RESTART` | servidor → cliente | Reiniciar equipo con delay configurable |
| `LOGOUT` | servidor → cliente | Cerrar sesión del usuario activo |
| `LOCK_WORKSTATION` | servidor → cliente | Bloquear estación de trabajo (Windows) |
| `OPEN_URL` | servidor → cliente | Abrir URL en el navegador predeterminado |
| `RUN_APP` | servidor → cliente | Ejecutar aplicación con argumentos |
| `CLIENT_MAC` | cliente → servidor | Reportar dirección MAC para Wake-on-LAN |
| `SEND_FILE` | servidor → cliente | Enviar archivo al Escritorio del estudiante |

---

## Estructura del proyecto

```
DLSlab/
├── requirements.txt
├── shared/
│   └── messages.py          # Dataclasses y serialización del protocolo JSON
├── server/
│   ├── main_server.py       # Servidor asyncio TCP — punto de entrada
│   ├── protocol.py          # Framing TCP (lectura/escritura de JSON por líneas)
│   ├── client_manager.py    # Registro y estado de clientes conectados
│   ├── screen_streamer.py   # Streamer de pantalla del profesor → alumnos
│   ├── student_streamer.py  # Relay de pantalla alumno → resto de la clase
│   └── wol_manager.py       # Gestión de MACs y envío de paquetes Wake-on-LAN
├── client/
│   ├── agent.py             # Agente del estudiante — punto de entrada
│   ├── screen_capture.py    # Captura de pantalla con mss + Pillow
│   ├── input_handler.py     # Ejecución de eventos de teclado/ratón con pynput
│   ├── blank_screen.py      # Superposición de bloqueo de pantalla
│   ├── teacher_display.py   # Ventana de recepción de pantalla del profesor
│   ├── student_display.py   # Ventana de recepción de presentación de compañero
│   ├── app_enforcer.py      # Enforcement de políticas de aplicaciones
│   ├── web_enforcer.py      # Enforcement de políticas web (archivo hosts)
│   └── power_manager.py     # Apagado, reinicio, bloqueo y cierre de sesión
└── ui/
    ├── main_window.py        # Ventana principal de la consola del profesor
    ├── thumbnail_widget.py   # Widget de miniatura individual por estudiante
    ├── policy_dialog.py      # Diálogo de configuración de políticas
    ├── power_dialog.py       # Diálogo de control de energía y ejecución remota
    ├── remote_desktop_window.py  # Ventana de control remoto
    ├── log_window.py         # Ventana de registro de violaciones de política
    └── splash_screen.py      # Pantalla de inicio
```

---

## Guía de uso rápido

### Para el Profesor

1. Instala y ejecuta `DLSlab_Server_Setup_v1.0.exe` (crea regla de Firewall automáticamente).
2. Abre **DLSlab Server** desde el Escritorio o Menú de Inicio.
3. Cuando los estudiantes enciendan sus equipos, sus miniaturas aparecerán en la cuadrícula.

**Acciones desde la barra de herramientas:**

| Botón | Función |
|---|---|
| 🔒 Bloquear pantalla | Muestra overlay negro con mensaje en equipos seleccionados |
| 🔓 Desbloquear | Quita el bloqueo de todos los equipos |
| 📡 Transmitir mi pantalla | Inicia transmisión de la pantalla del profesor |
| ⏹️ Detener transmisión | Detiene la transmisión activa |
| 🛡️ Políticas | Configura restricciones de apps y navegación web |
| ⚡ Energía | Apagado, reinicio, bloqueo, logout, URLs, apps y WoL |
| ⏻ Apagado de emergencia | Apaga todo el laboratorio con confirmación |
| 📤 Enviar archivo | Envía un documento al Escritorio de los estudiantes |
| 📋 Log de políticas | Ver registro de violaciones reportadas por los agentes |

### Para el Técnico (instalación del agente)

1. Copia `DLSlab_Agent_Setup_v1.0.exe` al equipo del estudiante.
2. Ejecuta el instalador como **Administrador**.
3. Ingresa la IP del PC del profesor cuando se solicite.
4. El agente quedará configurado para iniciarse automáticamente al iniciar sesión.

---

## Notas importantes

### Políticas web

La política web modifica el archivo `hosts` del sistema operativo:
- **Ruta:** `C:\Windows\System32\drivers\etc\hosts`
- **Backup automático:** `C:\Windows\System32\drivers\etc\hosts.dlslab.bak`
- **Requisito:** El agente debe ejecutarse con **permisos de Administrador**.
- Las políticas web permanecen activas aunque se pierda la conexión. El profesor debe enviar "Limpiar políticas" para restaurar el archivo `hosts` original.

> ⚠️ Algunos navegadores con DNS-over-HTTPS (DoH) activo ignoran el archivo `hosts`. Desactiva DoH en el navegador o aplica políticas de grupo para una cobertura completa.

### Wake-on-LAN

- Requiere WoL habilitado en BIOS/UEFI y en la NIC del equipo.
- El equipo debe haberse conectado al menos una vez para registrar su MAC en `ui/macs.json`.
- Solo funciona en redes cableadas (generalmente no funciona por WiFi).

### Firewall

El instalador del servidor crea automáticamente una regla de Firewall de Windows para el **puerto 9000 TCP**. En entornos de dominio, una política de grupo podría revertir esta regla.

---

## Licencia

MIT
