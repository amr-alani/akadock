r"""Pack the Claude Desktop connector.  python mcp\build_bundle.py [output folder]

Produces AKADock-1.1-connector.mcpb: a zip holding manifest.json, the icon and a copy of
akadock_mcp.py, which Claude Desktop installs when you double-click it. The server itself is not
bundled with a Python runtime - it runs on AKADock's own python.exe, whose folder the user picks
during installation and which reaches the server as AKADOCK_HOME.

Uses the official packer when Node is available (npx @anthropic-ai/mcpb), because it validates the
manifest against the published schema. Falls back to writing the zip directly - the format is a
plain zip with manifest.json at the root.
"""
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = ROOT / "mcp" / "bundle"


def build(out: Path) -> int:
    manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    name = f"AKADock-{manifest['version'].rsplit('.', 1)[0]}-connector.mcpb"
    stage = out / "_bundle"
    if stage.exists():
        shutil.rmtree(stage)
    (stage / "server").mkdir(parents=True)

    shutil.copy2(BUNDLE / "manifest.json", stage / "manifest.json")
    shutil.copy2(BUNDLE / "icon.png", stage / "icon.png")
    shutil.copy2(ROOT / "mcp" / "akadock_mcp.py", stage / "server" / "akadock_mcp.py")
    entry = stage / manifest["server"]["entry_point"]
    if not entry.exists():
        print(f"entry_point missing: {entry}", file=sys.stderr)
        return 1

    target = out / name
    target.unlink(missing_ok=True)
    npx = shutil.which("npx")
    if npx:
        r = subprocess.run([npx, "-y", "@anthropic-ai/mcpb@latest", "pack", str(stage), str(target)],
                           capture_output=True, text=True, shell=os.name == "nt")
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode != 0:
            print("the official packer refused this bundle - fix the manifest", file=sys.stderr)
            return 1
    else:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(stage.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(stage).as_posix())
        print("packed without validation (Node not found)")

    with zipfile.ZipFile(target) as z:
        names = z.namelist()
    assert "manifest.json" in names, names
    assert manifest["server"]["entry_point"] in names, names
    print(f"{target}  {target.stat().st_size // 1024} KB  {len(names)} files")
    shutil.rmtree(stage, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(build(Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT.parent.parent))
