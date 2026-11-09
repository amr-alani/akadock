/* AKADock front end: protein → compounds → run → results. Plain JS, no build step. */
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const EXAMPLES = `donepezil  COc1cc2c(cc1OC)C(=O)C(CC1CCN(Cc3ccccc3)CC1)C2
galantamine  COc1ccc2c3c1O[C@H]1C[C@@H](O)C=C[C@@]31CCN(C)C2
tacrine  Nc1c2c(nc3ccccc13)CCCC2
rivastigmine  CCN(C)C(=O)Oc1cccc(c1)[C@H](C)N(C)C
huperzine A  C/C=C\\1/[C@@H]2CC3=C([C@]1(CC(=C2)C)N)C=CC(=O)N3`;

const INTER = {
  hbond: { label: "Hydrogen bond", color: "#2f6fde" },
  saltbridge: { label: "Salt bridge", color: "#e5484d" },
  waterbridge: { label: "Water bridge", color: "#0ea5c6" },
  pication: { label: "π-cation", color: "#8e4ec6" },
  pistacking: { label: "π-stacking", color: "#8e4ec6" },
  halogen: { label: "Halogen bond", color: "#12a594" },
  metal: { label: "Metal", color: "#f5a524" },
  hydrophobic: { label: "Hydrophobic", color: "#98a2b3" },
};
const TYPE_ORDER = Object.keys(INTER);

const state = { protein: null, compounds: [], jobId: null, job: null, poll: null, sel: null };
let viewer = null;

// ------------------------------------------------------------------ helpers
async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (_) {}
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return r.json();
}
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
function msg(el, text, kind = "") { el.className = "msg " + kind; el.innerHTML = text; }
const abs = (p) => new URL(p, location.href).href;

// ------------------------------------------------------------------ viewer
const hasWebGL = () => { const c = document.createElement("canvas"); return !!(c.getContext("webgl2") || c.getContext("webgl")); };

async function initViewer() {
  if (typeof molstar !== "undefined" && !hasWebGL()) throw new Error("no WebGL");  // Mol* itself starts without complaint
  viewer = await molstar.Viewer.create("viewer", {
    layoutIsExpanded: false, layoutShowControls: false, layoutShowRemoteState: false,
    layoutShowSequence: false, layoutShowLog: false, layoutShowLeftPanel: false,
    viewportShowExpand: true, viewportShowSelectionMode: false, viewportShowAnimation: false,
    viewportShowTrajectoryControls: false,
    collapseLeftPanel: true,
  });
}

function mvs(root) {
  return JSON.stringify({ metadata: { version: "1.4", timestamp: new Date().toISOString() }, root: { kind: "root", children: root } });
}
const node = (kind, params = {}, children = []) => ({ kind, params, children });
const ELEMENTS = { N: "#3050f8", O: "#ff2a1f", S: "#e6c12a", P: "#ff8000", F: "#56c24a", CL: "#1fb81f", BR: "#a62929", I: "#940094", H: "#ffffff" };
// MVS colors are uniform unless set per element, so carbons get the given color and heteroatoms CPK colors.
const elementColors = (carbon) => [node("color", { color: carbon }),
  ...Object.entries(ELEMENTS).map(([el, c]) => node("color", { color: c, selector: { type_symbol: el } }))];

// One scene load at a time: overlapping loadMvsData calls break inside Mol*.
let sceneQueue = Promise.resolve();
function showScene(children) {
  if (!viewer) return Promise.resolve();
  const run = sceneQueue.then(() => viewer.loadMvsData(mvs(children), "mvsj", { replaceExisting: true }));
  sceneQueue = run.catch(() => {});
  return run;
}

