/* Algo DECA IV - Browser Demo using HiGHS WebAssembly */

const PRESETS = {
  convex: [100, 80, 55, 25, 5],
  linear: [5, 4, 3, 2, 1],
  "top-heavy": [100, 50, 20, 5, 1],
};

let highsInstance = null;
let defaultStudents = [];
let defaultClasses = [];
let activeStudents = [];
let activeClasses = [];
let lastRunData = null;

// --- Compact Pure-JS SHA-256 (for deterministic tie-breaking matching Python's hashlib.sha256) ---
function sha256Bytes(str) {
  const utf8 = new TextEncoder().encode(str);
  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);
  const H = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  const bitLen = utf8.length * 8;
  const paddedLen = Math.ceil((utf8.length + 9) / 64) * 64;
  const buf = new Uint8Array(paddedLen);
  buf.set(utf8);
  buf[utf8.length] = 0x80;
  const view = new DataView(buf.buffer);
  view.setUint32(paddedLen - 4, bitLen >>> 0, false);
  view.setUint32(paddedLen - 8, Math.floor(bitLen / 0x100000000), false);

  const W = new Uint32Array(64);
  const rotr = (x, n) => (x >>> n) | (x << (32 - n));
  for (let offset = 0; offset < paddedLen; offset += 64) {
    for (let i = 0; i < 16; i++) W[i] = view.getUint32(offset + i * 4, false);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(W[i - 15], 7) ^ rotr(W[i - 15], 18) ^ (W[i - 15] >>> 3);
      const s1 = rotr(W[i - 2], 17) ^ rotr(W[i - 2], 19) ^ (W[i - 2] >>> 10);
      W[i] = (W[i - 16] + s0 + W[i - 7] + s1) >>> 0;
    }
    let [a, b, c, d, e, f, g, h] = H;
    for (let i = 0; i < 64; i++) {
      const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
      const ch = (e & f) ^ (~e & g);
      const temp1 = (h + S1 + ch + K[i] + W[i]) >>> 0;
      const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
      const maj = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (S0 + maj) >>> 0;
      h = g; g = f; f = e; e = (d + temp1) >>> 0;
      d = c; c = b; b = a; a = (temp1 + temp2) >>> 0;
    }
    H[0] = (H[0] + a) >>> 0; H[1] = (H[1] + b) >>> 0;
    H[2] = (H[2] + c) >>> 0; H[3] = (H[3] + d) >>> 0;
    H[4] = (H[4] + e) >>> 0; H[5] = (H[5] + f) >>> 0;
    H[6] = (H[6] + g) >>> 0; H[7] = (H[7] + h) >>> 0;
  }
  const out = new Uint8Array(32);
  const outView = new DataView(out.buffer);
  for (let i = 0; i < 8; i++) outView.setUint32(i * 4, H[i], false);
  return out;
}

function deterministicTieBreak(seed, studentId, classId, epsilon) {
  if (epsilon <= 0) return 0;
  const digest = sha256Bytes(`${seed}:${studentId}:${classId}`);
  const hi = ((digest[0] << 24) | (digest[1] << 16) | (digest[2] << 8) | digest[3]) >>> 0;
  const lo = ((digest[4] << 24) | (digest[5] << 16) | (digest[6] << 8) | digest[7]) >>> 0;
  const val = hi * 4294967296 + lo;
  return (val / 18446744073709551616) * epsilon;
}

// --- Simple CSV Parser ---
function parseCSV(text) {
  const clean = text.replace(/^\uFEFF/, "").trim();
  const lines = clean.split(/\r?\n/).filter((l) => l.trim() !== "");
  if (lines.length < 2) return [];
  const headers = lines[0].split(",").map((h) => h.trim());
  return lines.slice(1).map((line) => {
    const cells = line.split(",").map((c) => c.trim());
    const obj = {};
    headers.forEach((h, i) => {
      obj[h] = cells[i] ?? "";
    });
    return obj;
  });
}

function parseStudentsRows(rows) {
  const choiceCols = ["choice_1", "choice_2", "choice_3", "choice_4", "choice_5"];
  return rows.map((r) => ({
    student_id: r.student_id,
    choices: choiceCols.map((c) => r[c]).filter((v) => v && v !== ""),
  }));
}

