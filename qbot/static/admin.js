"use strict";
const $ = id => document.getElementById(id);
let csrf = "", currentPage = 1, providers = [], defaultProvider = null, recordsRequest = 0;
const botNames = new Map();
function botLabel(id) { return `${botNames.get(Number(id)) || "机器人"} #${id}`; }
function updateBotLabels() {
  document.querySelectorAll("[data-bot-label]").forEach(node => {
    node.textContent = botLabel(node.dataset.botLabel);
  });
  if (detailRecord) renderDetailMeta(detailRecord);
}
const labels = {pending:"等待处理",processing:"处理中",replied:"已回复",model_failed:"模型失败",send_failed:"发送失败",delivery_unknown:"送达未知",expired:"已过期",rejected:"已拒绝"};
function toast(message) { $("toast").textContent = message; $("toast").hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => $("toast").hidden = true, 4500); }
function loggedOut() { $("bot-form").reset(); csrf = ""; $("workspace").hidden = true; $("login-screen").hidden = false; document.querySelectorAll("dialog[open]").forEach(d => d.close()); }
async function api(path, options = {}) {
  const response = await fetch("/api/admin" + path, {credentials:"same-origin", ...options, headers:{"Content-Type":"application/json", "X-QBot-Admin":"1", "X-CSRF-Token":csrf, ...options.headers}});
  let data; try { data = await response.json(); } catch { throw new Error("服务器响应异常，请稍后重试"); }
  if (!response.ok) { if (response.status === 401 && path !== "/login") loggedOut(); throw new Error(data.detail || "请求失败"); }
  return data;
}
async function enter() {
  const state = await api("/session"); csrf = state.csrf_token;
  $("username").textContent = state.username; $("login-screen").hidden = true; $("workspace").hidden = false;
  const warnings = []; if (!state.qq_configured) warnings.push("暂无运行中的机器人，请到机器人管理中配置并启动。");

  $("notice").textContent = warnings.join(" "); $("notice").hidden = !warnings.length;
  await Promise.all([loadRecords(), loadProviders(), loadBots()]);
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
    const row = element("tr"), timeCell = element("td", date(item.received_at)); const botName = element("small", botLabel(item.bot_id)); botName.dataset.botLabel = item.bot_id; timeCell.append(botName); timeCell.append(element("small", ({qq_group:"QQ 群聊",qq_c2c:"QQ 私聊",local:"本地聊天"}[item.source] || item.source))); row.append(timeCell);
    const identity = element("td", item.source === "qq_c2c" ? "私聊" : item.group_id); identity.append(element("small", item.user_id)); row.append(identity, element("td", item.question), element("td", item.model_name_snapshot || "—"));
    const status = element("td"); status.append(element("span", labels[item.status] || item.status, "badge " + item.status)); row.append(status);
    const action = element("td"); action.append(button("查看 →", () => showDetail(item.id))); row.append(action); $("records-body").append(row);
  }
}
let detailRecord = null;
function renderDetailMeta(item) {
  const source = {qq_group:"QQ 群聊", qq_c2c:"QQ 私聊", local:"本地聊天"}[item.source] || item.source;
  $("detail-meta").textContent = `${botLabel(item.bot_id)} · ${source} · ${date(item.received_at)} · ${labels[item.status] || item.status} · ${item.provider_name_snapshot || "未调用模型"} / ${item.model_name_snapshot || "—"}${item.source === "qq_group" ? " · 群 " + item.group_id : ""} · 用户 ${item.user_id}`;
}
async function showDetail(id) {
  const item = await api("/records/" + id);
  detailRecord = item;
  const form = $("binding-form"); form.reset();
  form.hidden = !["qq_group", "qq_c2c"].includes(item.source);
  $("open-send").hidden = form.hidden;
  form.elements.member_name.value = item.member_name || "";
  form.elements.family_role.value = item.family_role || "father";
  form.elements.qq_number_note.value = item.qq_number_note || "";
  $("unbind-member").disabled = !item.family_role;
  $("binding-error").textContent = "";
  renderDetailMeta(item);
  $("detail-question").textContent = item.question; $("detail-answer").textContent = item.answer || "尚无回复"; $("detail-error").textContent = item.error_message || ""; $("detail-dialog").showModal();
}
async function loadProviders() {
  const data = await api("/providers"); providers = data.items; defaultProvider = data.default_provider_id;
  $("default-label").textContent = providers.find(p => p.id === defaultProvider)?.name || "尚未选择，请添加并启用默认供应商";
  $("providers-list").replaceChildren(); $("providers-empty").hidden = providers.length > 0;
  for (const item of providers) {
    const card = element("article", undefined, "provider-card"), top = element("div", undefined, "top");
    top.append(element("h2", item.name), element("span", item.id === defaultProvider ? "默认使用" : item.enabled ? "已启用" : "已停用", "badge " + (item.enabled ? "replied" : "")));
    card.append(top, element("p", `${({chat:"chat · 聊天",image:"图片生成",asr:"asr · 语音识别",tts:"tts · 语音合成"})[item.model_type]} · ${item.api_type === "deepseek" ? "DeepSeek" : "兼容接口"} · ${item.model_name}`), element("p", item.base_url, "muted"), element("p", "API Key  " + item.api_key_mask, "muted"));
    const actions = element("div", undefined, "actions"); actions.append(button("编辑配置", () => editProvider(item)));
    if(item.model_type === "chat" && item.enabled && item.id !== defaultProvider) actions.append(button("设为默认", async () => { await api("/default-provider", {method:"PUT",body:JSON.stringify({provider_id:item.id})}); await loadProviders(); toast("默认供应商已更新"); }));
    const outcome = element("p", "", "muted"); outcome.setAttribute("role", "status");
    const probe = button("测试连接", async () => {
      outcome.textContent = "正在发送“你好”，请稍候…";
      try {
        const result = await api(`/providers/${item.id}/test`, {method:"POST"});
        outcome.textContent = `${result.success ? "成功" : "失败"} · 耗时 ${result.latency_ms} ms · ${result.message}${result.reply ? " · 回复：" + result.reply : ""}`;
      } catch(e) { outcome.textContent = "检查失败：" + e.message; }
    });
    probe.disabled = !item.has_api_key || item.model_type !== "chat";
    if (item.model_type !== "chat") outcome.textContent = "已保存模型配置，暂未接入调用及连接测试。";
    actions.append(probe);
    card.append(actions, outcome); $("providers-list").append(card);
  }
}
function editProvider(item) {
  const form = $("provider-form"); form.reset(); $("provider-error").textContent = "";
  const defaults = item || {id:"",name:"",model_type:"chat",api_type:"deepseek",base_url:"https://api.deepseek.com",model_name:"deepseek-flash",enabled:false};
  for(const key of ["id","name","model_type","api_type","base_url","model_name"]) form.elements[key].value = defaults[key];
  form.elements.enabled.checked = Boolean(defaults.enabled); form.elements.api_key.value = "";
  $("provider-title").textContent = item ? "编辑供应商" : "添加供应商"; $("provider-dialog").showModal();
}
$("login-form").onsubmit = async event => {event.preventDefault(); const form = event.currentTarget, submit = form.querySelector("button"); submit.disabled = true; $("login-error").textContent = ""; try {await api("/login", {method:"POST",body:JSON.stringify(Object.fromEntries(new FormData(form)))}); form.elements.password.value = ""; await enter();} catch(e) {$("login-error").textContent = e.message; toast(e.message);} finally {submit.disabled = false;}};
$("logout").onclick = async () => {try {await api("/logout", {method:"POST"}); loggedOut();} catch(e) {toast(e.message);}};
document.querySelectorAll("[data-page]").forEach(node => node.onclick = () => {
  const page = node.dataset.page;
  document.querySelectorAll("[data-page]").forEach(n => n.classList.toggle("active", n === node));
  for (const name of ["records", "providers", "bots", "messages", "greetings", "bindings"]) $(name + "-page").hidden = page !== name;
  $("page-name").textContent = {records:"聊天记录",providers:"模型供应商",bots:"机器人管理",messages:"发送消息",greetings:"定时任务",bindings:"用户绑定"}[page];
  if (page === "bindings") void loadUserBindings();
  if (page === "greetings") void loadGreetingTargets();
  if (page === "messages") void loadSendTargets().catch(e => toast(e.message));
});
document.querySelectorAll("[data-close]").forEach(node => node.onclick = () => $(node.dataset.close).close());
$("provider-dialog").addEventListener("close", () => $("provider-form").elements.api_key.value = "");
$("new-provider").onclick = () => editProvider(null);
$("environment-default").onclick = async () => {try {await api("/default-provider", {method:"PUT",body:JSON.stringify({provider_id:null})}); await loadProviders(); toast("已取消默认供应商，选择新默认项后才能聊天");} catch(e) {toast(e.message);}};
$("provider-form").onsubmit = async event => {event.preventDefault(); const form = event.currentTarget, submit = form.querySelector('[type="submit"]'), data = Object.fromEntries(new FormData(form)), id = data.id; delete data.id; data.enabled = form.elements.enabled.checked; submit.disabled = true; $("provider-error").textContent = ""; try {await api("/providers" + (id ? "/" + id : ""), {method:id ? "PUT" : "POST",body:JSON.stringify(data)}); $("provider-dialog").close(); await loadProviders(); toast("配置已保存，下次模型请求生效");} catch(e) {$("provider-error").textContent = e.message;} finally {submit.disabled = false;}};
$("filter-form").onsubmit = event => {event.preventDefault();currentPage = 1;loadRecords().catch(e => toast(e.message));};
$("reset-filters").onclick = () => {$("filter-form").reset();currentPage = 1;loadRecords().catch(e => toast(e.message));};
$("refresh-records").onclick = () => loadRecords().catch(e => toast(e.message));
$("prev-page").onclick = () => {currentPage--;loadRecords().catch(e => toast(e.message));};
$("next-page").onclick = () => {currentPage++;loadRecords().catch(e => toast(e.message));};
enter().catch(e => {if(!$("workspace").hidden) toast(e.message);});

