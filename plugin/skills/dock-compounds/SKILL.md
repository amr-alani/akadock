---
name: dock-compounds
description: Dock compounds into a protein and judge the result. Use when the user wants to dock, screen or score molecules against a target, asks whether a compound binds a protein, mentions a PDB code with compounds or SMILES, or asks to compare compounds against a known drug.
---

# Docking with AKADock

AKADock runs AutoDock Vina on the user's own computer through the `akadock` connector. Docking is
slow — roughly 20 to 40 seconds per compound — so the tools hand back a job id and you poll.

## The run

1. **`akadock_load_protein`** with the PDB code. It returns the ligands bound in the structure and a
   `suggested_ligand_key`. The co-crystallized ligand defines the binding site; use the suggested one
   unless the user names another. If the structure has no ligand, pass `site_residues` to
   `akadock_dock` instead, and tell the user the run cannot validate itself.
2. **`akadock_dock`** with the protein id, the ligand key and the compounds as SMILES. Leave the
   settings alone unless the user asks: the defaults are the benchmarked ones. It returns a job id.
3. **`akadock_status`** every 30 seconds or so. Do not report results while the status is `queued` or
   `running` — the ranking is not final until it is `done`.
4. **`akadock_results`** for the ranked table, then **`akadock_files`** if the user wants the report,
   the spreadsheet or the pose files.

## Reporting the result — read this before writing anything

Every run first redocks the ligand that came with the crystal structure and compares it with the
known answer. `akadock_results` returns that verdict and a `trust_ranking` flag **before** the
ranking, and they govern what you are allowed to say:

- **PASS** — the setup reproduces the crystal pose. Report the ranking, and state the verdict and
  its RMSD alongside it.
- **WARN** — the crystal pose is in the top three but was not ranked first. Report the ranking, say
  the scoring is unreliable for this target, and do not draw conclusions from differences of less
  than about 1 kcal/mol.
- **FAIL** — the setup did not reproduce the crystal pose. **Do not rank the compounds and do not
  present the scores as meaningful.** Say the run failed its own validation and suggest another
  structure of the same target, or a higher exhaustiveness.
- **No verdict** — there was no crystal ligand to redock. Say the run could not be checked.

Never report scores without the verdict. A ranking whose setup was never validated looks exactly
like one that was, which is the mistake this program exists to prevent.

Also true of every run, and worth saying once in any summary: **docking scores are predictions, not
measurements.** They are not evidence that a compound binds, and differences under about
1 kcal/mol are inside the method's own error. Results have to be confirmed in the laboratory.

## Reading the numbers

- **Affinity** (kcal/mol, lower is better) is the ranking column.
- **Ligand efficiency** is affinity per heavy atom — a small compound with a good LE has more room to
  grow than a large one scoring slightly better.
- **Contacts** are the residues the top pose touches. Compare them with the reference ligand's row:
  a compound that touches the same residues is more believable than one scoring well elsewhere in the
  box.

## When the user asks for something else

- **A careful run on a few compounds**: `exhaustiveness` 16–32 (slower in proportion).
- **Compounds larger than the crystal ligand**: `padding` 12–15 Å.
- **Induced fit**: `flex_residues`, 3 or 4 pocket residues. Those scores compare only with each other.
- **A covalent inhibitor**: `covalent_residue`, e.g. `Cys145`. Vina cannot form bonds; the result is a
  distance from the warhead to that residue, under about 4 Å meaning the bond could form.
- **A metal in the site**: Vina has no term for metal coordination, so AKADock docks against the
  protein alone. Warn that a pose binding a metal may be wrong however good the score looks.