function parseClassesRows(rows) {
  return rows.map((r) => ({
    class_id: r.class_id,
    max_capacity: r.max_capacity ? parseInt(r.max_capacity, 10) : null,
    min_capacity: r.min_capacity ? parseInt(r.min_capacity, 10) : null,
  }));
}

function wrapTerms(terms, perLine = 8) {
  const lines = [];
  for (let i = 0; i < terms.length; i += perLine) {
    lines.push("  " + terms.slice(i, i + perLine).join(" "));
  }
  return lines.join("\n");
}

function formatObjTerm(coeff, varName, isFirst) {
  const absVal = Math.abs(coeff);
  const numStr = Number.isInteger(absVal) ? String(absVal) : absVal.toFixed(8);
  if (isFirst) {
    return coeff < 0 ? `-${numStr} ${varName}` : `${numStr} ${varName}`;
  }
  return coeff < 0 ? `- ${numStr} ${varName}` : `+ ${numStr} ${varName}`;
}

function buildLPModel(students, classes, params) {
  const sortedStudents = [...students].sort((a, b) => a.student_id.localeCompare(b.student_id));
  const sortedClasses = [...classes].sort((a, b) => a.class_id.localeCompare(b.class_id));
  const classIndex = new Map(sortedClasses.map((c, idx) => [c.class_id, idx]));

  const varNames = [];
  const varMeta = new Map();
  const objTerms = [];
  const studentVars = sortedStudents.map(() => []);
  const classVars = sortedClasses.map(() => []);

  sortedStudents.forEach((stu, sIdx) => {
    const rankMap = new Map(stu.choices.map((cid, r) => [cid, r]));
    const candidates = params.allowUnranked
      ? sortedClasses.map((_, cIdx) => cIdx)
      : stu.choices.map((cid) => classIndex.get(cid)).filter((idx) => idx !== undefined);

    candidates.forEach((cIdx) => {
      const cid = sortedClasses[cIdx].class_id;
      const vName = `x_${sIdx}_${cIdx}`;
      const baseScore = rankMap.has(cid)
        ? params.rankWeights[rankMap.get(cid)]
        : params.unrankedPenalty;
      const tie = deterministicTieBreak(params.seed, stu.student_id, cid, 1e-5);
      const totalCoeff = baseScore + tie;

      varNames.push(vName);
      varMeta.set(vName, { sIdx, cIdx, cid, baseScore, rank: rankMap.has(cid) ? rankMap.get(cid) + 1 : null });
      objTerms.push(formatObjTerm(totalCoeff, vName, objTerms.length === 0));
      studentVars[sIdx].push(vName);
      classVars[cIdx].push(vName);
    });
  });

  const constraints = [];
  sortedStudents.forEach((_, sIdx) => {
    const lhs = studentVars[sIdx].join(" + ");
    constraints.push(` s_${sIdx}: ${lhs} = 1`);
  });

  sortedClasses.forEach((cls, cIdx) => {
    const maxCap = params.overrideCapacities ? params.maxCap : (cls.max_capacity ?? params.maxCap);
    const minCap = params.overrideCapacities ? params.minCap : (cls.min_capacity ?? params.minCap);
    const vars = classVars[cIdx];
    if (vars.length === 0) return;
    const lhs = vars.join(" + ");
    constraints.push(` cmax_${cIdx}: ${lhs} <= ${maxCap}`);
    if (minCap > 0) {
      constraints.push(` cmin_${cIdx}: ${lhs} >= ${minCap}`);
    }
  });

  const lp = [
    "Maximize",
    " obj:",
    wrapTerms(objTerms, 6),
    "Subject To",
    ...constraints,
    "Binary",
    wrapTerms(varNames, 12),
    "End",
    "",
  ].join("\n");

  return { lp, sortedStudents, sortedClasses, varMeta };
}

