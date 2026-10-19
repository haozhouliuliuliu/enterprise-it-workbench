"""最小工作台界面（无框架，API key 只存在内存变量，不写 localStorage/sessionStorage）。"""

INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Enterprise IT Onboarding & Compliance Workbench</title>
<style>
  :root { color-scheme: light; }
  body { font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 0; background:#f5f7fb; color:#172033; }
  header { background:#12305c; color:white; padding:14px 22px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  header h1 { font-size:17px; margin:0; font-weight:600; }
  header input { padding:8px; border-radius:6px; border:none; min-width:260px; }
  header button { padding:8px 14px; border:none; border-radius:6px; cursor:pointer; }
  #who { font-size:13px; opacity:.9; }
  nav { display:flex; gap:8px; padding:12px 22px; background:white; border-bottom:1px solid #dde3ee; flex-wrap:wrap; }
  nav button { padding:8px 14px; border:1px solid #c8d2e3; background:white; border-radius:20px; cursor:pointer; }
  nav button.active { background:#12305c; color:white; border-color:#12305c; }
  main { padding:20px 22px; max-width:1100px; }
  section { display:none; } section.active { display:block; }
  .card { background:white; border:1px solid #dde3ee; border-radius:10px; padding:16px; margin-bottom:14px; }
  textarea { width:100%; min-height:90px; padding:10px; border:1px solid #c8d2e3; border-radius:8px; box-sizing:border-box; }
  button.primary { background:#12305c; color:white; border:none; padding:9px 16px; border-radius:7px; cursor:pointer; }
  button.danger { background:#a51f1f; color:white; border:none; padding:7px 12px; border-radius:6px; cursor:pointer; }
  button.ok { background:#146c43; color:white; border:none; padding:7px 12px; border-radius:6px; cursor:pointer; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:8px; border-bottom:1px solid #edf0f6; vertical-align:top; }
  code, pre { background:#f0f3f9; padding:2px 5px; border-radius:4px; }
  pre { padding:10px; overflow:auto; }
  .badge { display:inline-block; padding:2px 8px; border-radius:10px; background:#e8eef9; font-size:12px; }
  .muted { color:#667085; font-size:12px; }
  .row { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
  input[type=file] { font-size:13px; }
</style>
</head>
<body>
<header>
  <h1>IT Onboarding &amp; Compliance Workbench</h1>
  <input id="apikey" type="password" placeholder="API key（只保存在内存，刷新后需重输）" autocomplete="off">
  <button onclick="login()">连接</button>
  <span id="who">未连接</span>
</header>
<nav>
  <button data-tab="request" class="active">发起请求</button>
  <button data-tab="tasks">任务</button>
  <button data-tab="approvals">审批队列</button>
  <button data-tab="knowledge">知识库</button>
</nav>
<main>
  <section id="request" class="active">
    <div class="card">
      <h3>新员工设备申请 / IT 请求</h3>
      <p class="muted">示例：<code>下周入职研发中心员工 EMP-1001 需要一台 ThinkPad T14 Gen5，请核查库存、制度和工单并给出方案。</code></p>
      <textarea id="requesttext" placeholder="请输入请求…"></textarea>
      <div class="row" style="margin-top:10px">
        <button class="primary" onclick="submitTask()">提交任务</button>
        <span id="submitmsg" class="muted"></span>
      </div>
    </div>
  </section>

  <section id="tasks">
    <div class="row"><button onclick="loadTasks()">刷新</button></div>
    <div id="tasklist" class="card" style="margin-top:12px"></div>
    <div id="taskdetail"></div>
  </section>

  <section id="approvals">
    <div class="row"><button onclick="loadApprovals()">刷新待审批</button><span class="muted">审批人只能处理本部门提案，且不能批准自己的申请。</span></div>
    <div id="approvallist" class="card" style="margin-top:12px"></div>
  </section>

  <section id="knowledge">
    <div class="card">
      <h3>导入文档（管理员）</h3>
      <div class="row">
        <input id="kfile" type="file" accept=".pdf,.docx,.txt,.md">
        <input id="ktitle" placeholder="标题">
        <input id="kdocid" placeholder="文档 ID，如 POL-IT-020">
        <input id="kversion" placeholder="版本，如 v1.0">
      </div>
      <div class="row" style="margin-top:8px">
        <select id="kstatus"><option value="current">current</option><option value="draft">draft</option><option value="superseded">superseded</option></select>
        <button class="primary" onclick="uploadDoc()">导入</button>
        <button onclick="reindex()">重建索引</button>
        <span id="kmsg" class="muted"></span>
      </div>
    </div>
    <div id="klist" class="card"></div>
  </section>
</main>
<script>
let apiKey = "";
let currentUser = null;

async function api(path, options = {}) {
  const opts = Object.assign({}, options);
  opts.headers = Object.assign({"Content-Type":"application/json"}, opts.headers || {});
  if (apiKey) opts.headers["Authorization"] = "Bearer " + apiKey;
  const res = await fetch(path, opts);
  let body = null;
  const text = await res.text();
  try { body = text ? JSON.parse(text) : null; } catch (e) { body = {raw:text}; }
  if (!res.ok) throw new Error((body && body.error) || ("HTTP " + res.status));
  return body;
}

async function login() {
  apiKey = document.getElementById("apikey").value.trim();
  try {
    currentUser = await api("/v1/me");
    document.getElementById("who").textContent = `${currentUser.user} / ${currentUser.role} / ${currentUser.department || "无部门"}`;
    await loadTasks();
  } catch (e) {
    currentUser = null;
    document.getElementById("who").textContent = "认证失败：" + e.message;
  }
}

document.querySelectorAll("nav button").forEach(btn => btn.onclick = () => {
  document.querySelectorAll("nav button").forEach(b => b.classList.remove("active"));
  document.querySelectorAll("section").forEach(s => s.classList.remove("active"));
  btn.classList.add("active");
  document.getElementById(btn.dataset.tab).classList.add("active");
  if (btn.dataset.tab === "approvals") loadApprovals();
  if (btn.dataset.tab === "knowledge") loadKnowledge();
  if (btn.dataset.tab === "tasks") loadTasks();
});

async function submitTask() {
  const text = document.getElementById("requesttext").value.trim();
  document.getElementById("submitmsg").textContent = "提交中…";
  try {
    const out = await api("/v1/tasks", {method:"POST", body: JSON.stringify({request:text})});
    document.getElementById("submitmsg").textContent = "已创建 " + out.task_id;
    await loadTasks();
  } catch (e) { document.getElementById("submitmsg").textContent = "失败：" + e.message; }
}

function renderTaskRow(t) {
  const conclusion = (t.result && t.result.conclusion) ? t.result.conclusion.slice(0, 80) : "";
  return `<tr><td><a href="#" class="task-link" data-task="${escapeHtml(t.task_id)}">${escapeHtml(t.task_id)}</a></td>` +
    `<td><span class="badge">${escapeHtml(t.status)}</span></td>` +
    `<td>${Number(t.model_calls) || 0}</td><td>${escapeHtml(conclusion)}</td></tr>`;
}

async function loadTasks() {
  try {
    const data = await api("/v1/tasks");
    const rows = data.items.map(renderTaskRow).join("");
    document.getElementById("tasklist").innerHTML = `<table><tr><th>任务</th><th>状态</th><th>模型调用</th><th>结论</th></tr>${rows || "<tr><td colspan=4>暂无任务</td></tr>"}</table>`;
    document.querySelectorAll(".task-link").forEach(link => {
      link.onclick = (event) => { event.preventDefault(); showTask(link.dataset.task); };
    });
  } catch (e) { document.getElementById("tasklist").textContent = "加载失败：" + e.message; }
}

async function showTask(id) {
  try {
    const t = await api("/v1/tasks/" + encodeURIComponent(id));
    const r = t.result || {};
    const ev = (r.evidence || []).map(e => `<li><code>${escapeHtml(e.evidence_id || "")}</code> ${escapeHtml(e.tool || "")} ${escapeHtml(e.citation || "")}</li>`).join("");
    const issues = (r.issues || []).map(i => `<li><b>${escapeHtml(i.level || "")}/${escapeHtml(i.type || "")}</b> ${escapeHtml(i.description || "")}</li>`).join("");
    let proposal = "";
    if (t.proposal) {
      let actions = t.proposal.actions;
      try { actions = t.proposal.actions_json ? JSON.parse(t.proposal.actions_json) : actions; } catch (e) { actions = t.proposal.actions_json; }
      proposal = `<h4>待审批提案</h4><pre>${escapeHtml(JSON.stringify(actions, null, 2))}</pre><p>参数哈希：<code>${escapeHtml(t.proposal.params_hash || "")}</code></p>`;
    }
    document.getElementById("taskdetail").innerHTML = `<div class="card"><h3>${escapeHtml(t.task_id)} <span class="badge">${escapeHtml(t.status)}</span></h3>
      <p><b>结论：</b>${escapeHtml(r.conclusion || "")}</p>
      <p><b>审核：</b>${escapeHtml(r.audit_verdict || "未审核")} ${r.policy_unavailable ? "（制度结论不可用）" : ""}</p>
      ${proposal}
      <h4>证据</h4><ul>${ev || "<li>无</li>"}</ul>
      <h4>问题与缺口</h4><ul>${issues || "<li>无</li>"}</ul>
      <p class="muted">执行结果：${r.execution ? escapeHtml(JSON.stringify(r.execution)) : "未执行"}</p></div>`;
  } catch (e) { alert("加载失败：" + e.message); }
}

async function loadApprovals() {
  try {
    const data = await api("/v1/approvals");
    const rows = data.items.map(p => {
      let actions = p.actions_json;
      try { actions = JSON.stringify(JSON.parse(p.actions_json), null, 2); } catch (e) {}
      return `<div class="card"><b>${escapeHtml(p.proposal_id)}</b> <span class="badge">${escapeHtml(p.status)}</span> 任务 ${escapeHtml(p.task_id)} 部门 ${escapeHtml(p.department || "")}
        <pre>${escapeHtml(actions)}</pre>
        <div class="row"><button class="ok decision-btn" data-id="${escapeHtml(p.proposal_id)}" data-hash="${escapeHtml(p.params_hash)}" data-decision="approve">批准并执行</button>
        <button class="danger decision-btn" data-id="${escapeHtml(p.proposal_id)}" data-hash="${escapeHtml(p.params_hash)}" data-decision="reject">拒绝</button></div></div>`;
    }).join("");
    const container = document.getElementById("approvallist");
    container.innerHTML = rows || "没有待审批提案";
    container.querySelectorAll(".decision-btn").forEach(button => {
      button.onclick = () => decide(button.dataset.id, button.dataset.decision, button.dataset.hash);
    });
  } catch (e) { document.getElementById("approvallist").textContent = "加载失败：" + e.message; }
}

async function decide(id, decision, hash) {
  try {
    const out = await api(`/v1/approvals/${encodeURIComponent(id)}/decision`, {method:"POST", body: JSON.stringify({decision, params_hash: hash})});
    alert(decision === "approve" ? "已执行：" + JSON.stringify(out.result) : "已拒绝");
    await loadApprovals(); await loadTasks();
  } catch (e) { alert("操作失败：" + e.message); }
}

async function loadKnowledge() {
  try {
    const data = await api("/v1/knowledge/documents");
    const rows = data.items.map(d => `<tr><td>${escapeHtml(d.doc_id)}</td><td>${escapeHtml(d.title)}</td><td>${escapeHtml(d.version)}</td><td>${escapeHtml(d.status)}</td><td>${Number(d.chunk_count) || 0}</td><td>${escapeHtml(d.index_status)}</td></tr>`).join("");
    document.getElementById("klist").innerHTML = `<table><tr><th>ID</th><th>标题</th><th>版本</th><th>状态</th><th>chunks</th><th>索引</th></tr>${rows}</table>`;
  } catch (e) { document.getElementById("klist").textContent = "加载失败：" + e.message; }
}

async function uploadDoc() {
  const file = document.getElementById("kfile").files[0];
  if (!file) { document.getElementById("kmsg").textContent = "请选择文件"; return; }
  const form = new FormData();
  form.append("file", file);
  form.append("title", document.getElementById("ktitle").value || file.name);
  form.append("doc_id", document.getElementById("kdocid").value || ("DOC-" + Date.now()));
  form.append("version", document.getElementById("kversion").value || "v1.0");
  form.append("status", document.getElementById("kstatus").value);
  try {
    const res = await fetch("/v1/knowledge/documents", {method:"POST", headers:{"Authorization":"Bearer " + apiKey}, body:form});
    const body = await res.json();
    if (!res.ok) throw new Error(body.error || res.status);
    document.getElementById("kmsg").textContent = "导入成功，chunks=" + body.chunks;
    await loadKnowledge();
  } catch (e) { document.getElementById("kmsg").textContent = "导入失败：" + e.message; }
}

async function reindex() {
  try { const out = await api("/v1/knowledge/reindex", {method:"POST"}); document.getElementById("kmsg").textContent = "重建 " + out.reindexed_chunks + " 个 chunk"; }
  catch (e) { document.getElementById("kmsg").textContent = "失败：" + e.message; }
}

function escapeHtml(s) { return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}[c])); }
</script>
</body>
</html>
"""
