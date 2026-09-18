(function () {
  "use strict";

  const API = "";
  let recs = [];
  let expandedKey = null;
  const fmt = (n) => Number(n).toLocaleString();

  async function api(path, opts) {
    const res = await fetch(API + path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
    if (!res.ok) throw new Error(await res.text());
    return res.status === 204 ? null : res.json();
  }

  function tagLabel(a) {
    return { transfer: "Transfer", markdown: "Markdown", replenish: "Replenish", hold: "Hold" }[a];
  }

  function currentFilters() {
    return {
      store: document.getElementById("fStore").value,
      category: document.getElementById("fCategory").value,
      action: document.getElementById("fAction").value,
      status: document.getElementById("fStatus").value,
    };
  }

  async function populateFilterOptions() {
    // derive store list from an unfiltered fetch once
    const all = await api("/api/recommendations");
    const stores = [...new Map(all.map((r) => [r.store_id, r.store_name])).entries()];
    const categories = [...new Set(all.map((r) => r.category))];
    const fStore = document.getElementById("fStore");
    stores.forEach(([id, name]) => {
      const o = document.createElement("option");
      o.value = id;
      o.textContent = name;
      fStore.appendChild(o);
    });
    const fCategory = document.getElementById("fCategory");
    categories.forEach((c) => {
      const o = document.createElement("option");
      o.value = c;
      o.textContent = c;
      fCategory.appendChild(o);
    });
  }

  async function loadRecommendations() {
    const f = currentFilters();
    const qs = new URLSearchParams(f).toString();
    recs = await api("/api/recommendations?" + qs);
  }

  function renderKPIs(allForKpis) {
    const total = allForKpis.length;
    const pending = allForKpis.filter((r) => r.status === "pending").length;
    const approved = allForKpis.filter((r) => r.status === "approved").length;
    const wastePrevented = allForKpis
      .filter((r) => r.status === "approved")
      .reduce((s, r) => s + (r.action === "transfer" ? r.qty : r.action === "markdown" ? r.waste_units : 0), 0);
    const stockoutPrevented = allForKpis
      .filter((r) => r.status === "approved")
      .reduce((s, r) => s + (r.action === "replenish" || r.action === "transfer" ? Math.min(r.qty, r.unmet_units) : 0), 0);
    const cards = [
      { num: total, lbl: "SKU / store combinations monitored" },
      { num: pending, lbl: "Awaiting planner review" },
      { num: approved, lbl: "Actions approved this cycle" },
      { num: wastePrevented, lbl: "Waste units addressed (approved)" },
      { num: stockoutPrevented, lbl: "Stockout units addressed (approved)" },
    ];
    document.getElementById("kpis").innerHTML = cards
      .map((c) => `<div class="kpi"><div class="num">${fmt(c.num)}</div><div class="lbl">${c.lbl}</div></div>`)
      .join("");
    document.getElementById("pendingCount").textContent = `${pending} pending review`;
  }

  function renderLedger() {
    const body = document.getElementById("ledgerBody");
    if (!recs.length) {
      body.innerHTML = `<tr><td colspan="7" class="loading">No rows match these filters.</td></tr>`;
      return;
    }
    body.innerHTML = recs
      .map((r) => {
        const statusHtml =
          r.status === "pending"
            ? `<span class="status-pill">Pending</span>`
            : r.status === "approved"
            ? `<span class="status-pill approved">Approved${r.modified ? " (modified)" : ""}</span>`
            : `<span class="status-pill rejected">Rejected</span>`;
        const actionsHtml =
          r.status === "pending"
            ? `<div class="row-actions">
                 <button class="btn small approve" data-act="approve" data-id="${r.id}">Approve</button>
                 <button class="btn small modify" data-act="modify" data-id="${r.id}">Modify</button>
                 <button class="btn small reject" data-act="reject" data-id="${r.id}">Reject</button>
               </div>`
            : `<div class="row-actions"><button class="btn small ghost" data-act="reset" data-id="${r.id}">Reset</button></div>`;

        const mainRow = `
          <tr class="row" data-id="${r.id}">
            <td>
              <div class="sku-name">${r.sku_name}</div>
              <div class="sku-meta">${r.store_name} · ${r.category}</div>
            </td>
            <td><span class="tag ${r.action}">${tagLabel(r.action)}</span></td>
            <td>${r.timing}</td>
            <td>${r.level}</td>
            <td><div class="conf"><div class="bar"><i style="width:${r.confidence}%"></i></div><span class="mono">${r.confidence}%</span></div></td>
            <td>${statusHtml}</td>
            <td>${actionsHtml}</td>
          </tr>`;

        let evidenceRow = "";
        if (expandedKey === r.id) {
          evidenceRow = `
            <tr class="evidence-row" data-id="${r.id}">
              <td colspan="7">
                <div class="effect">${r.effect}</div>
                <ul>${r.evidence.map((e) => `<li>${e}</li>`).join("")}</ul>
                <div class="modify-form ${r._showModify ? "" : "hidden"}" data-id="${r.id}">
                  <label>Timing<br><input type="text" class="m-timing" value="${r.timing}"></label>
                  <label>Qty / level<br><input type="text" class="m-level" value="${r.level}"></label>
                  <button class="btn small approve" data-act="confirmModify" data-id="${r.id}">Confirm &amp; approve</button>
                </div>
              </td>
            </tr>`;
        }
        return mainRow + evidenceRow;
      })
      .join("");
  }

  async function renderLogs() {
    const [sys, decisions] = await Promise.all([api("/api/logs/system"), api("/api/logs/decisions")]);
    document.getElementById("sysLog").innerHTML = sys
      .map((e) => `<div class="log-entry"><span class="ts mono">${new Date(e.ts).toLocaleTimeString()}</span><span class="src mono">[${e.source}]</span>${e.message}</div>`)
      .join("");
    document.getElementById("decisionLog").innerHTML = decisions.length
      ? decisions.map((e) => `<div class="log-entry"><span class="ts mono">${new Date(e.ts).toLocaleTimeString()}</span>${e.message}</div>`).join("")
      : `<div class="log-entry" style="color:var(--muted);">No planner decisions logged yet.</div>`;
  }

  async function renderEval() {
    const data = await api("/api/evaluation");
    const base = data.baseline,
      poc = data.poc;
    const maxWaste = Math.max(base.waste, poc.waste, 1);
    const maxStock = Math.max(base.stockout, poc.stockout, 1);
    document.getElementById("evalGrid").innerHTML = `
      <div class="evalcard">
        <h3>Projected waste (units, this cycle)</h3>
        <div class="barrow base"><span>Baseline</span><div class="track"><i style="width:${(base.waste / maxWaste) * 100}%"></i></div><span class="mono">${base.waste}</span></div>
        <div class="barrow poc"><span>Agentic POC</span><div class="track"><i style="width:${(poc.waste / maxWaste) * 100}%"></i></div><span class="mono">${poc.waste}</span></div>
        <div class="footnote">${base.waste ? Math.round((1 - poc.waste / base.waste) * 100) : 0}% lower projected waste when transfer + tiered markdown are used instead of a flat rule.</div>
      </div>
      <div class="evalcard">
        <h3>Projected stockout units (this cycle)</h3>
        <div class="barrow base"><span>Baseline</span><div class="track"><i style="width:${(base.stockout / maxStock) * 100}%"></i></div><span class="mono">${base.stockout}</span></div>
        <div class="barrow poc"><span>Agentic POC</span><div class="track"><i style="width:${(poc.stockout / maxStock) * 100}%"></i></div><span class="mono">${poc.stockout}</span></div>
        <div class="footnote">${base.stockout ? Math.round((1 - poc.stockout / base.stockout) * 100) : 0}% lower unmet demand once replenishment timing and cross-store transfer are considered together.</div>
      </div>
      <div class="evalcard">
        <h3>Margin protected by transfer routing</h3>
        <div class="num" style="font-family:'Fraunces',serif; font-size:28px; font-weight:600;">$${fmt(poc.margin_protected)}</div>
        <div class="footnote">Estimated margin saved this cycle by moving at-risk stock to a sibling store with unmet demand, instead of marking it down or writing it off.</div>
      </div>`;
  }

  async function refreshAll() {
    const all = await api("/api/recommendations"); // unfiltered, for KPIs
    renderKPIs(all);
    await loadRecommendations();
    renderLedger();
    await renderLogs();
    await renderEval();
  }

  document.getElementById("ledgerBody").addEventListener("click", async function (e) {
    const btn = e.target.closest("button[data-act]");
    if (btn) {
      e.stopPropagation();
      const id = Number(btn.dataset.id);
      const rec = recs.find((r) => r.id === id);
      const act = btn.dataset.act;
      if (act === "approve") {
        await api(`/api/recommendations/${id}`, { method: "PATCH", body: JSON.stringify({ status: "approved" }) });
      } else if (act === "reject") {
        await api(`/api/recommendations/${id}`, { method: "PATCH", body: JSON.stringify({ status: "rejected" }) });
      } else if (act === "reset") {
        await api(`/api/recommendations/${id}`, { method: "PATCH", body: JSON.stringify({ status: "pending" }) });
      } else if (act === "modify") {
        rec._showModify = true;
        expandedKey = id;
        renderLedger();
        return;
      } else if (act === "confirmModify") {
        const row = document.querySelector(`.modify-form[data-id="${id}"]`);
        const timing = row.querySelector(".m-timing").value;
        const level = row.querySelector(".m-level").value;
        await api(`/api/recommendations/${id}`, { method: "PATCH", body: JSON.stringify({ status: "approved", timing, level }) });
      }
      await refreshAll();
      return;
    }
    const row = e.target.closest("tr.row");
    if (row) {
      const id = Number(row.dataset.id);
      expandedKey = expandedKey === id ? null : id;
      renderLedger();
    }
  });

  ["fStore", "fCategory", "fAction", "fStatus"].forEach((id) => {
    document.getElementById(id).addEventListener("change", async () => {
      await loadRecommendations();
      renderLedger();
    });
  });

  document.getElementById("resim").addEventListener("click", async function () {
    this.disabled = true;
    this.textContent = "Running...";
    await api("/api/cycle/new", { method: "POST" });
    expandedKey = null;
    await refreshAll();
    this.disabled = false;
    this.textContent = "Run new simulation cycle";
  });

  document.getElementById("themeToggle").addEventListener("click", function () {
    const root = document.documentElement;
    const cur = root.getAttribute("data-theme");
    if (cur === "dark") {
      root.setAttribute("data-theme", "light");
      this.textContent = "Dark mode";
    } else {
      root.setAttribute("data-theme", "dark");
      this.textContent = "Light mode";
    }
  });

  function tickClock() {
    const d = new Date();
    document.getElementById("clockDay").textContent = d.toLocaleDateString([], { weekday: "long", month: "short", day: "numeric" });
    document.getElementById("clockTime").textContent = d.toLocaleTimeString();
  }
  setInterval(tickClock, 1000);
  tickClock();

  (async function boot() {
    document.getElementById("ledgerBody").innerHTML = `<tr><td colspan="7" class="loading">Starting orchestrator...</td></tr>`;
    await populateFilterOptions();
    await refreshAll();
  })();
})();
