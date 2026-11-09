"""Self-contained HTML report for a finished docking job."""
from __future__ import annotations

import html
import time
from collections import OrderedDict

TYPE_ORDER = ["hbond", "saltbridge", "waterbridge", "pication", "pistacking", "halogen", "metal", "hydrophobic"]
TYPE_SHORT = {"hbond": "HB", "saltbridge": "SB", "pication": "π+", "pistacking": "π-π", "halogen": "X",
              "metal": "M", "hydrophobic": "HP", "waterbridge": "W"}


def _covalent_cell(r: dict, s: dict) -> str:
    if not (s.get("covalent_residue") or "").strip():
        return ""
    c = r.get("covalent") or {}
    if not c.get("warhead"):
        return '<td class="num muted">no warhead</td>'
    ok = c.get("reachable")
    return (f'<td class="num" style="color:{"#067647" if ok else "#b42318"}">{c["closest"]:.1f} Å'
            f'<div class="small muted">{_esc(c["warhead"])}</div></td>')


def _esc(x) -> str:
    return html.escape(str(x))


def residue_summary(inter: list[dict]) -> OrderedDict:
    by_res: OrderedDict[str, set] = OrderedDict()
    for i in sorted(inter, key=lambda i: i["resnr"]):
        by_res.setdefault(i["residue"], set()).add(i["type"])
    return by_res


def _chips(inter: list[dict]) -> str:
    out = []
    for res, types in residue_summary(inter).items():
        t = sorted(types, key=TYPE_ORDER.index)
        cls = t[0]
        out.append(f'<span class="chip {cls}" title="{_esc(", ".join(t))}">{_esc(res)}'
                   f'<small>{_esc("/".join(TYPE_SHORT[x] for x in t))}</small></span>')
    return "".join(out) or '<span class="muted">none detected</span>'


def _detail_table(inter: list[dict]) -> str:
    if not inter:
        return '<p class="muted">No interactions detected by PLIP.</p>'
    rows = "".join(
        f"<tr><td>{_esc(i['residue'])}</td><td>{_esc(i['label'])}</td><td>{i['distance']:.2f}</td>"
        f"<td>{_esc(i.get('detail', ''))}</td></tr>"
        for i in sorted(inter, key=lambda i: (i["resnr"], TYPE_ORDER.index(i["type"])))
    )
    return ("<table class='detail'><thead><tr><th>Residue</th><th>Interaction</th><th>Distance (Å)</th>"
            f"<th>Note</th></tr></thead><tbody>{rows}</tbody></table>")