async function showProtein() {
  const p = state.protein;
  const lig = p.ligands.find((l) => l.key === $("#ligand-select").value);
  const fmt = p.format === "cif" ? "mmcif" : "pdb";
  const comps = [
    node("component", { selector: "polymer" }, [node("representation", { type: "cartoon" }, [node("color", { color: "#c6d4ea" })])]),
  ];
  if (!lig) comps.push(node("component", { selector: "ligand" }, [node("representation", { type: "ball_and_stick" }, elementColors("#9aa8bd"))]));
  if (!lig && state.site) {
    comps.push(node("component", { selector: state.site.residues.map((r) => ({ auth_asym_id: r.chain, auth_seq_id: r.resnum })) }, [
      node("representation", { type: "ball_and_stick" }, elementColors("#f59e0b")),
      node("focus", {}),
    ]));
  }
  if (lig) {
    const sel = { auth_asym_id: lig.chain, auth_seq_id: parseInt(lig.resnum, 10) };
    comps.push(node("component", { selector: sel }, [
      node("representation", { type: "ball_and_stick" }, elementColors("#f59e0b")),
      node("focus", {}),
    ]));
  }
  await showScene([node("download", { url: abs(`api/protein/${p.id}/structure.${p.format === "cif" ? "cif" : "pdb"}`) }, [
    node("parse", { format: fmt }, [node("structure", { type: "model" }, comps)]),
  ])]);
  if (viewer) $("#viewer-caption").textContent = lig ? `${p.source} · binding site of ${lig.resname} (orange)`
    : state.site ? `${p.source} · binding-site residues (orange) · box ${state.site.size.join(" × ")} Å` : p.source;
}

async function showPose(dir, name, pose) {
  const jid = state.jobId;
  state.sel = { dir, name, pose };
  const info = await api(`api/jobs/${jid}/pose?dir=${encodeURIComponent(dir)}&pose=${pose}`);
  const ires = new Map();
  for (const i of info.interactions) {
    if (!ires.has(i.residue)) ires.set(i.residue, i);
  }
  const siteSel = info.site.map((r) => ({ auth_asym_id: r.chain, auth_seq_id: r.resnum }));
  const comps = [
    node("component", { selector: "polymer" }, [node("representation", { type: "cartoon" }, [node("color", { color: "#d5deec" })])]),
    node("component", { selector: { auth_asym_id: "Y" } }, [
      node("representation", { type: "ball_and_stick", size_factor: 0.45 }, [node("color", { color: "#22a06b" })]),
    ]),
    node("component", { selector: { auth_asym_id: "Z" } }, [
      node("representation", { type: "ball_and_stick" }, elementColors("#f59e0b")),
      node("focus", {}),
    ]),
  ];
  if (siteSel.length) {
    comps.push(node("component", { selector: siteSel }, [
      node("representation", { type: "ball_and_stick", size_factor: 0.4 }, elementColors("#9aa8bd")),
    ]));
  }
  for (const [res, i] of ires) {
    comps.push(node("component", { selector: { auth_asym_id: i.chain, auth_seq_id: i.resnr, label_atom_id: "CA" } }, [
      node("label", { text: `${i.restype}${i.resnr}` }),
    ]));
  }
  const prims = info.interactions.filter((i) => i.p1 && i.p2).map((i) =>
    node("primitive", { kind: "tube", start: i.p1, end: i.p2, radius: 0.06, dash_length: 0.18, color: INTER[i.type].color }));
  const children = [node("download", { url: abs(`api/jobs/${jid}/complex/${pose}/${dir}/complex.pdb`) }, [
    node("parse", { format: "pdb" }, [node("structure", { type: "model" }, comps)]),
  ])];
  if (prims.length) children.push(node("primitives", {}, prims));
  try {
    await showScene(children);
  } catch (e) {
    console.error(e);
  }
  const rm = info.rmsd_to_crystal != null ? ` · RMSD to crystal ${info.rmsd_to_crystal.toFixed(2)} Å` : "";
  if (viewer) $("#viewer-caption").textContent = `${name} · pose ${pose + 1} (orange)${dir === "redock" ? " vs crystal pose (green)" : (state.job.protein.ligand ? " · crystal ligand in green" : "")}${rm}`;
  renderPosePanel(info);
}

function renderPosePanel(info) {
  const { dir, name, pose } = state.sel;
  const r = dir === "redock" ? state.job.validation : state.job.results.find((x) => x.dir === dir);
  $("#pose-panel").classList.remove("hidden");
  $("#pose-title").textContent = `${name}: pose ${pose + 1}, ${r.scores[pose].toFixed(2)} kcal/mol`;
  $("#pose-select").innerHTML = r.scores.map((s, i) => `<option value="${i}" ${i === pose ? "selected" : ""}>${i + 1}  (${s.toFixed(2)})</option>`).join("");
  const rows = [...info.interactions].sort((a, b) => a.resnr - b.resnr || TYPE_ORDER.indexOf(a.type) - TYPE_ORDER.indexOf(b.type))
    .map((i) => `<tr><td>${esc(i.residue)}</td><td><span class="swatch" style="background:${INTER[i.type].color}"></span>${esc(i.label)}</td><td>${i.distance.toFixed(2)} Å</td><td class="muted">${esc(i.detail)}</td></tr>`).join("");
  $("#pose-interactions").innerHTML = rows
    ? `<table><thead><tr><th>Residue</th><th>Interaction</th><th>Distance</th><th></th></tr></thead><tbody>${rows}</tbody></table>`
    : `<p class="muted small">No interactions detected for this pose.</p>`;
}

