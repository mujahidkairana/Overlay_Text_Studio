from __future__ import annotations

import argparse
import hashlib
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


HOST = "127.0.0.1"
FIRST_PORT = 8501
LAST_PORT = 65535
STARTUP_TIMEOUT = 180
ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "streamlit.log"
PORT_FILE = LOG_DIR / "current_port.txt"
PID_FILE = LOG_DIR / "streamlit.pid"
SOURCE_STAMP_FILE = LOG_DIR / "source_fingerprint.txt"


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _source_fingerprint() -> str:
    digest = hashlib.sha256()
    sources = [ROOT / "app.py", ROOT / "start_overlay.py"]
    sources.extend(sorted((ROOT / "overlay_studio").glob("*.py")))
    for source in sources:
        digest.update(source.name.encode("utf-8"))
        digest.update(source.read_bytes())
    return digest.hexdigest()


def _ready(port: int) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://{HOST}:{port}/_stcore/health", timeout=1.2
        ) as response:
            return response.status == 200 and b"ok" in response.read(64).lower()
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _pid_running(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _owned_streamlit_process(pid: int | None) -> bool:
    if not _pid_running(pid):
        return False
    if os.name != "nt":
        return True
    script = (
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}'; "
        "if($p){$p.CommandLine}"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    command = result.stdout.lower().replace("/", "\\")
    return "streamlit" in command and str((ROOT / "app.py").resolve()).lower() in command


def _stop_owned_server(pid: int | None, timeout: float = 8.0) -> bool:
    if not _owned_streamlit_process(pid):
        return False
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"], capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        os.kill(int(pid), 15)
    deadline = time.monotonic() + timeout
    while _pid_running(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    return not _pid_running(pid)


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((HOST, port))
            return True
        except OSError:
            return False


def _find_port() -> int:
    for port in range(FIRST_PORT, LAST_PORT + 1):
        if _port_available(port):
            return port
        if port < FIRST_PORT + 5:
            print(f"Port {port} is busy; trying {port + 1}...")
    raise RuntimeError("No free local port could be found.")


def _open(port: int) -> None:
    url = f"http://{HOST}:{port}"
    print(f"Opening {url}")
    webbrowser.open(url, new=2)


def run(no_browser: bool = False) -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    saved_port = _read_int(PORT_FILE)
    saved_pid = _read_int(PID_FILE)
    current_source = _source_fingerprint()
    try:
        saved_source = SOURCE_STAMP_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        saved_source = ""
    same_source = saved_source == current_source
    if saved_port and same_source and (_ready(saved_port) or _pid_running(saved_pid)):
        for _ in range(45):
            if _ready(saved_port):
                print(f"Overlay Text Studio is already running on port {saved_port}.")
                if not no_browser:
                    _open(saved_port)
                return 0
            time.sleep(1)
    elif saved_port and not same_source and (_ready(saved_port) or _pid_running(saved_pid)):
        if _stop_owned_server(saved_pid):
            print("App code changed; safely replaced the previous Overlay Text Studio server.")
        else:
            print("Saved process could not be verified as Overlay Text Studio; it was not stopped.")

    port = _find_port()
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ROOT / "app.py"),
        f"--server.address={HOST}",
        f"--server.port={port}",
        "--server.headless=true",
        "--server.fileWatcherType=none",
        "--global.developmentMode=false",
        "--browser.gatherUsageStats=false",
    ]
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
    with LOG_FILE.open("ab", buffering=0) as log:
        log.write(f"\n===== Start {time.strftime('%Y-%m-%d %H:%M:%S')} port {port} =====\n".encode())
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=flags,
            start_new_session=os.name != "nt",
        )
    PORT_FILE.write_text(str(port), encoding="utf-8")
    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    SOURCE_STAMP_FILE.write_text(current_source, encoding="utf-8")
    for _ in range(STARTUP_TIMEOUT):
        if _ready(port):
            if not no_browser:
                _open(port)
            return 0
        if process.poll() is not None:
            break
        time.sleep(1)
    print(f"App did not become ready. Review {LOG_FILE}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    return run(parser.parse_args().no_browser)


if __name__ == "__main__":
    raise SystemExit(main())