async function loadBots() {
  const data = await api("/bots");
  $("bots-list").replaceChildren();
  for (const id of ["binding-bot-filter", "binding-bot"]) {
    const select = $(id), selected = select.value;
    select.replaceChildren(new Option(id === "binding-bot" ? "请选择机器人" : "全部机器人", ""));
    for (const bot of data.items) {
      const option = new Option(botNames.has(bot.id) ? botLabel(bot.id) : `${bot.name} #${bot.id}`, bot.id);
      option.dataset.botLabel = bot.id; select.add(option);
    }
    select.value = selected;
  }
  const greetingSelected = $("greeting-bot").value;
  $("greeting-bot").replaceChildren(new Option("全部机器人", ""));
  for (const bot of data.items) {
    const option = new Option(botNames.has(bot.id) ? botLabel(bot.id) : `${bot.name} #${bot.id}`, bot.id);
    option.dataset.botLabel = bot.id; $("greeting-bot").add(option);
  }
  $("greeting-bot").value = greetingSelected;
  const selected = $("bot-filter").value;
  $("bot-filter").replaceChildren(new Option("全部机器人", ""));
  for (const item of data.items) {
    if (!botNames.has(item.id)) botNames.set(item.id, item.name);
    const option = new Option(botLabel(item.id), item.id); option.dataset.botLabel = item.id;
    $("bot-filter").add(option);
    const card = element("article", undefined, "provider-card");
    const profileHeading = element("div", undefined, "bot-profile-heading");
    const avatar = element("img", undefined, "bot-avatar"); avatar.alt = "QQ 机器人头像"; avatar.hidden = true; avatar.referrerPolicy = "no-referrer";
    const placeholder = element("span", "🤖", "bot-avatar-placeholder");
    const officialName = element("h2", "官方资料加载中…");
    const profileState = element("p", "", "muted");
    profileHeading.append(placeholder, avatar, officialName);
    avatar.onload = () => { avatar.hidden = false; placeholder.hidden = true; };
    avatar.onerror = () => {
      avatar.hidden = true; placeholder.hidden = false;
      profileState.textContent += " · 官方头像加载失败，请稍后刷新";
    };
    const renderProfile = async (refresh = false) => {
      profileState.textContent = "正在获取官方资料…";
      try {
        const result = await api(`/bots/${item.id}/profile?refresh=${refresh}`);
        officialName.textContent = result.profile?.username || item.name;
        botNames.set(item.id, officialName.textContent);
        updateBotLabels();
        profileState.textContent = result.success ? `官方 ID：${result.profile.id}` : result.message;
        const url = result.profile?.avatar;
        if(url) {
          avatar.hidden = true; placeholder.hidden = false;
          avatar.src = url;
          if (avatar.complete && avatar.naturalWidth > 0) { avatar.hidden = false; placeholder.hidden = true; }
        }
        else { avatar.removeAttribute("src"); avatar.hidden = true; placeholder.hidden = false; }
      } catch(e) { officialName.textContent = item.name; profileState.textContent = e.message; }
    };
    card.append(profileHeading, profileState, element("p", `本地备注：${item.name} #${item.id}`, "muted"), element("p", `AppID：${item.app_id || "未配置"}`),
      element("p", `${item.transport} · ${item.status}`), element("p", `AppSecret：${item.secret_mask}`, "muted"),
      element("p", item.provider_id ? `模型供应商 #${item.provider_id}` : "跟随全局默认供应商", "muted"));
    if (item.transport === "webhook") card.append(element("p", `回调路径：/qq/webhook/${item.id}`, "muted"));
    const actions = element("div", undefined, "actions");
    actions.append(button("编辑配置", () => editBot(item)), button("刷新官方资料", () => renderProfile(true)));
    actions.append(button(item.running ? "重新连接" : "启动", async () => {
      const result = await api(`/bots/${item.id}/start`, {method:"POST"});
      await loadBots(); toast(result.status);
    }));
    if(item.enabled || item.running) actions.append(button("停止", async () => {
      const result = await api(`/bots/${item.id}/stop`, {method:"POST"});
      await loadBots(); toast(result.status);
    }));
    card.append(actions); $("bots-list").append(card);
    void renderProfile();
  }
  $("bot-filter").value = selected;
  const sendBot = $("send-bot"), selectedSendBot = sendBot.value;
  sendBot.replaceChildren(new Option("请选择机器人", ""));
  for (const item of data.items) {
    const option = new Option(botLabel(item.id) + (item.running ? "" : "（未启动）"), item.id);
    option.dataset.botLabel = item.id; sendBot.add(option);
  }
  sendBot.value = selectedSendBot;
  updateBotLabels();
}
async function editBot(item) {
  const data = await api("/providers");
  const form = $("bot-form"); form.reset(); $("bot-error").textContent = "";
  $("bot-provider").replaceChildren(new Option("跟随全局默认供应商", ""));
  for(const provider of data.items) {
    if(provider.model_type === "chat" && (provider.enabled || provider.id === item?.provider_id)) {
      $("bot-provider").add(new Option(provider.name + (provider.enabled ? "" : "（已停用）"), provider.id));
    }
  }
  const values = item || {id:"", name:"", app_id:"", transport:"websocket", provider_id:null, system_prompt:"", enabled:false};
  for(const key of ["id", "name", "app_id", "transport", "provider_id", "system_prompt"]) form.elements[key].value = values[key] ?? "";
  form.elements.enabled.checked = Boolean(values.enabled);
  $("bot-title").textContent = item ? "编辑机器人" : "添加机器人";
  $("bot-dialog").showModal();
}
$("bot-form").onsubmit = async event => {
  event.preventDefault(); const form = event.currentTarget, submit = form.querySelector('[type="submit"]');
  const data = Object.fromEntries(new FormData(form)), id = data.id; delete data.id;
  data.enabled = form.elements.enabled.checked; data.provider_id = data.provider_id ? Number(data.provider_id) : null;
  submit.disabled = true; $("bot-error").textContent = "";
  try {
    const result = await api("/bots" + (id ? "/" + id : ""), {method:id ? "PUT" : "POST", body:JSON.stringify(data)});
    form.elements.app_secret.value = ""; $("bot-dialog").close(); await loadBots(); toast("已保存：" + result.status);
  } catch(e) { $("bot-error").textContent = e.message; } finally { submit.disabled = false; }
};
$("bot-dialog").addEventListener("close", () => $("bot-form").elements.app_secret.value = "");
$("new-bot").onclick = () => editBot(null).catch(e => toast(e.message));
$("refresh-bots").onclick = () => loadBots().catch(e => toast(e.message));