// ------------------------------------------------------------------ step 1: protein
function setProtein(p) {
  state.protein = p;
  state.site = null;
  $("#protein-info").classList.remove("hidden");
  $("#protein-title").textContent = p.title || p.source;
  const sel = $("#ligand-select");
  const manual = `<option value="">None: define the binding site by residues</option>`;
  sel.innerHTML = p.ligands.map((l) => `<option value="${esc(l.key)}">${esc(l.resname)} · chain ${esc(l.chain)} · ${esc(l.resnum)} (${l.heavy_atoms} heavy atoms)${l.has_metal ? " · contains metal, cannot dock" : ""}</option>`).join("") + manual;
  msg($("#site-msg"), "");
  if (!p.ligands.length) {
    msg($("#protein-msg"), `Loaded ${esc(p.source)}. It has no bound ligand, so type the binding-site residues below. Redocking validation needs a bound ligand and will be skipped.`, "");
  } else {
    msg($("#protein-msg"), `Loaded ${esc(p.source)}: ${p.chains.length} protein chain(s), ${p.ligands.length} candidate ligand(s).`, "ok");
  }
  onLigandChange();
}

function onLigandChange() {
  const manual = !$("#ligand-select").value;
  $("#site-box").classList.toggle("hidden", !manual);
  if (manual && $("#site-res").value.trim() && !state.site) return previewSite();
  updateRunButton();
  showProtein().catch(console.error);
}

async function previewSite() {
  const text = $("#site-res").value.trim();
  state.site = null;
  if (!text) { updateRunButton(); return; }
  try {
    state.site = await post(`api/protein/${state.protein.id}/site`, { residues: text });
    const box = `Box centered at ${state.site.center.join(", ")} Å, size ${state.site.size.join(" × ")} Å.`;
    msg($("#site-msg"), state.site.warning ? `${box} ${esc(state.site.warning)}` : box, state.site.warning ? "error" : "ok");
  } catch (e) { msg($("#site-msg"), esc(e.message), "error"); }
  updateRunButton();
  showProtein().catch(console.error);
}

async function fetchProtein() {
  const id = $("#pdb-id").value.trim();
  if (!id) return;
  msg($("#protein-msg"), `<span class="spinner"></span> Downloading ${esc(id.toUpperCase())}…`, "busy");
  try { setProtein(await post("api/protein/fetch", { pdb_id: id })); loadLibrary(); }
  catch (e) { msg($("#protein-msg"), esc(e.message), "error"); }
}

async function uploadProtein(file) {
  const fd = new FormData();
  fd.append("file", file);
  msg($("#protein-msg"), `<span class="spinner"></span> Reading ${esc(file.name)}…`, "busy");
  try { setProtein(await api("api/protein/upload", { method: "POST", body: fd })); loadLibrary(); }
  catch (e) { msg($("#protein-msg"), esc(e.message), "error"); }
}

// Saved protein library: everything loaded before, so a protein can be reused without downloading again.
async function loadLibrary() {
  try {
    const list = await api("api/proteins");
    const cur = state.protein && state.protein.id;
    $("#library").innerHTML = `<option value="">Saved proteins (${list.length})…</option>` + list.map((p) =>
      `<option value="${esc(p.id)}" ${p.id === cur ? "selected" : ""}>${esc(p.source)}${p.title ? " · " + esc(p.title.slice(0, 60)) : ""}</option>`).join("");
    $("#btn-lib-del").disabled = !$("#library").value;
  } catch (_) {}
}

async function openSaved(pid) {
  msg($("#protein-msg"), `<span class="spinner"></span> Opening…`, "busy");
  try { setProtein(await api(`api/protein/${encodeURIComponent(pid)}`)); }
  catch (e) { msg($("#protein-msg"), esc(e.message), "error"); }
  loadLibrary();
}

