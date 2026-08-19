let cachedUsers = [];
let cachedRoster = [];
let cachedUsersError = null;

function renderOperatorOauth(info) {
  const el = document.getElementById("operatorOauthStatus");
  if (!el) return;
  if (info && info.ok === false && info.error) {
    el.className = "status err";
    el.textContent =
      "運営 OAuth（シート作成・同意メール）が使えません: " + String(info.error);
    return;
  }
  el.className = "status";
  el.textContent = "";
}

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
  const lower = snippet.toLowerCase();
  if (
    res.status === 504 ||
    /timeout|upstream request timeout/i.test(snippet)
  ) {
    throw new Error(
      "サーバー側でタイムアウトしました（プロビジョンに時間がかかりすぎ）。しばらく待ってから再試行してください。"
    );
  }
  if (
    res.status === 502 ||
    res.status === 503 ||
    /service unavailable|bad gateway|connection (reset|error)/i.test(lower)
  ) {
    throw new Error(
      "サーバーが一時的に応答できませんでした（メモリ不足や再起動の可能性）。" +
        "30秒ほど待ってから再試行してください。" +
        (snippet ? ` (HTTP ${res.status || "?"}: ${snippet})` : "")
    );
  }
  throw new Error(
    snippet
      ? `応答が JSON ではありません (HTTP ${res.status}): ${snippet}`
      : `応答が空です (HTTP ${res.status})`
  );
}

async function fetchUsers() {
  const box = document.getElementById("userList");
  const rosterBox = document.getElementById("rosterList");
  box.innerHTML = '<p class="muted">読み込み中…</p>';
  rosterBox.innerHTML = '<p class="muted">読み込み中…</p>';
  try {
    const res = await fetch("/api/users");
    const data = await parseApiJson(res);
    if (!data.ok) throw new Error(data.error || "failed");
    cachedUsers = data.users || [];
    cachedRoster = data.roster || [];
    cachedUsersError = data.users_error || null;
    renderOperatorOauth(data.operator_oauth || null);
    renderRoster(cachedRoster, data.roster_error || null);
    fillFilters(cachedUsers);
    applyFilters();
    fillDeleteSelect(cachedUsers);
  } catch (err) {
    box.innerHTML = `<p class="status err">一覧の取得に失敗: ${escapeHtml(String(err.message || err))}</p>`;
    rosterBox.innerHTML = `<p class="status err">名簿の取得に失敗</p>`;
    renderOperatorOauth(null);
    fillDeleteSelect([]);
    fillFilters([]);
  }
}

function renderRoster(roster, rosterError) {
  const box = document.getElementById("rosterList");
  if (rosterError && !roster.length) {
    box.innerHTML = `<p class="status err">名簿の取得に失敗: ${escapeHtml(String(rosterError))}</p>`;
    return;
  }
  if (!roster.length) {
    box.innerHTML = '<p class="muted">アクティブなユーザーがありません。</p>';
    return;
  }
  box.innerHTML = roster
    .map(
      (u) => `
      <div class="roster-row" role="listitem">
        <span class="roster-id">${escapeHtml(u.user_id || "")}</span>
        <span class="roster-role">${escapeHtml(u.role || "")}</span>
      </div>`
    )
    .join("");
}

function fillFilters(users) {
  const userSel = document.getElementById("filterUser");
  const yearSel = document.getElementById("filterYear");
  const prevUser = userSel.value;
  const prevYear = yearSel.value;

  const userIds = new Set();
  for (const u of users) {
    const id = u.user_id || (u.gmail ? String(u.gmail).split("@")[0] : "");
    if (!id || u.is_template) continue;
    userIds.add(id);
  }
  const userOpts = [...userIds].sort((a, b) => a.localeCompare(b, "ja"));
  userSel.innerHTML =
    '<option value="">すべて</option>' +
    userOpts
      .map(
        (id) =>
          `<option value="${escapeAttr(id)}">${escapeHtml(id)}</option>`
      )
      .join("");

  const years = [
    ...new Set(users.map((u) => u.year).filter((y) => y != null)),
  ].sort((a, b) => b - a);
  yearSel.innerHTML =
    '<option value="">すべて</option>' +
    years
      .map((y) => `<option value="${escapeAttr(String(y))}">${escapeHtml(String(y))}</option>`)
      .join("");

  if (prevUser && userIds.has(prevUser)) userSel.value = prevUser;
  if (prevYear && years.some((y) => String(y) === prevYear)) yearSel.value = prevYear;
}

