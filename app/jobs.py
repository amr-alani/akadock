"""Background docking jobs: one job = one protein + a batch of compounds."""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rdkit import Chem

import pipeline as P
import report

# AKADOCK_WORKSPACE keeps runs and saved proteins somewhere else (another disk, or a test run).
WORKSPACE = Path(os.environ.get("AKADOCK_WORKSPACE") or P.ROOT_DIR / "workspace")
JOBS_DIR = WORKSPACE / "jobs"
PROTEINS_DIR = WORKSPACE / "proteins"

_executor = ThreadPoolExecutor(max_workers=1)  # Vina already uses every CPU core
SCREEN_AFTER = 25  # from this many compounds a run is a screen, not a comparison
SCREEN_TOP = 10    # how many of the best get the full interaction analysis
TIMEOUT_MINUTES = 10  # per compound, so one very flexible molecule cannot stall a screen
_lock = threading.Lock()
_jobs: dict[str, dict] = {}
_cancelled: set[str] = set()                      # runs the user asked to stop
_running_vina: dict[str, set] = {}                # jid -> the Vina processes working right now


class Cancelled(Exception):
    """Raised inside a worker when the user stopped the run."""


def cancel_job(jid: str) -> bool:
    """Ask a run to stop: no new compounds start, and the ones docking now are killed."""
    job = get_job(jid)
    if job is None:
        raise JobError("Run not found.")
    if job["status"] not in ("queued", "running"):
        raise JobError("That run has already finished.")
    with _lock:
        _cancelled.add(jid)
        procs = list(_running_vina.get(jid, ()))
    for proc in procs:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    _update(jid, log="Stopping the run at your request…")
    return True


def _vina_registrar(jid: str):
    """Give run_vina a way to say which process is working, so cancel_job can kill it."""
    current = {}

    def register(proc):
        with _lock:
            procs = _running_vina.setdefault(jid, set())
            if proc is None:
                procs.discard(current.pop("p", None))
            else:
                current["p"] = proc
                procs.add(proc)
    return register


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")[:40] or "compound"


def _save(job: dict):
    d = JOBS_DIR / job["id"]
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "job.json.tmp"
    tmp.write_text(json.dumps(job, indent=1))
    # On Windows the replace fails while anything (a reader, antivirus, the indexer) has job.json open.
    for attempt in range(50):
        try:
            tmp.replace(d / "job.json")
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.05)


def get_job(jid: str) -> dict | None:
    with _lock:
        if jid in _jobs:
            return json.loads(json.dumps(_jobs[jid]))
    f = JOBS_DIR / jid / "job.json"
    if f.exists():
        return json.loads(f.read_text())
    return None


def list_jobs() -> list[dict]:
    out = []
    if JOBS_DIR.exists():
        for f in JOBS_DIR.glob("*/job.json"):
            try:
                j = get_job(f.parent.name)  # jobs of this session come from memory, not the file being rewritten
                out.append({k: j.get(k) for k in ("id", "status", "created", "protein", "progress")}
                           | {"n_compounds": len(j.get("results", []))})
            except Exception:  # noqa: BLE001
                pass
    return sorted(out, key=lambda j: j.get("created") or 0, reverse=True)


def create_job(protein_id: str, ligand_key: str, compounds: list[dict], settings: dict) -> str:
    pdir = PROTEINS_DIR / protein_id
    meta = json.loads((pdir / "meta.json").read_text())
    jid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    job = {
        "id": jid,
        "status": "queued",
        "progress": 0,
        "step": "Waiting to start",
        "log": [],
        "created": time.time(),
        "settings": settings,
        "protein": {"id": protein_id, "source": meta.get("source"), "title": meta.get("title"),
                    "ligand": ligand_key},
        "box": None,
        "validation": None,
        "results": [
            {"idx": i, "name": c.get("name") or f"Compound {i + 1}", "smiles": c["smiles"], "status": "queued"}
            for i, c in enumerate(compounds)
        ],
    }
    with _lock:
        _jobs[jid] = job
        _save(job)
    _executor.submit(_run, jid)
    return jid


def _update(jid: str, **kw):
    with _lock:
        job = _jobs[jid]
        log = kw.pop("log", None)
        job.update(kw)
        if log:
            job["log"].append(f"{time.strftime('%H:%M:%S')}  {log}")
        _save(job)


