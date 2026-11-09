// AKADock as an MCP server, for the plugin. Node is used here rather than Python because every
// Claude app ships Node, so the plugin's command is a literal path and needs nothing installed.
//
// It is a thin client of the HTTP API the AKADock window already serves on 127.0.0.1:8801: the
// docking, the self-check and the files are the program's, not this file's. The Python twin in
// mcp/akadock_mcp.py does the same job for the .mcpb bundle and for manual installs.
//
// Only stderr may be written to: stdout carries the protocol.

import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { createInterface } from "node:readline";

const BASE = (process.env.AKADOCK_URL || "http://127.0.0.1:8801").replace(/\/$/, "");
const VERSION = "1.1";
const PROTOCOL = "2025-06-18";
const START_TIMEOUT_MS = 90_000;

const log = (m) => process.stderr.write(`[akadock-mcp] ${m}\n`);

class EngineError extends Error {}

async function api(path, body, timeoutMs = 300_000) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(BASE + path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctl.signal,
    });
  } catch (e) {
    throw new EngineError(`AKADock is not answering on ${BASE}: ${e.message}`);
  } finally {
    clearTimeout(timer);
  }
  const text = await res.text();
  if (!res.ok) {
    let detail;
    try { detail = JSON.parse(text).detail; } catch { /* not JSON */ }
    if (res.status === 404 && (!detail || detail === "Not Found")) {
      detail = `AKADock has no record of this (${path.split("/").pop()}). Check the id.`;
    }
    throw new EngineError(detail || `AKADock refused the request (${res.status}).`);
  }
  return JSON.parse(text);
}

async function engineUp() {
  try {
    return Boolean((await api("/api/ping", undefined, 3000)).ok);
  } catch {
    return false;
  }
}