function filteredUsers() {
  const userId = document.getElementById("filterUser").value;
  const year = document.getElementById("filterYear").value;
  return cachedUsers.filter((u) => {
    if (u.is_template) return true;
    if (userId) {
      const id = u.user_id || (u.gmail ? String(u.gmail).split("@")[0] : "");
      if (id !== userId) return false;
    }
    if (year) {
      if (String(u.year || "") !== year) return false;
    }
    return true;
  });
}

function applyFilters() {
  renderUsers(filteredUsers());
}

function renderUsers(users) {
  const box = document.getElementById("userList");
  if (cachedUsersError && !cachedUsers.length) {
    box.innerHTML = `<p class="status err">スプレッドシート一覧の取得に失敗: ${escapeHtml(String(cachedUsersError))}</p>`;
    return;
  }
  if (!cachedUsers.length) {
    box.innerHTML = '<p class="muted">スプレッドシートがありません。下のフォームからユーザーを追加してください。</p>';
    return;
  }
  if (!users.length) {
    box.innerHTML = '<p class="muted">条件に一致するファイルがありません。</p>';
    return;
  }
  box.innerHTML = users
    .map((u) => {
      if (u.is_template) {
        const open =
          u.on_drive && u.url
            ? `<a class="btn" href="${escapeAttr(u.url)}" target="_blank" rel="noopener">開く</a>`
            : "";
        return `
        <div class="user-row" role="listitem">
          <div class="user-main">
            <span class="title">${escapeHtml(u.title || "")}</span>
            <span class="gmail">正本テンプレート</span>
            <span class="badge template">テンプレート</span>
          </div>
          <div class="actions">${open}</div>
        </div>`;
      }
      const gmail = u.gmail || "";
      const yearLabel = u.year != null ? String(u.year) : "—";
      const badge = u.on_drive
        ? '<span class="badge">Drive 上</span>'
        : '<span class="badge missing">未作成</span>';
      const gmailBadge = u.gmail_linked
        ? '<span class="badge">Gmail 連携済</span>'
        : '<span class="badge missing">Gmail 未連携</span>';
      const open =
        u.on_drive && u.url
          ? `<a class="btn" href="${escapeAttr(u.url)}" target="_blank" rel="noopener">開く</a>`
          : "";
      const resend =
        u.gmail && !u.gmail_linked
          ? `<button type="button" class="ghost resend-consent" data-gmail="${escapeAttr(u.gmail)}">同意再送</button>`
          : "";
      return `
        <div class="user-row" role="listitem">
          <div class="user-main">
            <span class="title">${escapeHtml(u.title || u.user_id || "")}</span>
            <span class="gmail">${escapeHtml(u.user_id || "")} · ${escapeHtml(gmail)} · ${escapeHtml(yearLabel)}年</span>
            ${badge}${gmailBadge}
          </div>
          <div class="actions">${open}${resend}</div>
        </div>`;
    })
    .join("");
}

