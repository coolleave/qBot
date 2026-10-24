"use strict";
const $ = id => document.getElementById(id);
let csrf = "", currentPage = 1, providers = [], defaultProvider = null, recordsRequest = 0;
const labels = {pending:"等待处理",processing:"处理中",replied:"已回复",model_failed:"模型失败",send_failed:"发送失败",delivery_unknown:"送达未知",expired:"已过期",rejected:"已拒绝"};
function toast(message) { $("toast").textContent = message; $("toast").hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => $("toast").hidden = true, 4500); }
function loggedOut() { csrf = ""; $("workspace").hidden = true; $("login-screen").hidden = false; document.querySelectorAll("dialog[open]").forEach(d => d.close()); }
async function api(path, options = {}) {
  const response = await fetch("/api/admin" + path, {credentials:"same-origin", ...options, headers:{"Content-Type":"application/json", "X-QBot-Admin":"1", "X-CSRF-Token":csrf, ...options.headers}});
  let data; try { data = await response.json(); } catch { throw new Error("服务器响应异常，请稍后重试"); }
  if (!response.ok) { if (response.status === 401 && path !== "/login") loggedOut(); throw new Error(data.detail || "请求失败"); }
  return data;
}
async function enter() {
  const state = await api("/session"); csrf = state.csrf_token;
  $("username").textContent = state.username; $("login-screen").hidden = true; $("workspace").hidden = false;
  const warnings = []; if (!state.qq_configured) warnings.push("QQ 凭证未配置，可先管理供应商与历史记录。");
  if (!state.environment_model_configured) warnings.push("环境模型密钥未配置；请使用已启用的默认供应商进行聊天。");
  $("notice").textContent = warnings.join(" "); $("notice").hidden = !warnings.length;
  await Promise.all([loadRecords(), loadProviders()]);
}
function date(value) { return value ? new Date(/[Zz]|[+-]\d\d:\d\d$/.test(value) ? value : value + "Z").toLocaleString() : "—"; }
function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function button(text, action) { const node = element("button", text, "row-action"); node.type = "button"; node.onclick = async () => { node.disabled = true; try { await action(); } catch(e) { toast(e.message); } finally { node.disabled = false; } }; return node; }
async function loadRecords() {
  const request = ++recordsRequest, params = new URLSearchParams({page:String(currentPage),page_size:"20"});
  for (const [name,value] of new FormData($("filter-form"))) if(value) params.set(name, ["start","end"].includes(name) ? new Date(value).toISOString() : value.trim());
  $("record-count").textContent = "正在加载…";
  const data = await api("/records?" + params); if(request !== recordsRequest) return;
  $("records-body").replaceChildren(); $("records-empty").hidden = data.items.length > 0;
  $("record-count").textContent = `共 ${data.total} 条`; $("page-info").textContent = `第 ${data.page} / ${Math.max(1, Math.ceil(data.total / data.page_size))} 页`;
  $("prev-page").disabled = currentPage <= 1; $("next-page").disabled = currentPage * data.page_size >= data.total;
  for(const item of data.items) {
    const row = element("tr"), timeCell = element("td", date(item.received_at)); timeCell.append(element("small", item.source === "qq_group" ? "QQ 群聊" : "本地聊天")); row.append(timeCell);
    const identity = element("td", item.group_id); identity.append(element("small", item.user_id)); row.append(identity, element("td", item.question), element("td", item.model_name_snapshot || "—"));
    const status = element("td"); status.append(element("span", labels[item.status] || item.status, "badge " + item.status)); row.append(status);
    const action = element("td"); action.append(button("查看 →", () => showDetail(item.id))); row.append(action); $("records-body").append(row);
  }
}
async function showDetail(id) {
  const item = await api("/records/" + id);
  $("detail-meta").textContent = `${date(item.received_at)} · ${labels[item.status] || item.status} · ${item.provider_name_snapshot || "未调用模型"} / ${item.model_name_snapshot || "—"} · 群 ${item.group_id} · 用户 ${item.user_id}`;
  $("detail-question").textContent = item.question; $("detail-answer").textContent = item.answer || "尚无回复"; $("detail-error").textContent = item.error_message || ""; $("detail-dialog").showModal();
}
async function loadProviders() {
  const data = await api("/providers"); providers = data.items; defaultProvider = data.default_provider_id;
  $("default-label").textContent = providers.find(p => p.id === defaultProvider)?.name || "环境配置（.env）";
  $("providers-list").replaceChildren(); $("providers-empty").hidden = providers.length > 0;
  for (const item of providers) {
    const card = element("article", undefined, "provider-card"), top = element("div", undefined, "top");
    top.append(element("h2", item.name), element("span", item.id === defaultProvider ? "默认使用" : item.enabled ? "已启用" : "已停用", "badge " + (item.enabled ? "replied" : "")));
    card.append(top, element("p", `${item.api_type === "deepseek" ? "DeepSeek" : "兼容接口"} · ${item.model_name}`), element("p", item.base_url, "muted"), element("p", "API Key  " + item.api_key_mask, "muted"));
    const actions = element("div", undefined, "actions"); actions.append(button("编辑配置", () => editProvider(item)));
    if(item.enabled && item.id !== defaultProvider) actions.append(button("设为默认", async () => { await api("/default-provider", {method:"PUT",body:JSON.stringify({provider_id:item.id})}); await loadProviders(); toast("默认供应商已更新"); }));
    card.append(actions); $("providers-list").append(card);
  }
}
function editProvider(item) {
  const form = $("provider-form"); form.reset(); $("provider-error").textContent = "";
  const defaults = item || {id:"",name:"",api_type:"deepseek",base_url:"https://api.deepseek.com",model_name:"deepseek-flash",enabled:false};
  for(const key of ["id","name","api_type","base_url","model_name"]) form.elements[key].value = defaults[key];
  form.elements.enabled.checked = Boolean(defaults.enabled); form.elements.api_key.value = "";
  $("provider-title").textContent = item ? "编辑供应商" : "添加供应商"; $("provider-dialog").showModal();
}
$("login-form").onsubmit = async event => {event.preventDefault(); const form = event.currentTarget, submit = form.querySelector("button"); submit.disabled = true; $("login-error").textContent = ""; try {await api("/login", {method:"POST",body:JSON.stringify(Object.fromEntries(new FormData(form)))}); form.elements.password.value = ""; await enter();} catch(e) {$("login-error").textContent = e.message; toast(e.message);} finally {submit.disabled = false;}};
$("logout").onclick = async () => {try {await api("/logout", {method:"POST"}); loggedOut();} catch(e) {toast(e.message);}};
document.querySelectorAll("[data-page]").forEach(node => node.onclick = () => {const page = node.dataset.page; document.querySelectorAll("[data-page]").forEach(n => n.classList.toggle("active", n === node)); $("records-page").hidden = page !== "records"; $("providers-page").hidden = page !== "providers"; $("page-name").textContent = page === "records" ? "聊天记录" : "模型供应商";});
document.querySelectorAll("[data-close]").forEach(node => node.onclick = () => $(node.dataset.close).close());
$("provider-dialog").addEventListener("close", () => $("provider-form").elements.api_key.value = "");
$("new-provider").onclick = () => editProvider(null);
$("environment-default").onclick = async () => {try {await api("/default-provider", {method:"PUT",body:JSON.stringify({provider_id:null})}); await loadProviders(); toast("已切换为环境配置");} catch(e) {toast(e.message);}};
$("provider-form").onsubmit = async event => {event.preventDefault(); const form = event.currentTarget, submit = form.querySelector('[type="submit"]'), data = Object.fromEntries(new FormData(form)), id = data.id; delete data.id; data.enabled = form.elements.enabled.checked; submit.disabled = true; $("provider-error").textContent = ""; try {await api("/providers" + (id ? "/" + id : ""), {method:id ? "PUT" : "POST",body:JSON.stringify(data)}); $("provider-dialog").close(); await loadProviders(); toast("配置已保存，下次模型请求生效");} catch(e) {$("provider-error").textContent = e.message;} finally {submit.disabled = false;}};
$("filter-form").onsubmit = event => {event.preventDefault();currentPage = 1;loadRecords().catch(e => toast(e.message));};
$("reset-filters").onclick = () => {$("filter-form").reset();currentPage = 1;loadRecords().catch(e => toast(e.message));};
$("refresh-records").onclick = () => loadRecords().catch(e => toast(e.message));
$("prev-page").onclick = () => {currentPage--;loadRecords().catch(e => toast(e.message));};
$("next-page").onclick = () => {currentPage++;loadRecords().catch(e => toast(e.message));};
enter().catch(e => {if(!$("workspace").hidden) toast(e.message);});