// Where AKADock is installed. Only needed to start the engine: if the window is already open,
// this is never called.
function findAkadock() {
  const tried = [];
  if (process.env.AKADOCK_HOME) tried.push(process.env.AKADOCK_HOME);
  if (process.env.LOCALAPPDATA) tried.push(join(process.env.LOCALAPPDATA, "Programs", "AKADock"));
  for (const dir of tried) if (existsSync(join(dir, "run.py"))) return dir;
  return null;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function ensureEngine() {
  if (await engineUp()) return;
  if (BASE !== "http://127.0.0.1:8801") throw new EngineError(`No AKADock engine is running at ${BASE}.`);
  const root = findAkadock();
  if (!root) {
    throw new EngineError(
      "AKADock could not be found on this computer. Open the AKADock window once and try again, " +
      "or set AKADOCK_HOME to the folder that holds run.py. The program is at " +
      "https://amr-alani.github.io/akadock/");
  }
  log(`starting the engine in ${root}`);
  spawn(join(root, "python", "pythonw.exe"), [join(root, "run.py"), "--port", "8801", "--no-browser"],
        { cwd: root, detached: true, stdio: "ignore", windowsHide: true }).unref();
  const until = Date.now() + START_TIMEOUT_MS;
  while (Date.now() < until) {
    await sleep(1000);
    if (await engineUp()) { log("engine up"); return; }
  }
  throw new EngineError("AKADock did not start within 90 seconds. Open it by hand and try again.");
}

// ---------------------------------------------------------------- the five tools

async function loadProtein({ pdb_id }) {
  if (!pdb_id) throw new EngineError("Give a pdb_id, for example 4EY7.");
  const info = await api("/api/protein/fetch", { pdb_id });
  const ligands = (info.ligands || []).map((l) => ({
    ligand_key: l.key, name: l.resname, chain: l.chain,
    heavy_atoms: l.heavy_atoms, has_metal: l.has_metal,
  }));
  return {
    protein_id: info.id,
    title: info.title,
    ligands,
    suggested_ligand_key: ligands.length ? ligands[0].ligand_key : null,
    note: "The co-crystallized ligand defines the binding site. With no ligand, pass site_residues " +
          "instead - but then the run cannot validate itself.",
  };
}

async function dock(a) {
  const compounds = (a.compounds || []).map((c) => ({ name: c.name || "", smiles: c.smiles }));
  const redock = a.redock !== false;
  const req = {
    protein_id: a.protein_id,
    ligand_key: a.ligand_key || "",
    site_residues: a.site_residues || "",
    compounds,
    exhaustiveness: a.exhaustiveness ?? 8,
    padding: a.padding ?? 10.0,
    num_modes: a.num_modes ?? 9,
    protonate: a.protonate ?? false,
    keep_waters: a.keep_waters ?? false,
    flex_residues: a.flex_residues || "",
    covalent_residue: a.covalent_residue || "",
    redock,
    dock_reference: a.dock_reference !== false,
  };
  const { id } = await api("/api/jobs", req);
  return {
    job_id: id,
    n_compounds: compounds.length,
    validation: redock && req.ligand_key ? "on" : "off - no crystal pose to compare with",
    next: "Docking runs in the background, about 20-40 s per compound at exhaustiveness 8. " +
          "Poll akadock_status, then read akadock_results.",
  };
}

function verdictOf(job) {
  const v = job.validation || {};
  const rmsd = (v.rmsds || [])[0];
  return {
    verdict: (v.verdict || "NONE").toUpperCase(),
    top_pose_rmsd_A: typeof rmsd === "number" ? Math.round(rmsd * 100) / 100 : null,
    redock_affinity_kcal_mol: v.best ?? null,
    text: v.verdict_text ||
      "The run was not validated: no co-crystallized ligand was redocked, so there is nothing to " +
      "check the setup against.",
  };
}

function trustOf(verdict) {
  if (verdict === "PASS") {
    return [true, "The setup reproduces the crystal pose, so the ranking is worth reading. " +
                  "Differences under about 1 kcal/mol are still inside the method's error."];
  }
  if (verdict === "WARN") {
    return [false, "The crystal pose is among the top three but was not ranked first. Vina finds the " +
                   "right answer here but does not score it best: treat small differences between " +
                   "compounds as noise and say so in any summary."];
  }
  if (verdict === "FAIL") {
    return [false, "The run FAILED its own self-check: the redocked ligand did not reproduce the " +
                   "crystal pose. Do not rank these compounds and do not report the scores as " +
                   "meaningful. Say the validation failed."];
  }
  return [false, "This run was not validated - there was no crystal ligand to redock. The scores " +
                 "cannot be checked against anything, and must be reported that way."];
}

async function status({ job_id }) {
  const job = await api(`/api/jobs/${job_id}`);
  const results = job.results || [];
  const done = results.filter((r) => r.status === "done").length;
  const out = {
    job_id,
    status: job.status,
    step: job.step,
    progress_percent: job.progress,
    docked: `${done} of ${results.length}`,
    validation: verdictOf(job),
  };
  if (job.error) out.error = job.error;
  if (job.status === "queued" || job.status === "running") {
    out.next = "Not finished: poll again in 30 s rather than reporting partial results.";
  }
  return out;
}

async function results({ job_id, limit }) {
  const job = await api(`/api/jobs/${job_id}`);
  const val = verdictOf(job);
  const [trust, advice] = trustOf(val.verdict);
  const rows = (job.results || [])
    .filter((r) => r.status === "done")
    .sort((a, b) => (a.best ?? 0) - (b.best ?? 0))
    .map((r) => ({
      name: r.name,
      smiles: r.smiles,
      affinity_kcal_mol: r.best,
      ligand_efficiency: r.le,
      rmsd_to_crystal_A: (r.rmsds || [])[0] ?? null,
      mw: r.props?.mw,
      clogp: r.props?.logp,
      lipinski_violations: r.props?.lipinski_violations,
      contacts: [...new Set((r.interactions || []).map((i) => i.residue))].sort(),
      covalent: r.covalent ?? null,
    }));
  const settings = {};
  for (const k of ["exhaustiveness", "padding", "num_modes", "protonate", "keep_waters",
                   "flex_residues", "covalent_residue"]) settings[k] = job.settings?.[k];
  return {
    validation: val,
    trust_ranking: trust,
    how_to_report: advice + " Docking scores are predictions, not measurements, and are not " +
                   "evidence that a compound binds.",
    status: job.status,
    protein: job.protein?.title,
    settings,
    ranked: rows.slice(0, limit ?? 25),
    n_total: rows.length,
    failed: (job.results || []).filter((r) => r.status === "failed")
                               .map((r) => ({ name: r.name, error: r.error })),
  };
}

async function files({ job_id }) {
  await api(`/api/jobs/${job_id}`);          // 404s here rather than returning dead paths
  return {
    report_url: `${BASE}/api/jobs/${job_id}/file/report.html`,
    csv_url: `${BASE}/api/jobs/${job_id}/file/results.csv`,
    all_files_zip_url: `${BASE}/api/jobs/${job_id}/file/results.zip`,
    note: "The report is self-contained HTML. The zip holds poses.sdf and complex PDB files that " +
          "open directly in PyMOL or Chimera. Everything is also under workspace/jobs in the " +
          "AKADock folder.",
  };
}

const SMILES_LIST = {
  type: "array",
  description: "The compounds to dock.",
  items: {
    type: "object",
    required: ["smiles"],
    properties: { name: { type: "string" }, smiles: { type: "string" } },
  },
};

const TOOLS = [
  {
    name: "akadock_load_protein",
    description: "Download a protein structure by PDB ID and list the ligands bound in it. The " +
                 "ligand you choose defines the binding site. Call this before docking.",
    inputSchema: { type: "object", required: ["pdb_id"],
                   properties: { pdb_id: { type: "string", description: "4-character PDB ID, e.g. 4EY7." } } },
    fn: loadProtein,
  },
  {
    name: "akadock_dock",
    description: "Start a docking run with AutoDock Vina on this machine and return a job id. " +
                 "Docking takes about 20-40 seconds per compound, so this returns immediately: poll " +
                 "akadock_status. Leave the settings alone unless the user asks - the defaults are " +
                 "the benchmarked ones.",
    inputSchema: {
      type: "object",
      required: ["protein_id", "compounds"],
      properties: {
        protein_id: { type: "string", description: "From akadock_load_protein." },
        compounds: SMILES_LIST,
        ligand_key: { type: "string", description: "The co-crystallized ligand that marks the site; use suggested_ligand_key unless the user wants another." },
        site_residues: { type: "string", description: "Pocket residues (e.g. 'Trp86 Tyr337') when there is no bound ligand. The run then cannot validate itself." },
        exhaustiveness: { type: "integer", minimum: 1, maximum: 64, description: "Search effort, default 8. 16-32 for a careful run." },
        padding: { type: "number", description: "Box padding in A around the ligand, default 10." },
        num_modes: { type: "integer", description: "Poses kept per compound, default 9." },
        protonate: { type: "boolean", description: "Protonate at pH 7.4. Off by default." },
        keep_waters: { type: "boolean", description: "Keep crystal waters near the ligand. Only for close analogues of the reference ligand." },
        flex_residues: { type: "string", description: "Up to 4 side chains to let rotate. Scores from a flexible run compare only with each other." },
        covalent_residue: { type: "string", description: "e.g. 'Cys145': measures how close each warhead gets. Vina cannot form the bond." },
        redock: { type: "boolean", description: "Validate by redocking. Keep this on." },
        dock_reference: { type: "boolean" },
      },
    },
    fn: dock,
  },
  {
    name: "akadock_status",
    description: "Progress of a run, and the validation verdict as soon as the redock finishes. Do " +
                 "not report results while the status is queued or running.",
    inputSchema: { type: "object", required: ["job_id"], properties: { job_id: { type: "string" } } },
    fn: status,
  },
  {
    name: "akadock_results",
    description: "The ranked results of a finished run. The reply starts with the validation verdict " +
                 "and a trust_ranking flag: ALWAYS report the verdict together with any ranking, and " +
                 "never present scores from a FAILED or unvalidated run as meaningful.",
    inputSchema: {
      type: "object", required: ["job_id"],
      properties: { job_id: { type: "string" },
                    limit: { type: "integer", description: "How many top compounds, default 25." } },
    },
    fn: results,
  },
  {
    name: "akadock_files",
    description: "Where this run's report, spreadsheet, pose files and zip are.",
    inputSchema: { type: "object", required: ["job_id"], properties: { job_id: { type: "string" } } },
    fn: files,
  },
];

const INSTRUCTIONS =
  "AKADock docks compounds into a protein with AutoDock Vina on this machine. Every run redocks the " +
  "known crystal ligand first and reports PASS, WARN or FAIL. That verdict decides what the numbers " +
  "are worth: always state it alongside any ranking, and never rank compounds from a run that failed " +
  "its self-check. Docking scores are predictions, not measurements - they are not evidence that a " +
  "compound binds, and must be confirmed in the laboratory.";

// ---------------------------------------------------------------- MCP plumbing

async function callTool(name, args) {
  const tool = TOOLS.find((t) => t.name === name);
  if (!tool) return { content: [{ type: "text", text: `No such tool: ${name}` }], isError: true };
  try {
    await ensureEngine();
    const out = await tool.fn(args || {});
    return { content: [{ type: "text", text: JSON.stringify(out, null, 1) }] };
  } catch (e) {
    if (!(e instanceof EngineError)) log(`${name} failed: ${e.stack || e}`);
    return { content: [{ type: "text", text: String(e.message || e) }], isError: true };
  }
}

async function handle(msg) {
  switch (msg.method) {
    case "initialize":
      return {
        protocolVersion: typeof msg.params?.protocolVersion === "string" ? msg.params.protocolVersion : PROTOCOL,
        capabilities: { tools: {} },
        serverInfo: { name: "akadock", title: "AKADock", version: VERSION },
        instructions: INSTRUCTIONS,
      };
    case "tools/list":
      return { tools: TOOLS.map(({ name, description, inputSchema }) => ({ name, description, inputSchema })) };
    case "tools/call":
      return callTool(msg.params?.name || "", msg.params?.arguments || {});
    case "ping":
      return {};
    default:
      return null;                           // unknown: an error for a request, nothing for a notification
  }
}

log(`serving engine ${BASE}`);
const rl = createInterface({ input: process.stdin });
for await (const line of rl) {
  if (!line.trim()) continue;
  let msg;
  try { msg = JSON.parse(line); } catch { continue; }   // a half-written line is not worth a reply
  const result = await handle(msg);
  if (msg.id === undefined || msg.id === null) continue; // notifications get no response
  if (result === null) {
    process.stdout.write(JSON.stringify({
      jsonrpc: "2.0", id: msg.id,
      error: { code: -32601, message: `Unknown method: ${msg.method}` },
    }) + "\n");
  } else {
    process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result }) + "\n");
  }
}