def _set_result(jid: str, idx: int, **kw):
    with _lock:
        _jobs[jid]["results"][idx].update(kw)
        _save(_jobs[jid])


def _verdict(rmsds: list[float]) -> tuple[str, str]:
    top, best3 = rmsds[0], min(rmsds[:3])
    if top <= 2.0:
        return "pass", f"Top-ranked pose reproduces the crystal pose (RMSD {top:.2f} Å ≤ 2.0 Å)."
    if best3 <= 2.0:
        k = rmsds.index(best3) + 1
        return "warn", (f"Top-ranked pose deviates (RMSD {top:.2f} Å), but pose {k} reproduces the crystal pose "
                        f"(RMSD {best3:.2f} Å). Interpret rankings with care.")
    best = min(rmsds)
    hint = ""
    if best <= 2.0:
        k = rmsds.index(best) + 1
        hint = (f" Vina did find the crystal pose (pose {k}, RMSD {best:.2f} Å) but scored it lower, so the "
                f"scoring function does not favour the experimental binding mode here.")
    return "fail", (f"Redocking did not reproduce the crystal pose as a top-ranked pose (top RMSD {top:.2f} Å)."
                    f"{hint} Check protonation, waters or cofactors before trusting the ranking.")


def _dock_one(jid, job_dir: Path, name: str, mol_for_props: Chem.Mol, lig_pdbqt: Path, box, settings,
              receptor_pdbqt: Path, receptor_clean: Path, out_dir: Path, flex: Path | None = None,
              light: bool = False, cpu: int = 0, register=None) -> dict:
    out = out_dir / "docked.pdbqt"
    scores = P.run_vina(receptor_pdbqt, lig_pdbqt, box, out,
                        exhaustiveness=int(settings.get("exhaustiveness", 8)),
                        num_modes=int(settings.get("num_modes", 9)),
                        seed=int(settings.get("seed", 42)), flex=flex, cpu=cpu, register=register,
                        timeout=60 * float(settings.get("timeout_minutes", TIMEOUT_MINUTES)))
    poses = P.poses_to_mol(out)
    P.write_poses_sdf(poses, scores, out_dir / "poses.sdf", slug(name))
    props = P.properties(mol_for_props)
    res = {
        "scores": scores,
        "best": scores[0],
        "le": round(-scores[0] / max(props["heavy_atoms"], 1), 3),
        "props": props,
        "dir": str(out_dir.relative_to(job_dir)),
    }
    # Screening a library: the picture and the interaction profile are done later, for the winners only.
    if not light:
        res.update(_describe_pose(jid, name, mol_for_props, poses, out, out_dir, receptor_clean, flex))
    return res, poses


def _describe_pose(jid, name, mol, poses, out_pdbqt: Path, out_dir: Path, receptor_clean: Path,
                   flex: Path | None) -> dict:
    """The expensive per-compound extras: the complex file, the PLIP interactions and the 2D picture."""
    P.write_complex(receptor_clean, P.pose_mol(poses, 0), out_dir / "complex_top_pose.pdb",
                    moved=P.flex_sidechain_coords(out_pdbqt) if flex else None)
    try:
        inter = P.interactions(out_dir / "complex_top_pose.pdb")
    except Exception as e:  # noqa: BLE001
        inter = []
        _update(jid, log=f"PLIP interaction analysis failed for {name}: {e}")
    return {"interactions": inter, "svg": P.depict_svg(mol)}


