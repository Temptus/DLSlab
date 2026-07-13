# Changelog

Todos los cambios notables del proyecto DLSlab se documentan en este archivo.

El formato se basa en [Keep a Changelog](https://keepachangelog.com/es/1.1.0/)
y el proyecto sigue [Versionado Semántico](https://semver.org/lang/es/).

---

## [Unreleased]

> Cambios en desarrollo que aún no forman parte de una versión publicada.

---

## [1.0.1] — 2026-07-13

### Fixed

- **`client/blank_screen.py`** — Buffer overflow en `PAINTSTRUCT` al usar el
  overlay de bloqueo de pantalla (`BLANK_SCREEN`).  
  En Windows x64 `sizeof(PAINTSTRUCT)` es **72 bytes**, pero el código usaba
  `ctypes.create_string_buffer(64)` (64 bytes). Cada vez que Windows enviaba
  el mensaje `WM_PAINT`, `BeginPaint` escribía 8 bytes más allá del buffer,
  corrompiendo el heap de Python de forma silenciosa. La corrupción se
  manifestaba segundos o minutos después como una violación de acceso
  (`0xc0000005`) dentro de `python313.dll`, cerrando el agente sin mensaje de
  error visible.  
  **Solución:** `PAINTSTRUCT` se definió como `ctypes.Structure` con los
  campos correctos y `BeginPaint`/`EndPaint` reciben el puntero mediante
  `ctypes.byref()`. Adicionalmente, el callback nativo `wnd_proc_cb` se
  almacena como `self._wnd_proc_cb` para garantizar que el recolector de
  basura no lo elimine mientras la ventana exista.

---

## [1.0.0] — 2026-07-02

### Added

- Módulo cliente-servidor base (TCP, JSON, reconexión con backoff exponencial).
- Captura y transmisión de screenshots en miniatura desde los agentes.
- Control remoto de ratón y teclado vía `pynput`.
- **Blank Screen** — overlay nativo de Windows para bloquear/desbloquear la
  pantalla de las terminales desde el servidor.
- **Show Teacher** — transmisión de la pantalla del profesor a todos los
  estudiantes en tiempo real.
- **Show Student** — presentación de la pantalla de un estudiante al resto del
  aula.
- **Toma de control** — el profesor puede tomar el control completo de una
  terminal individual (mouse y teclado).
- **Políticas de aplicaciones** — modo whitelist y blacklist de procesos con
  log de violaciones en la UI.
- **Políticas web** — bloqueo total de navegadores o whitelist de URLs
  mediante proxy local.
- **Control de energía** — apagado, reinicio, cierre de sesión y bloqueo de
  estación de trabajo remoto; soporte Wake-on-LAN.
- **Envío de archivos** — el profesor puede enviar un archivo al escritorio de
  todas las terminales o de una terminal individual.
- **Icono de bandeja del sistema** en el agente con indicador de estado de
  conexión (conectado / conectando / desconectado), sin opción de cierre para
  los estudiantes.
- Sistema de empaquetado con PyInstaller para generar
  `DLSlab_Server.exe` y `DLSlab_Agent.exe`.
- Soporte de configuración vía `%ProgramData%\DLSlab\config.ini`.
- ID de cliente estable basado en hostname + MAC address para evitar
  duplicados al reconectar.
- Tema claro / oscuro persistido en el registro de Windows.
- Eliminación automática de terminales duplicadas al desconectarse.

---

[Unreleased]: https://github.com/Temptus/DLSlab/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/Temptus/DLSlab/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/Temptus/DLSlab/releases/tag/v1.0.0
