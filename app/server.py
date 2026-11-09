"""Local web server for the docking app. Run with:  python run.py"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from rdkit import Chem

import jobs
import pipeline as P

STATIC = P.APP_DIR / "static"
app = FastAPI(title="Docking App", version="1.1")


def _interrupted_jobs():
    """Jobs left 'running' by a previous server process will never finish."""
    for f in jobs.JOBS_DIR.glob("*/job.json") if jobs.JOBS_DIR.exists() else []:
        j = json.loads(f.read_text())
        if j.get("status") in ("queued", "running"):
            j["status"] = "failed"
            j["step"] = "Interrupted"
            j["error"] = "The app was closed before this job finished."
            f.write_text(json.dumps(j, indent=1))


_interrupted_jobs()


# ---------------------------------------------------------------- protein

def _register_protein(pid: str, source: str) -> dict:
    pdir = jobs.PROTEINS_DIR / pid
    path = next(pdir.glob("input.*"))
    try:
        info = P.list_ligands(path)
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(pdir, ignore_errors=True)
        raise HTTPException(400, f"Could not read the structure: {e}")
    meta = {"id": pid, "source": source, "title": info["title"], "format": path.suffix[1:]}
    (pdir / "meta.json").write_text(json.dumps(meta))
    return meta | info


class PdbRequest(BaseModel):
    pdb_id: str


@app.post("/api/protein/fetch")
def protein_fetch(req: PdbRequest):
    pid = req.pdb_id.strip().upper()
    if not re.fullmatch(r"[0-9][A-Z0-9]{3}", pid):  # before building a path: the error branch below deletes pdir
        raise HTTPException(400, f"'{pid}' is not a valid 4-character PDB ID.")
    pdir = jobs.PROTEINS_DIR / pid
    if not list(pdir.glob("input.*")):
        try:
            P.fetch_pdb(pid, pdir)
        except P.PipelineError as e:
            shutil.rmtree(pdir, ignore_errors=True)
            raise HTTPException(400, str(e))
    return _register_protein(pid, f"PDB {pid}")


@app.post("/api/protein/upload")
async def protein_upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".pdb", ".ent", ".cif", ".mmcif", ".mol2"):
        raise HTTPException(400, "Upload a .pdb, .cif or .mol2 file.")
    pid = "upload-" + uuid.uuid4().hex[:8]
    pdir = jobs.PROTEINS_DIR / pid
    pdir.mkdir(parents=True)
    data = await file.read()
    if suffix == ".mol2":
        raw = pdir / "original.mol2"
        raw.write_bytes(data)
        try:
            P.mol2_to_cif(raw, pdir / "input.cif")
        except Exception as e:  # noqa: BLE001
            shutil.rmtree(pdir, ignore_errors=True)
            raise HTTPException(400, f"Could not read the MOL2 file: {e}")
    else:
        ext = "cif" if suffix in (".cif", ".mmcif") else "pdb"
        (pdir / f"input.{ext}").write_bytes(data)
    return _register_protein(pid, file.filename)


@app.get("/api/proteins")
def protein_list():
    """Saved protein library: every structure loaded before, newest first."""
    out = []
    for f in jobs.PROTEINS_DIR.glob("*/meta.json") if jobs.PROTEINS_DIR.exists() else []:
        try:
            out.append(json.loads(f.read_text()) | {"saved": f.stat().st_mtime})
        except Exception:  # noqa: BLE001
            pass
    return sorted(out, key=lambda m: m["saved"], reverse=True)


def _protein_dir(pid: str) -> Path:
    pdir = jobs.PROTEINS_DIR / Path(pid).name
    if not (pdir / "meta.json").exists() or pdir.parent != jobs.PROTEINS_DIR:
        raise HTTPException(404, "This protein is no longer in the library.")
    return pdir


@app.get("/api/protein/{pid}")
def protein_open(pid: str):
    pdir = _protein_dir(pid)
    meta = json.loads((pdir / "meta.json").read_text())
    (pdir / "meta.json").touch()  # most recently used goes to the top of the library
    return meta | P.list_ligands(next(pdir.glob("input.*")))


@app.delete("/api/protein/{pid}")
def protein_delete(pid: str):
    """Removes the protein from the library. Finished runs keep their own prepared copies."""
    shutil.rmtree(_protein_dir(pid))
    return {"ok": True}


@app.get("/api/protein/{pid}/structure.{ext}")
def protein_structure(pid: str, ext: str):
    pdir = jobs.PROTEINS_DIR / Path(pid).name
    files = list(pdir.glob("input.*"))
    if not files:
        raise HTTPException(404)
    return FileResponse(files[0], media_type="text/plain")


class SiteRequest(BaseModel):
    residues: str


@app.post("/api/protein/{pid}/site")
def protein_site(pid: str, req: SiteRequest):
    files = list((jobs.PROTEINS_DIR / Path(pid).name).glob("input.*"))
    if not files:
        raise HTTPException(404)
    try:
        return P.site_box(files[0], req.residues)
    except P.PipelineError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------- compounds

class SmilesRequest(BaseModel):
    smiles: str


@app.post("/api/compound/check")
def compound_check(req: SmilesRequest):
    try:
        mol = P.parse_compound(req.smiles)
    except P.PipelineError as e:
        raise HTTPException(400, str(e))
    return {"smiles": Chem.MolToSmiles(mol), "svg": P.depict_svg(mol, (220, 160)), "props": P.properties(mol)}


@app.post("/api/compound/upload")
async def compound_upload(file: UploadFile = File(...)):
    """Import compounds from an SDF/MOL (e.g. downloaded from PubChem) or a .smi/.txt list."""
    suffix = Path(file.filename or "").suffix.lower()
    text = (await file.read()).decode("utf-8", errors="replace")
    mols = []
    if suffix in (".sdf", ".sd", ".mol"):
        sup = Chem.SDMolSupplier()
        sup.SetData(text if text.rstrip().endswith("$$$$") or suffix != ".mol" else text + "\n$$$$\n")
        for i, m in enumerate(sup):
            if m is None:
                continue
            name = m.GetProp("_Name").strip() if m.HasProp("_Name") else ""
            if m.HasProp("PUBCHEM_COMPOUND_CID") and (not name or name == m.GetProp("PUBCHEM_COMPOUND_CID")):
                name = f"CID {m.GetProp('PUBCHEM_COMPOUND_CID')}"
            mols.append((name or f"{Path(file.filename).stem} {i + 1}", Chem.RemoveHs(m)))
    elif suffix == ".mol2":
        # Tripos files hold one or many molecules, each starting with @<TRIPOS>MOLECULE.
        blocks = [b for b in text.split("@<TRIPOS>MOLECULE") if b.strip()]
        for i, block in enumerate(blocks):
            m = Chem.MolFromMol2Block("@<TRIPOS>MOLECULE" + block, removeHs=True)
            if m is None:
                m = Chem.MolFromMol2Block("@<TRIPOS>MOLECULE" + block, removeHs=True, sanitize=False)
                if m is not None:
                    try:
                        Chem.SanitizeMol(m)
                    except Exception:  # noqa: BLE001
                        m = None
            if m is not None:
                name = m.GetProp("_Name").strip() if m.HasProp("_Name") else ""
                mols.append((name or f"{Path(file.filename).stem} {i + 1}", m))
    elif suffix in (".smi", ".txt", ".csv"):
        for i, line in enumerate(text.splitlines()):
            parts = line.replace(",", " ").replace("\t", " ").split()
            if not parts or parts[0].startswith("#"):
                continue
            m = Chem.MolFromSmiles(parts[0])
            if m is None and len(parts) > 1:
                m, parts = Chem.MolFromSmiles(parts[-1]), [parts[-1]] + parts[:-1]
            if m is not None:
                mols.append((" ".join(parts[1:]) or f"Compound {i + 1}", m))
    else:
        raise HTTPException(400, "Upload an .sdf, .mol, .mol2 or .smi file.")
    if not mols:
        raise HTTPException(400, "No readable structures found in the file.")
    if len(mols) > MAX_COMPOUNDS:
        raise HTTPException(400, f"That file holds {len(mols)} structures. One run takes at most "
                                 f"{MAX_COMPOUNDS}; split the file or filter it first.")
    return _compound_list(mols)


MAX_COMPOUNDS = 500          # one screening run on a desktop PC
DRAW_LIMIT = 50              # above this, skip the 2D pictures: they dominate the page and the payload


def _compound_list(mols: list[tuple[str, Chem.Mol]]) -> list[dict]:
    """Compounds ready for the UI. Big sets come back without pictures so the page stays usable."""
    draw = len(mols) <= DRAW_LIMIT
    out, bad = [], 0
    for name, m in mols:
        try:
            m = P.parse_compound(Chem.MolToSmiles(m))
        except P.PipelineError:
            bad += 1
            continue
        out.append({"name": name, "smiles": Chem.MolToSmiles(m),
                    "svg": P.depict_svg(m, (220, 160)) if draw else "",
                    "props": P.properties(m)})
    if not out:
        raise HTTPException(400, "None of those structures could be read as a compound.")
    return out


class LibraryRequest(BaseModel):
    query: str = ""
    target_id: str = ""
    limit: int = Field(200, ge=1, le=MAX_COMPOUNDS)


@app.post("/api/compound/library")
def compound_library(req: LibraryRequest):
    """Find a target in ChEMBL, or pull its measured compounds as a ready-made screening set."""
    try:
        if not req.target_id:
            if not req.query.strip():
                raise HTTPException(400, "Type a target name, for example acetylcholinesterase.")
            targets = P.chembl_targets(req.query)
            if not targets:
                raise HTTPException(404, f"No single-protein target in ChEMBL matches '{req.query}'.")
            return {"targets": targets}
        actives = P.chembl_actives(req.target_id, req.limit)
    except P.PipelineError as e:
        raise HTTPException(502, str(e))
    mols = []
    for a in actives:
        m = Chem.MolFromSmiles(a["smiles"])
        if m is not None:
            mols.append((a["name"], m))
    if not mols:
        raise HTTPException(502, "ChEMBL returned compounds that could not be read.")
    return {"compounds": _compound_list(mols)}


# ---------------------------------------------------------------- jobs

class Compound(BaseModel):
    name: str = ""
    smiles: str


class JobRequest(BaseModel):
    protein_id: str
    ligand_key: str = ""
    site_residues: str = ""
    box_center: list[float] | None = None
    box_size: list[float] | None = None
    compounds: list[Compound] = Field(default_factory=list)
    exhaustiveness: int = Field(8, ge=1, le=64)
    num_modes: int = Field(9, ge=1, le=20)
    padding: float = Field(10.0, ge=2, le=30)
    seed: int = 42
    protonate: bool = False
    keep_waters: bool = False
    redock: bool = True
    dock_reference: bool = True
    flex_residues: str = ""
    covalent_residue: str = ""
    screen_top: int = Field(10, ge=1, le=50)
    timeout_minutes: float = Field(10, ge=0.5, le=180)
    workers: int = Field(0, ge=0, le=16)  # 0 = decide from the number of CPU cores


@app.post("/api/jobs")
def job_create(req: JobRequest):
    if not (jobs.PROTEINS_DIR / Path(req.protein_id).name / "meta.json").exists():
        raise HTTPException(400, "Load a protein first.")
    if not req.compounds and (not req.ligand_key or not (req.redock or req.dock_reference)):
        raise HTTPException(400, "Add at least one compound.")
    if len(req.compounds) > MAX_COMPOUNDS:
        raise HTTPException(400, f"{len(req.compounds)} compounds is more than one run can take "
                                 f"({MAX_COMPOUNDS}). Remove some, or split them over two runs.")
    if not req.ligand_key:
        if not req.box_center and not req.site_residues.strip():
            raise HTTPException(400, "Choose a reference ligand or define the binding site.")
        if not req.box_center:
            try:
                P.site_box(next((jobs.PROTEINS_DIR / Path(req.protein_id).name).glob("input.*")), req.site_residues)
            except P.PipelineError as e:
                raise HTTPException(400, str(e))
    if req.flex_residues.strip():
        try:
            P.flex_residue_list(next((jobs.PROTEINS_DIR / Path(req.protein_id).name).glob("input.*")),
                                req.flex_residues)
        except P.PipelineError as e:
            raise HTTPException(400, str(e))
    for c in req.compounds:
        if Chem.MolFromSmiles(c.smiles) is None:
            raise HTTPException(400, f"Invalid SMILES for {c.name or c.smiles}")
    settings = req.model_dump(exclude={"protein_id", "ligand_key", "compounds"})
    jid = jobs.create_job(req.protein_id, req.ligand_key, [c.model_dump() for c in req.compounds], settings)
    return {"id": jid}


class AddCompoundsRequest(BaseModel):
    compounds: list[Compound]


@app.post("/api/jobs/{jid}/compounds")
def job_add_compounds(jid: str, req: AddCompoundsRequest):
    if not req.compounds:
        raise HTTPException(400, "Add at least one compound.")
    if len(req.compounds) > MAX_COMPOUNDS:
        raise HTTPException(400, f"{len(req.compounds)} compounds is more than one run can take ({MAX_COMPOUNDS}).")
    for c in req.compounds:
        if Chem.MolFromSmiles(c.smiles) is None:
            raise HTTPException(400, f"Invalid SMILES for {c.name or c.smiles}")
    try:
        n = jobs.add_compounds(Path(jid).name, [c.model_dump() for c in req.compounds])
    except jobs.JobError as e:
        raise HTTPException(400, str(e))
    return {"id": jid, "added": n}


@app.post("/api/jobs/{jid}/cancel")
def job_cancel(jid: str):
    """Stop a run. Compounds already docked are kept and the report is written for them."""
    try:
        jobs.cancel_job(Path(jid).name)
    except jobs.JobError as e:
        raise HTTPException(400, str(e))
    return {"id": jid, "cancelled": True}


@app.get("/api/jobs")
def job_list():
    return jobs.list_jobs()


@app.get("/api/jobs/{jid}")
def job_get(jid: str):
    job = jobs.get_job(Path(jid).name)
    if not job:
        raise HTTPException(404)
    return job


@app.delete("/api/jobs/{jid}")
def job_delete(jid: str):
    jobs.delete_job(Path(jid).name)
    return {"ok": True}


def _job_file(jid: str, rel: str) -> Path:
    base = (jobs.JOBS_DIR / Path(jid).name).resolve()
    f = (base / rel).resolve()
    if base not in f.parents or not f.exists():
        raise HTTPException(404)
    return f


@app.get("/api/jobs/{jid}/file/{rel:path}")
def job_file(jid: str, rel: str):
    f = _job_file(jid, rel)
    if f.suffix == ".html":
        return HTMLResponse(f.read_text(encoding="utf-8"))
    if f.suffix == ".zip":
        return FileResponse(f, filename=f"docking-{jid}.zip")
    if f.suffix == ".csv":
        return FileResponse(f, filename=f"docking-{jid}.csv", media_type="text/csv")
    return PlainTextResponse(f.read_text())


@app.get("/api/jobs/{jid}/report/download")
def job_report_download(jid: str):
    return FileResponse(_job_file(jid, "report.html"), filename=f"docking-report-{jid}.html",
                        media_type="text/html")


def _pose_dir(jid: str, rel: str) -> Path:
    d = _job_file(jid, rel)
    if not (d / "docked.pdbqt").exists():
        raise HTTPException(404)
    return d


@app.get("/api/jobs/{jid}/pose")
def job_pose(jid: str, dir: str, pose: int = 0):
    """Receptor + chosen pose (+ crystal ligand) as one PDB, plus its interactions."""
    d = _pose_dir(jid, dir)
    job_dir = jobs.JOBS_DIR / Path(jid).name
    complex_pdb = d / f"complex_pose{pose + 1}.pdb"
    info_json = d / f"pose{pose + 1}.json"
    if not info_json.exists():
        poses = P.poses_to_mol(d / "docked.pdbqt")
        if not 0 <= pose < poses.GetNumConformers():
            raise HTTPException(404)
        m = P.pose_mol(poses, pose)
        xf = job_dir / "crystal_ligand.sdf"
        xtal = next(Chem.SDMolSupplier(str(xf))) if xf.exists() else None
        P.write_complex(job_dir / "receptor_clean.pdb", Chem.RemoveHs(m), complex_pdb, reference=xtal)
        plip_pdb = d / "_plip_tmp.pdb"
        P.write_complex(job_dir / "receptor_clean.pdb", m, plip_pdb)
        try:
            inter = P.interactions(plip_pdb)
        except Exception:  # noqa: BLE001
            inter = []
        plip_pdb.unlink(missing_ok=True)
        info = {
            "pose": pose,
            "interactions": inter,
            "site": P.nearby_residues(job_dir / "receptor_clean.pdb", m),
            "rmsd_to_crystal": round(P.symmetric_rmsd(m, xtal), 2) if dir == "redock" and xtal is not None else None,
        }
        info_json.write_text(json.dumps(info))
    return json.loads(info_json.read_text())


@app.get("/api/jobs/{jid}/complex/{pose}/{rel:path}")
def job_pose_pdb(jid: str, pose: int, rel: str):
    """URL ends in .../<pose dir>/complex.pdb so viewers recognise the format."""
    dir = rel.removesuffix("/complex.pdb")
    d = _pose_dir(jid, dir)
    f = d / f"complex_pose{pose + 1}.pdb"
    if not f.exists():
        job_pose(jid, dir, pose)
    return PlainTextResponse(f.read_text())


@app.get("/api/ping")
def ping():
    """Cheap readiness check for the app window (health also loads PLIP and runs Vina)."""
    return {"ok": True}


@app.get("/api/health")
def health():
    try:
        vina = P.find_vina()
    except P.PipelineError as e:
        return {"ok": False, "error": str(e)}
    ketcher = (STATIC / "vendor" / "ketcher" / "index.html").exists()
    molstar = (STATIC / "vendor" / "molstar" / "molstar.js").exists()
    return {"ok": ketcher and molstar, "vina": vina, "ketcher": ketcher, "molstar": molstar,
            "versions": jobs.versions()}


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
