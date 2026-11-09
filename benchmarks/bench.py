"""Redocking benchmark: for each complex, AKADock redocks the co-crystallized ligand (crystal
geometry) and also docks it from SMILES, then compares both with the crystal pose.
All AKADock defaults (exhaustiveness 8, padding 10 A, seed 42). Writes bench_results.json/csv."""
import csv, json, sys, time, urllib.error, urllib.request

B = "http://127.0.0.1:8801/"
OUT = sys.argv[1] if len(sys.argv) > 1 else "bench"

# Astex Diverse Set (Hartshorn et al., J Med Chem 2007) -- a standard docking benchmark.
ASTEX = """1G9V 1GKC 1GM8 1GPK 1HNN 1HP0 1HQ2 1HVY 1HWI 1HWW 1IA1 1IG3 1J3J 1JD0 1JLA
1K3U 1KE5 1KZK 1L2S 1L7F 1LPZ 1LRH 1M2Z 1MEH 1MMV 1MZC 1N1M 1N2V 1N46 1OF1""".split()
# Complexes the AKADock README already reports, for continuity.
README = ["1EVE", "4EY7", "1ACJ", "3OG7", "1IEP"]
TARGETS = ASTEX + README


def call(path, body=None, timeout=600):
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
for n, pdb in enumerate(TARGETS, 1):
    t0 = time.time()
    row = {"pdb": pdb, "set": "astex" if pdb in ASTEX else "readme"}
    p = call("api/protein/fetch", {"pdb_id": pdb})
    if "error" in p or not p.get("ligands"):
        row["status"] = "no_ligand" if "error" not in p else "fetch_failed"
        row["note"] = p.get("error", "no ligand with >=6 heavy atoms")
        rows.append(row); print(n, pdb, row["status"], row["note"], flush=True); continue
    lig = p["ligands"][0]
    row.update(ligand=lig["key"], ligand_atoms=lig["heavy_atoms"], title=(p.get("title") or "")[:80])
    j = call("api/jobs", {"protein_id": pdb, "ligand_key": lig["key"], "compounds": [],
                          "redock": True, "dock_reference": True})
    if "error" in j:
        row["status"] = "job_refused"; row["note"] = j["error"]
        rows.append(row); print(n, pdb, row["status"], row["note"], flush=True); continue
    while (job := call("api/jobs/" + j["id"]))["status"] not in ("done", "failed"):
        time.sleep(2)
    row["job"] = j["id"]
    row["seconds"] = round(time.time() - t0)
    if job["status"] == "failed":
        row["status"] = "failed"; row["note"] = job.get("error", "")[:200]
    else:
        v = job.get("validation") or {}
        ref = next((r for r in job["results"] if r.get("reference")), {})
        row["status"] = "done"
        row["redock_rmsd"] = (v.get("rmsds") or [None])[0]
        row["redock_best3"] = min(v["rmsds"][:3]) if v.get("rmsds") else None
        row["redock_score"] = round(v["best"], 2) if v.get("best") is not None else None
        row["verdict"] = v.get("verdict")
        row["smiles_rmsd"] = (ref.get("rmsds") or [None])[0]
        row["smiles_best3"] = min(ref["rmsds"][:3]) if ref.get("rmsds") else None
        row["smiles_score"] = round(ref["best"], 2) if ref.get("best") is not None else None
        row["smiles_status"] = ref.get("status", "missing")
        row["smiles_error"] = (ref.get("error") or "")[:120]
        row["bond_orders"] = v.get("bond_order_note", "")
    rows.append(row)
    print(n, pdb, row["status"], "redock", row.get("redock_rmsd"), row.get("verdict"),
          "| smiles", row.get("smiles_rmsd"), row.get("smiles_status"), row.get("smiles_error", ""),
          f"| {row.get('seconds')}s", flush=True)
    json.dump(rows, open(OUT + "_results.json", "w"), indent=1)

keys = sorted({k for r in rows for k in r})
with open(OUT + "_results.csv", "w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["pdb", "set", "status"] + [k for k in keys if k not in ("pdb", "set", "status")])
    w.writeheader()
    w.writerows(rows)

ok = [r for r in rows if r.get("status") == "done"]
rd = [r["redock_rmsd"] for r in ok if r.get("redock_rmsd") is not None]
sm = [r["smiles_rmsd"] for r in ok if r.get("smiles_rmsd") is not None]
print("\n==== SUMMARY")
print(f"attempted {len(rows)}, completed {len(ok)}")
print(f"redock      top pose <=2A: {sum(x <= 2 for x in rd)}/{len(rd)}   median {sorted(rd)[len(rd)//2]:.2f} A")
if sm:
    print(f"from SMILES top pose <=2A: {sum(x <= 2 for x in sm)}/{len(sm)}   median {sorted(sm)[len(sm)//2]:.2f} A")