async function saveBinding(remove = false) {
  const item = detailRecord, form = $("binding-form");
  if (!item) return;
  const controls = [...form.querySelectorAll("button")];
  controls.forEach(node => node.disabled = true); $("binding-error").textContent = "";
  try {
    const binding = await api(`/bots/${item.bot_id}/records/${item.id}/binding`, {
      method: remove ? "DELETE" : "PUT",
      ...(remove ? {} : {body: JSON.stringify(Object.fromEntries(new FormData(form)))})
    });
    Object.assign(item, binding);
    if (detailRecord === item) {
      form.elements.member_name.value = item.member_name || "";
      form.elements.family_role.value = item.family_role || "father";
      form.elements.qq_number_note.value = item.qq_number_note || "";
    }
    toast(remove ? "已解除绑定，下次对话生效" : "已保存绑定，下次对话生效");
  } catch(e) { $("binding-error").textContent = e.message; }
  finally { controls.forEach(node => node.disabled = false); $("unbind-member").disabled = !detailRecord?.family_role; }
}
$("binding-form").onsubmit = event => { event.preventDefault(); void saveBinding(); };
$("unbind-member").onclick = () => saveBinding(true);

let greetingTarget = null;
async function openGreetings(item) {
  if (!item) return;
  const data = await api(`/bots/${item.bot_id}/records/${item.id}/greetings`);
  greetingTarget = {botId:item.bot_id, recordId:item.id};
  const form = $("greeting-form"); form.reset();
  $("greeting-error").textContent = "";
  $("greeting-target").textContent = `${botLabel(item.bot_id)} · ${data.contact_name || "未命名（请先在用户绑定中设置）"} · ${item.source === "qq_group" ? "群 " + item.group_id : "QQ 私聊"} · 用户 ${item.user_id}`;
  for (const rule of data.rules) {
    form.elements[rule.slot + "_enabled"].checked = rule.enabled;
    form.elements[rule.slot + "_start"].value = rule.start;
    form.elements[rule.slot + "_end"].value = rule.end;
    form.elements[rule.slot + "_content"].value = rule.content;
    form.querySelectorAll(`[name="${rule.slot}_days"]`).forEach(node => node.checked = rule.weekdays.includes(Number(node.value)));
    $(rule.slot + "-preview").textContent = rule.content;
  }
  $("greeting-history").replaceChildren();
  for (const run of data.history) {
    const state = ["pending", "processing"].includes(run.status) ? "处理中或中断后未确认，不自动重发" : (labels[run.status] || run.status);
    $("greeting-history").append(element("p", `${run.local_date} · ${run.slot === "morning" ? "早间" : "晚间"} · ${state} · 对话 #${run.record_id}`), element("pre", run.content));
  }
  if (!data.history.length) $("greeting-history").append(element("p", "暂无记录", "muted"));
  $("greeting-dialog").showModal();
}
$("greeting-form").oninput = () => {
  for (const slot of ["morning", "evening"]) $(slot + "-preview").textContent = $("greeting-form").elements[slot + "_content"].value;
};
$("greeting-form").onsubmit = async event => {
  event.preventDefault();
  const form = event.currentTarget, target = greetingTarget, submit = form.querySelector('[type="submit"]');
  if (!target) return;
  const rules = ["morning", "evening"].map(slot => ({slot,
    enabled:form.elements[slot + "_enabled"].checked,
    start:form.elements[slot + "_start"].value, end:form.elements[slot + "_end"].value,
    content:form.elements[slot + "_content"].value.trim(),
    weekdays:[...form.querySelectorAll(`[name="${slot}_days"]:checked`)].map(node => Number(node.value))
  }));
  if (rules.some(r => !r.weekdays.length || r.start >= r.end)) {
    $("greeting-error").textContent = "每个时段至少选择一天，结束时间须晚于开始时间，暂不支持跨午夜。"; return;
  }
  submit.disabled = true; $("greeting-error").textContent = "";
  try {
    await api(`/bots/${target.botId}/records/${target.recordId}/greetings`, {method:"PUT", body:JSON.stringify({rules})});
    $("greeting-dialog").close(); toast("问候设置已保存，下一次符合条件的互动生效");
    void loadGreetingTargets();
  } catch(e) { $("greeting-error").textContent = e.message; }
  finally { submit.disabled = false; }
};

