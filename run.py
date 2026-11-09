"""Start the docking app and open it in the browser:  python run.py [--port 8801] [--no-browser]"""
import argparse
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "app"))

import uvicorn  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    url = f"http://{a.host}:{a.port}"
    if not a.no_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Docking app running at {url}  (Ctrl+C to stop)")
    uvicorn.run("server:app", host=a.host, port=a.port, log_level="warning")
