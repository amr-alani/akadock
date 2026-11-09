"""AKADock as an MCP server: lets Claude (or any MCP client) run real dockings locally.

    python mcp\akadock_mcp.py            speaks MCP over stdin/stdout, nothing to install

It is a thin wrapper around the HTTP API the app already serves on 127.0.0.1:8801, so the science,
the file layout and the self-check are exactly those of the normal app. If the engine is not
running it is started in the background with the bundled Python.

Standard library only, on purpose: the MCP client starts this file directly with AKADock's own
python.exe, so there is nothing for the user to pip install. The protocol is small enough
(initialize / tools/list / tools/call over newline-delimited JSON-RPC) that an SDK would be the
larger dependency.

Only stderr may be used for messages: stdout carries the protocol.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

def _find_akadock() -> Path:
    """Where AKADock is installed. Run from inside the folder this is the parent directory, but a
    Claude Desktop bundle or a plugin lives somewhere else entirely, so look the install up."""
    here = Path(__file__).resolve().parent.parent
    seen = [Path(os.environ["AKADOCK_HOME"])] if os.environ.get("AKADOCK_HOME") else []
    seen.append(here)
    try:                                                    # what the installer recorded
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AKADock") as k:
            seen.append(Path(winreg.QueryValueEx(k, "InstallLocation")[0]))
    except Exception:  # noqa: BLE001
        pass
    local = os.environ.get("LOCALAPPDATA")
    if local:
        seen.append(Path(local) / "Programs" / "AKADock")
    for p in seen:
        if (p / "run.py").exists():
            return p
    return seen[0]                                          # report the failure against what was asked for


ROOT = _find_akadock()
BASE = os.environ.get("AKADOCK_URL", "http://127.0.0.1:8801").rstrip("/")
VERSION = "1.1"
PROTOCOL = "2025-06-18"
START_TIMEOUT = 90


def log(msg: str) -> None:
    print(f"[akadock-mcp] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- the engine

class EngineError(Exception):
    """Something the user needs to read: a bad PDB ID, an invalid SMILES, a dead engine."""


def api(path: str, body: dict | None = None, method: str | None = None, timeout: float = 300):
    req = urllib.request.Request(
        f"{BASE}{path}", method=method or ("POST" if body is not None else "GET"),
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:                     # FastAPI puts the message in "detail"
        try:
            detail = json.loads(e.read().decode("utf-8")).get("detail")
        except Exception:  # noqa: BLE001
            detail = None
        if e.code == 404 and (not detail or detail == "Not Found"):
            detail = f"AKADock has no record of this ({path.rsplit('/', 1)[-1]}). Check the id."
        raise EngineError(detail or f"AKADock refused the request ({e.code}).")
    except urllib.error.URLError as e:
        raise EngineError(f"AKADock is not answering on {BASE}: {e.reason}")


def engine_up() -> bool:
    try:
        return bool(api("/api/ping", timeout=3).get("ok"))
    except EngineError:
        return False


def ensure_engine() -> None:
    """Start the app in the background if it is not already running."""
    if engine_up():
        return
    if BASE != "http://127.0.0.1:8801":                     # someone else's engine: not ours to start
        raise EngineError(f"No AKADock engine is running at {BASE}.")
    python = ROOT / "python" / "pythonw.exe"
    if not python.exists():
        python = Path(sys.executable)
    log(f"starting the engine with {python}")
    subprocess.Popen([str(python), str(ROOT / "run.py"), "--port", "8801", "--no-browser"],
                     cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(START_TIMEOUT):
        if engine_up():
            log("engine up")
            return
        time.sleep(1)
    raise EngineError("AKADock did not start within 90 seconds. Open AKADock once by hand and retry.")


# ---------------------------------------------------------------- tools

def _ligands(info: dict) -> list[dict]:
    return [{"ligand_key": l.get("key"), "name": l.get("resname"), "chain": l.get("chain"),
             "heavy_atoms": l.get("heavy_atoms"), "has_metal": l.get("has_metal")}
            for l in info.get("ligands", [])]


def load_protein(pdb_id: str = "", file_path: str = "") -> dict:
    if pdb_id:
        info = api("/api/protein/fetch", {"pdb_id": pdb_id})
    elif file_path:
        raise EngineError("Uploading a local structure is not available through MCP yet: load the "
                          "file once in the AKADock window, then use its protein_id here.")
    else:
        raise EngineError("Give a pdb_id, for example 4EY7.")
    ligs = _ligands(info)
    return {
        "protein_id": info.get("id"),
        "title": info.get("title"),
        "ligands": ligs,
        "suggested_ligand_key": ligs[0]["ligand_key"] if ligs else None,
        "note": "The co-crystallized ligand defines the binding site. With no ligand, pass "
                "site_residues instead - but then the run cannot validate itself.",
    }


def dock(protein_id: str, compounds: list[dict], ligand_key: str = "", site_residues: str = "",
         exhaustiveness: int = 8, padding: float = 10.0, num_modes: int = 9,
         protonate: bool = False, keep_waters: bool = False, flex_residues: str = "",
         covalent_residue: str = "", redock: bool = True, dock_reference: bool = True) -> dict:
    req = {"protein_id": protein_id, "ligand_key": ligand_key, "site_residues": site_residues,
           "compounds": [{"name": c.get("name", ""), "smiles": c["smiles"]} for c in compounds],
           "exhaustiveness": exhaustiveness, "padding": padding, "num_modes": num_modes,
           "protonate": protonate, "keep_waters": keep_waters, "flex_residues": flex_residues,
           "covalent_residue": covalent_residue, "redock": redock, "dock_reference": dock_reference}
    jid = api("/api/jobs", req)["id"]
    return {
        "job_id": jid,
        "n_compounds": len(compounds),
        "validation": "on" if (redock and ligand_key) else "off - no crystal pose to compare with",
        "next": "Docking runs in the background, about 20-40 s per compound at exhaustiveness 8. "
                "Poll akadock_status, then read akadock_results.",
    }


def _verdict(job: dict) -> dict:
    v = job.get("validation") or {}
    verdict = (v.get("verdict") or "").upper()
    rmsd = (v.get("rmsds") or [None])[0]
    return {
        "verdict": verdict or "NONE",
        "top_pose_rmsd_A": round(rmsd, 2) if isinstance(rmsd, (int, float)) else None,
        "redock_affinity_kcal_mol": v.get("best"),
        "text": v.get("verdict_text") or ("The run was not validated: no co-crystallized ligand was "
                                          "redocked, so there is nothing to check the setup against."),
    }


def _trust(verdict: str) -> tuple[bool, str]:
    if verdict == "PASS":
        return True, ("The setup reproduces the crystal pose, so the ranking is worth reading. "
                      "Differences under about 1 kcal/mol are still inside the method's error.")
    if verdict == "WARN":
        return False, ("The crystal pose is among the top three but was not ranked first. Vina finds "
                       "the right answer here but does not score it best: treat small differences "
                       "between compounds as noise and say so in any summary.")
    if verdict == "FAIL":
        return False, ("The run FAILED its own self-check: the redocked ligand did not reproduce the "
                       "crystal pose. Do not rank these compounds and do not report the scores as "
                       "meaningful. Say the validation failed.")
    return False, ("This run was not validated - there was no crystal ligand to redock. The scores "
                   "cannot be checked against anything, and must be reported that way.")


def status(job_id: str) -> dict:
    job = api(f"/api/jobs/{job_id}")
    done = sum(1 for r in job.get("results", []) if r.get("status") == "done")
    out = {
        "job_id": job_id, "status": job.get("status"), "step": job.get("step"),
        "progress_percent": job.get("progress"),
        "docked": f"{done} of {len(job.get('results', []))}",
        "validation": _verdict(job),
    }
    if job.get("error"):
        out["error"] = job["error"]
    if job.get("status") in ("queued", "running"):
        out["next"] = "Not finished: poll again in 30 s rather than reporting partial results."
    return out


def results(job_id: str, limit: int = 25) -> dict:
    job = api(f"/api/jobs/{job_id}")
    val = _verdict(job)
    trust, advice = _trust(val["verdict"])
    rows = []
    for r in sorted((r for r in job.get("results", []) if r.get("status") == "done"),
                    key=lambda r: r.get("best", 0)):
        p = r.get("props") or {}
        residues = sorted({i["residue"] for i in r.get("interactions", [])})
        rows.append({"name": r.get("name"), "smiles": r.get("smiles"),
                     "affinity_kcal_mol": r.get("best"), "ligand_efficiency": r.get("le"),
                     "rmsd_to_crystal_A": (r.get("rmsds") or [None])[0],
                     "mw": p.get("mw"), "clogp": p.get("logp"),
                     "lipinski_violations": p.get("lipinski_violations"),
                     "contacts": residues,
                     "covalent": r.get("covalent")})
    failed = [{"name": r.get("name"), "error": r.get("error")}
              for r in job.get("results", []) if r.get("status") == "failed"]
    return {
        "validation": val,
        "trust_ranking": trust,
        "how_to_report": advice + " Docking scores are predictions, not measurements, and are not "
                         "evidence that a compound binds.",
        "status": job.get("status"),
        "protein": (job.get("protein") or {}).get("title"),
        "settings": {k: (job.get("settings") or {}).get(k)
                     for k in ("exhaustiveness", "padding", "num_modes", "protonate", "keep_waters",
                               "flex_residues", "covalent_residue")},
        "ranked": rows[:limit],
        "n_total": len(rows),
        "failed": failed,
    }


def files(job_id: str) -> dict:
    api(f"/api/jobs/{job_id}")                              # 404s here rather than returning dead paths
    d = ROOT / "workspace" / "jobs" / job_id
    return {
        "report_url": f"{BASE}/api/jobs/{job_id}/file/report.html",
        "report_file": str(d / "report.html"),
        "csv_file": str(d / "results.csv"),
        "all_files_zip": str(d / "results.zip"),
        "poses": str(d / "compounds"),
        "note": "The report is self-contained HTML. poses.sdf and complex_top_pose.pdb inside each "
                "compound folder open directly in PyMOL or Chimera.",
    }


SMILES_LIST = {
    "type": "array", "description": "The compounds to dock.",
    "items": {"type": "object", "required": ["smiles"],
              "properties": {"name": {"type": "string"}, "smiles": {"type": "string"}}},
}

TOOLS = [
    {
        "name": "akadock_load_protein",
        "description": "Download a protein structure by PDB ID and list the ligands bound in it. "
                       "The ligand you choose defines the binding site. Call this before docking.",
        "inputSchema": {"type": "object", "required": ["pdb_id"], "properties": {
            "pdb_id": {"type": "string", "description": "4-character PDB ID, e.g. 4EY7."}}},
        "fn": lambda a: load_protein(a.get("pdb_id", ""), a.get("file_path", "")),
    },
    {
        "name": "akadock_dock",
        "description": "Start a docking run with AutoDock Vina on this machine and return a job id. "
                       "Docking takes about 20-40 seconds per compound, so this returns immediately: "
                       "poll akadock_status. Leave the settings alone unless the user asks - the "
                       "defaults are the benchmarked ones.",
        "inputSchema": {"type": "object", "required": ["protein_id", "compounds"], "properties": {
            "protein_id": {"type": "string", "description": "From akadock_load_protein."},
            "compounds": SMILES_LIST,
            "ligand_key": {"type": "string", "description": "The co-crystallized ligand that marks "
                           "the site; use suggested_ligand_key unless the user wants another."},
            "site_residues": {"type": "string", "description": "Pocket residues (e.g. 'Trp86 Tyr337') "
                              "when there is no bound ligand. The run then cannot validate itself."},
            "exhaustiveness": {"type": "integer", "minimum": 1, "maximum": 64,
                               "description": "Search effort, default 8. 16-32 for a careful run."},
            "padding": {"type": "number", "description": "Box padding in A around the ligand, default 10."},
            "num_modes": {"type": "integer", "description": "Poses kept per compound, default 9."},
            "protonate": {"type": "boolean", "description": "Protonate at pH 7.4. Off by default."},
            "keep_waters": {"type": "boolean", "description": "Keep crystal waters near the ligand. "
                            "Only for close analogues of the reference ligand."},
            "flex_residues": {"type": "string", "description": "Up to 4 side chains to let rotate. "
                              "Scores from a flexible run compare only with each other."},
            "covalent_residue": {"type": "string", "description": "e.g. 'Cys145': measures how close "
                                 "each warhead gets. Vina cannot form the bond."},
            "redock": {"type": "boolean", "description": "Validate by redocking. Keep this on."},
            "dock_reference": {"type": "boolean"}}},
        "fn": lambda a: dock(a["protein_id"], a.get("compounds") or [], a.get("ligand_key", ""),
                             a.get("site_residues", ""), a.get("exhaustiveness", 8),
                             a.get("padding", 10.0), a.get("num_modes", 9), a.get("protonate", False),
                             a.get("keep_waters", False), a.get("flex_residues", ""),
                             a.get("covalent_residue", ""), a.get("redock", True),
                             a.get("dock_reference", True)),
    },
    {
        "name": "akadock_status",
        "description": "Progress of a run, and the validation verdict as soon as the redock finishes. "
                       "Do not report results while the status is queued or running.",
        "inputSchema": {"type": "object", "required": ["job_id"],
                        "properties": {"job_id": {"type": "string"}}},
        "fn": lambda a: status(a["job_id"]),
    },
    {
        "name": "akadock_results",
        "description": "The ranked results of a finished run. The reply starts with the validation "
                       "verdict and a trust_ranking flag: ALWAYS report the verdict together with any "
                       "ranking, and never present scores from a FAILED or unvalidated run as "
                       "meaningful.",
        "inputSchema": {"type": "object", "required": ["job_id"], "properties": {
            "job_id": {"type": "string"},
            "limit": {"type": "integer", "description": "How many top compounds, default 25."}}},
        "fn": lambda a: results(a["job_id"], int(a.get("limit", 25))),
    },
    {
        "name": "akadock_files",
        "description": "Where this run's report, spreadsheet, pose files and zip are on disk.",
        "inputSchema": {"type": "object", "required": ["job_id"],
                        "properties": {"job_id": {"type": "string"}}},
        "fn": lambda a: files(a["job_id"]),
    },
]

INSTRUCTIONS = (
    "AKADock docks compounds into a protein with AutoDock Vina on this machine. Every run redocks "
    "the known crystal ligand first and reports PASS, WARN or FAIL. That verdict decides what the "
    "numbers are worth: always state it alongside any ranking, and never rank compounds from a run "
    "that failed its self-check. Docking scores are predictions, not measurements - they are not "
    "evidence that a compound binds, and must be confirmed in the laboratory."
)


# ---------------------------------------------------------------- MCP plumbing

def call_tool(name: str, args: dict) -> dict:
    tool = next((t for t in TOOLS if t["name"] == name), None)
    if tool is None:
        return {"content": [{"type": "text", "text": f"No such tool: {name}"}], "isError": True}
    try:
        ensure_engine()
        out = tool["fn"](args or {})
        return {"content": [{"type": "text", "text": json.dumps(out, indent=1, default=str)}]}
    except EngineError as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except Exception as e:  # noqa: BLE001
        log(f"{name} failed: {e!r}")
        return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}


def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion")
        return {"protocolVersion": asked if isinstance(asked, str) else PROTOCOL,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "akadock", "title": "AKADock", "version": VERSION},
                "instructions": INSTRUCTIONS}
    if method == "tools/list":
        return {"tools": [{k: t[k] for k in ("name", "description", "inputSchema")} for t in TOOLS]}
    if method == "tools/call":
        p = msg.get("params") or {}
        return call_tool(p.get("name", ""), p.get("arguments") or {})
    if method == "ping":
        return {}
    if mid is None:                                         # any other notification: nothing to answer
        return None
    raise LookupError(method or "")


def main() -> int:
    log(f"serving {ROOT}  engine {BASE}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue                                        # a half-written line is not worth a reply
        mid = msg.get("id")
        try:
            result = handle(msg)
        except LookupError as e:
            if mid is not None:
                reply = {"jsonrpc": "2.0", "id": mid,
                         "error": {"code": -32601, "message": f"Unknown method: {e}"}}
                print(json.dumps(reply), flush=True)
            continue
        if mid is None or result is None:                   # notifications get no response
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
