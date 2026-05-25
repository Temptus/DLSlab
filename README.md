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

## Module Overview

| Module                        | Status      | Description                                             |
|-------------------------------|-------------|---------------------------------------------------------|
| `shared/messages.py`          | ✅ Done     | JSON message protocol (dataclasses + helpers)           |
| `server/protocol.py`          | ✅ Done     | TCP framing / newline-delimited message I/O             |
| `server/client_manager.py`    | ✅ Done     | Registry of connected student agents                    |
| `server/main_server.py`       | ✅ Done     | asyncio TCP server, dispatches all message types        |
| `client/screen_capture.py`    | ✅ Done     | mss + Pillow screenshot → 320×180 JPEG → base64         |
| `client/input_handler.py`     | ✅ Done     | pynput — executes remote mouse/keyboard events          |
| `client/agent.py`             | ✅ Done     | Student agent: register, screenshot loop, PING, commands|
| `client/blank_screen.py`      | ✅ Done     | Fullscreen overlay + pynput input blocking (suppress)   |
| `ui/thumbnail_widget.py`      | ✅ Done     | PyQt6 widget: image + hostname + status dot + lock icon |
| `ui/main_window.py`           | ✅ Done     | PyQt6 grid + 🔒/🔓 toolbar buttons + lock dialog        |
| Wake-on-LAN                   | 🔜 Planned  | Send WoL magic packets via `wakeonlan`                  |
| URL whitelist / blacklist      | ✅ Done     | App + web policy controls (whitelist/blacklist)         |
| Screen broadcast (teacher→all)| 🔜 Planned  | Stream teacher's screen to all students                 |
| Auth / TLS                    | 🔜 Planned  | Shared-secret + TLS encryption for production use       |

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
