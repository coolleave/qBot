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
async function generateSpeech(providerId, text, voice = "Bunny") {
  if (!providerId || !text.trim()) throw new Error("请选择 TTS 模型并输入文本");
  const response = await fetch(`/api/admin/providers/${providerId}/speech`, {
    method:"POST", credentials:"same-origin",
    headers:{"Content-Type":"application/json", "X-QBot-Admin":"1", "X-CSRF-Token":csrf},
    body:JSON.stringify({text:text.trim(), voice:voice.trim()})
  });
  if (!response.ok) {
    if (response.status === 401) loggedOut();
    if (response.status === 404) throw new Error("当前后端未找到语音合成接口。请确认访问的是本项目服务，完成数据库迁移后重启 Python 服务，再刷新后台；仅刷新页面不会加载新接口。");
    const data = await response.json(); throw new Error(data.detail || "语音合成失败");
  }
  return new File([await response.blob()], "tts.wav", {type:"audio/wav"});
}
async function enter() {
  const state = await api("/session"); csrf = state.csrf_token;
  $("username").textContent = state.username; $("login-screen").hidden = true; $("workspace").hidden = false;
  const warnings = []; if (!state.qq_configured) warnings.push("暂无运行中的机器人，请到机器人管理中配置并启动。");

  $("notice").textContent = warnings.join(" "); $("notice").hidden = !warnings.length;
  await Promise.all([loadRecords(), loadProviders(), loadBots()]);
}
function date(value) { return value ? new Date(/[Zz]|[+-]\d\d:\d\d$/.test(value) ? value : value + "Z").toLocaleString() : "—"; }
function tokenNumber(value) { return value == null ? "未知" : Number(value).toLocaleString(); }
function tokenText(item) { return [item.input_tokens, item.output_tokens, item.total_tokens].map(tokenNumber).join(" / "); }
function element(tag, text, className) { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; }
function button(text, action) { const node = element("button", text, "row-action"); node.type = "button"; node.onclick = async () => { node.disabled = true; try { await action(); } catch(e) { toast(e.message); } finally { node.disabled = false; } }; return node; }
async function loadRecords() {
  const request = ++recordsRequest, params = new URLSearchParams({page:String(currentPage),page_size:"20"});
  for (const [name,value] of new FormData($("filter-form"))) if(value) params.set(name, ["start","end"].includes(name) ? new Date(value).toISOString() : value.trim());
  $("record-count").textContent = "正在加载…";
  $("token-summary").textContent = "正在加载…";
  let data;
  try { data = await api("/records?" + params); }
  catch (error) {
    if (request === recordsRequest) {
      $("token-summary").textContent = "统计加载失败，请刷新重试";
      $("record-count").textContent = "加载失败";
    }
    throw error;
  }
  if(request !== recordsRequest) return;
  const usage = data.token_usage;
  $("token-summary").textContent = usage
    ? `已知输入 ${tokenNumber(usage.input_tokens)} · 已知输出 ${tokenNumber(usage.output_tokens)} · 已知总计 ${tokenNumber(usage.total_tokens)} token · ${usage.known_records} / ${usage.records} 条记录有总用量`
    : "当前服务尚未提供 Token 统计，请升级后端";
  $("records-body").replaceChildren(); $("records-empty").hidden = data.items.length > 0;
  $("record-count").textContent = `共 ${data.total} 条`; $("page-info").textContent = `第 ${data.page} / ${Math.max(1, Math.ceil(data.total / data.page_size))} 页`;
  $("prev-page").disabled = currentPage <= 1; $("next-page").disabled = currentPage * data.page_size >= data.total;
  for(const item of data.items) {
    const row = element("tr"), timeCell = element("td", date(item.received_at)); const botName = element("small", botLabel(item.bot_id)); botName.dataset.botLabel = item.bot_id; timeCell.append(botName); timeCell.append(element("small", ({qq_group:"QQ 群聊",qq_c2c:"QQ 私聊",local:"本地聊天"}[item.source] || item.source))); row.append(timeCell);
    const identity = element("td", item.source === "qq_c2c" ? "私聊" : item.group_id); identity.append(element("small", item.user_id)); row.append(identity, element("td", item.question), element("td", item.model_name_snapshot || "—"));
    const status = element("td"); status.append(element("span", labels[item.status] || item.status, "badge " + item.status)); row.append(status);
    row.insertBefore(element("td", tokenText(item)), status);
    const action = element("td"); action.append(button("查看 →", () => showDetail(item.id))); row.append(action); $("records-body").append(row);
  }
}
let detailRecord = null;
function renderDetailMeta(item) {
  const source = {qq_group:"QQ 群聊", qq_c2c:"QQ 私聊", local:"本地聊天"}[item.source] || item.source;
  $("detail-meta").textContent = `${botLabel(item.bot_id)} · ${source} · ${date(item.received_at)} · ${labels[item.status] || item.status} · ${item.provider_name_snapshot || "未调用模型"} / ${item.model_name_snapshot || "—"}${item.source === "qq_group" ? " · 群 " + item.group_id : ""} · 用户 ${item.user_id}`;
  $("detail-meta").append(document.createTextNode(` · 聊天 Token（输入 / 输出 / 总计）：${tokenText(item)}`));
}
async function showDetail(id) {
  const item = await api("/records/" + id);
  detailRecord = item;
  $("detail-media").textContent = item.media_details ? JSON.stringify(item.media_details, null, 2) : "无附件处理记录";
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
  $("providers-list").querySelectorAll("audio").forEach(player => { player.pause(); if (player.dataset.url) URL.revokeObjectURL(player.dataset.url); });
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
    probe.disabled = !item.has_api_key || !["chat", "tts"].includes(item.model_type);
    if (item.model_type === "tts") {
      const text = element("textarea"), voice = element("input"), player = element("audio");
      text.maxLength = 500; text.value = "你好，这是语音合成效果测试。"; text.setAttribute("aria-label", "试听文本");
      voice.value = "Bunny"; voice.setAttribute("aria-label", "音色名称"); voice.setAttribute("list", "tts-voices");
      player.controls = true; player.hidden = true;
      card.append(element("p", "音色默认 Bunny（萌小姬），可选 Bella、Mochi、Momo 等，或填写官方音色 ID。测试会调用付费接口。Flash 模型不能通过填写描述自定义声音。", "muted"), text, voice, player);
      probe.onclick = async () => {
        probe.disabled = true; player.pause(); player.hidden = true;
        if (player.dataset.url) { URL.revokeObjectURL(player.dataset.url); delete player.dataset.url; }
        player.removeAttribute("src"); player.load();
        outcome.textContent = "正在合成语音…";
        const started = performance.now();
        try {
          const file = await generateSpeech(item.id, text.value, voice.value);
          if (!card.isConnected) return;
          player.src = URL.createObjectURL(file); player.dataset.url = player.src; player.hidden = false;
          outcome.textContent = `合成成功 · 耗时 ${Math.round(performance.now() - started)} ms · 点击播放器试听`;
        } catch(e) { outcome.textContent = "合成失败：" + e.message; }
        finally { probe.disabled = false; }
      };
    } else if (item.model_type !== "chat") outcome.textContent = "已保存模型配置，暂未接入调用及连接测试。";
    if (item.model_type === "asr" || (item.model_type === "chat" && item.supports_images)) {
      const kind = item.model_type === "asr" ? "audio" : "image", upload = element("input");
      upload.type = "file"; upload.accept = kind === "audio" ? ".wav,.mp3,.flac,.ogg,.silk" : ".png,.jpg,.jpeg,.webp,.gif";
      upload.setAttribute("aria-label", kind === "audio" ? "短语音文件" : "图片或表情包");
      card.append(upload, element("p", "文件最多8 MB；短语音最多60秒。点击试用会调用付费模型。" + (item.model_name.includes("filetrans") ? "音频会上传至百炼临时存储，48小时后自动删除，仅供开发试用。" : ""), "muted"));
      const inspect = button(kind === "audio" ? "转写语音" : "理解图片 / 表情包", async () => {
        const file = upload.files[0];
        if (!file || file.size > 8000000) throw new Error("请选择不超过8 MB的文件");
        outcome.textContent = item.model_name.includes("filetrans") ? "正在上传并等待异步转写，最长约80秒…" : "正在处理…";
        try {
          const result = await api(`/providers/${item.id}/inspect-media?kind=${kind}`, {method:"POST", headers:{"Content-Type":"application/octet-stream"}, body:file});
          outcome.textContent = `${result.success ? "成功" : "失败"} · ${result.latency_ms} ms · ${result.reply || result.message}`;
        } catch(e) { outcome.textContent = "处理失败：" + e.message; }
      });
      inspect.disabled = !item.has_api_key; actions.append(inspect);
      if (kind === "audio") { probe.hidden = true; outcome.textContent = "选择短语音后点击转写。"; }
    }
    actions.append(probe);
    card.append(actions, outcome); $("providers-list").append(card);
  }
}
// 预设仅填写表单，不创建数据库记录或替换已有供应商。
function providerPreset(item) {
  try {
    const host = new URL(item.base_url).hostname;
    if (host === "api.deepseek.com") return "deepseek";
    if (["dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"].includes(host) || host.endsWith(".maas.aliyuncs.com")) return "qwen";
  } catch { /* 编辑时保留无法归类的自定义地址。 */ }
  return "custom";
}
function showProviderPreset() {
  const form = $("provider-form"), preset = $("provider-preset").value;
  const kind = form.elements.model_type.value;
  $("provider-preset-hint").textContent = preset === "qwen"
    ? (kind === "asr" ? "千问 ASR 默认 Filetrans：先上传 WAV，再异步转写。临时文件保留48小时，仅供开发试用；正式部署请使用自有 OSS。" : "千问默认北京地域；切换类型会重新填写推荐模型和地址。其他地域可手动修改，API Key 须匹配地域。")
    : preset === "deepseek" ? "DeepSeek 预设用于聊天，默认 deepseek-flash。填写自己的 API Key 后保存。" : "自行填写 HTTPS 地址、模型和接口。自定义不会自动适配未知的 ASR / TTS 协议。";
  const names = preset === "qwen" ? ({chat:["qwen3.8-flash", "qwen3-vl-plus"], asr:["qwen-audio-3.0-asr-flash-filetrans", "qwen-audio-3.1-asr-flash-filetrans", "qwen-audio-3.0-asr-flash", "qwen-audio-3.1-asr-flash"], tts:["qwen3-tts-flash-realtime"]}[kind] || []) : preset === "deepseek" ? ["deepseek-flash", "deepseek-v4-pro"] : [];
  $("provider-model-suggestions").replaceChildren(...names.map(name => new Option(name, name)));
}
function applyProviderPreset() {
  const form = $("provider-form"), preset = $("provider-preset").value;
  if (preset === "custom") { showProviderPreset(); return; }
  if (preset === "deepseek") form.elements.model_type.value = "chat";
  const kind = form.elements.model_type.value;
  if (kind === "image") { $("provider-preset").value = "custom"; showProviderPreset(); return; }
  const base = preset === "deepseek" ? "https://api.deepseek.com" : "https://dashscope.aliyuncs.com" + (kind === "chat" ? "/compatible-mode/v1" : "");
  try {
    if (form.elements.base_url.value && new URL(form.elements.base_url.value).origin !== new URL(base).origin) form.elements.api_key.value = "";
  } catch { form.elements.api_key.value = ""; }
  form.elements.api_type.value = preset === "deepseek" ? "deepseek" : "openai_compatible";
  form.elements.base_url.value = base;
  form.elements.model_name.value = preset === "deepseek" ? "deepseek-flash" : {chat:"qwen3.8-flash", asr:"qwen-audio-3.0-asr-flash-filetrans", tts:"qwen3-tts-flash-realtime"}[kind];
  form.elements.supports_images.checked = preset === "qwen" && kind === "chat";
  if (!form.elements.name.value.trim()) form.elements.name.value = (preset === "qwen" ? "千问" : "DeepSeek") + " " + kind;
  showProviderPreset();
}
$("provider-preset").onchange = applyProviderPreset;
$("provider-form").elements.model_type.onchange = () => {
  if ($("provider-preset").value === "deepseek" && $("provider-form").elements.model_type.value !== "chat") $("provider-preset").value = "custom";
  applyProviderPreset();
};
function editProvider(item) {
  const form = $("provider-form"); form.reset(); $("provider-error").textContent = "";
  $("provider-preset").value = item ? providerPreset(item) : "deepseek";
  const defaults = item || {id:"",name:"",model_type:"chat",api_type:"deepseek",base_url:"https://api.deepseek.com",model_name:"deepseek-flash",enabled:false};
  for(const key of ["id","name","model_type","api_type","base_url","model_name"]) form.elements[key].value = defaults[key];
  form.elements.supports_images.checked = Boolean(defaults.supports_images);
  form.elements.enabled.checked = Boolean(defaults.enabled); form.elements.api_key.value = "";
  showProviderPreset();
  $("provider-title").textContent = item ? "编辑供应商" : "添加供应商"; $("provider-dialog").showModal();
}
$("login-form").onsubmit = async event => {event.preventDefault(); const form = event.currentTarget, submit = form.querySelector("button"); submit.disabled = true; $("login-error").textContent = ""; try {await api("/login", {method:"POST",body:JSON.stringify(Object.fromEntries(new FormData(form)))}); form.elements.password.value = ""; await enter();} catch(e) {$("login-error").textContent = e.message; toast(e.message);} finally {submit.disabled = false;}};
$("logout").onclick = async () => {try {await api("/logout", {method:"POST"}); loggedOut();} catch(e) {toast(e.message);}};
document.querySelectorAll("[data-page]").forEach(node => node.onclick = () => {
  const page = node.dataset.page;
  document.querySelectorAll("[data-page]").forEach(n => n.classList.toggle("active", n === node));
  for (const name of ["records", "providers", "bots", "messages", "greetings", "bindings", "identities"]) $(name + "-page").hidden = page !== name;
  $("page-name").textContent = {records:"聊天记录",providers:"模型供应商",bots:"机器人管理",messages:"发送消息",greetings:"定时任务",bindings:"用户绑定",identities:"身份绑定"}[page];
  if (page === "identities") void loadIdentityBindings();
  if (page === "bindings") void loadUserBindings();
  if (page === "greetings") void loadGreetingTargets();
  if (page === "messages") void loadSendTargets().catch(e => toast(e.message));
});
document.querySelectorAll("[data-close]").forEach(node => node.onclick = () => $(node.dataset.close).close());
$("provider-dialog").addEventListener("close", () => $("provider-form").elements.api_key.value = "");
$("new-provider").onclick = () => editProvider(null);
$("environment-default").onclick = async () => {try {await api("/default-provider", {method:"PUT",body:JSON.stringify({provider_id:null})}); await loadProviders(); toast("已取消默认供应商，选择新默认项后才能聊天");} catch(e) {toast(e.message);}};
$("provider-form").onsubmit = async event => {event.preventDefault(); const form = event.currentTarget, submit = form.querySelector('[type="submit"]'), data = Object.fromEntries(new FormData(form)), id = data.id; delete data.id; data.enabled = form.elements.enabled.checked; data.supports_images = form.elements.supports_images.checked; submit.disabled = true; $("provider-error").textContent = ""; try {await api("/providers" + (id ? "/" + id : ""), {method:id ? "PUT" : "POST",body:JSON.stringify(data)}); $("provider-dialog").close(); await loadProviders(); toast("配置已保存，下次模型请求生效");} catch(e) {$("provider-error").textContent = e.message;} finally {submit.disabled = false;}};
$("filter-form").onsubmit = event => {event.preventDefault();currentPage = 1;loadRecords().catch(e => toast(e.message));};
$("reset-filters").onclick = () => {$("filter-form").reset();currentPage = 1;loadRecords().catch(e => toast(e.message));};
$("refresh-records").onclick = () => loadRecords().catch(e => toast(e.message));
$("prev-page").onclick = () => {currentPage--;loadRecords().catch(e => toast(e.message));};
$("next-page").onclick = () => {currentPage++;loadRecords().catch(e => toast(e.message));};
enter().catch(e => {if(!$("workspace").hidden) toast(e.message);});

