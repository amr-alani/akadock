"""Docking pipeline: fetch protein, find the co-crystallized ligand, prepare
receptor and ligands, redock to validate, batch dock, analyse interactions.

Everything here runs locally with free tools (RDKit, Meeko, OpenBabel,
AutoDock Vina, PLIP). Only fetching a PDB ID / ligand template needs internet.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import gemmi
import numpy as np
from rdkit import Chem, RDLogger
from rdkit.Chem import AllChem, Crippen, Descriptors, Lipinski, rdMolAlign, rdMolDescriptors
from rdkit.Chem.Draw import rdMolDraw2D

RDLogger.DisableLog("rdApp.*")
try:
    from openbabel import openbabel as _ob

    _ob.obErrorLog.SetOutputLevel(0)  # silence harmless kekulization warnings on proteins
except Exception:  # noqa: BLE001
    pass

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent

# Hetero groups that are almost never the ligand of interest.
NON_LIGANDS = {
    "HOH", "WAT", "DOD", "SO4", "PO4", "GOL", "EDO", "PEG", "PG4", "P6G", "1PE", "PGE",
    "ACT", "ACY", "FMT", "DMS", "MPD", "TRS", "EPE", "MES", "BME", "IMD", "CIT", "TLA",
    "NAG", "NDG", "BMA", "MAN", "FUC", "GAL", "GLC", "SIA", "NO3", "SCN", "IOD", "BR",
    "CL", "NA", "K", "MG", "CA", "ZN", "MN", "FE", "FE2", "CO", "NI", "CU", "CD", "HG",
    "LI", "CS", "SR", "BA", "AU", "PT", "AZI", "NH4", "UNX", "UNL", "PEO", "MLI",
}
METALS = {"ZN", "MG", "CA", "MN", "FE", "FE2", "CO", "NI", "CU"}


class PipelineError(Exception):
    pass


# --------------------------------------------------------------------------
# Protein input
# --------------------------------------------------------------------------

def _download(url: str, dest: Path, timeout: int = 30) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": "DockingApp/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(dest, "wb") as f:
        f.write(r.read())
    return dest


def fetch_pdb(pdb_id: str, dest_dir: Path) -> Path:
    pdb_id = pdb_id.strip().upper()
    if not re.fullmatch(r"[0-9][A-Z0-9]{3}", pdb_id):
        raise PipelineError(f"'{pdb_id}' is not a valid 4-character PDB ID.")
    dest_dir.mkdir(parents=True, exist_ok=True)
    errors = []
    for ext in ("pdb", "cif"):
        try:
            return _download(f"https://files.rcsb.org/download/{pdb_id}.{ext}", dest_dir / f"input.{ext}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{ext}: {e}")
    raise PipelineError(
        f"Could not download {pdb_id} from the RCSB PDB ({'; '.join(errors)}). "
        "Check your internet connection, or upload a local .pdb/.cif file instead."
    )



STANDARD_RESIDUES = set(gemmi.find_tabulated_residue(n).name for n in (
    "ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL "
    "HID HIE HIP HSD HSE HSP CYX ASH GLH LYN MSE SEC PYL").split() if gemmi.find_tabulated_residue(n))
MOL2_RESNAMES = {"HID": "HIS", "HIE": "HIS", "HIP": "HIS", "HSD": "HIS", "HSE": "HIS", "HSP": "HIS",
                 "CYX": "CYS", "ASH": "ASP", "GLH": "GLU", "LYN": "LYS", "WAT": "HOH"}


def mol2_to_cif(mol2: Path, out_cif: Path) -> Path:
    """Convert a whole-complex Tripos MOL2 (e.g. exported from Discovery Studio, Chimera or MOE)
    to mmCIF, keeping residue names, numbers and chains from its SUBSTRUCTURE records."""
    section, atoms, subs = None, [], {}
    for line in mol2.read_text(errors="replace").splitlines():
        if line.startswith("@<TRIPOS>"):
            section = line[9:].strip()
            continue
        f = line.split()
        if section == "ATOM" and len(f) >= 6:
            atoms.append(f)
        elif section == "SUBSTRUCTURE" and len(f) >= 2:
            subs[f[0]] = f
    if not atoms:
        raise PipelineError("No atoms found in the MOL2 file.")
    st = gemmi.Structure()
    st.name = mol2.stem
    model = gemmi.Model(1)
    chains: dict[str, gemmi.Chain] = {}
    residues: dict[tuple, gemmi.Residue] = {}
    order = []
    for f in atoms:
        name, x, y, z, atype = f[1], float(f[2]), float(f[3]), float(f[4]), f[5]
        sub_id = f[6] if len(f) > 6 else "1"
        sub_name = f[7] if len(f) > 7 else "LIG1"
        sub = subs.get(sub_id, [])
        chain_id = (sub[5] if len(sub) > 5 and sub[5] not in ("****", "") else "A")
        m = re.match(r"([A-Za-z0-9]*?[A-Za-z])(-?\d+)?([A-Za-z]?)$", sub_name)
        resname = (sub[6] if len(sub) > 6 and sub[6] not in ("****", "") else (m.group(1) if m else sub_name))[:3]
        resname = MOL2_RESNAMES.get(resname.upper(), resname.upper())
        resnum = int(m.group(2)) if m and m.group(2) else int(sub_id)
        key = (chain_id, sub_id)
        if key not in residues:
            r = gemmi.Residue()
            r.name = resname
            r.seqid = gemmi.SeqId(resnum, " ")
            r.het_flag = "A" if resname in STANDARD_RESIDUES else "H"
            residues[key] = r
            order.append(key)
        a = gemmi.Atom()
        a.name = name
        a.pos = gemmi.Position(x, y, z)
        el = atype.split(".")[0]
        a.element = gemmi.Element(el if el not in ("Du", "LP", "Any") else name[0])
        a.occ = 1.0
        residues[key].add_atom(a)
    for key in order:
        cid = key[0]
        if cid not in chains:
            chains[cid] = gemmi.Chain(cid)
        chains[cid].add_residue(residues[key])
    for c in chains.values():
        model.add_chain(c)
    st.add_model(model)
    st.setup_entities()
    st.make_mmcif_document().write_file(str(out_cif))
    return out_cif


def read_structure(path: Path) -> gemmi.Structure:
    st = gemmi.read_structure(str(path))
    st.setup_entities()
    st.remove_alternative_conformations()
    if len(st) > 1:
        for i in range(len(st) - 1, 0, -1):
            del st[i]
    return st


def list_ligands(path: Path) -> dict:
    st = read_structure(path)
    ligands = []
    chains = []
    for chain in st[0]:
        n_aa = sum(1 for r in chain if r.het_flag == "A")
        if n_aa:
            chains.append({"id": chain.name, "residues": n_aa})
        for res in chain:
            if res.het_flag != "H" or res.name in NON_LIGANDS:
                continue
            heavy = [a for a in res if a.element.name not in ("H", "D")]
            if len(heavy) < 6:
                continue
            xyz = np.array([[a.pos.x, a.pos.y, a.pos.z] for a in heavy])
            has_metal = any(a.element.is_metal for a in heavy)
            ligands.append({
                "has_metal": has_metal,
                "key": f"{res.name}:{chain.name}:{res.seqid.num}{res.seqid.icode.strip()}",
                "resname": res.name,
                "chain": chain.name,
                "resnum": f"{res.seqid.num}{res.seqid.icode.strip()}",
                "heavy_atoms": len(heavy),
                "center": [round(float(v), 3) for v in xyz.mean(axis=0)],
            })
    ligands.sort(key=lambda l: (l["has_metal"], -l["heavy_atoms"]))  # metal-free first: Vina can't dock metal complexes
    title = st.info["_struct.title"] if "_struct.title" in st.info else ""
    return {"title": title.strip(), "ligands": ligands, "chains": chains, "name": st.name}


def _find_residue(st: gemmi.Structure, key: str):
    resname, chain_id, resnum = key.split(":")
    for chain in st[0]:
        if chain.name != chain_id:
            continue
        for res in chain:
            if res.name == resname and f"{res.seqid.num}{res.seqid.icode.strip()}" == resnum:
                return res
    raise PipelineError(f"Ligand {key} not found in the structure.")


def _residue_pdb_block(resname: str, atoms) -> str:
    lines = []
    for i, a in enumerate(atoms, 1):
        name = a.name if len(a.name) == 4 else f" {a.name:<3}"
        lines.append(
            f"HETATM{i:5d} {name:4s} {resname:>3s} A{1:4d}    "
            f"{a.pos.x:8.3f}{a.pos.y:8.3f}{a.pos.z:8.3f}  1.00  0.00          {a.element.name.upper():>2s}"
        )
    return "\n".join(lines) + "\nEND\n"


CCD_TABLE = APP_DIR / "data" / "ccd_smiles.smi"
_ccd_cache: dict[str, str] | None = None


def local_ccd_smiles(resname: str) -> str | None:
    """Offline lookup in the PDB Chemical Component Dictionary SMILES table that
    scripts/setup_assets.py downloads (Ligand Expo, ~45k entries)."""
    global _ccd_cache
    if _ccd_cache is None:
        _ccd_cache = {}
        if CCD_TABLE.exists():
            for line in CCD_TABLE.read_text(errors="replace").splitlines():
                parts = line.split("\t")
                if len(parts) >= 2 and parts[0] and parts[1]:
                    _ccd_cache.setdefault(parts[1].strip().upper(), parts[0].strip())
    return _ccd_cache.get(resname.upper())


def fetch_ccd_smiles(resname: str) -> str | None:
    local = local_ccd_smiles(resname)
    if local:
        return local
    try:
        req = urllib.request.Request(
            f"https://data.rcsb.org/rest/v1/core/chemcomp/{resname}",
            headers={"User-Agent": "DockingApp/0.1"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.load(r)
        desc = d.get("rcsb_chem_comp_descriptor", {})
        return desc.get("SMILES_stereo") or desc.get("SMILES") or desc.get("smiles_stereo") or desc.get("smiles")
    except Exception:  # noqa: BLE001
        return None


CHEMBL = "https://www.ebi.ac.uk/chembl/api/data"


def _chembl(url: str, timeout: int = 60) -> dict:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AKADock/0.3"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except Exception as e:  # noqa: BLE001
        raise PipelineError(f"Could not reach the ChEMBL database: {e}. Check your internet connection.")


def chembl_targets(query: str) -> list[dict]:
    """Protein targets in ChEMBL matching a name, so the user can pick the right species."""
    q = urllib.parse.quote(query.strip())
    d = _chembl(f"{CHEMBL}/target/search.json?q={q}&limit=15"
                "&only=target_chembl_id,pref_name,organism,target_type")
    return [{"id": t["target_chembl_id"], "name": t.get("pref_name") or "", "organism": t.get("organism") or "",
             "type": t.get("target_type") or ""}
            for t in d.get("targets", []) if t.get("target_type") == "SINGLE PROTEIN"]


def chembl_actives(target_id: str, limit: int = 200, max_nm: float = 10000.0) -> list[dict]:
    """Compounds measured against a ChEMBL target, strongest first — a ready-made screening set.

    One molecule can have many measurements, so keep each molecule's best (lowest) value.
    """
    best: dict[str, dict] = {}
    for offset in range(0, 1000, 200):  # a few pages are enough to fill `limit` distinct molecules
        d = _chembl(f"{CHEMBL}/activity.json?target_chembl_id={urllib.parse.quote(target_id)}"
                    f"&standard_type__in=IC50,Ki,Kd&standard_relation=%3D&standard_units=nM"
                    f"&limit=200&offset={offset}"
                    "&only=molecule_chembl_id,canonical_smiles,standard_type,standard_value,standard_units")
        acts = d.get("activities", [])
        for a in acts:
            smiles, value = a.get("canonical_smiles"), a.get("standard_value")
            if not smiles or value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if value <= 0 or value > max_nm:
                continue
            cid = a["molecule_chembl_id"]
            if cid not in best or value < best[cid]["value"]:
                best[cid] = {"name": cid, "smiles": smiles, "value": value, "type": a.get("standard_type") or ""}
        if len(best) >= limit or len(acts) < 200:
            break
    out = sorted(best.values(), key=lambda m: m["value"])[:limit]
    if not out:
        raise PipelineError(f"ChEMBL has no measured IC50/Ki/Kd compounds for {target_id} under "
                            f"{max_nm:.0f} nM. Try another target.")
    for m in out:
        m["name"] = f"{m['name']} ({m['type']} {m['value']:g} nM)"
    return out


COVALENT_BOND_MAX = 2.1  # C-S is ~1.8 A; anything this close is a bond, not a contact


def covalent_link(st, ligand_res) -> str | None:
    """The protein residue this ligand is bonded to, if it is a covalent inhibitor.

    A covalently bound ligand cannot be redocked as a free compound, and saying so plainly is much
    more useful than letting bond-order assignment fail further down.
    """
    lig = np.array([[a.pos.x, a.pos.y, a.pos.z] for a in ligand_res if a.element.name not in ("H", "D")])
    if not len(lig):
        return None
    for chain in st[0]:
        for res in chain:
            if res.het_flag != "A" or res.name not in REACTIVE_ATOMS:
                continue
            for a in res:
                if a.name != REACTIVE_ATOMS[res.name]:
                    continue
                d = np.linalg.norm(lig - np.array([a.pos.x, a.pos.y, a.pos.z]), axis=1).min()
                if d <= COVALENT_BOND_MAX:
                    return f"{res.name}{res.seqid.num} (chain {chain.name}, atom {a.name})"
    return None


def extract_crystal_ligand(path: Path, key: str) -> tuple[Chem.Mol, str]:
    """Return the co-crystallized ligand as an RDKit mol (heavy atoms, crystal
    coordinates, correct bond orders when possible) and a note on how bond
    orders were assigned."""
    st = read_structure(path)
    res = _find_residue(st, key)
    if any(a.element.is_metal for a in res):
        raise PipelineError(f"{res.name} contains a metal atom (e.g. a heme or metal complex). AutoDock Vina cannot "
                            "dock metal-containing ligands; pick another ligand or define the site by residues.")
    link = covalent_link(st, res)
    if link:
        raise PipelineError(
            f"{res.name} is covalently bonded to {link} in this structure, so it cannot be pulled out and "
            "redocked as a free compound. Choose 'None' as the reference ligand and define the binding site "
            f"by residues instead (for example: {link.split()[0]}). You can still use the covalent reactivity "
            "check in Settings to see how close your compounds get to that residue.")
    block = _residue_pdb_block(res.name, [a for a in res if a.element.name not in ("H", "D")])
    raw = Chem.MolFromPDBBlock(block, removeHs=True, proximityBonding=True, sanitize=False)
    if raw is None:
        raise PipelineError("Could not read the co-crystallized ligand coordinates.")

    smiles = fetch_ccd_smiles(res.name)
    why = "no dictionary template found (offline or unknown ligand)"
    if smiles:
        why = "the dictionary template did not match the crystal atoms (missing atoms?)"
        try:
            template = Chem.MolFromSmiles(smiles)
            mol = AllChem.AssignBondOrdersFromTemplate(template, Chem.RemoveHs(raw, sanitize=False))
            Chem.SanitizeMol(mol)
            Chem.AssignStereochemistryFrom3D(mol)
            return mol, f"bond orders from the PDB Chemical Component Dictionary entry {res.name}"
        except Exception:  # noqa: BLE001
            pass

    # Offline / template mismatch: let OpenBabel perceive bond orders.
    from openbabel import pybel

    obmol = pybel.readstring("pdb", block)
    obmol.OBMol.PerceiveBondOrders()
    mol = Chem.MolFromMolBlock(obmol.write("mol"), removeHs=True)
    if mol is None:
        raise PipelineError("Could not assign bond orders to the co-crystallized ligand.")
    Chem.AssignStereochemistryFrom3D(mol)
    return mol, f"bond orders guessed by OpenBabel ({why}); check the SMILES"


def docking_box(mol: Chem.Mol, padding: float = 10.0, min_size: float = 20.0) -> dict:
    xyz = mol.GetConformer().GetPositions()
    lo, hi = xyz.min(axis=0), xyz.max(axis=0)
    center = (lo + hi) / 2
    size = np.maximum(hi - lo + padding, min_size)
    return {"center": [round(float(v), 3) for v in center], "size": [round(float(v), 1) for v in size]}


METAL_ELEMENTS = {"FE", "ZN", "MG", "MN", "CU", "NI", "CO", "CA", "CD", "HG", "MO", "W", "V", "RU", "PT"}


def metal_near_box(path: Path, box: dict) -> str:
    """Warn when a metal sits in the pocket: Vina scores no metal coordination, so poses that should
    bind the metal (azoles on a heme iron, hydroxamates on a zinc) come out wrong."""
    center, size = np.array(box["center"]), np.array(box["size"])
    lo, hi = center - size / 2, center + size / 2
    found = {}
    for res in (r for c in read_structure(path)[0] for r in c if r.het_flag == "H"):
        for a in res:
            if a.element.name.upper() in METAL_ELEMENTS and np.all(lo <= [a.pos.x, a.pos.y, a.pos.z]) \
                    and np.all([a.pos.x, a.pos.y, a.pos.z] <= hi):
                found[f"{a.element.name.upper()} in {res.name}" if res.name not in METAL_ELEMENTS
                      else a.element.name.upper()] = True
    if not found:
        return ""
    return (f"The docking box contains a metal ({', '.join(sorted(found))}). AutoDock Vina does not model "
            "metal coordination, so a compound that binds the metal (for example an azole on a heme iron) "
            "can be placed in the wrong pose with a plausible-looking score. Treat these results with care.")


THREE_LETTER = {v: k for k, v in {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H",
    "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
    "TYR": "Y", "VAL": "V"}.items()}


def find_residues(path: Path, residues: str) -> tuple[list[dict], list[list[float]]]:
    """Residues typed by the user, e.g. "Trp86 Tyr337 A:Phe338 447", with their heavy-atom coordinates."""
    st = read_structure(path)
    tokens = [t for t in re.split(r"[\s,;]+", residues.strip()) if t]
    if not tokens:
        raise PipelineError("Type at least one residue, e.g. Trp86 Tyr337.")
    found, missing, xyz = [], [], []
    for tok in tokens:
        chain = None
        if ":" in tok:
            chain, tok = tok.split(":", 1)
        m = re.fullmatch(r"([A-Za-z]{0,3})(-?\d+)", tok)
        if not m:
            missing.append(tok)
            continue
        name, num = m.group(1).upper(), int(m.group(2))
        if len(name) == 1:
            name = THREE_LETTER.get(name, name)
        hits = [(c, r) for c in st[0] for r in c
                if r.seqid.num == num and r.het_flag == "A" and (not name or r.name == name)
                and (chain is None or c.name == chain)]
        if not hits:
            missing.append(tok if chain is None else f"{chain}:{tok}")
            continue
        for c, r in hits[:1]:
            found.append({"chain": c.name, "resnum": r.seqid.num, "resname": r.name})
            xyz += [[a.pos.x, a.pos.y, a.pos.z] for a in r if a.element.name not in ("H", "D")]
    if missing:
        raise PipelineError(f"Residue(s) not found in the structure: {', '.join(missing)}")
    return found, xyz


def site_box(path: Path, residues: str, padding: float = 4.0, min_size: float = 20.0) -> dict:
    """Docking box around binding-site residues typed by the user, e.g. "Trp86 Tyr337 A:Phe338 447"."""
    found, xyz = find_residues(path, residues)
    xyz = np.array(xyz)
    lo, hi = xyz.min(axis=0), xyz.max(axis=0)
    size = np.maximum(hi - lo + padding, min_size)
    warning = ""
    if size.max() > 40:
        chains = sorted({r["chain"] for r in found})
        warning = (f"The box is very large ({size.max():.0f} Å), so docking will be slow and less reliable. "
                   + (f"The residues are on different chains ({', '.join(chains)}); add a chain to each, e.g. A:Trp86. "
                      if len(chains) > 1 else "Check that the residues all line the same pocket. "))
    return {"center": [round(float(v), 3) for v in (lo + hi) / 2], "size": [round(float(v), 1) for v in size],
            "residues": found, "warning": warning.strip()}


# --------------------------------------------------------------------------
# Receptor preparation
# --------------------------------------------------------------------------

WATERS = {"HOH", "WAT", "DOD"}


def prepare_receptor(path: Path, workdir: Path, keep_metals: bool = True,
                     waters_near: Chem.Mol | None = None, water_cutoff: float = 3.5) -> tuple[Path, Path]:
    """Strip waters/ligands/hydrogens, add hydrogens at pH 7.4, write PDBQT. With `waters_near`,
    crystal waters within `water_cutoff` Å of that ligand are kept as part of the rigid receptor.

    `keep_metals` keeps metals in receptor_clean.pdb only (PLIP and the viewer read it); Open
    Babel's rigid-receptor PDBQT writer drops every hetero group except water, so Vina docks
    against a receptor with no metals. Measured 2026-11-08: appending them to the PDBQT makes
    redocking significantly worse (41 held-out complexes, 17->11 passes, McNemar p=0.031),
    because Vina has no attractive term for metal coordination - it is purely repulsive. Do not
    "fix" this without a metal-aware engine (AutoDock-GPU + autogrid4)."""
    st = read_structure(path)
    st.remove_hydrogens()
    model = st[0]
    lig_xyz = waters_near.GetConformer().GetPositions() if waters_near is not None else None
    for chain in model:
        for i in range(len(chain) - 1, -1, -1):
            res = chain[i]
            if res.het_flag == "A":
                continue
            if keep_metals and res.name in METALS:
                continue
            if lig_xyz is not None and res.name in WATERS:
                o = np.array([[a.pos.x, a.pos.y, a.pos.z] for a in res])
                if np.min(np.linalg.norm(lig_xyz[:, None, :] - o[None, :, :], axis=2)) <= water_cutoff:
                    continue
            del chain[i]
    st.remove_empty_chains()
    clean = workdir / "receptor_clean.pdb"
    st.write_pdb(str(clean))

    from openbabel import pybel

    obmol = next(pybel.readfile("pdb", str(clean)))
    obmol.OBMol.DeleteHydrogens()
    obmol.OBMol.CorrectForPH(7.4)
    obmol.OBMol.AddHydrogens(False, True, 7.4)
    pdbqt = workdir / "receptor.pdbqt"
    obmol.write("pdbqt", str(pdbqt), overwrite=True, opt={"r": None, "x": None})
    # Keep only ATOM/HETATM lines (Vina rejects ROOT/BRANCH in receptors).
    lines = [l for l in pdbqt.read_text().splitlines() if l.startswith(("ATOM", "HETATM"))]
    pdbqt.write_text("\n".join(lines) + "\n")
    return clean, pdbqt


# Side chains Vina cannot usefully rotate: no rotatable bond past CB, or a backbone-only residue.
RIGID_SIDE_CHAINS = {"GLY", "ALA", "PRO"}
MAX_FLEX_RESIDUES = 8


def flex_residue_list(path: Path, residues: str) -> list[dict]:
    """The residues to make flexible, refusing the ones that would not work. Used to check the
    request before a run starts, and again when the receptor is split."""
    found, _ = find_residues(path, residues)
    bad = sorted({r["resname"] for r in found} & RIGID_SIDE_CHAINS)
    if bad:
        raise PipelineError(f"{', '.join(bad)} has no side chain that Vina can rotate. Remove it and keep "
                            "residues with longer side chains, such as Tyr, Phe, Trp, Leu, Lys or Arg.")
    if len(found) > MAX_FLEX_RESIDUES:
        raise PipelineError(f"{len(found)} flexible side chains is too many — each one makes the search "
                            f"harder and the result less reliable. Use at most {MAX_FLEX_RESIDUES}, "
                            "and 3 or 4 is usually better.")
    return found


def prepare_flexible_receptor(clean_pdb: Path, residues: str, workdir: Path) -> tuple[Path, Path, list[dict]]:
    """Split the receptor into a rigid part and rotatable side chains, so Vina can move them
    while docking (induced fit). Returns (rigid pdbqt, flex pdbqt, the residues made flexible)."""
    found = flex_residue_list(clean_pdb, residues)
    spec = ",".join(f"{r['chain']}:{r['resnum']}" for r in found)
    base = workdir / "receptor"
    proc = subprocess.run([sys.executable, "-m", "meeko.cli.mk_prepare_receptor",
                           "--read_pdb", str(clean_pdb), "-o", str(base), "-p", "-f", spec],
                          capture_output=True, text=True)
    rigid, flex = base.with_name("receptor_rigid.pdbqt"), base.with_name("receptor_flex.pdbqt")
    if proc.returncode != 0 or not (rigid.exists() and flex.exists()):
        raise PipelineError("Could not make those side chains flexible: "
                            f"{(proc.stderr or proc.stdout).strip()[-500:]}")
    return rigid, flex, found


def flex_sidechain_coords(out_pdbqt: Path, pose: int = 0) -> dict[tuple[str, int, str], tuple[float, float, float]]:
    """Where the flexible side chains ended up in one pose, keyed by (chain, residue number, atom name).

    Vina writes the moved side chains into the pose file after the ligand, between BEGIN_RES/END_RES.
    Without this the interaction analysis would measure the original, un-moved side chains.
    """
    coords, model, inside = {}, -1, False
    for line in out_pdbqt.read_text().splitlines():
        if line.startswith("MODEL"):
            model += 1
        elif line.startswith("BEGIN_RES"):
            inside = True
        elif line.startswith("END_RES"):
            inside = False
        elif inside and model == pose and line.startswith(("ATOM", "HETATM")):
            name, chain, num = line[12:16].strip(), line[21].strip(), line[22:26].strip()
            if name.startswith("H") or not num:
                continue  # hydrogens are re-added later; PLIP only needs the heavy atoms
            coords[(chain, int(num), name)] = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
    return coords


# --------------------------------------------------------------------------
# Ligand preparation
# --------------------------------------------------------------------------

def protonate(smiles: str, ph: float = 7.4) -> str:
    try:
        from dimorphite_dl import protonate_smiles

        out = protonate_smiles(smiles, ph_min=ph, ph_max=ph, max_variants=1)
        if out:
            return out[0]
    except Exception:  # noqa: BLE001
        pass
    return smiles


def parse_compound(smiles: str) -> Chem.Mol:
    mol = Chem.MolFromSmiles(smiles.strip())
    if mol is None:
        raise PipelineError(f"Invalid SMILES: {smiles}")
    frags = Chem.GetMolFrags(mol, asMols=True)
    if len(frags) > 1:  # drop counter-ions / salts
        mol = max(frags, key=lambda m: m.GetNumHeavyAtoms())
    return mol


def properties(mol: Chem.Mol) -> dict:
    mw = Descriptors.MolWt(mol)
    logp = Crippen.MolLogP(mol)
    hbd = Lipinski.NumHDonors(mol)
    hba = Lipinski.NumHAcceptors(mol)
    violations = sum([mw > 500, logp > 5, hbd > 5, hba > 10])
    return {
        "mw": round(mw, 1),
        "logp": round(logp, 2),
        "hbd": hbd,
        "hba": hba,
        "rotb": rdMolDescriptors.CalcNumRotatableBonds(mol),
        "tpsa": round(rdMolDescriptors.CalcTPSA(mol), 1),
        "heavy_atoms": mol.GetNumHeavyAtoms(),
        "lipinski_violations": violations,
        "formula": rdMolDescriptors.CalcMolFormula(mol),
    }


def depict_svg(mol: Chem.Mol, size=(320, 240), highlight: list[int] | None = None) -> str:
    m = Chem.Mol(mol)
    m.RemoveAllConformers()
    AllChem.Compute2DCoords(m)
    d = rdMolDraw2D.MolDraw2DSVG(*size)
    opts = d.drawOptions()
    opts.clearBackground = False
    opts.bondLineWidth = 1.6
    d.DrawMolecule(m, highlightAtoms=highlight or [])
    d.FinishDrawing()
    svg = d.GetDrawingText()
    return svg[svg.find("<svg"):]


def _embed(mol: Chem.Mol, seed: int = 42) -> Chem.Mol:
    molh = Chem.AddHs(mol)
    params = AllChem.ETKDGv3()
    params.randomSeed = seed
    if AllChem.EmbedMolecule(molh, params) != 0:
        params.useRandomCoords = True
        if AllChem.EmbedMolecule(molh, params) != 0:
            raise PipelineError(f"Could not generate 3D coordinates for {Chem.MolToSmiles(mol)}")
    try:
        AllChem.MMFFOptimizeMolecule(molh, maxIters=2000)
    except Exception:  # noqa: BLE001
        AllChem.UFFOptimizeMolecule(molh, maxIters=2000)
    return molh


def write_ligand_pdbqt(molh: Chem.Mol, out_pdbqt: Path):
    from meeko import MoleculePreparation, PDBQTWriterLegacy

    setups = MoleculePreparation().prepare(molh)
    pdbqt, ok, err = PDBQTWriterLegacy.write_string(setups[0])
    if not ok:
        raise PipelineError(f"Meeko could not prepare the ligand: {err}")
    out_pdbqt.write_text(pdbqt)


def prepare_ligand(smiles: str, out_pdbqt: Path, ph: float | None = None, seed: int = 42) -> Chem.Mol:
    """SMILES -> (optionally protonated) 3D conformer -> PDBQT. Returns the
    2D molecule that was docked."""
    mol = parse_compound(smiles)
    if ph is not None:
        mol = Chem.MolFromSmiles(protonate(Chem.MolToSmiles(mol), ph)) or mol
    write_ligand_pdbqt(_embed(mol, seed), out_pdbqt)
    return mol


def prepare_crystal_ligand(xtal: Chem.Mol, out_pdbqt: Path, ph: float | None = None) -> Chem.Mol:
    """Prepare the co-crystallized ligand for redocking, keeping its crystal
    ring geometry (standard self-docking). Vina randomizes position,
    orientation and torsions, so the crystal pose is not given away."""
    mol = Chem.Mol(xtal)
    if ph is not None:
        try:
            template = Chem.MolFromSmiles(protonate(Chem.MolToSmiles(xtal), ph))
            mol = AllChem.AssignBondOrdersFromTemplate(template, xtal)
            Chem.SanitizeMol(mol)
        except Exception:  # noqa: BLE001
            mol = Chem.Mol(xtal)
    write_ligand_pdbqt(Chem.AddHs(mol, addCoords=True), out_pdbqt)
    return mol


# --------------------------------------------------------------------------
# Docking
# --------------------------------------------------------------------------

def find_vina() -> str:
    env = os.environ.get("DOCKAPP_VINA")
    if env and Path(env).exists():
        return env
    exe = "vina.exe" if platform.system() == "Windows" else "vina"
    local = ROOT_DIR / "bin" / exe
    if local.exists():
        return str(local)
    found = shutil.which("vina")
    if found:
        return found
    raise PipelineError("AutoDock Vina was not found. Run `python scripts/setup_assets.py` first.")


def run_vina(receptor: Path, ligand: Path, box: dict, out: Path, exhaustiveness: int = 8,
             num_modes: int = 9, seed: int = 42, cpu: int = 0, flex: Path | None = None,
             timeout: float | None = None, register=None) -> list[float]:
    c, s = box["center"], box["size"]
    cmd = [
        find_vina(), "--receptor", str(receptor), "--ligand", str(ligand),
        "--center_x", str(c[0]), "--center_y", str(c[1]), "--center_z", str(c[2]),
        "--size_x", str(s[0]), "--size_y", str(s[1]), "--size_z", str(s[2]),
        "--exhaustiveness", str(exhaustiveness), "--num_modes", str(num_modes),
        "--seed", str(seed), "--out", str(out),
    ]
    if cpu:
        cmd += ["--cpu", str(cpu)]
    if flex:
        cmd += ["--flex", str(flex)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if register is not None:
        register(proc)  # so the run can be stopped while Vina is working
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        # One very flexible molecule can otherwise hold up a whole screening run.
        raise PipelineError(f"docking took longer than {timeout / 60:.0f} minutes and was stopped. "
                            "Molecules with many rotatable bonds are slow; raise the time limit in "
                            "Settings, or lower exhaustiveness, if you need this one.")
    finally:
        if register is not None:
            register(None)
    if proc.returncode != 0 or not out.exists():
        raise PipelineError(f"Vina failed: {(stderr or stdout).strip()[-800:]}")
    return [float(l.split()[3]) for l in out.read_text().splitlines() if l.startswith("REMARK VINA RESULT")]




def poses_to_mol(out_pdbqt: Path) -> Chem.Mol:
    """Docked poses as one RDKit mol (all H) with one conformer per pose."""
    from meeko import PDBQTMolecule, RDKitMolCreate

    pm = PDBQTMolecule.from_file(str(out_pdbqt), skip_typing=True)
    mols = RDKitMolCreate.from_pdbqt_mol(pm)
    if not mols or mols[0] is None:
        raise PipelineError("Could not convert docked poses back to a molecule.")
    return mols[0]


def write_poses_sdf(mol: Chem.Mol, scores: list[float], path: Path, name: str):
    w = Chem.SDWriter(str(path))
    for i, conf in enumerate(mol.GetConformers()):
        m = Chem.Mol(mol, confId=conf.GetId())
        m.SetProp("_Name", f"{name}_pose{i + 1}")
        if i < len(scores):
            m.SetProp("vina_affinity_kcal_mol", f"{scores[i]:.2f}")
        w.write(m, confId=conf.GetId())
    w.close()


def pose_mol(mol: Chem.Mol, idx: int) -> Chem.Mol:
    conf = mol.GetConformers()[idx]
    m = Chem.Mol(mol)
    m.RemoveAllConformers()
    m.AddConformer(Chem.Conformer(conf), assignId=True)
    return m


def _graph_only(mol: Chem.Mol) -> Chem.Mol:
    """Heavy-atom graph without bond orders or charges, so the RMSD match
    ignores protonation / tautomer differences."""
    m = Chem.RWMol(Chem.RemoveHs(mol, sanitize=False))
    for a in m.GetAtoms():
        a.SetFormalCharge(0)
        a.SetIsAromatic(False)
        a.SetNoImplicit(True)
        a.SetNumExplicitHs(0)
        a.SetChiralTag(Chem.ChiralType.CHI_UNSPECIFIED)
    for b in m.GetBonds():
        b.SetBondType(Chem.BondType.SINGLE)
        b.SetIsAromatic(False)
    m.UpdatePropertyCache(strict=False)
    Chem.FastFindRings(m)
    return m.GetMol()


# The atom of each residue that forms the covalent bond with an inhibitor.
REACTIVE_ATOMS = {"CYS": "SG", "SER": "OG", "THR": "OG1", "LYS": "NZ", "HIS": "NE2",
                  "TYR": "OH", "ASP": "OD2", "GLU": "OE2", "MET": "SD"}

# Common covalent warheads. The index is the atom of the SMARTS that attacks the residue.
WARHEADS = [
    ("acrylamide", "[CX3]=[CX3][CX3](=O)[NX3]", 0),
    ("vinyl sulfone", "[CX3]=[CX3][SX4](=O)=O", 0),
    ("chloroacetamide", "[Cl][CX4;H2][CX3](=O)[NX3]", 1),
    ("fluoroacetamide", "[F][CX4;H2][CX3](=O)[NX3]", 1),
    ("bromoacetamide", "[Br][CX4;H2][CX3](=O)[NX3]", 1),
    ("nitrile", "[NX1]#[CX2]", 1),
    ("aldehyde", "[CX3H1]=O", 0),
    ("epoxide", "[CX4]1[OX2][CX4]1", 0),
    ("boronic acid", "[BX3]([OX2H])[OX2H]", 0),
    ("beta-lactam", "[CX3]1(=O)[NX3][CX4][CX4]1", 0),
    ("alpha-keto amide", "[CX3](=O)[CX3](=O)[NX3]", 0),
]


def find_warhead(mol: Chem.Mol) -> tuple[str, int] | None:
    """The reactive group of a covalent inhibitor, and which atom does the attacking."""
    for name, smarts, idx in WARHEADS:
        patt = Chem.MolFromSmarts(smarts)
        if patt is None:
            continue
        hit = mol.GetSubstructMatches(patt)
        if hit:
            return name, hit[0][idx]
    return None


def reactive_atom_position(path: Path, residue: str) -> tuple[np.ndarray, str]:
    """The coordinates of the atom that would form the covalent bond, e.g. SG of Cys145."""
    found, _ = find_residues(path, residue)
    if len(found) != 1:
        raise PipelineError(f"Name exactly one residue for the covalent bond, not {len(found)}.")
    r = found[0]
    atom_name = REACTIVE_ATOMS.get(r["resname"])
    if atom_name is None:
        raise PipelineError(f"{r['resname']}{r['resnum']} does not have a side-chain atom that normally "
                            "forms a covalent bond. Use Cys, Ser, Thr, Lys, His, Tyr, Asp, Glu or Met.")
    for chain in read_structure(path)[0]:
        if chain.name != r["chain"]:
            continue
        for res in chain:
            if res.seqid.num == r["resnum"] and res.name == r["resname"]:
                for a in res:
                    if a.name == atom_name:
                        return np.array([a.pos.x, a.pos.y, a.pos.z]), f"{r['resname']}{r['resnum']} {atom_name}"
    raise PipelineError(f"{r['resname']}{r['resnum']} has no {atom_name} atom in this structure.")


# A covalent bond forms at roughly 1.8 A; a pose within this distance is close enough that the
# bond could form after the small rearrangement that real binding involves.
COVALENT_REACH = 4.0


def covalent_check(poses: Chem.Mol, target_xyz: np.ndarray, target_label: str) -> dict:
    """Could this compound actually reach the residue and react with it?

    Vina cannot form bonds, so poses are scored as if the compound only sits in the pocket. This
    measures, for each pose, how far the reactive group ends up from the residue it should attack.
    The warhead is located on the pose molecule itself so the atom numbering always matches.
    """
    hit = find_warhead(poses)
    if hit is None:
        return {"target": target_label, "warhead": None,
                "note": "No common covalent warhead was recognised in this compound, so no reactivity "
                        "distance could be measured."}
    name, idx = hit
    dists = []
    for i in range(poses.GetNumConformers()):
        p = poses.GetConformer(i).GetAtomPosition(idx)
        dists.append(round(float(np.linalg.norm(np.array([p.x, p.y, p.z]) - target_xyz)), 2))
    best = min(dists) if dists else None
    reachable = best is not None and best <= COVALENT_REACH
    return {
        "target": target_label, "warhead": name, "distances": dists, "closest": best,
        "reachable": reachable,
        "note": (f"The {name} group reaches {target_label} ({best:.1f} Å in the best pose), close enough "
                 f"that the covalent bond could form." if reachable else
                 f"The {name} group never comes closer than {best:.1f} Å to {target_label}, too far for "
                 f"the covalent bond to form in these poses." if best is not None else
                 "No poses to measure."),
    }


def symmetric_rmsd(probe: Chem.Mol, ref: Chem.Mol) -> float:
    """In-place (no superposition) symmetry-corrected heavy-atom RMSD."""
    p, r = _graph_only(probe), _graph_only(ref)
    matches = r.GetSubstructMatches(p, uniquify=False, useChirality=False, maxMatches=10000)
    if not matches:
        raise PipelineError("Docked ligand and crystal ligand do not have the same atoms.")
    pp, rp = p.GetConformer().GetPositions(), r.GetConformer().GetPositions()
    best = min(float(np.sqrt(((pp[list(range(len(m)))] - rp[list(m)]) ** 2).sum(axis=1).mean()))
               for m in matches)
    return best


# --------------------------------------------------------------------------
# Interactions (PLIP)
# --------------------------------------------------------------------------

def _ligand_pdb_lines(ligand: Chem.Mol, serial: int, resname: str, chain: str) -> list[str]:
    lig = Chem.Mol(ligand)
    counts = {}
    for a in lig.GetAtoms():
        el = a.GetSymbol()
        counts[el] = counts.get(el, 0) + 1
        nm = f"{el}{counts[el]}"[:4]
        info = Chem.AtomPDBResidueInfo(nm if len(nm) == 4 else f" {nm:<3}", residueName=resname,
                                       residueNumber=1, chainId=chain, isHeteroAtom=True)
        a.SetMonomerInfo(info)
    out = []
    for line in Chem.MolToPDBBlock(lig, flavor=0).splitlines():
        if line.startswith(("HETATM", "ATOM")):
            out.append(f"HETATM{int(line[6:11]) + serial:5d}{line[11:]}")
        elif line.startswith("CONECT"):
            nums = [int(line[i:i + 5]) + serial for i in range(6, len(line), 5) if line[i:i + 5].strip()]
            out.append("CONECT" + "".join(f"{x:5d}" for x in nums))
    return out


def _moved_atom(line: str, moved: dict) -> str:
    """Rewrite one receptor atom's coordinates if this pose moved it."""
    try:
        key = (line[21].strip(), int(line[22:26]), line[12:16].strip())
    except ValueError:
        return line
    xyz = moved.get(key)
    return f"{line[:30]}{xyz[0]:8.3f}{xyz[1]:8.3f}{xyz[2]:8.3f}{line[54:]}" if xyz else line