function getParamsFromUI(overrideCapacities = true) {
  const rankWeights = [1, 2, 3, 4, 5].map((i) => parseFloat(document.getElementById(`w${i}`).value));
  const minCap = parseInt(document.getElementById("min-cap").value, 10);
  const maxCap = parseInt(document.getElementById("max-cap").value, 10);
  const seed = parseInt(document.getElementById("seed").value, 10);
  const unrankedPenalty = parseFloat(document.getElementById("unranked-penalty").value);
  const allowUnranked = document.getElementById("allow-unranked").checked;
  return {
    rankWeights,
    minCap,
    maxCap,
    seed: Number.isNaN(seed) ? 42 : seed,
    unrankedPenalty: Number.isNaN(unrankedPenalty) ? -500 : unrankedPenalty,
    allowUnranked,
    overrideCapacities,
  };
}

function showBanner(type, message) {
  const el = document.getElementById("status-banner");
  el.className = `alert ${type}`;
  el.textContent = message;
  el.classList.remove("hidden");
}

function hideBanner() {
  document.getElementById("status-banner").classList.add("hidden");
}

function generateUUIDv4() {
  if (crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function runOptimization() {
  if (!highsInstance || activeStudents.length === 0 || activeClasses.length === 0) return;

  const params = getParamsFromUI(true);
  if (params.minCap > params.maxCap) {
    showBanner("error", `Invalid seat limits: min (${params.minCap}) cannot exceed max (${params.maxCap}).`);
    return;
  }

  const t0 = performance.now();
  const { lp, sortedStudents, sortedClasses, varMeta } = buildLPModel(activeStudents, activeClasses, params);

  let solution;
  try {
    solution = highsInstance.solve(lp);
  } catch (err) {
    showBanner("error", `Solver error: ${err.message}`);
    return;
  }
  const elapsedMs = performance.now() - t0;

  if (!solution || solution.Status !== "Optimal") {
    document.getElementById("kpi-status").textContent = solution ? solution.Status : "Failed";
    document.getElementById("kpi-time").textContent = `${elapsedMs.toFixed(1)} ms`;
    showBanner(
      "error",
      `No feasible allocation found (status: ${solution ? solution.Status : "Unknown"}). Try increasing max seats per course, lowering min seats, or enabling unranked fallback.`
    );
    return;
  }

  hideBanner();

  const assignedByStudent = new Map();
  const assignedCounts = new Map(sortedClasses.map((c) => [c.class_id, 0]));
  const rankCounts = { "1": 0, "2": 0, "3": 0, "4": 0, "5": 0, UNRANKED: 0 };
  let totalScore = 0;

  for (const [vName, col] of Object.entries(solution.Columns || {})) {
    if ((col.Primal || 0) > 0.5) {
      const meta = varMeta.get(vName);
      if (!meta) continue;
      const stu = sortedStudents[meta.sIdx];
      assignedByStudent.set(stu.student_id, {
        student_id: stu.student_id,
        assigned_class: meta.cid,
        assigned_rank: meta.rank,
        preference_score: meta.baseScore,
        choices: stu.choices,
      });
      assignedCounts.set(meta.cid, (assignedCounts.get(meta.cid) || 0) + 1);
      totalScore += meta.baseScore;
      if (meta.rank !== null) rankCounts[String(meta.rank)] += 1;
      else rankCounts.UNRANKED += 1;
    }
  }

  const firstChoiceDemand = new Map(sortedClasses.map((c) => [c.class_id, 0]));
  activeStudents.forEach((s) => {
    if (s.choices[0]) {
      firstChoiceDemand.set(s.choices[0], (firstChoiceDemand.get(s.choices[0]) || 0) + 1);
    }
  });

  const classSummaries = sortedClasses.map((c) => {
    const count = assignedCounts.get(c.class_id) || 0;
    const fc = firstChoiceDemand.get(c.class_id) || 0;
    const isBottleneck = fc > params.maxCap || (count === params.maxCap && fc >= params.maxCap);
    return {
      class_id: c.class_id,
      assigned_count: count,
      min_capacity: params.minCap,
      max_capacity: params.maxCap,
      utilization_pct: params.maxCap > 0 ? (count / params.maxCap) * 100 : 0,
      first_choice_demand: fc,
      is_bottleneck: isBottleneck,
    };
  });

  const orderedAssignments = activeStudents.map((s) => assignedByStudent.get(s.student_id));
  const nowIso = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  const runId = generateUUIDv4();

  lastRunData = {
    runId,
    timestampUtc: nowIso,
    elapsedMs,
    totalScore,
    params,
    rankCounts,
    classSummaries,
    assignments: orderedAssignments,
  };

  renderResults(lastRunData);
}

function renderResults(data) {
  const n = data.assignments.length;
  const c1 = data.rankCounts["1"];
  const top2 = c1 + data.rankCounts["2"];
  const top3 = top2 + data.rankCounts["3"];

  document.getElementById("kpi-status").textContent = "Optimal";
  document.getElementById("kpi-time").textContent = `${data.elapsedMs.toFixed(0)} ms (HiGHS WASM)`;
  document.getElementById("kpi-c1").textContent = `${((c1 / n) * 100).toFixed(1)}%`;
  document.getElementById("kpi-c1-count").textContent = `${c1} of ${n} students`;
  document.getElementById("kpi-top2").textContent = `${((top2 / n) * 100).toFixed(1)}%`;
  document.getElementById("kpi-top3").textContent = `${((top3 / n) * 100).toFixed(1)}%`;
  document.getElementById("kpi-score").textContent = data.totalScore.toFixed(0);
  document.getElementById("kpi-unranked").textContent = `${data.rankCounts.UNRANKED} unranked`;

  // Rank bars
  const rankBars = document.getElementById("rank-bars");
  rankBars.innerHTML = "";
  ["1", "2", "3", "4", "5", "UNRANKED"].forEach((rk) => {
    const count = data.rankCounts[rk] || 0;
    const pct = n > 0 ? (count / n) * 100 : 0;
    const row = document.createElement("div");
    row.className = "bar-row";
    row.innerHTML = `
      <div class="bar-label">${rk === "UNRANKED" ? "Unranked" : `Choice ${rk}`}</div>
      <div class="bar-track"><div class="bar-fill ${rk === "UNRANKED" ? "unranked" : ""}" style="width: ${pct.toFixed(1)}%"></div></div>
      <div class="bar-stat">${count} (${pct.toFixed(1)}%)</div>
    `;
    rankBars.appendChild(row);
  });

  // Courses table
  const tbody = document.getElementById("courses-tbody");
  tbody.innerHTML = "";
  data.classSummaries.forEach((cs) => {
    const tr = document.createElement("tr");
    const isFull = cs.assigned_count >= cs.max_capacity;
    tr.innerHTML = `
      <td class="mono">${cs.class_id}</td>
      <td class="num">${cs.assigned_count}</td>
      <td class="num">${cs.min_capacity}..${cs.max_capacity}</td>
      <td>
        <div class="fill-mini">
          <div class="fill-mini-bar"><div class="fill-mini-inner ${isFull ? "full" : ""}" style="width: ${Math.min(100, cs.utilization_pct).toFixed(1)}%"></div></div>
          <span>${cs.utilization_pct.toFixed(0)}%</span>
        </div>
      </td>
      <td class="num">${cs.first_choice_demand}</td>
      <td>${cs.is_bottleneck ? '<span class="pill bottleneck">Bottleneck</span>' : '<span class="pill ok">Balanced</span>'}</td>
    `;
    tbody.appendChild(tr);
  });

  // Populate student dropdown (preserve selection if present, else pick first non-rank-1 or STU-0001)
  const select = document.getElementById("student-select");
  const prevId = select.value;
  select.innerHTML = "";
  data.assignments.forEach((a) => {
    const opt = document.createElement("option");
    opt.value = a.student_id;
    opt.textContent = `${a.student_id} -> ${a.assigned_class} (${a.assigned_rank ? `Choice ${a.assigned_rank}` : "Unranked"})`;
    select.appendChild(opt);
  });

  if (prevId && data.assignments.some((a) => a.student_id === prevId)) {
    select.value = prevId;
  } else {
    const nonFirst = data.assignments.find((a) => a.assigned_rank !== 1);
    select.value = nonFirst ? nonFirst.student_id : data.assignments[0].student_id;
  }
  renderStudentAudit(select.value);

  document.getElementById("download-csv-btn").disabled = false;
  document.getElementById("download-json-btn").disabled = false;
}

function renderStudentAudit(studentId) {
  if (!lastRunData) return;
  const rec = lastRunData.assignments.find((a) => a.student_id === studentId);
  if (!rec) return;

  const summaryEl = document.getElementById("student-audit-summary");
  const rankLabel = rec.assigned_rank ? `Choice ${rec.assigned_rank}` : "Unranked";
  summaryEl.innerHTML = `
    <strong>${rec.student_id}</strong> was placed in <strong>${rec.assigned_class}</strong>
    (<strong>${rankLabel}</strong>, score: <code>${rec.preference_score}</code>).<br />
    Submitted preferences: <code>${rec.choices.join(" > ")}</code>
  `;

  const missedWrap = document.getElementById("student-missed-wrap");
  const missedTbody = document.getElementById("student-missed-tbody");
  missedTbody.innerHTML = "";

  const limit = rec.assigned_rank !== null ? rec.assigned_rank - 1 : rec.choices.length;
  if (limit <= 0) {
    missedWrap.classList.add("hidden");
    return;
  }

  missedWrap.classList.remove("hidden");
  const classMap = new Map(lastRunData.classSummaries.map((c) => [c.class_id, c]));
  for (let idx = 0; idx < limit; idx++) {
    const cid = rec.choices[idx];
    const targetRank = idx + 1;
    const cs = classMap.get(cid);
    const higherOrEqual = lastRunData.assignments.filter((other) => {
      if (other.assigned_class !== cid) return false;
      const rIdx = other.choices.indexOf(cid);
      return rIdx !== -1 && rIdx + 1 <= targetRank;
    }).length;
    const isFull = cs.assigned_count >= cs.max_capacity;

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="num">${targetRank}</td>
      <td class="mono">${cid}</td>
      <td class="num">${cs.assigned_count}/${cs.max_capacity}</td>
      <td>${isFull ? '<span class="pill bottleneck">YES</span>' : "NO"}</td>
      <td class="num">${cs.first_choice_demand}</td>
      <td class="num">${higherOrEqual}</td>
    `;
    missedTbody.appendChild(tr);
  }
}

function downloadFile(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function setPreset(name) {
  const weights = PRESETS[name];
  if (!weights) return;
  weights.forEach((val, idx) => {
    document.getElementById(`w${idx + 1}`).value = val;
  });
  document.querySelectorAll("[data-preset]").forEach((btn) => {
    btn.classList.toggle("active-preset", btn.dataset.preset === name);
  });
  runOptimization();
}

function renderInputPreviews() {
  const stuTbody = document.getElementById("students-preview-tbody");
  const clsTbody = document.getElementById("classes-preview-tbody");
  const stuCount = document.getElementById("students-preview-count");
  const clsCount = document.getElementById("classes-preview-count");

  if (stuTbody) {
    const sampleStu = activeStudents.slice(0, 5);
    stuCount.textContent = `Showing ${sampleStu.length} of ${activeStudents.length} rows`;
    stuTbody.innerHTML = "";
    sampleStu.forEach((s) => {
      const tr = document.createElement("tr");
      const cols = [0, 1, 2, 3, 4].map((i) => `<td>${s.choices[i] || ""}</td>`).join("");
      tr.innerHTML = `<td class="mono">${s.student_id}</td>${cols}`;
      stuTbody.appendChild(tr);
    });
  }

  if (clsTbody) {
    const sampleCls = activeClasses.slice(0, 5);
    clsCount.textContent = `Showing ${sampleCls.length} of ${activeClasses.length} rows`;
    clsTbody.innerHTML = "";
    sampleCls.forEach((c) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td class="mono">${c.class_id}</td>
        <td class="num">${c.max_capacity ?? "-"}</td>
        <td class="num">${c.min_capacity ?? "-"}</td>
      `;
      clsTbody.appendChild(tr);
    });
  }
}

