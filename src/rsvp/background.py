"""Run the reader in the background (no window to keep open), stop it, and start it at login.

`rsvp start` launches the server as a detached, windowless process, waits until it answers,
opens the browser, and exits. The server keeps running until you click Quit in the library
(or run `rsvp stop`), log out, or shut down. Its output goes to a log file.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from .library import PROJECT_ROOT

APP_LABEL = "com.rsvp-reader"           # macOS LaunchAgent label
SHORTCUT_NAME = "RSVP Reader.lnk"       # Windows Startup-folder shortcut
SHUTDOWN_HEADER = {"X-RSVP-Reader": "1"}  # custom header: browsers block it cross-site


def state_dir() -> Path:
    """Per-computer state (log file), next to the Python environment the launchers create."""
    d = Path(os.environ.get("RSVP_STATE_DIR") or Path.home() / ".rsvp-reader")
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_path() -> Path:
    return state_dir() / "server.log"


def url_for(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def is_running(url: str, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/settings", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _server_python() -> str:
    """The interpreter to run the server with: pythonw on Windows, so no console window appears."""
    exe = Path(sys.executable)
    if os.name == "nt":
        w = exe.with_name("pythonw.exe")
        if w.exists():
            return str(w)
    return str(exe)


def server_command(port: int, library: Path | None = None) -> list[str]:
    cmd = [_server_python(), "-m", "rsvp.cli", "serve", "--port", str(port)]
    if library:
        cmd += ["--library", str(library)]
    return cmd


def start(host: str = "127.0.0.1", port: int = 8765, library: Path | None = None,
          open_browser: bool = True, wait: float = 90.0, log=print) -> int:
    url = url_for(host, port)
    if is_running(url):
        log(f"RSVP Reader is already running at {url}")
        if open_browser:
            webbrowser.open(url)
        return 0

    logf = open(log_path(), "a", encoding="utf-8")
    logf.write(f"\n--- starting {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
    logf.flush()
    kwargs: dict = dict(cwd=str(PROJECT_ROOT), stdin=subprocess.DEVNULL, stdout=logf,
                        stderr=subprocess.STDOUT, close_fds=True)
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        try:  # also leave the launching window's job, so closing that window can't take us down
            proc = subprocess.Popen(server_command(port, library), creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kwargs)
        except OSError:
            proc = subprocess.Popen(server_command(port, library), creationflags=flags, **kwargs)
    else:
        proc = subprocess.Popen(server_command(port, library), start_new_session=True, **kwargs)
    logf.close()

    log("Starting RSVP Reader in the background…")
    deadline = time.time() + wait
    while time.time() < deadline:
        if is_running(url, timeout=1.0):
            log(f"RSVP Reader is running at {url}. You can close this window.")
            log("To stop it, click Quit in the library page.")
            if open_browser:
                webbrowser.open(url)
            return 0
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    log("RSVP Reader didn't start. The end of its log:")
    try:
        log("".join(log_path().read_text(encoding="utf-8", errors="replace").splitlines(True)[-15:]))
    except OSError:
        pass
    return 1


def stop(host: str = "127.0.0.1", port: int = 8765, log=print) -> int:
    url = url_for(host, port)
    if not is_running(url):
        log("RSVP Reader isn't running.")
        return 0
    req = urllib.request.Request(url + "/api/shutdown", method="POST", headers=SHUTDOWN_HEADER, data=b"")
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass
    for _ in range(20):
        if not is_running(url, timeout=0.5):
            log("RSVP Reader stopped.")
            return 0
        time.sleep(0.25)
    log("RSVP Reader didn't stop; close it from Task Manager / Activity Monitor (it's a Python process).")
    return 1


# --- start automatically at login ----------------------------------------------------------

def _startup_dir_windows() -> Path:
    if os.environ.get("RSVP_STARTUP_DIR"):
        return Path(os.environ["RSVP_STARTUP_DIR"])
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _launch_agent_path() -> Path:
    base = Path(os.environ["RSVP_STARTUP_DIR"]) if os.environ.get("RSVP_STARTUP_DIR") else Path.home() / "Library" / "LaunchAgents"
    return base / f"{APP_LABEL}.plist"


def autostart_supported() -> bool:
    return os.name == "nt" or sys.platform == "darwin"


def autostart_enabled() -> bool:
    if os.name == "nt":
        return (_startup_dir_windows() / SHORTCUT_NAME).exists()
    if sys.platform == "darwin":
        return _launch_agent_path().exists()
    return False


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def set_autostart(on: bool) -> bool:
    """Create/remove a login item that starts the server in the background. Returns the new state."""
    port_args = ["-m", "rsvp.cli", "serve"]
    if os.name == "nt":
        lnk = _startup_dir_windows() / SHORTCUT_NAME
        if not on:
            lnk.unlink(missing_ok=True)
            return False
        lnk.parent.mkdir(parents=True, exist_ok=True)
        script = (
            "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + _ps_quote(str(lnk)) + "); "
            "$s.TargetPath = " + _ps_quote(_server_python()) + "; "
            "$s.Arguments = " + _ps_quote(" ".join(port_args)) + "; "
            "$s.WorkingDirectory = " + _ps_quote(str(PROJECT_ROOT)) + "; "
            "$s.Description = 'Starts RSVP Reader in the background'; $s.Save()"
        )
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        return lnk.exists()
    if sys.platform == "darwin":
        plist = _launch_agent_path()
        if not on:
            plist.unlink(missing_ok=True)  # takes effect at next login; doesn't stop the running reader
            return False
        plist.parent.mkdir(parents=True, exist_ok=True)
        from xml.sax.saxutils import escape
        args = "".join(f"<string>{escape(a)}</string>" for a in [sys.executable, *port_args])
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{APP_LABEL}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>WorkingDirectory</key><string>{escape(str(PROJECT_ROOT))}</string>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{escape(str(log_path()))}</string>
  <key>StandardErrorPath</key><string>{escape(str(log_path()))}</string>
</dict></plist>
""", encoding="utf-8")
        return True
    raise RuntimeError("Starting at login is only supported on Windows and macOS.")