def write_complex(receptor_clean: Path, ligand: Chem.Mol, path: Path, reference: Chem.Mol | None = None,
                  moved: dict | None = None):
    """Receptor + docked ligand (residue LIG, chain Z) [+ crystal ligand XTL, chain Y].
    `moved` replaces flexible side-chain coordinates with where this pose put them."""
    # Serials must be contiguous (PLIP renumbers atoms sequentially), otherwise ligand CONECT records
    # point at the wrong atoms and the ligand's bonds are scrambled.
    rec = [l for l in receptor_clean.read_text().splitlines() if l.startswith(("ATOM", "HETATM"))]
    if moved:
        rec = [_moved_atom(l, moved) for l in rec]
    out = [f"{l[:6]}{i:5d}{l[11:]}" for i, l in enumerate(rec, 1)]
    serial = len(out) + 1
    out.append(f"TER   {serial:5d}")
    conect = []
    for mol, resname, chain in [(ligand, "LIG", "Z")] + ([(reference, "XTL", "Y")] if reference is not None else []):
        lines = _ligand_pdb_lines(mol, serial, resname, chain)
        out += [l for l in lines if l.startswith("HETATM")]
        conect += [l for l in lines if l.startswith("CONECT")]
        serial += mol.GetNumAtoms()
    path.write_text("\n".join(out + conect + ["END"]) + "\n")


