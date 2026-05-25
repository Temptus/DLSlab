# DLSlab 🖥️

**DLSlab** is a classroom laboratory management system built in Python for
**Windows 10/11**.  It allows a teacher to monitor student screens in real
time, send remote keyboard/mouse input, and push administrative commands
(shutdown, open URL, etc.) to any or all student PCs on the local network.

---

## Architecture

```
┌─────────────────────────────────┐
│     CONSOLA DEL PROFESOR        │  ← PyQt6 (UI)
│     (Servidor Central)          │  ← asyncio server
└──────────────┬──────────────────┘
               │  TCP Sockets (port 9000)
    ┌──────────┴──────────┐
    ▼          ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐
│Agente 1│ │Agente 2│ │Agente N│  ← Python script
│Cliente │ │Cliente │ │Cliente │     running as a service
└────────┘ └────────┘ └────────┘
```

---

## System Requirements

- **OS:** Windows 10 / 11 (also runs on Linux/macOS for development)
- **Python:** 3.11 or newer
- **Network:** All machines on the same LAN

---

## Installation

```bash
pip install -r requirements.txt
```

---

## Running the Teacher Server (professor console)

```bash
python -m server.main_server
```

Or launch the full PyQt6 GUI:

```bash
python -m ui.main_window
```

---

## Running the Student Agent

On each student PC, run:

```bash
python -m client.agent --server-ip 192.168.x.x
```

| Flag            | Default     | Description                         |
|-----------------|-------------|-------------------------------------|
| `--server-ip`   | `127.0.0.1` | IP address of the teacher's machine |
| `--server-port` | `9000`      | TCP port the server listens on      |
| `--client-id`   | auto        | Override the auto-generated ID      |

---

## Módulos Implementados

| Módulo | Estado | Descripción |
|--------|--------|-------------|
| Comunicación Cliente-Servidor | ✅ | TCP asyncio, puerto 9000, protocolo JSON |
| Thumbnails en Tiempo Real | ✅ | Captura 320×180, JPEG 40%, 2s intervalo |
| Control Remoto | ✅ | Teclado y ratón vía pynput |
| Bloqueo de Pantallas | ✅ | Blank screen + bloqueo de input |
| Transmisión Pantalla Profesor | ✅ | Show Teacher, 10 FPS, 1280×720 |
| Pantalla de Alumno | ✅ | Show Student, relay de frames |
| Limitación Apps y Web | ✅ | Whitelist/Blacklist con psutil + hosts |
| Control de Energía | ✅ | Apagado, reinicio, WoL, ejecución remota |

## Arquitectura final

- **`shared/`**: protocolo de mensajes y serialización JSON.
- **`server/`**: servidor asyncio, registro de clientes, streamers (teacher/student),
  políticas y módulo de energía/WoL (`wol_manager.py` + persistencia `macs.json`).
- **`client/`**: agente de alumno con captura, input remoto, bloqueo de pantalla,
  visualización de transmisiones, enforcement de políticas y control de energía
  (`power_manager.py`).
- **`ui/`**: consola PyQt6 del profesor con thumbnails en tiempo real, diálogos de
  bloqueo/transmisión/políticas/energía y botones rápidos de emergencia.

## Uso como administrador en Windows

1. Instala dependencias:
   ```bash
   pip install -r requirements.txt
   ```
2. Ejecuta la consola del profesor:
   ```bash
   python -m ui.main_window
   ```
3. Ejecuta cada agente de alumno (idealmente como **Administrador** para políticas web):
   ```bash
   python -m client.agent --server-ip <IP_DEL_PROFESOR>
   ```
4. Desde la toolbar:
   - `⚡ Energía`: abre apagado/reinicio/bloqueo/logout, apertura de URL/APP y WoL.
   - `🔴 Apagar Todo`: apagado de emergencia con confirmación.
5. Para Wake-on-LAN, habilita WoL en BIOS/UEFI y en la NIC de Windows.

---

## 📦 Build y Distribución

### Requisitos para compilar
- Windows 10/11
- Python 3.11+
- Las dependencias de `requirements.txt`

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