function fillDeleteSelect(users) {
  const sel = document.getElementById("deleteSelect");
  const btn = document.getElementById("deleteBtn");
  // One option per user_id (display ID only). value remains gmail for the API.
  const byId = new Map();
  for (const u of users) {
    if (!u.gmail || u.is_template) continue;
    const id = u.user_id || String(u.gmail).split("@")[0];
    if (!id || id === "26964u") continue;
    if (!byId.has(id)) byId.set(id, u);
  }
  const rows = [...byId.entries()].sort((a, b) => a[0].localeCompare(b[0], "ja"));
  const prev = sel.value;
  sel.innerHTML =
    '<option value="">選択してください</option>' +
    rows
      .map(
        ([id, u]) =>
          `<option value="${escapeAttr(u.gmail)}" data-user-id="${escapeAttr(id)}">` +
          `${escapeHtml(id)}</option>`
      )
      .join("");
  if (prev && rows.some(([, u]) => u.gmail === prev)) {
    sel.value = prev;
  }
  btn.disabled = !sel.value;
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

document.getElementById("refreshBtn").addEventListener("click", fetchUsers);
document.getElementById("openTemplateBtn").addEventListener("click", () => {
  const btn = document.getElementById("openTemplateBtn");
  const url = (btn.getAttribute("data-template-url") || "").trim();
  if (!url) {
    window.alert("テンプレート URL が設定されていません（config の template_spreadsheet_id）。");
    return;
  }
  window.open(url, "_blank", "noopener");
});
document.getElementById("filterUser").addEventListener("change", applyFilters);
document.getElementById("filterYear").addEventListener("change", applyFilters);

document.getElementById("deleteSelect").addEventListener("change", (ev) => {
  document.getElementById("deleteBtn").disabled = !ev.currentTarget.value;
});

document.getElementById("deleteBtn").addEventListener("click", async () => {
  const sel = document.getElementById("deleteSelect");
  const btn = document.getElementById("deleteBtn");
  const status = document.getElementById("deleteStatus");
  const opt = sel.selectedOptions[0];
  const gmail = sel.value;
  const userId = (opt && opt.getAttribute("data-user-id")) || "";
  if (!gmail) return;

  const ok = window.confirm(
    `ユーザーを削除しますか？\n\n` +
      `ユーザー ID: ${userId || "(不明)"}\n` +
      `Gmail: ${gmail}\n\n` +
      `※ 名簿除外 / quitted_user.txt 追加 / スプレッドシート共有解除 / Gmail 連携解除 / 設定アーカイブを行います。\n` +
      `※ シート本体・scraping-data / log は残します。`
  );
  if (!ok) return;

  status.className = "status";
  status.textContent = "共有解除中…";
  btn.disabled = true;
  sel.disabled = true;
  try {
    const res = await fetch("/api/users", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gmail }),
    });
    const data = await parseApiJson(res);
    if (!data.ok) throw new Error(data.error || "failed");
    const u = data.user;
    status.className = "status ok";
    status.textContent =
      `削除完了: ${userId || u.gmail}` +
      (u.unshared ? " · スプレッドシート共有解除" : "") +
      (u.removed_from_config || (u.clipping && u.clipping.removed_from_roster)
        ? " · 名簿から削除"
        : "") +
      (u.clipping && u.clipping.added_to_quitted_list
        ? " · quitted_user.txt へ追加"
        : "") +
      (u.gmail_poll_disabled || u.gmail_token_removed
        ? " · Gmail 連携解除"
        : "") +
      (u.file_kept ? "（シート本体は残置）" : "");
    if (u.clipping_error) {
      status.className = "status err";
      status.textContent += ` / 名簿: ${u.clipping_error}`;
    } else if (u.clipping && u.clipping.archive && u.clipping.archive.archived) {
      status.textContent += " · 設定を退会アーカイブへ移動";
    }
    await fetchUsers();
  } catch (err) {
    status.className = "status err";
    status.textContent = `削除失敗: ${String(err.message || err)}`;
    btn.disabled = !sel.value;
  } finally {
    sel.disabled = false;
  }
});