def _run(jid: str):
    job = get_job(jid)
    job_dir = JOBS_DIR / jid
    settings = job["settings"]
    ph = 7.4 if settings.get("protonate") else None
    try:
        _update(jid, status="running", step="Preparing the protein", progress=2,
                log="Job started")
        pdir = PROTEINS_DIR / job["protein"]["id"]
        src = next(pdir.glob("input.*"))
        key = job["protein"]["ligand"]

        if key:
            xtal, note = P.extract_crystal_ligand(src, key)
            w = Chem.SDWriter(str(job_dir / "crystal_ligand.sdf"))
            w.write(xtal)
            w.close()
            box = P.docking_box(xtal, padding=float(settings.get("padding", 10)))
            _update(jid, box=box, log=f"Co-crystallized ligand {key}: {Chem.MolToSmiles(xtal)} ({note})")
            _update(jid, log=f"Docking box centered on the ligand at {box['center']}, size {box['size']} Å")
        else:
            settings["redock"] = False
            if settings.get("box_center"):
                box = {"center": settings["box_center"], "size": settings.get("box_size") or [22, 22, 22]}
                where = "the coordinates you entered"
            else:
                box = P.site_box(src, settings.get("site_residues", ""))
                where = "residues " + " ".join(f"{r['resname']}{r['resnum']}" for r in box["residues"])
            _update(jid, box=box, settings=settings,
                    log=f"No reference ligand: docking box centered on {where} at {box['center']}, "
                        f"size {box['size']} Å. Redocking validation skipped.")
            if box.get("warning"):
                _update(jid, log=f"Warning: {box['warning']}")

        if metal := P.metal_near_box(src, box):
            box["warning"] = (box.get("warning", "") + " " + metal).strip()
            _update(jid, box=box, log=f"Warning: {metal}")

        keep_w = bool(settings.get("keep_waters")) and bool(key)
        receptor_clean, receptor_pdbqt = P.prepare_receptor(src, job_dir, keep_metals=True,
                                                            waters_near=xtal if keep_w else None)
        if keep_w:
            n_w = sum(1 for l in receptor_clean.read_text().splitlines() if l[17:20] in P.WATERS and l[12:16].strip() == "O")
            _update(jid, progress=8, log=f"Receptor cleaned; kept {n_w} crystal water(s) within 3.5 Å of {key.split(':')[0]} "
                                         f"as part of the rigid receptor; hydrogens added at pH 7.4")
        else:
            _update(jid, progress=8, log="Receptor cleaned (waters and other ligands removed), hydrogens added at pH 7.4")

        flex_pdbqt = None
        if settings.get("flex_residues", "").strip():
            receptor_pdbqt, flex_pdbqt, flex_found = P.prepare_flexible_receptor(
                receptor_clean, settings["flex_residues"], job_dir)
            names = " ".join(f"{r['chain']}:{r['resname']}{r['resnum']}" for r in flex_found)
            _update(jid, settings={**settings, "flex_found": flex_found},
                    log=f"Flexible side chains: {names}. These can move while docking, which is slower "
                        "and searches a larger space — treat the scores as comparable only within this run.")

        if key and settings.get("dock_reference"):
            _add_reference_compound(jid, key, xtal)
        idxs = [r["idx"] for r in get_job(jid)["results"]]
        steps = len(idxs) + (1 if settings.get("redock", True) else 0)
        done = 0

        if settings.get("redock", True):
            _update(jid, step=f"Validating: redocking {key.split(':')[0]}", progress=10,
                    log="Redocking the co-crystallized ligand to validate the setup")
            rd = job_dir / "redock"
            rd.mkdir(exist_ok=True)
            mol = P.prepare_crystal_ligand(xtal, rd / "ligand.pdbqt", ph)
            res, poses = _dock_one(jid, job_dir, f"{key.split(':')[0]} (redock)", mol, rd / "ligand.pdbqt", box,
                                   settings, receptor_pdbqt, receptor_clean, rd, flex_pdbqt)
            rmsds = _rmsds(poses, xtal)
            verdict, text = _verdict(rmsds)
            res.update({"name": f"{key.split(':')[0]} (co-crystallized ligand)", "smiles": Chem.MolToSmiles(xtal),
                        "rmsds": rmsds, "verdict": verdict, "verdict_text": text, "bond_order_note": note})
            _update(jid, validation=res, log=f"Redock: {res['best']:.2f} kcal/mol, top-pose RMSD {rmsds[0]:.2f} Å "
                                             f"→ {verdict.upper()}")
            done += 1

        _dock_compounds(jid, idxs, box, settings, receptor_pdbqt, receptor_clean, xtal if key else None, ph, done,
                        steps, flex_pdbqt)
        _finish(jid)
    except Cancelled:
        _stopped(jid)
    except Exception as e:  # noqa: BLE001
        if jid in _cancelled:
            _stopped(jid)
        else:
            traceback.print_exc()
            _update(jid, status="failed", step="Failed", log=f"Error: {e}", error=str(e))
    finally:
        _forget_run(jid)