### Archivos generados
Tras el build, encontrarás en la carpeta `dist\`:

| Archivo | Destino | Admin requerido |
|---------|---------|----------------|
| `DLSlab_Server.exe` | PC del Profesor | No |
| `DLSlab_Agent.exe` | PCs de Alumnos | Sí |

### Despliegue del agente
1. Copiar `DLSlab_Agent.exe` a cada PC del laboratorio
2. Ejecutar como Administrador
3. Al iniciar: `DLSlab_Agent.exe --server-ip 192.168.x.x`

### Despliegue masivo (PowerShell + red compartida)
```powershell
# Copiar agente a todos los equipos del laboratorio
$computers = @("PC-LAB-01", "PC-LAB-02", "PC-LAB-03")  # etc.
foreach ($pc in $computers) {
    Copy-Item "dist\DLSlab_Agent.exe" "\\$pc\C$\DLSlab\"
}
```

---

## Message Protocol

All messages are **newline-terminated UTF-8 JSON** with this structure:

```json
{
  "type":      "REGISTER",
  "client_id": "DESKTOP-ABC-1a2b3c4d",
  "payload":   { "hostname": "DESKTOP-ABC", "ip": "192.168.1.42" }
}
```

| `type`           | Direction         | Description                                     |
|------------------|-------------------|-------------------------------------------------|
| `REGISTER`       | client → server   | Announce hostname and IP on connect             |
| `SCREENSHOT`     | client → server   | Base64 JPEG thumbnail (every 2 s)               |
| `REMOTE_INPUT`   | server → client   | Mouse / keyboard events                         |
| `PING`           | both              | Heartbeat (every 5 s)                           |
| `PONG`           | both              | Heartbeat reply                                 |
| `COMMAND`        | server → client   | Admin commands (shutdown, open_url, …)          |
| `BLANK_SCREEN`   | server → client   | Lock screen with overlay message                |
| `UNBLANK_SCREEN` | server → client   | Restore screen (remove overlay)                 |
| `SET_APP_POLICY` | server → client   | Apply app policy (whitelist / blacklist)        |
| `CLEAR_APP_POLICY` | server → client | Remove app policy restrictions                  |
| `SET_WEB_POLICY` | server → client   | Apply web policy (block all / URL whitelist)    |
| `CLEAR_WEB_POLICY` | server → client | Remove web policy restrictions                  |
| `POLICY_VIOLATION` | client → server | Report blocked app/web policy violation         |
| `SHUTDOWN`       | server → client   | Apagar equipo con delay configurable            |
| `RESTART`        | server → client   | Reiniciar equipo con delay configurable         |
| `LOGOUT`         | server → client   | Cerrar sesión del usuario activo                |
| `LOCK_WORKSTATION` | server → client | Bloquear estación de trabajo                    |
| `OPEN_URL`       | server → client   | Abrir URL en navegador predeterminado           |
| `RUN_APP`        | server → client   | Ejecutar aplicación con argumentos              |
| `CLIENT_MAC`     | client → server   | Reportar MAC para Wake-on-LAN                   |

---

## Project Structure

```
DLSlab/
├── requirements.txt
├── shared/
│   ├── __init__.py
│   └── messages.py          # Message dataclasses + serialisation helpers
├── server/
│   ├── __init__.py
│   ├── protocol.py          # TCP framing (read/write newline-delimited JSON)
│   ├── client_manager.py    # ClientInfo registry
│   └── main_server.py       # asyncio TCP server entry point
├── client/
│   ├── __init__.py
│   ├── app_enforcer.py      # Whitelist/blacklist process enforcement
│   ├── screen_capture.py    # mss + Pillow screenshot capture
│   ├── input_handler.py     # pynput remote input execution
│   ├── web_enforcer.py      # Browser blocking + URL whitelist policy
│   └── agent.py             # Student agent entry point
└── ui/
    ├── __init__.py
    ├── policy_dialog.py     # App/Web policy configuration dialog
    ├── thumbnail_widget.py  # Single student thumbnail widget
    └── main_window.py       # Teacher console main window
```

---

## License

MIT

---

## Nota sobre políticas web en Windows

La política web con whitelist modifica el archivo hosts en:
`C:\Windows\System32\drivers\etc\hosts` (con backup en
`C:\Windows\System32\drivers\etc\hosts.dlslab.bak`).

Para que esta operación funcione, el agente de alumno debe ejecutarse con
**permisos de Administrador** en Windows.