async function deleteSaved() {
  const pid = $("#library").value;
  if (!pid) return;
  const name = $("#library").selectedOptions[0].textContent;
  if (!confirm(`Remove ${name} from the saved proteins?\n\nFinished runs are not affected.`)) return;
  try { await api(`api/protein/${encodeURIComponent(pid)}`, { method: "DELETE" }); }
  catch (e) { msg($("#protein-msg"), esc(e.message), "error"); }
  loadLibrary();
}

// ------------------------------------------------------------------ step 2: compounds
async function addCompound(name, smiles) {
  const c = await post("api/compound/check", { smiles });
  name = name || `Compound ${state.compounds.length + 1}`;
  state.compounds.push({ name, smiles: c.smiles, svg: c.svg, props: c.props });
  renderCompounds();
}

function renderCompounds() {
  $("#cmp-count").textContent = state.compounds.length;
  $("#compound-list").innerHTML = state.compounds.map((c, i) => `
    <li><div class="thumb">${c.svg || ""}</div>
      <div><div class="nm" contenteditable="true" data-i="${i}" spellcheck="false">${esc(c.name)}</div>
      <div class="smi">${esc(c.smiles)}</div><div class="smi">MW ${c.props.mw} · cLogP ${c.props.logp}</div></div>
      <button data-del="${i}" title="Remove">×</button></li>`).join("");
  updateRunButton();
}

async function addPasted(text) {
  const lines = text.split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));
  const errors = [];
  for (const line of lines) {
    const parts = line.split(/\s+|,|\t/).filter(Boolean);
    let smiles = parts[parts.length - 1];
    let name = parts.slice(0, -1).join(" ");
    try { await addCompound(name, smiles); }
    catch (e) {
      // maybe "SMILES name" order
      try { await addCompound(parts.slice(1).join(" "), parts[0]); }
      catch (_) { errors.push(line); }
    }
  }
  msg($("#compound-msg"), errors.length ? `Could not read: ${errors.map(esc).join("; ")}` : `${lines.length} compound(s) added.`, errors.length ? "error" : "ok");
}

async function uploadCompounds(file) {
  const fd = new FormData();
  fd.append("file", file);
  msg($("#compound-msg"), `<span class="spinner"></span> Reading ${esc(file.name)}…`, "busy");
  try {
    const list = await api("api/compound/upload", { method: "POST", body: fd });
    state.compounds.push(...list);
    renderCompounds();
    msg($("#compound-msg"), `${list.length} compound(s) added from ${esc(file.name)}.`, "ok");
  } catch (e) { msg($("#compound-msg"), esc(e.message), "error"); }
}

async function searchLibrary() {
  const query = $("#lib-query").value.trim();
  const sel = $("#lib-targets");
  if (!query) return msg($("#lib-msg"), "Type a target name first.", "error");
  if (/^CHEMBL\d+$/i.test(query)) return fetchLibrary(query.toUpperCase());
  msg($("#lib-msg"), `<span class="spinner"></span> Searching ChEMBL for ${esc(query)}…`, "busy");
  sel.classList.add("hidden");
  try {
    const { targets } = await post("api/compound/library", { query });
    sel.innerHTML = targets
      .map((t) => `<option value="${esc(t.id)}">${esc(t.name)} — ${esc(t.organism)} (${esc(t.id)})</option>`)
      .join("");
    sel.classList.remove("hidden");
    msg($("#lib-msg"), `${targets.length} target(s). Pick the right species, then it downloads automatically.`, "ok");
  } catch (e) { msg($("#lib-msg"), esc(e.message), "error"); }
}

async function fetchLibrary(targetId) {
  const limit = Math.max(1, Math.min(500, +$("#lib-limit").value || 100));
  msg($("#lib-msg"), `<span class="spinner"></span> Downloading up to ${limit} compounds for ${esc(targetId)}…`, "busy");
  try {
    const { compounds } = await post("api/compound/library", { target_id: targetId, limit });
    state.compounds.push(...compounds);
    renderCompounds();
    msg($("#lib-msg"), `${compounds.length} compound(s) added, strongest measured activity first.`, "ok");
    msg($("#compound-msg"), compounds.length > 50
      ? `${compounds.length} compounds loaded. This is a screening run: every compound is docked and scored, and the best ones get the full interaction analysis.`
      : `${compounds.length} compound(s) added from ChEMBL.`, "ok");
  } catch (e) { msg($("#lib-msg"), esc(e.message), "error"); }
}

