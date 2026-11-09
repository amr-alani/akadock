r"""Build AKADock-Setup.exe, the portable zip and the source zip.

    python installer\build.py [output folder]      (default: the folder this tree sits in)

Needs only what Windows already has: the .NET Framework 4 C# compiler (csc.exe). Run it from the
Windows build tree (the one with python\ and bin\ in it); the source zip is taken from the
AKADock-source tree next door.

The installer carries the portable zip as an embedded resource named "AKADock.payload.zip" and the
uninstaller as "AKADock.Uninstall.exe". Setup.cs looks them up by those exact names, so a rename
here fails at install time, not at compile time.
"""
import os
import subprocess
import sys
import zipfile
from pathlib import Path

CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / r"Microsoft.NET\Framework64\v4.0.30319\csc.exe"
SKIP_DIRS = {"workspace", "__pycache__", ".venv", ".git"}
# Shipped in the source zip only: too big or not source (.venv and the Ketcher/Mol* bundles are
# excluded the same way, by living under app/static/vendor which is not listed here).
SOURCE_FILES = [
    "README.md", "START-HERE.txt", "requirements.txt", "run.py",
    "example-report-1EVE.html", "example-report-4EY7.html",
    "screenshot-4EY7.png", "screenshot-results.png",
    "app/jobs.py", "app/pipeline.py", "app/report.py", "app/server.py",
    "app/static/app.js", "app/static/index.html", "app/static/style.css",
    "benchmarks/bench.py", "benchmarks/bench_results.csv",
    "benchmarks/repro.py", "benchmarks/repro_results.csv",
    "installer/LICENSE.txt", "installer/Setup.cs", "installer/Uninstall.cs", "installer/build.py",
    "mcp/akadock_mcp.py", "mcp/install_mcp.py", "mcp/build_bundle.py",
    "mcp/bundle/manifest.json", "mcp/bundle/icon.png",
    "scripts/selfcheck_metal.py", "scripts/selfcheck_mcp.py", "scripts/selfcheck_warheads.py",
    "scripts/setup_assets.py",
    "shell/AKADock.cs", "shell/akadock.ico", "shell/build.bat",
    "shell/Microsoft.Web.WebView2.Core.dll", "shell/Microsoft.Web.WebView2.WinForms.dll",
    "shell/WebView2Loader.dll",
]


def csc(out: Path, sources: list[Path], refs: list[str], icon: Path) -> None:
    cmd = [str(CSC), "/nologo", "/optimize", "/target:winexe", "/platform:x64",
           f"/win32icon:{icon}", f"/out:{out}"] + [f"/r:{r}" for r in refs] + [str(s) for s in sources]
    subprocess.run(cmd, check=True)


def zip_tree(zip_path: Path, root: Path, prefix: str, exclude: set[Path]) -> int:
    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for f in filenames:
                p = Path(dirpath) / f
                if p in exclude:
                    continue
                z.write(p, f"{prefix}/{p.relative_to(root).as_posix()}")
                n += 1
    return n


def main() -> int:
    tree = Path(__file__).resolve().parent.parent          # ...\AKADock-Windows\AKADock
    src_tree = tree.parent.parent / "AKADock-source" / "AKADock"
    out = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else tree.parent.parent
    inst, shell = tree / "installer", tree / "shell"
    build = out / "_build"
    build.mkdir(parents=True, exist_ok=True)

    if not CSC.exists():
        print(f"C# compiler not found at {CSC}", file=sys.stderr)
        return 1
    if not src_tree.exists():
        print(f"source tree not found at {src_tree}", file=sys.stderr)
        return 1

    print("app window  AKADock.exe")
    csc(tree / "AKADock.exe", [shell / "AKADock.cs"],
        [str(shell / "Microsoft.Web.WebView2.Core.dll"), str(shell / "Microsoft.Web.WebView2.WinForms.dll"),
         "System.Windows.Forms.dll", "System.Drawing.dll"], shell / "akadock.ico")

    print("uninstaller Uninstall.exe")
    uninst = build / "Uninstall.exe"
    csc(uninst, [inst / "Uninstall.cs"],
        ["System.Windows.Forms.dll", "System.Drawing.dll", "System.Core.dll"], shell / "akadock.ico")

    print("payload     AKADock-1.1-portable.zip")
    payload = out / "AKADock-1.1-portable.zip"
    # The portable zip IS the installer payload: one artifact, so they can never disagree.
    n = zip_tree(payload, tree, "AKADock", exclude={payload})
    print(f"            {n} files, {payload.stat().st_size // 1024 // 1024} MB")

    print("installer   AKADock-Setup.exe")
    cmd = [str(CSC), "/nologo", "/optimize", "/target:winexe", "/platform:x64",
           f"/win32icon:{shell / 'akadock.ico'}", f"/out:{out / 'AKADock-Setup.exe'}",
           "/r:System.Windows.Forms.dll", "/r:System.Drawing.dll",
           "/r:System.IO.Compression.dll", "/r:System.IO.Compression.FileSystem.dll",
           f"/resource:{payload},AKADock.payload.zip",
           f"/resource:{uninst},AKADock.Uninstall.exe",
           str(inst / "Setup.cs")]
    subprocess.run(cmd, check=True)

    print("source zip  AKADock-1.1-source.zip")
    szip = out / "AKADock-1.1-source.zip"
    with zipfile.ZipFile(szip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for rel in SOURCE_FILES:
            p = src_tree / rel
            if not p.exists():
                print(f"            MISSING {rel}", file=sys.stderr)
                return 1
            z.write(p, f"AKADock-1.1-source/{rel}")
    print(f"            {len(SOURCE_FILES)} files, {szip.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
