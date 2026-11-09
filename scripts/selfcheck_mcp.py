r"""Checks the MCP server speaks the protocol and reaches the engine.  python scripts\selfcheck_mcp.py

Does not dock anything: it starts the server, runs a handshake, lists the tools and asks for a job
that does not exist, which must come back as a readable error rather than a crash.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "mcp" / "akadock_mcp.py"


def main() -> int:
    p = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)

    def call(mid, method, params=None):
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method,
                                  "params": params or {}}) + "\n")
        p.stdin.flush()
        return json.loads(p.stdout.readline())

    try:
        r = call(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "selfcheck", "version": "1"}})
        assert r["result"]["protocolVersion"] == "2025-06-18", r
        assert r["result"]["serverInfo"]["name"] == "akadock", r
        assert "self-check" in r["result"]["instructions"], "the verdict rule must be in instructions"
        print("handshake OK")

        p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        p.stdin.flush()

        names = [t["name"] for t in call(2, "tools/list")["result"]["tools"]]
        assert names == ["akadock_load_protein", "akadock_dock", "akadock_status",
                         "akadock_results", "akadock_files"], names
        print("tools:", ", ".join(names))

        r = call(3, "tools/call", {"name": "akadock_status", "arguments": {"job_id": "no-such-job"}})
        assert r["result"].get("isError"), "a missing job must be an error, not a result"
        print("engine reachable, unknown job refused:", r["result"]["content"][0]["text"][:60])

        r = call(4, "nonsense/method")
        assert r["error"]["code"] == -32601, r
        print("unknown method OK")
    finally:
        p.stdin.close()
        p.wait(timeout=20)
    print("OK: MCP server healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
