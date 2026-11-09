# AKADock

Molecular docking on your own computer, from a conversation. Ask Claude to dock a set of compounds
into a protein and it loads the structure, runs AutoDock Vina locally, and reads back a ranked table
with affinities, ligand efficiency and the residues each pose contacts.

## Install it

```
/plugin install akadock --marketplace amr-alani/akadock
```

In claude.ai or the desktop app: **Customize → Plugins → Add → Add marketplace**, then enter
`amr-alani/akadock`.

## Use it

Install [AKADock 1.1 or newer](https://amr-alani.github.io/akadock/) first — it is a free Windows
program and carries its own Python, AutoDock Vina and chemistry libraries. The plugin finds it in
the folder the installer uses, and starts it when you ask for a docking. If you keep AKADock
somewhere else, either leave its window open or set `AKADOCK_HOME` to the folder that holds
`run.py`; the plugin says so itself if it cannot find the program.

Then ask for a run in words:

> Dock donepezil, tacrine and galantamine into 4EY7 and tell me which contacts they share with the
> crystal ligand.

Claude loads the protein, docks the compounds, waits for the run to finish and reports the ranking.
A 5-compound run takes two or three minutes on a normal laptop. Every run also appears under
**Previous runs** in the AKADock window, with its report, spreadsheet and pose files.

## The validation verdict

Before your compounds are docked, AKADock removes the ligand that came with the crystal structure
and docks it back. If the top pose lands on the crystal pose, the setup is sound. That verdict —
PASS, WARN or FAIL — comes back with every result, and the bundled skill instructs Claude to report
it alongside any ranking and to refuse to rank compounds from a run that failed its own check.

Docking scores are predictions, not measurements. They are not evidence that a compound binds, and
must be confirmed in the laboratory.

## Data

Nothing is uploaded. The docking, the structures and the results stay on your computer, in the
AKADock workspace folder. The only network traffic is AKADock fetching a structure from the RCSB
Protein Data Bank, or compounds from ChEMBL, when you ask for one by code.

## Where it works

The connector runs a program on your machine, so it works in Claude Code and in Cowork sessions
running on your computer. Web chat has no machine to dock on, so the connector does not load there.

Windows only, because AKADock is a Windows program. Free and open source under the GNU General
Public License v2: <https://github.com/amr-alani/akadock>