INTERACTION_LABELS = {
    "hbond": "Hydrogen bond",
    "hydrophobic": "Hydrophobic",
    "pistacking": "π-stacking",
    "pication": "π-cation",
    "saltbridge": "Salt bridge",
    "halogen": "Halogen bond",
    "metal": "Metal",
    "waterbridge": "Water bridge",
}



def interactions(complex_pdb: Path) -> list[dict]:
    from plip.structure.preparation import PDBComplex


    pc = PDBComplex()
    pc.output_path = str(complex_pdb.parent)
    pc.load_pdb(str(complex_pdb))
    pc.analyze()
    site = next((s for k, s in pc.interaction_sets.items() if k.startswith("LIG:Z")), None)
    if site is None:
        return []
    found = []

    def xyz(obj):
        c = getattr(obj, "coords", None) or getattr(obj, "center", None)
        return [round(float(v), 3) for v in c] if c is not None else None

    def ends(kind, i):
        try:
            if kind == "hbond":
                return xyz(i.d), xyz(i.a)
            if kind == "hydrophobic":
                return xyz(i.bsatom), xyz(i.ligatom)
            if kind == "pistacking":
                return xyz(i.proteinring), xyz(i.ligandring)
            if kind == "pication":
                return xyz(i.ring), xyz(i.charge)
            if kind == "saltbridge":
                return xyz(i.positive), xyz(i.negative)
            if kind == "halogen":
                return xyz(i.don.x), xyz(i.acc.o)
            if kind == "metal":
                return xyz(i.metal), xyz(i.target.atom)
            if kind == "waterbridge":
                return xyz(i.water), xyz(i.d if not i.protisdon else i.a)
        except Exception:  # noqa: BLE001
            pass
        return None, None

    def add(kind, i, dist, detail=""):
        p1, p2 = ends(kind, i)
        found.append({
            "p1": p1,
            "p2": p2,
            "type": kind,
            "label": INTERACTION_LABELS[kind],
            "residue": f"{i.restype}{i.resnr}{i.reschain}",
            "restype": i.restype,
            "resnr": int(i.resnr),
            "chain": i.reschain,
            "distance": round(float(dist), 2),
            "detail": detail,
        })

    for i in site.hbonds_ldon + site.hbonds_pdon:
        add("hbond", i, i.distance_ad, "ligand donor" if not i.protisdon else "protein donor")
    for i in site.hydrophobic_contacts:
        add("hydrophobic", i, i.distance)
    for i in site.pistacking:
        add("pistacking", i, i.distance, {"P": "parallel", "T": "T-shaped"}.get(i.type, i.type))
    for i in site.pication_laro + site.pication_paro:
        add("pication", i, i.distance)
    for i in site.saltbridge_lneg + site.saltbridge_pneg:
        add("saltbridge", i, i.distance)
    for i in site.halogen_bonds:
        add("halogen", i, i.distance)
    for i in site.metal_complexes:
        add("metal", i, i.distance)
    for i in site.water_bridges:
        add("waterbridge", i, i.distance_dw if not i.protisdon else i.distance_aw,
            f"via water {i.water_orig_idx}")
    found.sort(key=lambda x: (x["resnr"], x["type"]))
    return found


def nearby_residues(receptor_clean: Path, ligand: Chem.Mol, cutoff: float = 4.5) -> list[dict]:
    st = gemmi.read_structure(str(receptor_clean))
    ns = gemmi.NeighborSearch(st[0], st.cell, 6).populate()
    seen = {}
    for p in ligand.GetConformer().GetPositions():
        for mark in ns.find_atoms(gemmi.Position(*p), "\0", radius=cutoff):
            cra = mark.to_cra(st[0])
            k = (cra.chain.name, cra.residue.seqid.num)
            seen[k] = {"chain": cra.chain.name, "resnum": cra.residue.seqid.num, "resname": cra.residue.name}
    return sorted(seen.values(), key=lambda r: (r["chain"], r["resnum"]))