function ketcher() { return $("#ketcher").contentWindow?.ketcher; }

function openDraw() {
  const f = $("#ketcher");
  if (!f.src) f.src = "vendor/ketcher/index.html";
  $("#draw-dialog").showModal();
  msg($("#draw-msg"), "");
}

async function addDrawn() {
  const k = ketcher();
  if (!k) return msg($("#draw-msg"), "The editor is still loading…", "busy");
  try {
    const smiles = await k.getSmiles();
    if (!smiles) return msg($("#draw-msg"), "Draw a structure first.", "error");
    await addCompound($("#draw-name").value.trim(), smiles);
    msg($("#draw-msg"), `Added ${esc(state.compounds.at(-1).name)}. Draw the next one, or close.`, "ok");
    $("#draw-name").value = "";
    k.setMolecule("");
  } catch (e) { msg($("#draw-msg"), esc(e.message || e), "error"); }
}

// ------------------------------------------------------------------ step 3: run
function updateRunButton() {
  const lig = state.protein && $("#ligand-select").value;
  const site = lig || state.site;
  const ok = state.protein && site && (state.compounds.length || (lig && ($("#set-redock").checked || $("#set-ref").checked)));
  $("#btn-run").disabled = !ok;
  const j = state.job;
  const canAdd = j && (j.status === "done" || j.status === "failed" || j.status === "cancelled") && j.box && state.compounds.length;
  $("#btn-add").classList.toggle("hidden", !canAdd);
  if (canAdd) $("#btn-add").textContent = `Add ${state.compounds.length} compound(s) to the open run (${j.protein.source}, same setup)`;
}

async function addToRun() {
  const jid = state.jobId;
  try {
    const r = await post(`api/jobs/${jid}/compounds`, { compounds: state.compounds.map((c) => ({ name: c.name, smiles: c.smiles })) });
    state.compounds = [];
    renderCompounds();
    msg($("#run-msg"), `${r.added} compound(s) added to the open run. They use the same prepared protein, box and settings.`, "ok");
    openJob(jid);
  } catch (e) { msg($("#run-msg"), esc(e.message), "error"); }
}

// Copy protein, binding site and settings of the open run into the form, for a new run with other compounds.
async function sameSetup() {
  const job = state.job;
  if (!job) return;
  const s = job.settings;
  try {
    setProtein(await api(`api/protein/${encodeURIComponent(job.protein.id)}`));
  } catch (e) {
    return msg($("#protein-msg"), `${esc(e.message)} Load the protein again (${esc(job.protein.source || "")}) to reuse this setup.`, "error");
  }
  const sel = $("#ligand-select");
  sel.value = job.protein.ligand || "";
  if (sel.value !== (job.protein.ligand || "")) sel.value = "";
  $("#site-res").value = s.site_residues || "";
  $("#set-exh").value = s.exhaustiveness ?? 8;
  $("#set-pad").value = s.padding ?? 10;
  $("#set-modes").value = s.num_modes ?? 9;
  $("#set-redock").checked = !!job.protein.ligand && s.redock !== false;
  $("#set-ref").checked = s.dock_reference !== false;
  $("#set-prot").checked = !!s.protonate;
  $("#set-waters").checked = !!s.keep_waters;
  $("#set-flex").value = s.flex_residues || "";
  $("#set-covalent").value = s.covalent_residue || "";
  $("#set-timeout").value = s.timeout_minutes ?? 10;
  state.site = null;
  onLigandChange();
  loadLibrary();
  state.compounds = [];
  renderCompounds();
  msg($("#run-msg"), `Setup copied from the run of ${new Date(job.created * 1000).toLocaleString()}. Add compounds, then press Run docking.`, "ok");
  $("#step-compounds").scrollIntoView({ behavior: "smooth" });
}

async function runJob() {
  const body = {
    protein_id: state.protein.id,
    ligand_key: $("#ligand-select").value,
    site_residues: $("#ligand-select").value ? "" : $("#site-res").value,
    compounds: state.compounds.map((c) => ({ name: c.name, smiles: c.smiles })),
    exhaustiveness: +$("#set-exh").value,
    padding: +$("#set-pad").value,
    num_modes: +$("#set-modes").value,
    redock: $("#set-redock").checked,
    dock_reference: $("#set-ref").checked,
    protonate: $("#set-prot").checked,
    keep_waters: $("#set-waters").checked,
    flex_residues: $("#set-flex").value,
    covalent_residue: $("#set-covalent").value,
    timeout_minutes: +$("#set-timeout").value,
  };
  try {
    const { id } = await post("api/jobs", body);
    msg($("#run-msg"), "Started. You can keep this window open; the run continues in the background.", "ok");
    openJob(id);
    loadHistory();
  } catch (e) { msg($("#run-msg"), esc(e.message), "error"); }
}