async function init() {
  const statusEl = document.getElementById("wasm-status");
  try {
    const [stuRes, clsRes, highs] = await Promise.all([
      fetch("./data/students.csv"),
      fetch("./data/classes.csv"),
      window.Module({
        locateFile: (file) => `https://cdn.jsdelivr.net/npm/highs@1.8.0/build/${file}`,
      }),
    ]);

    highsInstance = highs;
    defaultStudents = parseStudentsRows(parseCSV(await stuRes.text()));
    defaultClasses = parseClassesRows(parseCSV(await clsRes.text()));
    activeStudents = defaultStudents;
    activeClasses = defaultClasses;

    statusEl.textContent = "HiGHS WebAssembly Ready";
    statusEl.classList.add("ok");
    renderInputPreviews();
    runOptimization();
  } catch (err) {
    statusEl.textContent = "Solver Load Failed";
    statusEl.classList.add("err");
    showBanner("error", `Failed to initialize browser solver: ${err.message}`);
  }
}

// --- Event Listeners ---
document.querySelectorAll("[data-preset]").forEach((btn) => {
  btn.addEventListener("click", () => setPreset(btn.dataset.preset));
});

document.getElementById("run-btn").addEventListener("click", runOptimization);

document.getElementById("reset-btn").addEventListener("click", () => {
  document.getElementById("min-cap").value = 10;
  document.getElementById("max-cap").value = 21;
  document.getElementById("seed").value = 42;
  document.getElementById("unranked-penalty").value = -500;
  document.getElementById("allow-unranked").checked = true;
  setPreset("convex");
});