async function loadBots() {
  const data = await api("/bots");
  $("bots-list").replaceChildren();
  for (const id of ["binding-bot-filter", "binding-bot", "identity-bot-filter", "identity-bot"]) {
    const select = $(id), selected = select.value;
    select.replaceChildren(new Option(["binding-bot", "identity-bot"].includes(id) ? "请选择机器人" : "全部机器人", ""));
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
  const mm = item?.multimodal || {};
  for (const key of ["images", "gifs", "asr"]) form.elements["mm_" + key].checked = Boolean(mm[key]);
  for (const kind of ["vision", "asr", "tts"]) {
    const key = kind + "_provider_id", select = form.elements["mm_" + key];
    select.replaceChildren(new Option(kind === "vision" ? "请选择独立视觉理解模型" : "请选择", ""));
    for (const provider of data.items.filter(p => (p.enabled || p.id === mm[key]) && (kind === "vision" ? p.model_type === "chat" && p.supports_images && p.api_type === "openai_compatible" : p.model_type === kind))) {
      select.add(new Option(provider.name, provider.id));
    }
    select.value = mm[key] || "";
  }
  form.elements.mm_voice.value = mm.voice || "Bunny";
  for (const key of ["private_reply", "group_reply"]) form.elements["mm_" + key].value = mm[key] || "text";
  const values = item || {id:"", name:"", app_id:"", transport:"websocket", provider_id:null, system_prompt:"", enabled:false};
  for(const key of ["id", "name", "app_id", "transport", "provider_id", "system_prompt"]) form.elements[key].value = values[key] ?? "";
  form.elements.enabled.checked = Boolean(values.enabled);
  $("bot-title").textContent = item ? "编辑机器人" : "添加机器人";
  $("bot-dialog").showModal();
}
$("bot-form").onsubmit = async event => {
  event.preventDefault(); const form = event.currentTarget, submit = form.querySelector('[type="submit"]');
  const data = Object.fromEntries(new FormData(form)), id = data.id; delete data.id;
  data.multimodal = {};
  for (const key of ["images", "gifs", "asr", "vision_provider_id", "asr_provider_id", "tts_provider_id", "voice", "private_reply", "group_reply"]) {
    const field = form.elements["mm_" + key];
    data.multimodal[key] = field.type === "checkbox" ? field.checked : key.endsWith("_id") ? (field.value ? Number(field.value) : null) : field.value;
    delete data["mm_" + key];
  }
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

let greetingTarget = null, greetingItem = null, greetingDirty = false, greetingRunning = false;
$("greeting-target").after(element("p", "生成文案会调用此机器人配置的聊天模型，可能产生费用。按最新保存的人设生成，替换当前时段的草稿；确认后请保存设置。", "muted"));
for (const slot of ["morning", "evening"]) {
  const generate = button("按人设生成文案", async () => {
    const form = $("greeting-form");
    if (!greetingTarget || greetingRunning || form.querySelector('[type="submit"]').disabled) return;
    const target = greetingTarget;
    const controls = [...$("greeting-dialog").querySelectorAll("button, input, textarea")];
    greetingRunning = true; controls.forEach(node => node.disabled = true);
    $("greeting-error").textContent = "";
    $("greeting-run-result").textContent = "正在按机器人人设生成文案…";
    try {
      const result = await api(`/bots/${target.botId}/records/${target.recordId}/greetings/${slot}/generate`, {method:"POST"});
      if (greetingTarget !== target || !$("greeting-dialog").open) return;
      form.elements[slot + "_content"].value = result.content;
      $(slot + "-preview").textContent = result.content;
      greetingDirty = true;
      $("greeting-run-result").textContent = "文案已生成，可继续修改；点击保存设置后生效。";
    } catch(e) {
      $("greeting-error").textContent = e.message;
      $("greeting-run-result").textContent = "生成未完成，原文案已保留。";
    } finally { greetingRunning = false; controls.forEach(node => node.disabled = false); }
  });
  $(slot + "-preview").before(generate);
}
$("greeting-dialog").addEventListener("cancel", event => { if (greetingRunning) event.preventDefault(); });
async function openGreetings(item) {
  if (!item) return;
  const data = await api(`/bots/${item.bot_id}/records/${item.id}/greetings`);
  greetingTarget = {botId:item.bot_id, recordId:item.id};
  greetingItem = item; greetingDirty = false;
  $("greeting-run-result").textContent = "";
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
    $("greeting-history").append(element("p", `${run.local_date} · ${run.slot === "morning" ? "早间" : "晚间"} · ${state} · 对话 #${run.record_id}`), element("pre", run.content), element("p", run.error_message || "", "error"));
  }
  if (!data.history.length) $("greeting-history").append(element("p", "暂无记录", "muted"));
  $("greeting-dialog").showModal();
}
$("greeting-form").oninput = () => {
  greetingDirty = true;
  for (const slot of ["morning", "evening"]) $(slot + "-preview").textContent = $("greeting-form").elements[slot + "_content"].value;
};
for (const slot of ["morning", "evening"]) $(slot + "-run").onclick = async () => {
  if (!greetingTarget || greetingRunning) return;
  if (greetingDirty) { $("greeting-error").textContent = "请先保存修改后的任务，再执行一次。"; return; }
  const target = greetingTarget, item = greetingItem;
  const controls = [...$("greeting-dialog").querySelectorAll("button, input, textarea")];
  greetingRunning = true; controls.forEach(node => node.disabled = true);
  $("greeting-error").textContent = ""; $("greeting-run-result").textContent = "正在执行，请稍候…";
  try {
    const result = await api(`/bots/${target.botId}/records/${target.recordId}/greetings/${slot}/run`, {method:"POST"});
    const message = `记录 #${result.record_id} · ${result.status === "replied" ? "QQ 已确认发送" : labels[result.status] || result.status}。${result.message || ""}${result.duplicate ? " 今天已执行，未重复发送。" : ""}`;
    $("greeting-run-result").textContent = message;
    try { await openGreetings(item); } catch(e) { $("greeting-error").textContent = "记录刷新失败：" + e.message; }
    $("greeting-run-result").textContent = message;
  } catch(e) {
    $("greeting-run-result").textContent = e.message + "。如请求结果未确认，可再次点击查询；今天已领取的任务不会重复发送。";
  } finally { greetingRunning = false; controls.forEach(node => node.disabled = false); }
};
$("greeting-form").onsubmit = async event => {
  event.preventDefault();
  const form = event.currentTarget, target = greetingTarget, submit = form.querySelector('[type="submit"]');
  if (!target || greetingRunning) return;
  const rules = ["morning", "evening"].map(slot => ({slot,
    enabled:form.elements[slot + "_enabled"].checked,
    start:form.elements[slot + "_start"].value, end:form.elements[slot + "_end"].value,
    content:form.elements[slot + "_content"].value.trim(),
    weekdays:[...form.querySelectorAll(`[name="${slot}_days"]:checked`)].map(node => Number(node.value))
  }));
  if (rules.some(r => !r.weekdays.length || r.start >= r.end)) {
    $("greeting-error").textContent = "每条任务至少选择一天，最晚执行时间须晚于发送时间，暂不支持跨午夜。"; return;
  }
  submit.disabled = true; $("greeting-error").textContent = "";
  try {
    await api(`/bots/${target.botId}/records/${target.recordId}/greetings`, {method:"PUT", body:JSON.stringify({rules})});
    $("greeting-dialog").close(); toast("定时任务已保存，将在设置的时间主动发送");
    void loadGreetingTargets();
  } catch(e) { $("greeting-error").textContent = e.message; }
  finally { submit.disabled = false; }
};

const MAX_MEDIA_BYTES = 3500000;
let sendTarget = null, sendPending = false, sendPayload = null, mediaPreviewUrl = null, generatedSpeech = null;
$("send-dialog").addEventListener("cancel", event => { if (sendPending) event.preventDefault(); });
function clearMediaPreview() {
  $("send-audio-preview").pause();
  for (const id of ["send-image-preview", "send-audio-preview"]) { $(id).removeAttribute("src"); $(id).hidden = true; }
  $("send-audio-preview").load();
  if (mediaPreviewUrl) URL.revokeObjectURL(mediaPreviewUrl);
  mediaPreviewUrl = null; $("send-file-info").textContent = "";
}
function updateSendKind() {
  const form = $("send-form"), kind = form.elements.message_kind.value;
  clearMediaPreview(); generatedSpeech = null; form.elements.media_file.value = "";
  $("send-tts-result").textContent = "";
  $("send-tts-panel").hidden = kind !== "audio";
  $("send-text-label").hidden = kind !== "text";
  form.elements.content.required = kind === "text";
  $("send-media-panel").hidden = kind === "text";
  form.elements.media_file.required = kind !== "text";
  const group = sendTarget?.source === "qq_group";
  form.elements.media_file.accept = kind === "image" ? ".png,.jpg,.jpeg" : group ? ".silk,.mp3,.wav" : ".silk,.wav";
  $("send-media-hint").textContent = kind === "image" ? "PNG/JPG，最大 3.5 MB。预览仅在本地进行。" :
    `${group ? "群聊支持 SILK、MP3、WAV，可生成 TTS 后发送" : "私聊支持 SILK，单声道 16 位 PCM WAV（8/12/16/24 kHz）会自动转为 SILK；TTS 可直接生成后发送"}，最大 3.5 MB。SILK 无法在浏览器直接试听。`;
}
function previewMedia() {
  generatedSpeech = null;
  $("send-tts-result").textContent = "";
  $("send-form").elements.media_file.required = true;
  clearMediaPreview();
  const form = $("send-form"), file = form.elements.media_file.files[0];
  if (!file) return;
  if (!file.size || file.size > MAX_MEDIA_BYTES) {
    $("send-file-info").textContent = "请选择非空且不超过 3.5 MB 的文件"; form.elements.media_file.value = ""; return;
  }
  $("send-file-info").textContent = `${file.name} · ${(file.size / 1000000).toFixed(2)} MB`;
  if (/\.silk$/i.test(file.name)) return;
  mediaPreviewUrl = URL.createObjectURL(file);
  const id = form.elements.message_kind.value === "image" ? "send-image-preview" : "send-audio-preview";
  $(id).src = mediaPreviewUrl; $(id).hidden = false;
}
$("send-form").elements.message_kind.onchange = updateSendKind;
$("send-form").elements.media_file.onchange = previewMedia;
for (const id of ["send-tts-provider", "send-tts-text", "send-tts-voice"]) $(id).oninput = () => {
  if (sendPending || sendPayload || !generatedSpeech) return;
  generatedSpeech = null; clearMediaPreview();
  $("send-form").elements.media_file.required = true;
  $("send-tts-result").textContent = "合成设置已修改，请重新生成语音。";
};
$("send-tts-generate").onclick = async () => {
  if (sendPending || sendPayload) return;
  const form = $("send-form"), target = sendTarget;
  generatedSpeech = null; clearMediaPreview(); form.elements.media_file.value = "";
  form.elements.media_file.required = true;
  sendPending = true;
  const controls = [...$("send-dialog").querySelectorAll("button, input, select, textarea")];
  const states = controls.map(node => node.disabled); controls.forEach(node => node.disabled = true);
  $("send-tts-result").textContent = "正在生成语音…";
  try {
    const file = await generateSpeech($("send-tts-provider").value, $("send-tts-text").value, $("send-tts-voice").value);
    if (target !== sendTarget || !$("send-dialog").open) return;
    mediaPreviewUrl = URL.createObjectURL(file);
    $("send-audio-preview").src = mediaPreviewUrl; $("send-audio-preview").hidden = false;
    $("send-file-info").textContent = `${file.name} · ${(file.size / 1000000).toFixed(2)} MB`;
    generatedSpeech = file; form.elements.media_file.required = false;
    $("send-tts-result").textContent = "生成成功，可试听后点击立即发送。" +
      (target.source === "qq_c2c" ? "私聊发送时自动转成 SILK。" : "") + "修改文本或音色后需重新生成。";
  } catch(e) { $("send-tts-result").textContent = e.message; }
  finally { sendPending = false; controls.forEach((node, index) => node.disabled = states[index]); }
};
$("send-image-preview").onerror = () => { $("send-file-info").textContent = "图片无法预览，请检查文件格式。"; };
$("send-audio-preview").onerror = () => {
  if (mediaPreviewUrl) $("send-file-info").textContent = "此音频无法在当前浏览器试听，发送前仍会校验文件格式。";
};
$("send-dialog").addEventListener("close", () => { clearMediaPreview(); generatedSpeech = null; $("send-form").elements.media_file.value = ""; sendPayload = null; });
function composeMessage(item) {
  if (!item || sendPending) return;
  sendTarget = {botId:item.bot_id, recordId:item.id, source:item.source, requestId:crypto.randomUUID()};
  sendPayload = null;
  const form = $("send-form"); form.reset(); form.elements.content.readOnly = false;
  $("send-tts-provider").replaceChildren();
  for (const provider of providers.filter(p => p.model_type === "tts" && p.enabled && p.has_api_key)) {
    const option = element("option", provider.name + " · " + provider.model_name); option.value = provider.id;
    $("send-tts-provider").append(option);
  }
  $("send-tts-generate").disabled = false;
  $("send-tts-result").textContent = "";
  form.elements.message_kind.disabled = false; form.elements.media_file.disabled = false;
  updateSendKind();
  $("send-submit").disabled = false; $("send-submit").textContent = "立即发送";
  $("send-result").textContent = "";
  $("send-target").textContent = `${botLabel(item.bot_id)} → ${item.source === "qq_group" ? "整个群：" + item.group_id : "私聊用户：" + (item.contact_name || "未命名") + " · " + item.user_id}`;
  $("send-dialog").showModal();
}
const openSend = button("主动发送消息", () => composeMessage(detailRecord));
openSend.id = "open-send"; openSend.hidden = true; $("detail-dialog").append(openSend);
$("send-form").onsubmit = async event => {
  event.preventDefault();
  const target = sendTarget, form = event.currentTarget;
  if (!target || sendPending) return;
  if (!sendPayload) {
    const kind = form.elements.message_kind.value;
    if (kind === "text") {
      if (!form.elements.content.value.trim()) return;
      sendPayload = {kind, content:form.elements.content.value.trim()};
    } else {
      const file = generatedSpeech || form.elements.media_file.files[0];
      if (!file || !file.size || file.size > MAX_MEDIA_BYTES) { $("send-result").textContent = "请选择非空且不超过 3.5 MB 的文件"; return; }
      const allowed = kind === "image" ? /\.(png|jpe?g)$/i : target.source === "qq_group" ? /\.(silk|mp3|wav)$/i : /\.(silk|wav)$/i;
      if (!allowed.test(file.name)) { $("send-result").textContent = "文件格式不适用于此消息类型或会话，请按上方说明选择。"; return; }
      sendPayload = {kind, file};
    }
  }
  const payload = sendPayload, submit = $("send-submit");
  submit.disabled = true; form.elements.content.readOnly = true;
  form.elements.message_kind.disabled = true; form.elements.media_file.disabled = true;
  sendPending = true;
  $("send-tts-generate").disabled = true;
  const closeButtons = [...$("send-dialog").querySelectorAll("[data-close]")]; closeButtons.forEach(node => node.disabled = true);
  $("send-result").textContent = payload.kind === "text" ? "正在发送，请稍候…" :
    target.source === "qq_c2c" && /\.wav$/i.test(payload.file.name)
      ? "正在转为 SILK、上传并请求 QQ 发送，请稍候…" : "正在上传文件并请求 QQ 发送，请稍候…";
  try {
    const base = `/bots/${target.botId}/records/${target.recordId}`;
    const result = payload.kind === "text" ? await api(base + "/send", {
      method:"POST", body:JSON.stringify({content:payload.content, request_id:target.requestId})
    }) : await api(base + "/send-media?" + new URLSearchParams({kind:payload.kind, filename:payload.file.name, request_id:target.requestId}), {
      method:"POST", headers:{"Content-Type":"application/octet-stream"}, body:payload.file
    });
    $("send-result").textContent = `记录 #${result.record_id} · ${result.status === "replied" ? "QQ 已确认发送" : labels[result.status] || result.status}。${result.message || ""}${result.duplicate ? "此请求已记录，未重复发送。" : ""}`;
    if (["pending", "processing", "delivery_unknown"].includes(result.status)) $("send-result").textContent += " 可能已送达或处理中，请先核对 QQ 和聊天记录，不要新建相同消息。";
    void loadRecords().catch(e => toast(e.message));
  } catch(e) {
    $("send-result").textContent = e.message + "。请求结果未确认；再次提交沿用同一请求标识和文件。若需更换内容，请关闭后重新打开。";
    submit.textContent = "重新提交同一请求"; submit.disabled = false;
  } finally { sendPending = false; closeButtons.forEach(node => node.disabled = false); }
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

let identitiesPage = 1, identitiesRequest = 0;
async function loadIdentityBindings() {
  const request = ++identitiesRequest, params = new URLSearchParams({page:String(identitiesPage)});
  if ($("identity-bot-filter").value) params.set("bot_id", $("identity-bot-filter").value);
  if ($("identity-user-filter").value.trim()) params.set("user_id", $("identity-user-filter").value.trim());
  $("identities-body").replaceChildren(); $("identities-empty").hidden = false;
  $("identities-empty").textContent = "正在加载…";
  $("identity-count").textContent = ""; $("identity-page").textContent = "";
  $("identity-prev").disabled = true; $("identity-next").disabled = true;
  try {
    const data = await api("/identity-bindings?" + params);
    if (request !== identitiesRequest) return;
    $("identity-count").textContent = `共 ${data.total} 个用户（同一机器人内按 OpenID 合并）`;
    $("identity-page").textContent = `第 ${data.page} / ${Math.max(1, Math.ceil(data.total / data.page_size))} 页`;
    $("identity-prev").disabled = data.page <= 1;
    $("identity-next").disabled = data.page * data.page_size >= data.total;
    $("identities-empty").hidden = data.items.length > 0;
    $("identities-empty").textContent = "没有匹配的用户，请先让用户私聊此机器人或在群里 @机器人。";
    for (const item of data.items) {
      const row = element("tr"), bot = element("td", botLabel(item.bot_id)); bot.dataset.botLabel = item.bot_id;
      const actions = element("td"); actions.append(button(item.family_role ? "编辑身份" : "绑定身份", () => editIdentityBinding(item)));
      if (item.family_role) actions.append(button("解除身份", async () => {
        if (!confirm(`解除 ${botLabel(item.bot_id)} 下 ${item.member_name} 的身份？所有同 OpenID 群聊和私聊都会生效。`)) return;
        await api(`/bots/${item.bot_id}/records/${item.record_id}/binding`, {method:"DELETE"});
        toast("身份已解除，下次对话生效"); await loadIdentityBindings();
      }));
      row.append(bot, element("td", item.user_id), element("td", item.member_name || "未绑定"),
        element("td", ({father:"爸爸",mother:"妈妈",friend:"朋友"})[item.family_role] || "—"),
        element("td", item.qq_number_note || "—"), actions);
      $("identities-body").append(row);
    }
  } catch(e) { if (request === identitiesRequest) $("identities-empty").textContent = "加载失败：" + e.message; }
}
function editIdentityBinding(item = null) {
  const form = $("identity-form"); form.reset(); $("identity-error").textContent = "";
  const values = item || {bot_id:$("identity-bot-filter").value, user_id:$("identity-user-filter").value.trim(), family_role:"friend"};
  for (const key of ["bot_id", "user_id", "member_name", "family_role", "qq_number_note"]) form.elements[key].value = values[key] || (key === "family_role" ? "friend" : "");
  form.elements.bot_id.disabled = Boolean(item); form.elements.user_id.readOnly = Boolean(item);
  $("identity-dialog").showModal();
}
$("new-identity-binding").onclick = () => editIdentityBinding();
$("identity-filter").onsubmit = event => { event.preventDefault(); identitiesPage = 1; void loadIdentityBindings(); };
$("identity-bot-filter").onchange = () => { identitiesPage = 1; void loadIdentityBindings(); };
$("identity-prev").onclick = () => { identitiesPage--; void loadIdentityBindings(); };
$("identity-next").onclick = () => { identitiesPage++; void loadIdentityBindings(); };
$("identity-form").onsubmit = async event => {
  event.preventDefault();
  const form = event.currentTarget, submit = form.querySelector('[type="submit"]'), values = Object.fromEntries(new FormData(form));
  const botId = form.elements.bot_id.value; delete values.bot_id;
  submit.disabled = true; $("identity-error").textContent = "";
  try {
    await api(`/bots/${botId}/identity-bindings`, {method:"PUT", body:JSON.stringify(values)});
    $("identity-dialog").close(); toast("身份已保存，所有同 OpenID 会话下次对话生效"); await loadIdentityBindings();
  } catch(e) { $("identity-error").textContent = e.message; }
  finally { submit.disabled = false; }
};

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