let sendTarget = null, sendPending = false;
$("send-dialog").addEventListener("cancel", event => { if (sendPending) event.preventDefault(); });
function composeMessage(item) {
  if (!item || sendPending) return;
  sendTarget = {botId:item.bot_id, recordId:item.id, requestId:crypto.randomUUID()};
  $("send-form").reset(); $("send-form").elements.content.readOnly = false;
  $("send-submit").disabled = false; $("send-submit").textContent = "立即发送";
  $("send-result").textContent = "";
  $("send-target").textContent = `${botLabel(item.bot_id)} → ${item.source === "qq_group" ? "整个群：" + item.group_id : "私聊用户：" + (item.contact_name || "未命名") + " · " + item.user_id}`;
  $("send-dialog").showModal();
}
const openSend = button("主动发送消息", () => composeMessage(detailRecord));
openSend.id = "open-send"; openSend.hidden = true;
$("detail-dialog").append(openSend);
$("send-form").onsubmit = async event => {
  event.preventDefault();
  const target = sendTarget, input = event.currentTarget.elements.content;
  if (!target || !input.value.trim()) return;
  const submit = $("send-submit"); submit.disabled = true; input.readOnly = true;
  sendPending = true;
  const closeButtons = [...$("send-dialog").querySelectorAll("[data-close]")];
  closeButtons.forEach(node => node.disabled = true);
  $("send-result").textContent = "正在发送，请稍候…";
  try {
    const result = await api(`/bots/${target.botId}/records/${target.recordId}/send`, {
      method:"POST", body:JSON.stringify({content:input.value.trim(), request_id:target.requestId})
    });
    $("send-result").textContent = `记录 #${result.record_id} · ${result.status === "replied" ? "QQ 已确认发送" : labels[result.status] || result.status}。${result.message || ""}${result.duplicate ? "此请求已记录，未重复发送。" : ""}`;
    if (["pending", "processing", "delivery_unknown"].includes(result.status)) {
      $("send-result").textContent += " 可能已送达或处理中，请先核对 QQ 和聊天记录，不要新建相同消息。";
    }
    void loadRecords().catch(e => toast(e.message));
  } catch(e) {
    $("send-result").textContent = e.message + "。请求结果未确认；再次提交会沿用同一请求标识，避免重复发送。";
    submit.textContent = "重新提交同一请求"; submit.disabled = false;
  } finally {
    sendPending = false; closeButtons.forEach(node => node.disabled = false);
  }
};