def render(job: dict, versions: dict) -> str:
    s = job["settings"]
    box = job["box"]
    cov_head = ('<th>Covalent reach<br><small>to ' + _esc(s.get("covalent_residue", "")) + '</small></th>'
                if (s.get("covalent_residue") or "").strip() else "")
    flex = s.get("flex_found") or []
    flex_text = (" The side chains of " + ", ".join(f"{r['resname']}{r['resnum']} (chain {r['chain']})" for r in flex) +
                 " were kept flexible, so Vina rotated them while docking; the rest of the receptor was rigid."
                 " Scores from a flexible run are comparable with each other but not with rigid-receptor scores."
                 ) if flex else ""
    caution_html = (f'<div class="caution"><b>Caution</b> {_esc(box["warning"])}</div>'
                    if box.get("warning") else "")
    v = job.get("validation")
    done = sorted([r for r in job["results"] if r.get("status") == "done"], key=lambda r: r["best"])
    failed = [r for r in job["results"] if r.get("status") == "failed"]
    lig = job["protein"]["ligand"]
    if lig:
        site_label = lig
        box_text = (f"The docking box was centered on the co-crystallized ligand {_esc(lig)} (center {box['center']} Å; "
                    f"size {box['size']} Å, ligand extent + {s.get('padding', 10)} Å, minimum 20 Å).")
    elif box.get("residues"):
        names = " ".join(f"{r['resname']}{r['resnum']}" for r in box["residues"])
        site_label = f"residues {names}"
        box_text = (f"No co-crystallized ligand was available, so the docking box was placed around the binding-site "
                    f"residues {_esc(names)} (center {box['center']} Å; size {box['size']} Å, residue extent + 4 Å, "
                    f"minimum 20 Å). Redocking validation was therefore not possible.")
    else:
        site_label = f"box at {box['center']}"
        box_text = (f"No co-crystallized ligand was available; the docking box was set manually (center {box['center']} Å; "
                    f"size {box['size']} Å). Redocking validation was therefore not possible.")
    src = job["protein"]["source"] or ""

    rows = []
    if v:
        rows.append(("ref", v))
    rows += [("cmp", r) for r in done]
    table_rows = []
    rank = 0
    for kind, r in rows:
        p = r["props"]
        if kind == "cmp":
            rank += 1
        delta = ""
        if v and kind == "cmp":
            d = r["best"] - v["best"]
            delta = f'<div class="delta {"better" if d < 0 else "worse"}">{d:+.2f} vs ref</div>'
        table_rows.append(f"""
<tr class="{'ref' if kind == 'ref' else ''}">
 <td class="rank">{'Ref' if kind == 'ref' else rank}</td>
 <td class="struct">{r.get('svg', '')}</td>
 <td><b>{_esc(r['name'])}</b>{f'<div class="small muted">top-pose RMSD to crystal {r["rmsds"][0]:.2f} Å</div>' if kind == "cmp" and r.get("rmsds") else ""}<div class="smiles">{_esc(r['smiles'])}</div></td>
 <td class="num score">{r['best']:.2f}{delta}</td>
 <td class="num">{r['le']:.2f}</td>
 <td class="num">{p['mw']}</td><td class="num">{p['logp']}</td>
 <td class="num">{p['hbd']}/{p['hba']}</td><td class="num">{p['tpsa']}</td>
 <td class="num">{p['lipinski_violations']}</td>
 <td class="chips">{_chips(r.get('interactions') or [])}</td>
 {_covalent_cell(r, s)}
</tr>""")

    validation_html = ""
    if v:
        rm = ", ".join(f"{x:.2f}" for x in v["rmsds"][:5])
        validation_html = f"""
<section>
 <h2>1. Validation by redocking</h2>
 <div class="verdict {v['verdict']}"><b>{v['verdict'].upper()}</b> &nbsp; {_esc(v['verdict_text'])}</div>
 <p>The co-crystallized ligand <b>{_esc(lig)}</b> was removed, prepared from its crystal geometry and redocked
 into the same box. Heavy-atom RMSD (symmetry-corrected, no superposition) of poses 1–5 vs. the crystal pose:
 <b>{rm} Å</b>. Redock affinity: <b>{v['best']:.2f} kcal/mol</b>. Ligand bond orders: {_esc(v.get('bond_order_note', ''))}.</p>
</section>"""

    failed_html = ""
    if failed:
        failed_html = "<section><h2>Compounds that could not be docked</h2><ul>" + "".join(
            f"<li><b>{_esc(r['name'])}</b> ({_esc(r['smiles'])}): {_esc(r.get('error', ''))}</li>" for r in failed
        ) + "</ul></section>"

    detailed = [(k, r) for k, r in rows if r.get("interactions") is not None and r.get("svg")]
    details = "".join(f"""
<div class="cmpd">
 <h3>{_esc(r['name'])} <span class="muted">· {r['best']:.2f} kcal/mol</span></h3>
 <div class="cmpd-grid"><div>{r['svg']}</div><div>{_detail_table(r['interactions'])}
 <p class="muted small">All poses (kcal/mol): {', '.join(f'{x:.2f}' for x in r['scores'])}</p></div></div>
</div>""" for _, r in detailed)
    if len(detailed) < len(rows):
        details = (f'<p class="muted">This was a screening run of {len(rows)} compounds, so the interaction '
                   f'analysis below covers the {len(detailed)} best-scoring ones. Every compound is in the '
                   f'table above and in <code>results.csv</code>.</p>') + details

    water_text = ("crystal waters within 3.5 Å of the reference ligand kept as part of the rigid receptor, other waters, "
                  if s.get("keep_waters") and lig else "waters, ")
    created = time.strftime("%Y-%m-%d %H:%M", time.localtime(job["created"]))
    prot = "protonated at pH 7.4 (Dimorphite-DL)" if s.get("protonate") else "used as drawn (no pH adjustment)"
    ref_text = (" The co-crystallized ligand was also docked from its SMILES with exactly the same protocol as the "
                "compounds, for a like-for-like comparison." if any(r.get("reference") for r in job["results"]) else "")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Docking report – {_esc(src)}</title>
