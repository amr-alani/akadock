"""Reproduce published AutoDock Vina docking results with AKADock.

For each paper we redock the co-crystallized ligand the paper itself redocked, using the paper's
exhaustiveness, and compare AKADock's score and RMSD with the published ones.
Papers were selected from Europe PMC open-access full texts that report PDB ID, grid box and
exhaustiveness, and that validated their protocol by redocking (the minority that can be checked).
"""
import csv, json, sys, time, urllib.error, urllib.request

B = "http://127.0.0.1:8801/"

# paper, doi, pdb, ligand resname the paper redocked, exhaustiveness, reported score, reported RMSD
PAPERS = [
    ("PMC13610358", "10.3390/pharmaceutics18091050", "3KK6", "CEL", 32, -8.1, 1.41, "COX-1 / celecoxib"),
    ("PMC13610358", "10.3390/pharmaceutics18091050", "5KIR", "RCX", 32, -10.4, 1.34, "COX-2 / rofecoxib"),
    ("PMC13510811", "10.3390/cimb48080807", "1E7U", "KWT", 32, -8.40, 0.239, "PI3K / KWT"),
    ("PMC13510811", "10.3390/cimb48080807", "3RZF", "XNM", 32, -8.30, 0.478, "NF-kB IKK / XNM"),
    ("PMC13510811", "10.3390/cimb48080807", "4JT6", "X6K", 32, -8.10, 0.162, "mTOR / X6K"),
    ("PMC13552277", "10.5812/ijpr-170515", "2Z7G", "EHN", 64, -6.8, None, "ADA / EHNA (paper exh 100)"),
    ("PMC13552277", "10.5812/ijpr-170515", "2PGR", "DCF", 64, -6.3, None, "ADA / pentostatin (paper exh 100)"),
    ("PMC13524263", "10.1371/journal.pone.0355997", "6CM4", "9KZ", 16, None, 0.458, "D2R / risperidone"),
    ("PMC13524263", "10.1371/journal.pone.0355997", "6A93", "8NU", 16, None, 0.48, "5-HT2A / risperidone"),
    ("PMC13396737", "10.1038/s41598-026-56505-6", "3GB2", None, 32, -6.80, 1.45, "GSK-3beta / native ligand"),
    ("PMC13366417", "10.3389/fchem.2026.1859262", "7ALV", "RM5", 32, -9.6, 0.87, "NLRP3 / RM5"),
    ("PMC13515186", "10.3390/molecules31162837", "4MBS", "3MV", 32, -10.8, 0.209, "CCR5 / maraviroc"),
    ("PMC13453077", "10.1002/cbdv.71569", "6C6P", None, 32, None, 0.55, "squalene monooxygenase / NB-598"),
    ("PMC13453077", "10.1002/cbdv.71569", "4WMZ", "TPF", 32, None, 1.54, "lanosterol 14a-demethylase / fluconazole"),
    ("PMC13338215", "10.1038/s41598-026-00000", "5XGI", None, 32, None, 1.7, "PI3K alpha / native ligand"),
    ("PMC13616462", "10.1002/cbdv.71742", "5TZ1", None, 16, None, None, "CYP51 template structure"),
]


def call(path, body=None, timeout=900):
    req = urllib.request.Request(B + path, json.dumps(body).encode() if body is not None else None,
                                 {"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": json.loads(e.read()).get("detail", str(e))}
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e)}


for _ in range(120):
    if "ok" in call("api/ping"):
        break
    time.sleep(1)

rows = []
for pmcid, doi, pdb, resname, exh, rep_score, rep_rmsd, label in PAPERS:
    row = {"paper": pmcid, "doi": doi, "pdb": pdb, "target": label, "want_ligand": resname or "(largest)",
           "exhaustiveness": exh, "reported_score": rep_score, "reported_rmsd": rep_rmsd}
    p = call("api/protein/fetch", {"pdb_id": pdb})
    if "error" in p or not p.get("ligands"):
        row["status"] = "fetch_failed"; row["note"] = p.get("error", "no ligand found")
        rows.append(row); print(pdb, row["status"], row["note"], flush=True); continue
    row["ligands_available"] = ",".join(sorted({l["resname"] for l in p["ligands"]}))
    lig = next((l for l in p["ligands"] if resname and l["resname"].upper() == resname.upper()), None)
    if lig is None:
        if resname:
            row["note"] = f"ligand {resname} not found; used largest ({p['ligands'][0]['resname']})"
        lig = p["ligands"][0]
    row["used_ligand"] = lig["key"]
    j = call("api/jobs", {"protein_id": pdb, "ligand_key": lig["key"], "compounds": [],
                          "redock": True, "dock_reference": True, "exhaustiveness": exh})
    if "error" in j:
        row["status"] = "job_refused"; row["note"] = j["error"]
        rows.append(row); print(pdb, row["status"], j["error"], flush=True); continue
    t0 = time.time()
    while (job := call("api/jobs/" + j["id"]))["status"] not in ("done", "failed"):
        time.sleep(2)
    row["job"] = j["id"]; row["seconds"] = round(time.time() - t0)
    if job["status"] == "failed":
        row["status"] = "failed"; row["note"] = (job.get("error") or "")[:200]
    else:
        v = job.get("validation") or {}
        ref = next((r for r in job["results"] if r.get("reference")), {})
        row["status"] = "done"
        row["akadock_score"] = round(v["best"], 2) if v.get("best") is not None else None
        row["akadock_rmsd"] = (v.get("rmsds") or [None])[0]
        row["verdict"] = v.get("verdict")
        row["smiles_score"] = round(ref["best"], 2) if ref.get("best") is not None else None
        row["smiles_rmsd"] = (ref.get("rmsds") or [None])[0]
        row["box"] = f"{job['box']['size']}"
        if rep_score is not None and row["akadock_score"] is not None:
            row["score_diff"] = round(row["akadock_score"] - rep_score, 2)
        if rep_rmsd is not None and row["akadock_rmsd"] is not None:
            row["rmsd_diff"] = round(row["akadock_rmsd"] - rep_rmsd, 2)
    rows.append(row)
    print(f"{pdb} {label[:34]:34s} reported {rep_score} / {rep_rmsd} A  ->  AKADock "
          f"{row.get('akadock_score')} / {row.get('akadock_rmsd')} A  ({row.get('seconds')}s) {row.get('note','')}",
          flush=True)
    json.dump(rows, open("repro_results.json", "w"), indent=1)

keys = sorted({k for r in rows for k in r})
with open("repro_results.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["paper", "doi", "pdb", "target", "status"] +
                                     [k for k in keys if k not in ("paper", "doi", "pdb", "target", "status")])
    w.writeheader(); w.writerows(rows)

ds = [r["score_diff"] for r in rows if r.get("score_diff") is not None]
print("\n==== SUMMARY")
print(f"{len(rows)} redocks attempted, {sum(r['status'] == 'done' for r in rows)} completed")
if ds:
    a = sorted(abs(x) for x in ds)
    print(f"score vs published: n={len(ds)} median |diff| {a[len(a)//2]:.2f} kcal/mol, "
          f"within 1.0: {sum(x <= 1 for x in a)}/{len(a)}, within 2.0: {sum(x <= 2 for x in a)}/{len(a)}")
rr = [r for r in rows if r.get("akadock_rmsd") is not None]
print(f"AKADock redock <=2 A: {sum(r['akadock_rmsd'] <= 2 for r in rr)}/{len(rr)}")
