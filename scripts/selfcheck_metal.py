r"""Self-check for pipeline.metal_near_box: the warning must depend on where the metal is, not on
whether the protein has one. Run:  python\python.exe -I scripts\selfcheck_metal.py
Downloads 3 small structures to a temp folder; needs internet."""
import sys, tempfile, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import pipeline as P  # noqa: E402

# pdb, ligand key, metal warning expected?
CASES = [
    ("4WMZ", "TPF:A:602", True),   # fluconazole on the CYP51 heme iron -- Vina cannot score this
    ("5TZ1", "VT1:A:602", True),   # same pocket, different azole
    ("3KK6", "CEL:A:701", False),  # COX-1 has a heme, but in the peroxidase site far from this pocket
]

tmp = Path(tempfile.mkdtemp(prefix="akadock-selfcheck-"))
for pdb, key, expect in CASES:
    src = tmp / f"{pdb}.cif"
    if not src.exists():
        urllib.request.urlretrieve(f"https://files.rcsb.org/download/{pdb}.cif", src)
    xtal, _ = P.extract_crystal_ligand(src, key)
    warning = P.metal_near_box(src, P.docking_box(xtal))
    print(f"{pdb} {'warns' if warning else 'clean':5s} {'ok' if bool(warning) == expect else 'UNEXPECTED'}")
    assert bool(warning) == expect, f"{pdb}: expected warning={expect}"
print("metal_near_box: all cases as expected")