let sendTargetsPage = 1, sendTargetsRequest = 0;
async function loadSendTargets() {
  const request = ++sendTargetsRequest, botId = $("send-bot").value;
  $("send-targets-body").replaceChildren(); $("send-targets-empty").hidden = false;
  $("send-targets-empty").textContent = botId ? "正在加载…" : "请选择发送机器人。";
  $("send-target-count").textContent = ""; $("send-target-page").textContent = "";
  $("send-target-prev").disabled = true; $("send-target-next").disabled = true;
  if (!botId) return;
  try {
    const data = await api(`/bots/${botId}/send-targets?source=${$("send-source").value}&page=${sendTargetsPage}`);
    if (request !== sendTargetsRequest) return;
    $("send-target-count").textContent = `共 ${data.total} 个接收对象`;
    $("send-target-page").textContent = `第 ${data.page} / ${Math.max(1, Math.ceil(data.total / data.page_size))} 页`;
    $("send-target-prev").disabled = data.page <= 1;
    $("send-target-next").disabled = data.page * data.page_size >= data.total;
    $("send-targets-empty").hidden = data.items.length > 0;
    $("send-targets-empty").textContent = "暂无此类会话，请先让用户私聊机器人或在群里 @机器人。";
    for (const item of data.items) {
      const group = item.source === "qq_group", row = element("tr");
      const identity = element("td", group ? item.group_id : (item.contact_name || "私聊用户"));
      if (!group) identity.append(element("small", item.user_id));
      const action = element("td"); action.append(button("编写消息", () => composeMessage(item)));
      row.append(identity, element("td", group ? "整个群" : "仅此用户"), action);
      $("send-targets-body").append(row);
    }
  } catch(e) {
    if (request === sendTargetsRequest) $("send-targets-empty").textContent = "加载失败：" + e.message;
  }
}
for (const id of ["send-bot", "send-source"]) $(id).onchange = () => { sendTargetsPage = 1; void loadSendTargets(); };
$("refresh-send-targets").onclick = () => { sendTargetsPage = 1; void loadSendTargets(); };
$("send-target-prev").onclick = () => { sendTargetsPage--; void loadSendTargets(); };
$("send-target-next").onclick = () => { sendTargetsPage++; void loadSendTargets(); };

