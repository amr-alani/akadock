"""One-time download of the non-Python pieces: the AutoDock Vina program,
the Ketcher structure editor and the Mol* 3D viewer. After this the app
runs fully offline (except fetching structures by PDB ID)."""
import io
import platform
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "app" / "static" / "vendor"
BIN = ROOT / "bin"

VINA = "https://github.com/ccsb-scripps/AutoDock-Vina/releases/download"
KETCHER = "https://github.com/epam/ketcher/releases/download/v3.7.0/ketcher-standalone-3.7.0.zip"
MOLSTAR = "https://registry.npmjs.org/molstar/-/molstar-5.13.1.tgz"
# Ligand Expo's ready-made SMILES tables are retired; build the table from the wwPDB dictionary instead.
CCD_CIF = "https://files.wwpdb.org/pub/pdb/data/monomers/components.cif.gz"


def get(url: str) -> bytes:
    print(f"  downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "DockingApp-setup"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def vina_url() -> tuple[str, str]:
    system, machine = platform.system(), platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if system == "Linux":
        return f"{VINA}/v1.2.5/vina_1.2.5_linux_{'aarch64' if arm else 'x86_64'}", "vina"
    if system == "Darwin":
        return f"{VINA}/v1.2.5/vina_1.2.5_mac_{'aarch64' if arm else 'x86_64'}", "vina"
    if system == "Windows":
        return f"{VINA}/v1.2.5/vina_1.2.5_win.exe", "vina.exe"
    sys.exit(f"Unsupported platform {system} {machine}; put a vina binary in {BIN} yourself.")


def setup_vina():
    url, name = vina_url()
    dest = BIN / name
    if dest.exists():
        print(f"Vina already present: {dest}")
        return
    print("AutoDock Vina")
    BIN.mkdir(exist_ok=True)
    dest.write_bytes(get(url))
    dest.chmod(dest.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def setup_ketcher():
    dest = VENDOR / "ketcher"
    if (dest / "index.html").exists():
        print("Ketcher already present")
        return
    print("Ketcher structure editor")
    with zipfile.ZipFile(io.BytesIO(get(KETCHER))) as z:
        tmp = VENDOR / "_ketcher_tmp"
        z.extractall(tmp)
    src = next(p.parent for p in tmp.rglob("index.html"))
    shutil.move(str(src), str(dest))
    shutil.rmtree(tmp, ignore_errors=True)
    for m in dest.rglob("*.map"):
        m.unlink()


def setup_molstar():
    dest = VENDOR / "molstar"
    if (dest / "molstar.js").exists():
        print("Mol* already present")
        return
    print("Mol* 3D viewer")
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(get(MOLSTAR)), mode="r:gz") as t:
        for name in ("molstar.js", "molstar.css"):
            f = t.extractfile(f"package/build/viewer/{name}")
            (dest / name).write_bytes(f.read())


def ccd_smiles_table(cif_gz: bytes) -> str:
    """Lines of 'SMILES<TAB>ID' from components.cif.gz, preferring OpenEye then CACTVS stereo SMILES."""
    import gzip
    import shlex

    rank = {("SMILES_CANONICAL", "OpenEye OEToolkits"): 0, ("SMILES_CANONICAL", "CACTVS"): 1,
            ("SMILES", "OpenEye OEToolkits"): 2, ("SMILES", "CACTVS"): 3, ("SMILES", "ACDLabs"): 4}
    best: dict[str, tuple[int, str]] = {}
    in_loop = False
    for line in gzip.decompress(cif_gz).decode("utf-8", "replace").splitlines():
        if line.startswith("_pdbx_chem_comp_descriptor."):
            in_loop = True
            continue
        if not in_loop:
            continue
        if line.startswith(("#", "loop_", "data_", "_")):
            in_loop = False
            continue
        lex = shlex.shlex(line, posix=True)
        lex.whitespace_split, lex.escape = True, ""  # keep the backslashes of SMILES stereo bonds
        try:
            f = list(lex)
        except ValueError:
            continue
        if len(f) == 5:  # comp_id type program version descriptor; multi-line values are skipped
            r = rank.get((f[1], f[2]))
            if r is not None and r < best.get(f[0], (99, ""))[0]:
                best[f[0]] = (r, f[4])
    return "".join(f"{smi}\t{cid}\n" for cid, (_, smi) in sorted(best.items()))


def setup_ccd():
    """SMILES for every ligand in the PDB, so ligand bond orders are exact even offline."""
    dest = ROOT / "app" / "data" / "ccd_smiles.smi"
    if dest.exists():
        print("Ligand dictionary already present")
        return
    print("PDB ligand dictionary (bond orders for co-crystallized ligands, ~120 MB download)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        table = ccd_smiles_table(get(CCD_CIF))
        if table.count("\n") > 1000:
            dest.write_text(table, encoding="utf-8")
            return
    except Exception as e:  # noqa: BLE001
        print(f"  could not download ({e})")
    print("  skipped: the app will fetch ligand templates online, or guess bond orders offline")


if __name__ == "__main__":
    VENDOR.mkdir(parents=True, exist_ok=True)
    setup_vina()
    setup_ketcher()
    setup_molstar()
    setup_ccd()
    print("Done. Start the app with:  python run.py")
