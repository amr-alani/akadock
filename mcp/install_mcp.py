r"""Register the AKADock MCP server with Claude Desktop.  python mcp\install_mcp.py [--remove]

Adds one entry to %APPDATA%\Claude\claude_desktop_config.json, keeping everything already in it and
writing a .bak first. Restart Claude Desktop afterwards. For Claude Code, the command this prints
is all you need - there is no config file to edit.
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp" / "akadock_mcp.py"
PYTHON = ROOT / "python" / "python.exe"          # the bundled one: no system Python needed
CONFIG = Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
NAME = "akadock"


def main() -> int:
    remove = "--remove" in sys.argv
    python = PYTHON if PYTHON.exists() else Path(sys.executable)
    if not SERVER.exists():
        print(f"Not found: {SERVER}", file=sys.stderr)
        return 1

    cfg = {}
    if CONFIG.exists():
        try:
            cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        except ValueError:
            print(f"{CONFIG} is not valid JSON. Fix or delete it first.", file=sys.stderr)
            return 1
        shutil.copy2(CONFIG, CONFIG.with_suffix(".json.bak"))

    servers = cfg.setdefault("mcpServers", {})
    if remove:
        if servers.pop(NAME, None) is None:
            print("AKADock was not registered.")
            return 0
    else:
        servers[NAME] = {"command": str(python), "args": [str(SERVER)]}

    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(("Removed from " if remove else "Added to ") + str(CONFIG))
    print("Restart Claude Desktop for the change to take effect.\n")
    print("For Claude Code, run this once instead:")
    print(f'  claude mcp add akadock --scope user -- "{python}" "{SERVER}"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