let greetingTargetsPage = 1, greetingTargetsRequest = 0;
async function loadGreetingTargets() {
  const request = ++greetingTargetsRequest;
  const params = new URLSearchParams({page:String(greetingTargetsPage)});
  if ($("greeting-bot").value) params.set("bot_id", $("greeting-bot").value);
  $("greeting-targets-body").replaceChildren();
  $("greeting-targets-empty").hidden = false; $("greeting-targets-empty").textContent = "正在加载…";
  $("greeting-target-count").textContent = ""; $("greeting-target-page").textContent = "";
  $("greeting-target-prev").disabled = true; $("greeting-target-next").disabled = true;
  try {
    const data = await api("/greeting-targets?" + params);
    if (request !== greetingTargetsRequest) return;
    $("greeting-target-count").textContent = `共 ${data.total} 个对象`;
    $("greeting-target-page").textContent = `第 ${data.page} / ${Math.max(1, Math.ceil(data.total / data.page_size))} 页`;
    $("greeting-target-prev").disabled = data.page <= 1;
    $("greeting-target-next").disabled = data.page * data.page_size >= data.total;
    $("greeting-targets-empty").hidden = data.items.length > 0;
    $("greeting-targets-empty").textContent = "暂无问候对象，请先在用户绑定中设置接收人名称，然后刷新。";
    for (const item of data.items) {
      const row = element("tr"), bot = element("td", botLabel(item.bot_id)); bot.dataset.botLabel = item.bot_id;
      const member = element("td", item.contact_name || "已解除绑定");
      member.append(element("small", item.user_id));
      const action = element("td"); action.append(button("配置 / 记录", () => openGreetings(item)));
      row.append(bot, member, element("td", item.source === "qq_c2c" ? "QQ 私聊" : "群 " + item.group_id),
        element("td", Number(item.enabled_count) ? `已启用 ${item.enabled_count} 个时段` : "未启用"), action);
      $("greeting-targets-body").append(row);
    }
  } catch(e) {
    if (request === greetingTargetsRequest) $("greeting-targets-empty").textContent = "加载失败：" + e.message;
  }
}
$("greeting-bot").onchange = () => { greetingTargetsPage = 1; void loadGreetingTargets(); };
$("refresh-greeting-targets").onclick = () => { greetingTargetsPage = 1; void loadGreetingTargets(); };
$("greeting-target-prev").onclick = () => { greetingTargetsPage--; void loadGreetingTargets(); };
$("greeting-target-next").onclick = () => { greetingTargetsPage++; void loadGreetingTargets(); };