// ------------------------------------------------------------------ results
function chips(inter) {
  const by = new Map();
  for (const i of [...(inter || [])].sort((a, b) => a.resnr - b.resnr)) {
    if (!by.has(i.residue)) by.set(i.residue, new Set());
    by.get(i.residue).add(i.type);
  }
  const out = [];
  for (const [res, types] of by) {
    const t = [...types].sort((a, b) => TYPE_ORDER.indexOf(a) - TYPE_ORDER.indexOf(b));
    out.push(`<span class="chip ${t[0]}" title="${t.map((x) => INTER[x].label).join(", ")}">${esc(res)}</span>`);
  }
  return out.join("") || '<span class="muted small">none</span>';
}

function duration(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return `${sec} sec`;
  const m = Math.round(sec / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

// Time left is estimated from the wall-clock time per finished compound, so it already accounts
// for however many are docking at once.
function timingLine(job, running) {
  const total = job.results.length;
  const finished = job.results.filter((r) => r.status === "done" || r.status === "failed").length;
  const elapsed = Date.now() / 1000 - job.created;
  if (!running) {
    return total ? `${finished} of ${total} compound(s) · took ${esc(duration(elapsed))}` : "";
  }
  const now = job.results.filter((r) => r.status === "running").map((r) => r.name);
  const parts = [`Running for ${duration(elapsed)}`];
  if (finished && finished < total) parts.push(`about ${duration((elapsed / finished) * (total - finished))} left`);
  else if (!finished) parts.push("estimating the time left after the first compound finishes");
  if (now.length) parts.push(`docking now: ${now.map(esc).join(", ")}`);
  return parts.join(" · ");
}

// Which column the results table is sorted by. Affinity ascending is the default: best binder first.
const sort = { key: "best", dir: 1 };

function sorter() {
  const get = {
    name: (r) => (r.name || "").toLowerCase(),
    best: (r) => r.best,
    le: (r) => r.le,
    mw: (r) => r.props?.mw,
    logp: (r) => r.props?.logp,
  }[sort.key];
  return (a, b) => {
    const x = get(a), y = get(b);
    if (x === y) return 0;
    if (x === undefined || x === null) return 1;
    if (y === undefined || y === null) return -1;
    return (x > y ? 1 : -1) * sort.dir;
  };
}

function renderJob(job) {
  state.job = job;
  $("#results").classList.remove("hidden");
  $("#job-title").textContent = `Results · ${job.protein.source || ""}${job.protein.ligand ? " · " + job.protein.ligand.split(":")[0] : ""}`;
  const running = job.status === "running" || job.status === "queued";
  $("#job-step").innerHTML = running ? `<span class="spinner"></span> ${esc(job.step)}`
    : job.status === "failed" ? `<span style="color:var(--bad)">Failed: ${esc(job.error || job.step)}</span>`
    : job.status === "cancelled" ? `Stopped · the compounds below were finished before it stopped`
    : `Finished · ${new Date(job.created * 1000).toLocaleString()}`;
  $("#progress-bar").style.width = `${job.progress}%`;
  $("#btn-stop").classList.toggle("hidden", !running);
  $("#job-timing").innerHTML = timingLine(job, running);
  for (const [id, f] of [["#btn-report", "report.html"], ["#btn-zip", "results.zip"], ["#btn-csv", "results.csv"]]) {
    const a = $(id);
    a.href = `api/jobs/${job.id}/file/${f}`;
    a.classList.toggle("disabled", job.status !== "done");
  }
  const v = job.validation;
  $("#validation").innerHTML = v ? `<div class="verdict ${v.verdict}"><b>Validation ${v.verdict.toUpperCase()}</b> · ${esc(v.verdict_text)} <a href="#" data-view="redock">View redocked pose</a></div>` : (running && job.settings.redock ? `<div class="verdict"><span class="spinner"></span> Validating the setup by redocking the co-crystallized ligand…</div>` : "");

  const rows = [];
  if (v) rows.push({ ...v, dir: "redock", ref: true, status: "done" });
  const done = job.results.filter((r) => r.status === "done").sort(sorter());
  const other = job.results.filter((r) => r.status !== "done");
  rows.push(...done, ...other);
  let rank = 0;
  $("#results-body").innerHTML = rows.map((r) => {
    if (r.status !== "done") {
      const st = r.status === "running" ? '<span class="spinner"></span> docking…'
        : r.status === "failed" ? `failed: ${esc(r.error)}`
        : r.status === "cancelled" ? `not docked — ${esc(r.error || "the run was stopped")}`
        : "waiting";
      return `<tr><td></td><td></td><td><b>${esc(r.name)}</b></td><td colspan="5" class="status ${r.status}">${st}</td></tr>`;
    }
    if (!r.ref) rank++;
    const sel = state.sel && state.sel.dir === r.dir ? "selected" : "";
    return `<tr class="${r.ref ? "ref" : ""} ${sel}" data-dir="${esc(r.dir)}" data-name="${esc(r.name)}">
      <td>${r.ref ? "Ref" : rank}</td><td class="thumb">${r.svg}</td>
      <td><b>${esc(r.name)}</b>${r.ref ? `<div class="muted small">redock RMSD ${r.rmsds[0].toFixed(2)} Å</div>` : r.reference && r.rmsds ? `<div class="muted small">original ligand docked from SMILES · RMSD to crystal ${r.rmsds[0].toFixed(2)} Å</div>` : ""}</td>
      <td class="r score">${r.best.toFixed(2)}</td><td class="r">${r.le.toFixed(2)}</td>
      <td class="r">${r.props.mw}</td><td class="r">${r.props.logp}</td><td>${chips(r.interactions)}</td></tr>`;
  }).join("");
  $("#job-log").textContent = job.log.join("\n");
  $("#btn-same").disabled = running;
  updateRunButton();
}

async function openJob(id) {
  state.jobId = id;
  state.sel = null;
  $("#pose-panel").classList.add("hidden");
  clearInterval(state.poll);
  const tick = async () => {
    try {
      const job = await api(`api/jobs/${id}`);
      if (state.jobId !== id) return;
      const wasRunning = state.job && state.job.id === id && state.job.status === "running";
      renderJob(job);
      if (job.status === "done" || job.status === "failed" || job.status === "cancelled") {
        clearInterval(state.poll);
        if (wasRunning || !state.sel) autoShowBest(job);
        loadHistory();
      }
    } catch (e) { console.error(e); }
  };
  await tick();
  state.poll = setInterval(tick, 2000);
}

function autoShowBest(job) {
  const best = job.results.filter((r) => r.status === "done").sort((a, b) => a.best - b.best)[0];
  if (best) showPose(best.dir, best.name, 0).then(() => renderJob(job));
  else if (job.validation) showPose("redock", job.validation.name, 0).then(() => renderJob(job));
}

async function loadHistory() {
  try {
    const list = await api("api/jobs");
    $("#history").innerHTML = '<option value="">Previous runs…</option>' + list.map((j) =>
      `<option value="${j.id}">${new Date(j.created * 1000).toLocaleString()} · ${esc(j.protein?.source || "")} · ${j.n_compounds} cpd · ${j.status}</option>`).join("");
  } catch (_) {}
}

async function checkHealth() {
  try {
    const h = await api("api/health");
    if (!h.ok) throw new Error(h.error || "Missing components: run python scripts/setup_assets.py");
    $("#health").textContent = `${h.versions.vina} · RDKit ${h.versions.rdkit}`;
  } catch (e) {
    $("#health").textContent = e.message;
    $("#health").classList.add("bad");
  }
}

// ------------------------------------------------------------------ wiring
$("#btn-fetch").onclick = fetchProtein;
$("#pdb-id").addEventListener("keydown", (e) => { if (e.key === "Enter") fetchProtein(); });
$("#pdb-file").onchange = (e) => e.target.files[0] && uploadProtein(e.target.files[0]);
$("#ligand-select").onchange = onLigandChange;
$("#btn-site").onclick = previewSite;
$("#site-res").addEventListener("keydown", (e) => { if (e.key === "Enter") previewSite(); });
$("#site-res").addEventListener("input", () => { state.site = null; updateRunButton(); });
$("#btn-draw").onclick = openDraw;
$("#btn-draw-close").onclick = () => $("#draw-dialog").close();
$("#btn-draw-add").onclick = addDrawn;
$("#cmp-file").onchange = (e) => { if (e.target.files[0]) uploadCompounds(e.target.files[0]); e.target.value = ""; };
$("#btn-paste").onclick = () => $("#paste-box").classList.toggle("hidden");
$("#btn-lib-fetch").onclick = () => $("#lib-box").classList.toggle("hidden");
$("#btn-lib-search").onclick = searchLibrary;
$("#lib-query").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); searchLibrary(); } };
$("#lib-targets").onchange = (e) => { if (e.target.value) fetchLibrary(e.target.value); };
$("#btn-example").onclick = () => { $("#paste-text").value = EXAMPLES; };
$("#btn-paste-add").onclick = async () => { await addPasted($("#paste-text").value); $("#paste-text").value = ""; };
$("#compound-list").addEventListener("click", (e) => {
  const d = e.target.closest("[data-del]");
  if (d) { state.compounds.splice(+d.dataset.del, 1); renderCompounds(); }
});
$("#compound-list").addEventListener("input", (e) => {
  const n = e.target.closest("[data-i]");
  if (n) state.compounds[+n.dataset.i].name = n.textContent.trim();
});
$("#set-redock").onchange = updateRunButton;
$("#set-ref").onchange = updateRunButton;
$("#btn-add").onclick = addToRun;
document.querySelectorAll("th[data-sort]").forEach((th) => {
  th.classList.add("sortable");
  th.onclick = () => {
    const key = th.dataset.sort;
    // same column again reverses; a new column starts the way that column reads best
    sort.dir = sort.key === key ? -sort.dir : (key === "name" ? 1 : key === "best" ? 1 : -1);
    sort.key = key;
    document.querySelectorAll("th[data-sort]").forEach((o) => (o.dataset.dir = ""));
    th.dataset.dir = sort.dir > 0 ? "up" : "down";
    if (state.job) renderJob(state.job);
  };
});