<style>
:root {{ --ink:#1b1f24; --muted:#667085; --line:#e4e7ec; --accent:#2f6fde; --bg:#fff; }}
body {{ font: 14px/1.5 -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; color:var(--ink);
       background:var(--bg); max-width: 1180px; margin: 32px auto; padding: 0 16px; }}
h1 {{ font-size: 24px; margin: 0 0 4px; }} h2 {{ font-size: 18px; margin: 28px 0 10px; border-bottom:1px solid var(--line); padding-bottom:6px; }}
h3 {{ font-size: 15px; margin: 0 0 8px; }}
.muted {{ color: var(--muted); }} .small {{ font-size: 12px; }}
.caution {{ border:1px solid #e9b949; background:#fdf6e3; border-radius:8px; padding:10px 12px; margin:16px 0; }}
.meta {{ display:grid; grid-template-columns: repeat(auto-fit,minmax(220px,1fr)); gap: 8px 24px; margin: 16px 0; }}
.meta div b {{ display:block; font-size:11px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
table {{ border-collapse: collapse; width:100%; }}
th, td {{ border-bottom: 1px solid var(--line); padding: 6px 8px; text-align:left; vertical-align: middle; }}
th {{ font-size: 11px; text-transform: uppercase; letter-spacing:.04em; color: var(--muted); }}
td.num {{ text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap; }}
td.score {{ font-weight: 700; font-size: 15px; }}
td.struct svg {{ width: 150px; height: 112px; }}
.cmpd-grid svg {{ width: 280px; height: 210px; }}
tr.ref {{ background: #f5f8ff; }} td.rank {{ font-weight:700; color:var(--muted); }}
.smiles {{ font: 11px/1.3 ui-monospace, Menlo, Consolas, monospace; color: var(--muted); word-break: break-all; max-width: 260px; }}
.delta {{ font-size: 11px; font-weight: 500; }} .delta.better {{ color:#1a7f37; }} .delta.worse {{ color:#b42318; }}
.chip {{ display:inline-block; border-radius: 10px; padding: 1px 7px; margin: 2px; font-size: 12px; background:#f2f4f7; }}
.chip small {{ margin-left: 4px; color: var(--muted); font-size: 10px; }}
.chip.hbond {{ background:#e0ecff; }} .chip.saltbridge {{ background:#fde7e7; }} .chip.pication, .chip.pistacking {{ background:#efe5ff; }}
.chip.hydrophobic {{ background:#f2f4f7; }} .chip.waterbridge {{ background:#dff4fb; }} .chip.halogen {{ background:#e3f6ec; }} .chip.metal {{ background:#fff1d6; }}
.verdict {{ padding: 10px 14px; border-radius: 8px; margin: 8px 0; }}
.verdict.pass {{ background:#e3f6ec; }} .verdict.warn {{ background:#fff4d6; }} .verdict.fail {{ background:#fde7e7; }}
.cmpd {{ border:1px solid var(--line); border-radius: 10px; padding: 14px; margin: 12px 0; break-inside: avoid; }}
.cmpd-grid {{ display:grid; grid-template-columns: 290px 1fr; gap: 16px; }}
table.detail td, table.detail th {{ padding: 3px 8px; font-size: 13px; }}
.legend {{ font-size: 12px; color: var(--muted); }}
@media (max-width: 700px) {{ .cmpd-grid {{ grid-template-columns: 1fr; }} }}
@media print {{ body {{ margin: 0; }} .cmpd, tr {{ break-inside: avoid; }} }}
</style></head><body>
<h1>Molecular docking report</h1>
<div class="muted">{_esc(job['protein'].get('title') or '')}</div>
<div class="meta">
 <div><b>Protein</b>{_esc(src)}</div>
 <div><b>{"Reference ligand" if lig else "Binding site"}</b>{_esc(site_label)}</div>
 <div><b>Compounds docked</b>{len(done)} of {len(job['results'])}</div>
 <div><b>Generated</b>{created}</div>
</div>
{caution_html}
{validation_html}
<section>
 <h2>{'2' if v else '1'}. Docking results</h2>
 <p class="legend">Ranked by best AutoDock Vina affinity (kcal/mol, lower is better). LE = ligand efficiency
 (−affinity / heavy atoms). HBD/HBA = H-bond donors/acceptors. Ro5 = Lipinski rule-of-five violations.
 Interacting residues from PLIP on the top pose: HB hydrogen bond, SB salt bridge, π+ π-cation, π-π π-stacking,
 W water bridge, X halogen bond, M metal, HP hydrophobic.</p>
 <table><thead><tr><th>#</th><th>Structure</th><th>Compound</th><th style="text-align:right">Affinity</th>
 <th style="text-align:right">LE</th><th style="text-align:right">MW</th><th style="text-align:right">cLogP</th>
 <th style="text-align:right">HBD/HBA</th><th style="text-align:right">TPSA</th><th style="text-align:right">Ro5</th>
 <th>Interacting residues (top pose)</th>{cov_head}</tr></thead>
 <tbody>{''.join(table_rows)}</tbody></table>
</section>
{failed_html}
<section>
 <h2>{'3' if v else '2'}. Interactions per compound</h2>
 {details}
</section>
<section>
 <h2>Methods</h2>
 <p>The protein structure ({_esc(src)}) was cleaned with Gemmi ({water_text}alternate conformations and hetero groups
 other than metal ions removed) and hydrogens were added at pH 7.4 with Open Babel {_esc(versions.get('openbabel'))},
 which also wrote the receptor PDBQT. Metal ions are kept in the cleaned structure used for interaction profiling and
 the 3D viewer, but they are <strong>not part of the receptor Vina docks against</strong>: Vina has no attractive term
 for metal coordination, and including them was measured to make redocking worse, so a pose in a metal site is scored
 from the surrounding protein only. {box_text}
 Compounds were parsed with RDKit {_esc(versions.get('rdkit'))} (largest fragment kept), {prot},
 embedded in 3D with ETKDGv3 and MMFF94-minimized, then converted to PDBQT with Meeko {_esc(versions.get('meeko'))}.
 Docking used {_esc(versions.get('vina'))} with exhaustiveness {s.get('exhaustiveness', 8)}, {s.get('num_modes', 9)}
 poses and random seed {s.get('seed', 42)}.{flex_text}{ref_text} Protein–ligand interactions of the top-ranked pose were profiled with
 PLIP {_esc(versions.get('plip'))}. Physicochemical properties were computed with RDKit.</p>
 <p class="muted small">Docking scores estimate binding affinity and are not proof of activity. The receptor is
 treated as rigid; protonation states, tautomers and ring conformations are assigned automatically and may be wrong
 for unusual chemistry.</p>
</section>
</body></html>"""
