"use strict";

const $ = (s) => document.querySelector(s);
const form = $("#form");
const tickersEl = $("#tickers");
const marketEl = $("#market");
const statusEl = $("#status");
const resultsEl = $("#results");
const covEl = $("#coverage");

function showStatus(msg, kind) {
  statusEl.textContent = msg;
  statusEl.className = "status " + (kind || "info");
  statusEl.hidden = false;
}
function hideStatus() {
  statusEl.hidden = true;
}

function fmtPct(x) {
  return (Math.round(x * 100) / 100).toFixed(2) + "%";
}

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])
  );
}

async function lookup(ev) {
  if (ev) ev.preventDefault();
  const q = tickersEl.value.trim();
  if (!q) return;
  const params = new URLSearchParams({ tickers: q });
  if (marketEl.value) params.set("market", marketEl.value);

  showStatus("查詢中…", "info");
  resultsEl.innerHTML = "";
  try {
    const r = await fetch("/api/lookup?" + params.toString());
    const data = await r.json();
    if (!r.ok) {
      showStatus(data.detail || "查詢失敗", "err");
      return;
    }
    render(data);
  } catch (e) {
    showStatus("連線失敗：" + e.message, "err");
  }
}

function render(data) {
  const notes = [];
  if (data.notes && data.notes.length) notes.push(...data.notes);
  if (data.unknown_tickers && data.unknown_tickers.length)
    notes.push("查無資料：" + data.unknown_tickers.join("、") + "（沒有被清單內任何 ETF 持有）");

  if (data.index_count === 0) {
    showStatus(
      "沒有找到任何包含這些代號的指數。" + (notes.length ? "（" + notes.join("；") + "）" : ""),
      "warn"
    );
    return;
  }
  if (notes.length) showStatus(notes.join("；"), "warn");
  else hideStatus();

  const multi = data.mode === "multi";
  const head = document.createElement("div");
  head.className = "summary";
  head.innerHTML = multi
    ? `輸入 <b>${esc(data.normalized.join("、"))}</b>：命中 <b>${data.index_count}</b> 個指數，依輸入股票的<b>權重總和</b>排序`
    : `<b>${esc(data.normalized[0] || data.query[0])}</b>：被納入 <b>${data.index_count}</b> 個指數，依<b>權重</b>排序`;
  resultsEl.appendChild(head);

  for (const idx of data.results) resultsEl.appendChild(renderIndex(idx, multi));
}

function renderIndex(idx, multi) {
  const el = document.createElement("div");
  el.className = "idx";

  const etfTags = idx.etfs
    .map(
      (e) =>
        `<span class="etf-tag ${e.has_data ? "" : "nodata"}" title="${esc(e.name)}${
          e.has_data ? "" : "（尚無持股資料）"
        }">${esc(e.ticker)}${e.has_data ? "" : " ·無資料"}</span>`
    )
    .join("");

  const weightLabel = multi ? "<span>總權重</span>" : "<span>權重</span>";

  let breakdown = "";
  if (multi) {
    const rows = [
      ...idx.matched.map(
        (m) =>
          `<tr><td>${esc(m.query)}</td><td>${esc(m.name || "")}</td><td>${fmtPct(m.weight)}</td></tr>`
      ),
      ...idx.missing.map(
        (t) => `<tr class="miss"><td>${esc(t)}</td><td>—</td><td>不在此指數</td></tr>`
      ),
    ].join("");
    breakdown = `
      <div class="breakdown">
        <table class="bd">
          <thead><tr><th>代號</th><th>名稱</th><th>權重</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>`;
  }

  el.innerHTML = `
    <div class="idx-head">
      <div class="idx-name">${esc(idx.index_name)}<span class="idx-provider">${esc(
    idx.index_provider || ""
  )}</span></div>
      <div class="idx-weight">${fmtPct(idx.total_weight)} ${weightLabel}</div>
    </div>
    <div class="etfs">${etfTags}</div>
    ${breakdown}
    <div class="meta">
      權重取自 <code>${esc(idx.primary_etf)}</code>（${esc(idx.source)}）持股，
      資料日期 ${esc(idx.as_of)}，共 ${idx.holding_count} 檔成分股
    </div>`;
  return el;
}

const SOURCE_LABEL = {
  ishares: "美股・每日",
  ssga: "美股・每日",
  invesco: "美股・每日",
  yuanta: "台股・每日（完整）",
  sitca: "台股・季更（≥1%）",
  manual: "手動維護",
};

function fmtDateRange(dates) {
  const ds = dates.filter(Boolean).sort();
  if (!ds.length) return "—";
  return ds[0] === ds[ds.length - 1] ? ds[0] : `${ds[0]} ~ ${ds[ds.length - 1]}`;
}

function covTable(rows) {
  const trs = rows
    .map((e) => {
      const stale = !e.as_of;
      return `<tr class="${stale ? "miss" : ""}">
        <td><code>${esc(e.ticker)}</code></td>
        <td>${esc(e.name)}</td>
        <td>${esc(e.index || "")}</td>
        <td>${e.as_of ? esc(e.as_of) : "無資料"}</td>
        <td>${e.holding_count ?? "—"}</td>
      </tr>`;
    })
    .join("");
  return `<div class="cov-wrap"><table class="bd covtable">
    <thead><tr><th>代號</th><th>名稱</th><th>指數 / 分類</th><th>資料日期</th><th>檔數</th></tr></thead>
    <tbody>${trs}</tbody>
  </table></div>`;
}

async function loadCoverage() {
  try {
    const etfs = await (await fetch("/api/etfs")).json();
    const groups = {
      us: etfs.filter((e) => e.market === "US"),
      twDaily: etfs.filter((e) => e.market === "TW" && e.source === "yuanta"),
      twQuarterly: etfs.filter((e) => e.market === "TW" && e.source === "sitca"),
      twManual: etfs.filter((e) => e.market === "TW" && e.source === "manual"),
    };
    const withData = etfs.filter((e) => e.as_of).length;

    const sections = [
      ["us", "美股（每日）", groups.us],
      ["twDaily", "台股・每日完整（元大官網）", groups.twDaily],
      ["twQuarterly", "台股・季更 ≥1%（SITCA）", groups.twQuarterly],
      ["twManual", "台股・手動維護", groups.twManual],
    ].filter(([, , rows]) => rows.length);

    const summary = sections
      .map(([, label, rows]) => `${esc(label)} <b>${rows.length}</b>`)
      .join("　·　");

    const details = sections
      .map(([key, label, rows]) => {
        const sorted = [...rows].sort((a, b) => a.ticker.localeCompare(b.ticker));
        return `<details class="cov-group">
          <summary>${esc(label)}（${rows.length} 檔，資料日期 ${esc(
          fmtDateRange(rows.map((r) => r.as_of))
        )}）</summary>
          ${covTable(sorted)}
        </details>`;
      })
      .join("");

    covEl.innerHTML = `
      <details class="cov-main">
        <summary>目前涵蓋的 ETF 清單（共 ${etfs.length} 檔，${withData} 檔有資料）</summary>
        <p class="cov-summary">${summary}</p>
        <p class="cov-note">「指數成分」以 ETF 公布的持股近似，非官方指數檔；SITCA 來源只含占淨值 ≥1% 的持股。</p>
        ${details}
      </details>`;
  } catch (e) {
    covEl.textContent = "";
  }
}

form.addEventListener("submit", lookup);
document.querySelectorAll(".examples button").forEach((b) => {
  b.addEventListener("click", () => {
    tickersEl.value = b.dataset.q;
    marketEl.value = b.dataset.m || "";
    lookup();
  });
});
loadCoverage();