document.getElementById("student-select").addEventListener("change", (e) => {
  renderStudentAudit(e.target.value);
});

document.getElementById("students-upload").addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  activeStudents = parseStudentsRows(parseCSV(await file.text()));
  renderInputPreviews();
  runOptimization();
});

document.getElementById("classes-upload").addEventListener("change", async (e) => {
  const file = e.target.files?.[0];
  if (!file) return;
  activeClasses = parseClassesRows(parseCSV(await file.text()));
  renderInputPreviews();
  runOptimization();
});

document.getElementById("use-default-data-btn").addEventListener("click", () => {
  activeStudents = defaultStudents;
  activeClasses = defaultClasses;
  document.getElementById("students-upload").value = "";
  document.getElementById("classes-upload").value = "";
  renderInputPreviews();
  runOptimization();
});

document.getElementById("download-csv-btn").addEventListener("click", () => {
  if (!lastRunData) return;
  const stamp = lastRunData.timestampUtc.replace(/[-:]/g, "").replace("T", "_").replace("Z", "");
  const shortId = lastRunData.runId.slice(0, 8);
  const header = "run_id,run_timestamp_utc,student_id,assigned_class,assigned_rank,preference_score,choice_1,choice_2,choice_3,choice_4,choice_5";
  const rows = lastRunData.assignments.map((a) => {
    const padded = [0, 1, 2, 3, 4].map((i) => a.choices[i] || "");
    return [
      lastRunData.runId,
      lastRunData.timestampUtc,
      a.student_id,
      a.assigned_class,
      a.assigned_rank ?? "UNRANKED",
      a.preference_score,
      ...padded,
    ].join(",");
  });
  downloadFile(`assignments_${stamp}_${shortId}.csv`, [header, ...rows].join("\n") + "\n", "text/csv");
});

document.getElementById("download-json-btn").addEventListener("click", () => {
  if (!lastRunData) return;
  const stamp = lastRunData.timestampUtc.replace(/[-:]/g, "").replace("T", "_").replace("Z", "");
  const shortId = lastRunData.runId.slice(0, 8);
  const manifest = {
    run_id: lastRunData.runId,
    short_id: shortId,
    timestamp_utc: lastRunData.timestampUtc,
    solver: "highs-wasm-browser-demo",
    parameters: lastRunData.params,
    summary: {
      total_students: lastRunData.assignments.length,
      total_preference_score: lastRunData.totalScore,
      rank_counts: lastRunData.rankCounts,
    },
    class_summaries: lastRunData.classSummaries,
  };
  downloadFile(`audit_manifest_${stamp}_${shortId}.json`, JSON.stringify(manifest, null, 2) + "\n", "application/json");
});

init();