def _stopped(jid: str):
    """Finish a stopped run properly: keep what was docked, and write the report for it."""
    done = [r for r in get_job(jid)["results"] if r.get("status") == "done"]
    _update(jid, log=f"Run stopped. {len(done)} compound(s) finished and are kept.")
    try:
        _finish(jid)
    except Exception:  # noqa: BLE001
        pass
    _update(jid, status="cancelled", step="Stopped", error=None)


def _forget_run(jid: str):
    with _lock:
        _cancelled.discard(jid)
        _running_vina.pop(jid, None)


def _rmsds(poses: Chem.Mol, xtal: Chem.Mol) -> list[float]:
    return [round(P.symmetric_rmsd(P.pose_mol(poses, i), xtal), 2) for i in range(poses.GetNumConformers())]


def _add_reference_compound(jid: str, key: str, xtal: Chem.Mol):
    """Dock the co-crystallized ligand from its SMILES like any lead compound (ETKDG conformer, no crystal
    geometry), so it can be ranked fairly against the leads. Goes first in the compound list."""
    with _lock:
        results = _jobs[jid]["results"]
        results.insert(0, {"name": f"{key.split(':')[0]} (from SMILES)", "smiles": Chem.MolToSmiles(xtal),
                           "status": "queued", "reference": True})
        for i, r in enumerate(results):
            r["idx"] = i
        _save(_jobs[jid])
    _update(jid, log=f"The co-crystallized ligand will also be docked from its SMILES, like the other compounds")


def _covalent_target(jid: str, settings: dict):
    """The residue a covalent inhibitor should attack, as (coordinates, label), or None."""
    residue = (settings.get("covalent_residue") or "").strip()
    if not residue:
        return None
    try:
        xyz, label = P.reactive_atom_position(JOBS_DIR / jid / "receptor_clean.pdb", residue)
    except P.PipelineError as e:
        _update(jid, log=f"Covalent check skipped: {e}")
        return None
    _update(jid, log=f"Covalent check: measuring how close each compound's reactive group gets to {label}.")
    return xyz, label