let userBindingsPage = 1, userBindingsRequest = 0;
async function loadUserBindings() {
  const request = ++userBindingsRequest, params = new URLSearchParams({page:String(userBindingsPage)});
  if ($("binding-bot-filter").value) params.set("bot_id", $("binding-bot-filter").value);
  if ($("binding-user-filter").value.trim()) params.set("user_id", $("binding-user-filter").value.trim());
  $("user-bindings-body").replaceChildren(); $("user-bindings-empty").hidden = false;
  $("user-bindings-empty").textContent = "正在加载…";
  $("user-binding-count").textContent = ""; $("user-binding-page").textContent = "";
  $("user-binding-prev").disabled = true; $("user-binding-next").disabled = true;
  try {
    const data = await api("/user-bindings?" + params);
    if (request !== userBindingsRequest) return;
    $("user-binding-count").textContent = `共 ${data.total} 个会话身份`;
    $("user-binding-page").textContent = `第 ${data.page} / ${Math.max(1, Math.ceil(data.total / data.page_size))} 页`;
    $("user-binding-prev").disabled = data.page <= 1;
    $("user-binding-next").disabled = data.page * data.page_size >= data.total;
    $("user-bindings-empty").hidden = data.items.length > 0;
    $("user-bindings-empty").textContent = "没有匹配的用户，请先让该用户私聊机器人或在群里 @机器人。";
    for (const item of data.items) {
      const row = element("tr"), bot = element("td"), name = element("span", botLabel(item.bot_id)); name.dataset.botLabel = item.bot_id;
      bot.append(name, element("small", item.source === "qq_c2c" ? "QQ 私聊" : "QQ 群聊"));
      const identity = element("td", item.user_id);
      if (item.source === "qq_group") identity.append(element("small", "群 " + item.group_id));
      const member = element("td", item.contact_name || "未命名");
      const actions = element("td"); actions.append(button(item.contact_name ? "编辑" : "绑定", () => editUserBinding(item)));
      if (item.contact_name && item.record_id) actions.append(button("解除", async () => {
        await api(`/bots/${item.bot_id}/records/${item.record_id}/user-binding`, {method:"DELETE"});
        toast("已解除绑定，相关问候已停用"); await loadUserBindings();
      }));
      row.append(bot, identity, member, element("td", item.contact_qq || "—"), actions); $("user-bindings-body").append(row);
    }
  } catch(e) { if (request === userBindingsRequest) $("user-bindings-empty").textContent = "加载失败：" + e.message; }
}
function updateBindingSource() {
  const form = $("user-binding-form"), group = form.elements.source.value === "qq_group";
  $("binding-group-label").hidden = !group; form.elements.group_id.required = group;
  if (!group) form.elements.group_id.value = "";
}
function editUserBinding(item = null) {
  const form = $("user-binding-form"); form.reset(); $("user-binding-error").textContent = "";
  const values = item || {bot_id:$("binding-bot-filter").value, source:"qq_c2c", user_id:$("binding-user-filter").value.trim()};
  for (const key of ["bot_id", "source", "user_id", "group_id", "contact_name", "contact_qq"]) form.elements[key].value = values[key] || "";
  updateBindingSource(); $("user-binding-dialog").showModal();
}
$("user-binding-form").elements.source.onchange = updateBindingSource;
$("new-user-binding").onclick = () => editUserBinding();
$("user-binding-filter").onsubmit = event => { event.preventDefault(); userBindingsPage = 1; void loadUserBindings(); };
$("binding-bot-filter").onchange = () => { userBindingsPage = 1; void loadUserBindings(); };
$("user-binding-prev").onclick = () => { userBindingsPage--; void loadUserBindings(); };
$("user-binding-next").onclick = () => { userBindingsPage++; void loadUserBindings(); };
$("user-binding-form").onsubmit = async event => {
  event.preventDefault(); const form = event.currentTarget, submit = form.querySelector('[type="submit"]');
  const values = Object.fromEntries(new FormData(form)), botId = values.bot_id; delete values.bot_id;
  submit.disabled = true; $("user-binding-error").textContent = "";
  try {
    await api(`/bots/${botId}/user-bindings`, {method:"PUT", body:JSON.stringify(values)});
    $("user-binding-dialog").close(); toast("用户名称已保存，可在定时任务和发送消息中选择"); await loadUserBindings();
  } catch(e) { $("user-binding-error").textContent = e.message; }
  finally { submit.disabled = false; }
};
