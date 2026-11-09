# AKADock v1.1

**A**utomated **K**it for **A**utoDock.

*New in 1.1: AKADock can be used from Claude Desktop and Claude Code through an MCP server — see
[Using AKADock from Claude](#using-akadock-from-claude-mcp). Everything else is unchanged from 1.0.*

One app for structure-based docking: draw your compounds, pick a protein by PDB ID, press **Run**.
AKADock finds the co-crystallized ligand, centers the docking box on it, prepares the receptor and
ligands, validates the setup by redocking the original ligand, docks every compound with AutoDock Vina,
profiles interactions with PLIP and writes a ready-to-use report.

It runs locally on a normal laptop (CPU only, no GPU, no AI) and works offline once installed. Only
**fetching a structure by PDB ID** needs internet; you can upload a local `.pdb`/`.cif` instead.

## Install (once)

On Windows, run **AKADock-Setup.exe** — it brings its own Python and needs nothing from this section.
Install from source only if you are on Linux/macOS or working on the code. Needs Python 3.10 or newer.

```bash
cd AKADock
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install plip --no-deps          # PLIP's own dependency list would try to compile Open Babel
python scripts/setup_assets.py       # downloads Vina, the Ketcher editor and the Mol* viewer (~75 MB)
```

## Run

On Windows, double-click **AKADock.exe**. AKADock opens in its own maximized window (no browser,
no terminal). F11 switches to full screen and Esc goes back. Closing the window stops AKADock; if a
docking run is still working, it asks first. The app window needs the Microsoft Edge WebView2 Runtime,
which is part of Windows 10 and 11. Its source is `shell/AKADock.cs`; `shell/build.bat` rebuilds it.

On any system you can also start it from a terminal:

```bash
python run.py
```

Your browser opens at http://127.0.0.1:8801. Close the terminal (or press Ctrl+C) to stop.
Everything you run is saved under `workspace/` and listed under **Previous runs**. The app window
writes the engine's messages to `workspace/akadock.log`.

## How to use it

1. **Protein**: type a PDB ID (e.g. `4EY7`) and press Load, or upload a `.pdb`, `.cif` or whole-complex
   `.mol2` file (e.g. exported from Discovery Studio). Pick the co-crystallized ligand that marks the
   binding site (the largest one is preselected). If the structure has no bound ligand, or you choose
   *None*, type the binding-site residues instead (e.g. `Trp86 Tyr124 Trp286 Tyr337 Phe338`; one-letter
   codes like `W86` and chains like `B:W86` also work). The box is placed around them, and redocking
   validation is skipped because there is no crystal pose to compare with.
   Every protein you load is kept under **Saved proteins**, so you can pick it again later without
   downloading or uploading it (**Remove** takes it out of the list; finished runs are not affected).
2. **Compounds**: press **Draw a compound** to open the Ketcher editor, **Paste SMILES** (one per
   line, `name SMILES`), or **Upload file** — a multi-compound `.sdf` from PubChem or a vendor, a
   `.mol2` file (one molecule or many), or a `.smi`/`.csv` list. **Fetch a library** downloads
   compounds measured against a target from ChEMBL: type a target name (e.g. `acetylcholinesterase`)
   or a ChEMBL id (`CHEMBL220`), pick the right species, and choose how many to take — the most
   potent measured compounds (IC50/Ki/Kd) come first. **Load AChE examples** fills in five known
   acetylcholinesterase inhibitors. One run takes up to **500** compounds.
3. **Run**. Settings are optional: exhaustiveness (default 8), box padding (default 10 Å), number of
   poses, redocking validation (on), protonation at pH 7.4 (off, see below), keeping crystal waters
   near the reference ligand (off, see below), docking the co-crystallized ligand from its SMILES
   (on, see below), and **flexible side chains** (empty, see below).

**Screening a library.** From **25 compounds** a run switches to screening mode by itself: every
compound is still docked and scored and appears in the table and the CSV, but the slow part — the
interaction analysis and the 2D pictures — runs afterwards for the **10 best-scoring** compounds only.
That keeps a 500-compound run to the docking time alone. Everything else is unchanged, so you can
open any pose in 3D as usual.

**Covalent inhibitors.** Put the residue your compounds should bond to (e.g. `Cys145`) in
*Covalent inhibitor: residue it should bond to*. The compounds are docked normally, and AKADock
then finds each one's reactive group — acrylamide, chloroacetamide, nitrile, epoxide, aldehyde,
boronic acid, vinyl sulfone, β-lactam, α-keto amide — and measures how close it gets to that
residue's reacting atom in every pose. Under about 4 Å the bond could form after the small
rearrangement real binding involves; much further and the pose is not compatible with covalent
binding however good its score looks. The results table and the report show the distance and which
warhead was found. Loading a structure whose ligand is already bonded to the protein is detected and
explained, because such a ligand cannot be redocked as a free compound.

**Flexible side chains (induced fit).** By default the protein is rigid, which is what nearly all
docking papers do. Type a few pocket residues into *Flexible side chains* (same syntax as the binding
site, e.g. `Tyr337 Phe338`) and Vina is allowed to rotate those side chains while docking. Use 3 or 4
at most — eight is the hard limit, and each one makes the search harder. Two things to know:

- Scores from a flexible run are **not comparable** with rigid-receptor scores, only with each other.
- Flexible is not automatically better. On 4EY7 the rigid redock reproduces the crystal pose to 0.28 Å
  and the flexible one to 0.89 Å, because the crystal side chains are already in the right place.
  Flexibility helps when your compound is bigger than the original ligand or the pocket must open.

**Reusing a setup.** Two buttons let you dock more compounds with exactly the same protein and settings:

- **Add to the open run**: with a finished run open, add compounds in step 2 and press this button
  (under Run docking). The new compounds use the run's already prepared receptor, box and settings, and
  appear in the same ranked table, report, CSV and zip. Scores are directly comparable with the
  compounds docked before.
- **New run, same setup** (next to Open report): copies the run's protein, binding site and settings
  into the form, so you only add the new compounds and press Run docking. This makes a separate run.

**The original ligand as a compound.** Redocking (validation) starts from the crystal geometry of the
ligand. With *Also dock the co-crystallized ligand from its SMILES* (on by default), the same ligand is
also docked exactly like your lead compounds (3D structure generated from SMILES), shown as for example
"E20 (from SMILES)" in the ranked table with its RMSD to the crystal pose. This is the fair reference
for comparing lead compounds with the original ligand.

When the run finishes you get:

- A **validation** verdict: the original ligand is redocked and its top pose compared with the crystal
  pose (symmetry-corrected heavy-atom RMSD). PASS ≤ 2.0 Å; WARN if only a top-3 pose is within 2.0 Å;
  FAIL otherwise.
- A **results table** ranked by Vina affinity, with ligand efficiency, MW, cLogP and the residues each
  compound contacts. Click a row to see the pose in 3D next to the crystal ligand (green), with
  interactions drawn as dashed lines, and switch between poses.
- **Open report**: a self-contained HTML report (validation, ranked table with 2D structures,
  per-compound interaction tables, methods paragraph with software versions). Print it to PDF from the
  browser if you need a PDF.
- **Download all files**: a zip with the receptor, every pose (`poses.sdf`, `docked.pdbqt`),
  complex PDB files for PyMOL/Chimera, the CSV and the report.

## What it does under the hood

| Step | Tool |
|---|---|
| Fetch structure | RCSB PDB download (PDB, falling back to mmCIF) |
| Find ligand, clean receptor | Gemmi (removes waters, alternate locations, other hetero groups; keeps metal ions for the report and viewer, but not in the receptor Vina docks against — see the benchmark notes) |
| Ligand bond orders | PDB Chemical Component Dictionary template via RDKit (offline copy downloaded by `setup_assets.py`, then the RCSB API); Open Babel perception as a last resort |
| Receptor hydrogens + PDBQT | Open Babel at pH 7.4 |
| Compound 3D + PDBQT | RDKit ETKDGv3 + MMFF94, optional Dimorphite-DL protonation, Meeko |
| Docking | AutoDock Vina 1.2.5 command-line program |
| Interactions | PLIP |
| Viewer / editor | Mol* (MolViewSpec scenes), Ketcher |

The redocked ligand keeps its crystal ring geometry (standard self-docking); Vina still randomizes its
position, orientation and torsions.

**Protonation default.** Vina's scoring function has no electrostatics, and in testing an explicitly
protonated piperidine made Vina flip donepezil in AChE when it was docked from SMILES. So compounds are
docked as drawn by default; tick *Protonate at pH 7.4* if your protocol requires it.

**Crystal waters (advanced).** With *Keep crystal waters near the ligand*, waters within 3.5 Å of the
reference ligand stay in the rigid receptor and PLIP reports water bridges. On 4EY7 this reproduces the
water-mediated contact between donepezil and Tyr341 described by Cheung et al. (2012), and the redock
improves slightly (0.25 Å, −12.86 kcal/mol). But those waters sit where other ligands want to bind:
galantamine dropped from −7.79 to −5.46 and huperzine A from −9.69 to −6.06 kcal/mol. Use it only for
close analogues of the reference ligand.

## Tested

On a 4-core machine (1EVE and 1ACJ are Torpedo californica AChE):

- **1EVE, donepezil**: redock top pose RMSD **0.69 Å** (PASS), −10.98 kcal/mol. Donepezil, huperzine A,
  tacrine, galantamine and rivastigmine docked in about a minute in total at exhaustiveness 8
  (−10.82, −9.65, −8.60, −7.96, −7.63 kcal/mol).
- **4EY7, human AChE with donepezil** (original PDB file): redock top pose RMSD **0.27 Å** (PASS),
  −12.22 kcal/mol. Donepezil docked from SMILES landed 0.82 Å from the crystal pose (−11.93), contacting
  Trp86, Trp286, Tyr337, Phe338, Tyr341. Huperzine A −9.69, tacrine −8.61, rivastigmine −7.80,
  galantamine −7.79 kcal/mol; about 50 s for the whole run.
- **1ACJ, tacrine** (uploaded as mmCIF): redock FAIL. Vina finds the crystal pose (pose 5, 0.25 Å) but
  ranks a flipped pose first (3.3 Å). This is what the validation step is for: it tells you when the
  ranking for a target should not be trusted.
- **Official Vina tutorial (1IEP, imatinib in Abl kinase)**: the tutorial reports −13.23 kcal/mol. With
  the tutorial's own input files our Vina gives −13.24 (RMSD 0.36 Å); with AKADock's own receptor and
  ligand preparation −13.22 to −13.30 (0.34 to 0.37 Å, three seeds); from SMILES −12.57 (0.59 Å).
- **Redocking benchmark, 31 complexes** (the PLIP test set, PoseBusters protocol: 25 Å box on the ligand,
  ETKDG conformer, exhaustiveness 32, success = top pose ≤ 2 Å): **16/31 (52%)**. PoseBusters reports
  58% for Vina on the Astex set. With AKADock's default settings: 17/31 on the same complexes, 19/37
  overall. Failures are mostly nucleotides bound with Mg²⁺, covalently bound PLP, free amino acids, an
  NMR structure, and a handful of genuine scoring failures (1ACJ, 3PXF, 4QNB, 3SHY). Correct ligand bond
  orders matter: taking them from the PDB Chemical Component Dictionary instead of guessing rescued
  vemurafenib in 3OG7 (8.6 → 0.64 Å), 1AKU and 1HET.
- **Astex Diverse Set, 35 complexes** (the 30-complex Astex subset above plus 1EVE, 4EY7, 1ACJ, 3OG7,
  1IEP), all at AKADock's **default settings** (exhaustiveness 8, ligand box + 10 Å, seed 42), 112 min
  of docking in total:

  | | top pose ≤ 2 Å | best of top 3 | median RMSD |
  |---|---|---|---|
  | Redock (crystal geometry) | **24/35 (69%)** | 27/35 (77%) | 0.57 Å |
  | Dock from SMILES (3D rebuilt) | 17/35 (49%) | 22/35 (63%) | 2.05 Å |

  On the 30 Astex complexes alone: 20/30 (67%) redock, 14/30 (47%) from SMILES. Published Vina results
  on Astex are around 58–64%, so this is in the expected range — and reached at exhaustiveness 8 rather
  than 32. Bond orders came from the Chemical Component Dictionary for all 35 ligands.
  The gap between the two rows is the honest cost of starting from a 2D structure, which is what you do
  with a real lead compound: 4 complexes lose a good pose entirely that way (1GPK, 1IA1, 1L7F, 1EVE).
  The 8 redocking failures are metal pockets, which Vina docks without the metal (1HQ2 Mg²⁺, 1JD0 Zn²⁺,
  1MZC Zn²⁺), a second ligand sharing the site (1HVY), near-covalent substrate complexes (1GM8, 1IG3),
  a surface site on hemoglobin (1G9V) and one genuine scoring failure (1ACJ).

  **Why metal sites are docked without the metal.** Open Babel's rigid-receptor PDBQT writer drops every
  hetero group except water, so metal ions reach the report and the viewer but not Vina. Putting them
  back was tested on 41 held-out metal/cofactor complexes and made redocking significantly **worse**
  (17 → 11 passes, 6 lost and 0 gained, exact McNemar p = 0.031), because Vina has no attractive term
  for metal coordination — a restored metal is purely repulsive and pushes the ligand out of its site.
  So this stays as it is until the engine can model coordination (AutoDock-GPU + autogrid4).
- **Published scores are not always reproducible.** Hakeem et al. (2023, Bioinformation 19:565) docked
  AChE inhibitors into 4EY7 with PyRx; our scores differ by 1 to 1.5 kcal/mol (donepezil −11.9 vs −10.8)
  however exhaustiveness, protonation or box are varied. They minimized the protein in Discovery Studio,
  used Vina 1.1.2 and did not report box size or a redock RMSD, so the difference is in their inputs.
- **Reproducing 10 published papers, 16 redocking targets.** Papers were chosen from Europe PMC open-access
  full texts that report all of: PDB ID, grid box, exhaustiveness, and their own redocking validation. Each
  paper's exhaustiveness was used; everything else was AKADock's default.

  | | result |
  |---|---|
  | Vina score vs. published (10 targets) | median difference **1.16 kcal/mol**; 5/10 within 1.0, 8/10 within 2.0 |
  | AKADock redock top pose ≤ 2 Å | 9/16 (a 10th, 2Z7G, at 2.05 Å) |

  Close matches: CCR5 / maraviroc 4MBS (published 0.21 Å, −10.8 → **0.31 Å, −12.16**), NLRP3 7ALV
  (0.87 Å, −9.6 → **0.71 Å, −9.47**), COX-2 / rofecoxib 5KIR (1.34 Å, −10.4 → **0.89 Å, −10.19**),
  squalene monooxygenase / NB-598 6C6P (0.55 Å → **0.67 Å**), GSK-3β 3GB2 (1.45 Å, −6.8 → **1.43 Å, −9.45**).
  Pose failures: 4WMZ and 5TZ1 are azoles on the CYP51 **heme iron**, which Vina cannot score at all;
  6A93 and 6CM4 are deep GPCR pockets; 3RZF and 4JT6 are genuine ranking failures.
  Three of the ten papers cite a ligand code that is not in the PDB entry they name (EHN, 9KZ, 3MV).
  For comparison, a 2026 audit of 236 docking papers found only 8% could be re-executed at all, with a
  median re-run difference near 0.8 kcal/mol — so scores that agree to about 1 kcal/mol is the realistic
  target, not an exact match. Full tables: `bench_results.csv` and `repro_results.csv`.

## Using AKADock from Claude (MCP)

AKADock can be used from Claude Desktop and Claude Code, so you can ask for a docking run in words
and get real numbers back from your own machine.

**Claude Desktop:** double-click **AKADock-1.1-connector.mcpb** (on the download page, or build it
yourself with `python mcp\build_bundle.py`). Claude Desktop shows what the connector does, asks which
folder AKADock is in - the usual one is filled in already - and installs it. Nothing to type, nothing
to edit.

**Claude Code, or Claude on the web:** install the plugin, which brings the same connector plus a
skill that tells Claude how to run a docking and how to report the verdict:

```
/plugin install akadock --marketplace amr-alani/akadock
```

or add the marketplace `amr-alani/akadock` under **Customize > Plugins > Add > Add marketplace**. The
plugin finds your AKADock install by itself - the folder you installed into, or `AKADOCK_HOME` if you
moved it. Its source is in `plugin/` in the GitHub repository. The connector runs a program on your
computer, so it loads in Claude Code and in Cowork sessions on the desktop, not in web chat.

**By hand:** `python mcp\install_mcp.py` registers the server with Claude Desktop without the bundle;
`--remove` undoes it.

The server starts
the engine itself if it is not already running, and talks to the same `127.0.0.1:8801` API as the
window, so a run started from Claude appears under **Previous runs** like any other.

Five tools: `akadock_load_protein` (PDB ID in, ligands out), `akadock_dock` (returns a job id
immediately - docking is slow), `akadock_status`, `akadock_results` and `akadock_files`.

**The verdict travels with the numbers.** `akadock_results` puts the redocking verdict and a
`trust_ranking` flag at the top of every reply, and tells the model in writing not to rank
compounds from a run that failed its self-check. An assistant that summarises docking output without
that verdict is the exact failure this program exists to prevent. It is standard library only, so
there is nothing to install, and it inherits every limit listed above - a score from Claude is the
same prediction as a score from the window.

Check it with `python scripts\selfcheck_mcp.py` (handshake, tool list, error handling; docks nothing).
## Limits of v1.1

- Docking scores estimate affinity; they are not proof of activity.
- The receptor is rigid unless you name flexible side chains, and even then only those side chains
  move — the backbone never does. Covalent binding is not handled. Ligands containing a metal (heme,
  metal clusters) cannot be docked with Vina and are refused with a clear message.
- **Vina does not score metal coordination, so AKADock docks against the protein alone.** Metal ions
  and cofactors are not part of the receptor Vina sees — they are kept for the interaction analysis and
  the 3D viewer only. A compound that binds a metal in the pocket (an azole nitrogen on a heme iron,
  a hydroxamate on a zinc) can therefore be placed in the wrong pose with a score that looks fine.
  When a metal falls inside the docking box, AKADock says so in the run log and prints a "Caution" box
  at the top of the report. Putting the metals and cofactors back was tried and measured: it makes
  redocking significantly worse (see *Why metal sites are docked without the metal* above). This is a
  limit of the scoring function, not an oversight, and lifting it needs a different engine.
- Protonation, tautomers and ring conformations are assigned automatically and can be wrong for unusual
  chemistry. Missing side-chain atoms in the PDB file are not rebuilt.
- No blind (whole-protein) docking; the box comes from a bound ligand or from residues you name.
- **True covalent docking is not possible with AutoDock Vina, and AKADock does not pretend to do
  it.** Vina's scoring function has no term for a covalent bond, so it reads one as a steric clash.
  Measured on 8DZ2 (main protease with nirmatrelvir bonded to Cys145): the real crystal pose has the
  bond at 1.81 Å and scores −2.69 kcal/mol; Vina's own optimiser pulls that atom out to 2.96 Å and
  calls the broken-bond version 4.37 kcal/mol *better*. Any attempt to hold the ligand in place is
  therefore fighting the scoring function. Proper covalent docking needs AutoDock-GPU with
  pre-computed grid maps, where the ligand becomes part of a flexible residue and the bond is never
  scored as a contact between two molecules. What AKADock offers instead is the **covalent
  reactivity check** below.
- Screening is limited to 500 compounds in one run, docked one after another. That is a focused
  library, not an industrial screen of 100,000 compounds.
- No GNINA rescoring, molecular dynamics or ADMET prediction yet. The report has no 3D pose images or
  2D interaction diagrams yet.
- macOS is untested so far. Windows was tested under Wine and on a real Windows 11 PC.

## Layout

```
run.py                  start the app
scripts/setup_assets.py one-time download of Vina, Ketcher, Mol*
app/pipeline.py         the science: fetch, prep, dock, RMSD, PLIP
app/jobs.py             background job runner, CSV and zip export
app/report.py           HTML report
app/server.py           FastAPI endpoints
app/static/             the browser UI
shell/                  the C# app window (AKADock.exe) and its build script
mcp/akadock_mcp.py      the MCP server (AKADock as a tool for Claude)
mcp/build_bundle.py     packs the Claude Desktop connector (.mcpb)
scripts/selfcheck_metal.py     checks the metal-in-box warning (needs internet)
scripts/selfcheck_warheads.py  checks the covalent warhead detection
benchmarks/             the validation runs in "Tested": result tables + the scripts that made them
                        (re-run with AKADOCK_WORKSPACE set, so your own runs are untouched)
workspace/              your runs (created on first use)
```
