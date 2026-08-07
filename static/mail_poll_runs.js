let rosterReady = false;

async function parseApiJson(res) {
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (data && typeof data === "object") return data;
  const snippet = (text || "").replace(/\s+/g, " ").trim().slice(0, 160);
  throw new Error(
    snippet
      ? `応答が JSON ではありません (HTTP ${res.status}): ${snippet}`
      : `応答が空です (HTTP ${res.status})`
  );
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function escapeAttr(s) {
  return escapeHtml(s).replaceAll("'", "&#39;");
}

function todayJstYmd() {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Tokyo",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return fmt.format(new Date());
}

function formatTime(iso) {
  if (!iso) return "—";
  const s = String(iso);
  const m = s.match(/T(\d{2}:\d{2}:\d{2})/);
  return m ? m[1] : s;
}

function filterLabel({ date, userId, errorsOnly }) {
  const parts = [];
  parts.push(date || "日付指定なし");
  if (userId) parts.push(userId);
  if (errorsOnly) parts.push("エラーあり");
  return parts.join(" · ");
}

async function loadRosterOptions() {
  const sel = document.getElementById("filterUser");
  const prev = sel.value;
  try {
    const res = await fetch("/api/users");
    const data = await parseApiJson(res);
    if (!data.ok) throw new Error(data.error || "failed");
    const ids = new Set();
    for (const u of data.roster || []) {
      if (u.user_id) ids.add(u.user_id);
    }
    for (const u of data.users || []) {
      if (u.is_template) continue;
      const id = u.user_id || (u.gmail ? String(u.gmail).split("@")[0] : "");
      if (id) ids.add(id);
    }
    const opts = [...ids].sort((a, b) => a.localeCompare(b, "ja"));
    sel.innerHTML =
      '<option value="">すべて</option>' +
      opts
        .map(
          (id) =>
            `<option value="${escapeAttr(id)}">${escapeHtml(id)}</option>`
        )
        .join("");
    if (prev && ids.has(prev)) sel.value = prev;
    rosterReady = true;
  } catch {
    sel.innerHTML = '<option value="">すべて</option>';
  }
}

function resultSummary(rows) {
  if (!rows || !rows.length) return "ユーザー結果なし";
  const ok = rows.filter((r) => r.ok).length;
  const processed = rows.reduce((n, r) => n + (Number(r.processed) || 0), 0);
  const err = rows.length - ok;
  const parts = [`${rows.length} ユーザー`, `成功 ${ok}`];
  if (err) parts.push(`失敗 ${err}`);
  parts.push(`取込 ${processed}`);
  return parts.join(" · ");
}

function renderRuns(runs, { date, userId, errorsOnly }) {
  const box = document.getElementById("runsList");
  const status = document.getElementById("runsStatus");
  const label = filterLabel({ date, userId, errorsOnly });
  if (!runs.length) {
    box.innerHTML =
      '<p class="muted">条件に一致する実行記録がありません。</p>';
    status.className = "status";
    status.textContent = `${label} · 0 件`;
    return;
  }
  status.className = "status ok";
  status.textContent = `${label} · ${runs.length} 件`;
  box.innerHTML = runs
    .map((run) => {
      const rows = run.results || [];
      const badge = run.ok
        ? '<span class="badge">OK</span>'
        : '<span class="badge missing">エラーあり</span>';
      const only = run.only_gmail
        ? `<span class="run-meta">単独: ${escapeHtml(run.only_gmail)}</span>`
        : "";
      const dayLabel =
        !date && run.date
          ? `<span class="run-meta">${escapeHtml(run.date)}</span>`
          : "";
      const body = rows
        .map((r) => {
          const rowBadge = r.ok
            ? '<span class="badge">OK</span>'
            : '<span class="badge missing">NG</span>';
          const err = r.error
            ? `<span class="run-error">${escapeHtml(r.error)}</span>`
            : "";
          const stats = r.ok
            ? `取込 ${escapeHtml(String(r.processed ?? 0))} · miss ${escapeHtml(String(r.parse_miss ?? 0))} · seen skip ${escapeHtml(String(r.skipped_seen ?? 0))}`
            : "";
          return `
            <div class="run-user-row">
              <span class="run-user-id">${escapeHtml(r.user_id || "")}</span>
              ${rowBadge}
              <span class="run-user-stats">${stats}${err}</span>
            </div>`;
        })
        .join("");
      return `
        <details class="run-card" role="listitem">
          <summary>
            ${dayLabel}
            <span class="run-time">${escapeHtml(formatTime(run.started_at))}–${escapeHtml(formatTime(run.finished_at))}</span>
            ${badge}
            <span class="run-summary">${escapeHtml(resultSummary(rows))}</span>
            ${only}
          </summary>
          <div class="run-body">
            <p class="run-id muted">run_id: <code>${escapeHtml(run.run_id || "")}</code></p>
            ${body || '<p class="muted">詳細なし</p>'}
          </div>
        </details>`;
    })
    .join("");
}

async function fetchRuns() {
  const box = document.getElementById("runsList");
  const status = document.getElementById("runsStatus");
  const date = document.getElementById("filterDate").value || "";
  const userId = document.getElementById("filterUser").value;
  const errorsOnly = document.getElementById("filterErrors").value === "1";
  box.innerHTML = '<p class="muted">読み込み中…</p>';
  status.className = "status";
  status.textContent = "";
  const params = new URLSearchParams();
  if (date) params.set("date", date);
  if (userId) params.set("user_id", userId);
  if (errorsOnly) params.set("errors_only", "1");
  try {
    const res = await fetch(`/api/mail-poll/runs?${params}`);
    const data = await parseApiJson(res);
    if (!data.ok) throw new Error(data.error || "failed");
    renderRuns(data.runs || [], {
      date: data.date || date || "",
      userId,
      errorsOnly: Boolean(data.errors_only) || errorsOnly,
    });
  } catch (err) {
    box.innerHTML = `<p class="status err">取得失敗: ${escapeHtml(String(err.message || err))}</p>`;
    status.className = "status err";
    status.textContent = "検索に失敗しました";
  }
}

document.getElementById("filterDate").value = todayJstYmd();
document.getElementById("searchBtn").addEventListener("click", fetchRuns);
document.getElementById("clearDateBtn").addEventListener("click", () => {
  document.getElementById("filterDate").value = "";
  fetchRuns();
});
document.getElementById("filterDate").addEventListener("change", fetchRuns);
document.getElementById("filterUser").addEventListener("change", fetchRuns);
document.getElementById("filterErrors").addEventListener("change", fetchRuns);

(async () => {
  await loadRosterOptions();
  await fetchRuns();
})();