def worker_plan(settings, n_compounds: int) -> tuple[int, int]:
    """How many compounds to dock at once, and how many CPU cores each one gets.

    Vina alone does not use many cores well on a small ligand, so several at once finishes a library
    sooner than one at a time. Cores are divided between them so the machine is not oversubscribed.
    """
    cores = os.cpu_count() or 4
    want = int(settings.get("workers", 0))
    workers = want if want > 0 else max(1, min(cores // 2, 8))
    workers = max(1, min(workers, n_compounds, cores))
    return workers, max(1, cores // workers)


def _dock_compounds(jid, idxs, box, settings, receptor_pdbqt: Path, receptor_clean: Path, xtal, ph, done, steps,
                    flex: Path | None = None):
    job_dir = JOBS_DIR / jid
    # Screening a library: study only the best ones afterwards (see _describe_top).
    screening = len(idxs) >= int(settings.get("screen_after", SCREEN_AFTER))
    workers, cpus = worker_plan(settings, len(idxs))
    covalent = _covalent_target(jid, settings)
    if screening:
        _update(jid, screening=True,
                log=f"Screening mode: {len(idxs)} compounds. Each one is docked and scored; the pictures and "
                    f"the interaction analysis are done afterwards for the best {int(settings.get('screen_top', SCREEN_TOP))}.")
    if workers > 1:
        _update(jid, log=f"Docking {workers} compounds at a time, {cpus} CPU core(s) each.")

    def dock(i: int):
        """Runs in a worker thread: ligand preparation and Vina only, never PLIP."""
        if jid in _cancelled:
            raise Cancelled
        r = get_job(jid)["results"][i]
        cd = job_dir / "compounds" / f"{i + 1:02d}_{slug(r['name'])}"
        cd.mkdir(parents=True, exist_ok=True)
        _set_result(jid, i, status="running")
        t0 = time.time()
        mol = P.prepare_ligand(r["smiles"], cd / "ligand.pdbqt", ph)
        res, poses = _dock_one(jid, job_dir, r["name"], mol, cd / "ligand.pdbqt", box, settings,
                               receptor_pdbqt, receptor_clean, cd, flex, light=True, cpu=cpus,
                               register=_vina_registrar(jid))
        res.update(docked_smiles=Chem.MolToSmiles(mol), seconds=round(time.time() - t0, 1))
        return r, res, poses, mol, cd

    finished = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(dock, i): i for i in idxs}
        for fut in as_completed(futures):
            i = futures[fut]
            name = get_job(jid)["results"][i]["name"]
            try:
                r, res, poses, mol, cd = fut.result()
                if r.get("reference") and xtal is not None:
                    res["rmsds"] = _rmsds(poses, xtal)
                if covalent is not None:
                    res["covalent"] = P.covalent_check(poses, *covalent)
                # PLIP and the 2D picture stay on this one thread: they are not safe to run in parallel.
                if not screening:
                    res.update(_describe_pose(jid, name, mol, poses, cd / "docked.pdbqt", cd, receptor_clean, flex))
                _set_result(jid, i, status="done", **res)
                extra = f", top-pose RMSD to crystal {res['rmsds'][0]:.2f} Å" if "rmsds" in res else ""
                _update(jid, log=f"{name}: {res['best']:.2f} kcal/mol ({res['seconds']} s{extra})")
            except Cancelled:
                _set_result(jid, i, status="cancelled", error="stopped before this compound started")
            except Exception as e:  # noqa: BLE001
                if jid in _cancelled:
                    _set_result(jid, i, status="cancelled", error="stopped while docking")
                else:
                    _set_result(jid, i, status="failed", error=str(e))
                    _update(jid, log=f"{name}: failed ({e})")
            finished += 1
            _update(jid, step=f"Docked {finished} of {len(idxs)} compounds",
                    progress=int(10 + 85 * (done + finished) / steps))
    if jid in _cancelled:
        raise Cancelled
    if screening:
        _describe_top(jid, settings, receptor_clean, flex)


def _describe_top(jid, settings, receptor_clean: Path, flex: Path | None):
    """After a screening run, work out the pictures and interactions for the best-scoring compounds only."""
    job_dir = JOBS_DIR / jid
    todo = [r for r in get_job(jid)["results"]
            if r.get("status") == "done" and r.get("best") is not None and "interactions" not in r]
    todo = sorted(todo, key=lambda r: r["best"])[:int(settings.get("screen_top", SCREEN_TOP))]
    for n, r in enumerate(todo, 1):
        _update(jid, step=f"Studying the best compounds ({n}/{len(todo)})", progress=95)
        cd = job_dir / r["dir"]
        try:
            mol = Chem.MolFromSmiles(r.get("docked_smiles") or r["smiles"])
            poses = P.poses_to_mol(cd / "docked.pdbqt")
            _set_result(jid, r["idx"], **_describe_pose(jid, r["name"], mol, poses, cd / "docked.pdbqt",
                                                        cd, receptor_clean, flex))
        except Exception as e:  # noqa: BLE001
            _update(jid, log=f"Could not analyse {r['name']}: {e}")
    if todo:
        _update(jid, log=f"Interaction analysis done for the best {len(todo)} compound(s) by score.")


def _finish(jid: str):
    job_dir = JOBS_DIR / jid
    _update(jid, step="Writing the report", progress=96)
    final = get_job(jid)
    (job_dir / "report.html").write_text(report.render(final, versions()), encoding="utf-8")
    _write_csv(final, job_dir / "results.csv")
    _write_zip(job_dir)
    _update(jid, status="done", step="Finished", progress=100, log="Report written")


class JobError(Exception):
    pass


def _receptor_files(job_dir: Path) -> tuple[Path, Path | None]:
    """The prepared receptor of a finished run: the flexible pair if it has one, otherwise the rigid file."""
    flex = job_dir / "receptor_flex.pdbqt"
    if flex.exists():
        return job_dir / "receptor_rigid.pdbqt", flex
    return job_dir / "receptor.pdbqt", None


def add_compounds(jid: str, compounds: list[dict]) -> int:
    """Dock more compounds into a finished run, reusing its prepared receptor, box and settings."""
    job_dir = JOBS_DIR / jid
    job = get_job(jid)
    if job is None:
        raise JobError("Run not found.")
    if job["status"] in ("queued", "running"):
        raise JobError("This run is still working. Add compounds when it has finished.")
    if not job.get("box") or not _receptor_files(job_dir)[0].exists():
        raise JobError("This run stopped before the protein was prepared, so there is no setup to reuse.")
    with _lock:
        if jid not in _jobs:
            _jobs[jid] = job
        job = _jobs[jid]
        start = len(job["results"])
        idxs = list(range(start, start + len(compounds)))
        for i, c in zip(idxs, compounds):
            job["results"].append({"idx": i, "name": c.get("name") or f"Compound {i + 1}", "smiles": c["smiles"],
                                   "status": "queued"})
        job.update(status="queued", step="Waiting to start", progress=0, error=None)
        _save(job)
    _executor.submit(_run_more, jid, idxs)
    return len(idxs)


def _run_more(jid: str, idxs: list[int]):
    job = get_job(jid)
    job_dir = JOBS_DIR / jid
    settings = job["settings"]
    try:
        _update(jid, status="running", progress=10,
                log=f"Docking {len(idxs)} more compound(s) with the same receptor, box and settings")
        xf = job_dir / "crystal_ligand.sdf"
        xtal = next(Chem.SDMolSupplier(str(xf))) if job["protein"].get("ligand") and xf.exists() else None
        rigid, flex = _receptor_files(job_dir)
        _dock_compounds(jid, idxs, job["box"], settings, rigid, job_dir / "receptor_clean.pdb",
                        xtal, 7.4 if settings.get("protonate") else None, 0, len(idxs), flex)
        _finish(jid)
    except Cancelled:
        _stopped(jid)
    except Exception as e:  # noqa: BLE001
        if jid in _cancelled:
            _stopped(jid)
        else:
            traceback.print_exc()
            _update(jid, status="failed", step="Failed", log=f"Error: {e}", error=str(e))
    finally:
        _forget_run(jid)


def _write_csv(job: dict, path: Path):
    rows = []
    v = job.get("validation")
    if v:
        rows.append(("reference", v, v["props"]))
    for r in job["results"]:
        if r.get("status") == "done":
            rows.append(("compound", r, r["props"]))
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # BOM so Excel reads non-ASCII names
        w = csv.writer(f)
        w.writerow(["role", "name", "smiles", "best_affinity_kcal_mol", "ligand_efficiency", "rmsd_top_pose_A",
                    "mw", "logp", "hbd", "hba", "tpsa", "rotb", "lipinski_violations", "interacting_residues"])
        for role, r, p in rows:
            residues = sorted({i["residue"] for i in r.get("interactions", [])},
                              key=lambda x: int(re.sub(r"\D", "", x) or 0))
            w.writerow([role, r["name"], r["smiles"], f"{r['best']:.2f}", r["le"],
                        r.get("rmsds", [""])[0], p["mw"], p["logp"], p["hbd"], p["hba"], p["tpsa"], p["rotb"],
                        p["lipinski_violations"], " ".join(residues)])


def _write_zip(job_dir: Path):
    with zipfile.ZipFile(job_dir / "results.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(job_dir.rglob("*")):
            if f.is_file() and f.name not in ("results.zip", "job.json.tmp"):
                z.write(f, f.relative_to(job_dir))


_versions = None


def versions() -> dict:
    global _versions
    if _versions is None:
        import meeko
        import plip
        import rdkit
        from openbabel import openbabel as ob

        try:
            vina = subprocess.run([P.find_vina(), "--version"], capture_output=True, text=True).stdout.strip()
        except Exception:  # noqa: BLE001
            vina = "AutoDock Vina"
        _versions = {
            "vina": vina,
            "rdkit": rdkit.__version__,
            "meeko": getattr(meeko, "__version__", "?"),
            "openbabel": ob.OBReleaseVersion(),
            "plip": getattr(plip, "__version__", "") or _pkg_version("plip"),
        }
    return _versions


def _pkg_version(name):
    try:
        from importlib.metadata import version

        return version(name)
    except Exception:  # noqa: BLE001
        return "?"


def delete_job(jid: str):
    d = JOBS_DIR / jid
    if d.exists() and d.parent == JOBS_DIR:
        shutil.rmtree(d)
    with _lock:
        _jobs.pop(jid, None)