document.getElementById("addForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const form = ev.currentTarget;
  const status = document.getElementById("status");
  const btn = document.getElementById("addBtn");
  const roleSelect = document.getElementById("roleSelect");
  const fd = new FormData(form);
  const gmail = String(fd.get("gmail") || "").trim();
  const role = String(fd.get("role") || roleSelect.value || "Normal").trim();
  status.className = "status";
  status.textContent = "作成・共有中…（1〜数分かかることがあります）";
  btn.disabled = true;

  async function postAdd(existingFileAction) {
    const body = { gmail, role };
    if (existingFileAction) body.existing_file_action = existingFileAction;
    const res = await fetch("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await parseApiJson(res);
    return { res, data };
  }

  try {
    let { res, data } = await postAdd(null);
    if (!data.ok && data.code === "workbook_exists") {
      // 運用既定: 同年次ファイルがあれば再利用（共有・保護・名簿のみ更新）
      status.textContent = "既存のスプレッドシートを再利用して共有・名簿を更新中…";
      ({ res, data } = await postAdd("keep"));
    }
    if (!data.ok) throw new Error(data.error || "failed");
    const u = data.user;
    status.className = "status ok";
    const link =
      u.url
        ? `<a href="${escapeAttr(u.url)}" target="_blank" rel="noopener">${escapeHtml(u.title)}</a>`
        : escapeHtml(u.title || "");
    if (u.skipped_create) {
      status.innerHTML =
        `完了: 既存の ${link} を再利用し、共有・保護・名簿を更新しました` +
        `（注文データはそのまま残っています）。`;
    } else if (u.rebuilt || (u.created_new && u.initialized)) {
      status.innerHTML = u.rebuilt
        ? `完了: ${link} を上書き作成・共有しました。`
        : `完了: ${link} を新規作成・共有し、通知メールを送信しました。`;
    } else {
      status.innerHTML = `完了: ${link} を共有・config 更新しました。`;
    }
    if (u.clipping_error) {
      status.innerHTML +=
        `<br><span class="status err">名簿・GCS 同期失敗: ${escapeHtml(u.clipping_error)}</span>`;
    } else if (u.clipping) {
      const restored = u.clipping.restored_from_quitted;
      status.innerHTML +=
        `<br>名簿・GCS: ${escapeHtml(u.clipping.user_id)}（${escapeHtml(u.clipping.role)}）` +
        (restored
          ? "（退会アーカイブから復元）"
          : u.clipping.seeded && u.clipping.seeded.length
            ? `（新規 seed ${escapeHtml(String(u.clipping.seeded.length))}）`
            : "（既存設定は再作成せず）") +
        `。`;
    }
    if (u.consent_email) {
      status.innerHTML +=
        `<br>Gmail 連携のお願いメールを ${escapeHtml(u.gmail)} へ送信しました。`;
    } else if (u.consent_email_error) {
      status.innerHTML +=
        `<br><span class="status err">同意メール送信失敗: ${escapeHtml(u.consent_email_error)}</span>`;
    }
    if (u.mail_ingest) {
      status.innerHTML +=
        `<br>Gmail 連携済のためメール取込を実行: 処理 ${escapeHtml(String(u.mail_ingest.processed))} 件。`;
    } else if (u.mail_ingest_error) {
      status.innerHTML +=
        `<br><span class="status err">メール取込失敗: ${escapeHtml(u.mail_ingest_error)}</span>`;
    }
    form.reset();
    roleSelect.value = "Normal";
    await fetchUsers();
  } catch (err) {
    status.className = "status err";
    status.textContent = `失敗: ${String(err.message || err)}`;
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("userList").addEventListener("click", async (ev) => {
  const btn = ev.target.closest(".resend-consent");
  if (!btn) return;
  const gmail = btn.getAttribute("data-gmail");
  if (!gmail) return;
  btn.disabled = true;
  try {
    const res = await fetch("/api/users/resend-consent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gmail }),
    });
    const data = await parseApiJson(res);
    if (!data.ok) throw new Error(data.error || "failed");
    window.alert(`同意メールを再送しました: ${gmail}`);
  } catch (err) {
    window.alert(`再送失敗: ${String(err.message || err)}`);
  } finally {
    btn.disabled = false;
  }
});

fetchUsers();