$("#btn-viewer-big").onclick = () => {
  const wrap = document.querySelector(".viewer-wrap");
  const big = wrap.classList.toggle("big");
  $("#btn-viewer-big").textContent = big ? "Shrink" : "Enlarge";
  // Mol* measures its canvas on resize, so tell it the box changed
  setTimeout(() => window.dispatchEvent(new Event("resize")), 60);
};

$("#btn-same").onclick = sameSetup;
$("#btn-stop").onclick = async () => {
  if (!state.job || !confirm("Stop this run? The compounds already docked are kept.")) return;
  try {
    await post(`api/jobs/${state.job.id}/cancel`);
    msg($("#run-msg"), "Stopping… the compounds already docked are kept.", "busy");
  } catch (e) { msg($("#run-msg"), esc(e.message), "error"); }
};
$("#library").onchange = (e) => { $("#btn-lib-del").disabled = !e.target.value; if (e.target.value) openSaved(e.target.value); };
$("#btn-lib-del").onclick = deleteSaved;
$("#btn-run").onclick = runJob;
$("#results-body").addEventListener("click", (e) => {
  const tr = e.target.closest("tr[data-dir]");
  if (tr) showPose(tr.dataset.dir, tr.dataset.name, 0).then(() => renderJob(state.job));
});
$("#validation").addEventListener("click", (e) => {
  if (e.target.dataset.view) { e.preventDefault(); showPose("redock", state.job.validation.name, 0).then(() => renderJob(state.job)); }
});
$("#pose-select").onchange = (e) => showPose(state.sel.dir, state.sel.name, +e.target.value);
$("#history").onchange = (e) => e.target.value && openJob(e.target.value);

function viewerError(e) {
  if (typeof molstar === "undefined") {
    return "3D viewer failed to load: its files did not download. Check that the AKADock window is still open, then reload this page (F5).";
  }
  if (!hasWebGL()) {
    return "3D viewer failed to load: WebGL is turned off in this browser. Turn on \"Use graphics acceleration\" in the browser's System settings, restart the browser and reload, or open AKADock in another browser.";
  }
  return "3D viewer failed to load: " + (e && e.message || e);
}
initViewer().catch((e) => { console.error(e); $("#viewer-caption").textContent = viewerError(e); });
checkHealth();
loadHistory();
loadLibrary();
