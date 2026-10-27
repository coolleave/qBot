"use strict";
// 对话页只消费已保存的记录，发送仍走原有主动发送与幂等接口。
let sendTargetsPage = 1, sendTargetsRequest = 0;
let chatTarget = null, chatRows = [], chatHistoryRequest = 0, chatHistoryBusy = false;
let chatHasMore = false, chatPollingPaused = false, chatSending = false, chatScope = "";
const chatDrafts = new Map(), chatTabsState = new Map();
const chatKey = item => `${item.bot_id}:${item.source}:${item.source === "qq_group" ? item.group_id : item.user_id}`;
const chatScopeKey = () => `${$("send-bot").value}:${$("send-source").value}`;
function chatDraft(item = chatTarget) {
  const key = chatKey(item);
  if (!chatDrafts.has(key)) chatDrafts.set(key, {text:"", pending:null, result:""});
  return chatDrafts.get(key);
}
function chatName(item) { return item.source === "qq_group" ? item.group_remark || "群聊 · " + item.group_id.slice(-6) : item.user_remark || item.platform_nickname || item.bound_name || item.contact_name || "用户 · " + item.user_id.slice(-6); }
// OpenID 必须与所属机器人的 AppID 配对，不能使用 QQ 号或群 ID 代替。
function chatUserAvatarUrl(item) {
  const appId = botAppIds.get(Number(item.bot_id));
  if (!appId || !item.user_id) return "";
  return `https://q.qlogo.cn/qqapp/${encodeURIComponent(appId)}/${encodeURIComponent(item.user_id)}/640`;
}
function chatAvatar(url, name, className) {
  const node = element("span", Array.from(name || "用户")[0], className);
  node.setAttribute("aria-hidden", "true");
  if (!url) return node;
  const img = element("img");
  img.alt = ""; img.referrerPolicy = "no-referrer"; img.decoding = "async";
  // 保留固定尺寸占位，加载失败时不显示破图，也不递归重试。
  img.onload = () => node.replaceChildren(img);
  img.onerror = () => { img.remove(); };
  img.src = url;
  return node;
}
function chatBotAvatar(botId) {
  const node = chatAvatar(botAvatarUrls.get(Number(botId)), botNames.get(Number(botId)) || "机器人", "chat-message-avatar");
  node.dataset.chatBotAvatar = botId;
  return node;
}
function clearChatSelection() {
  chatTarget = null; chatRows = []; chatHistoryRequest++; chatHistoryBusy = false;
  $("chat-active").hidden = true; $("chat-welcome").hidden = false;
  $("chat-message-list").replaceChildren();
}
function updateChatComposer() {
  if (!chatTarget) return;
  const draft = chatDraft();
  $("chat-text").value = draft.pending ? draft.pending.content : draft.text;
  $("chat-text").readOnly = chatSending || Boolean(draft.pending);
  $("chat-send").disabled = chatSending;
  $("chat-send").textContent = chatSending ? "发送中…" : draft.pending ? "核对本次结果" : "发送 ↑";
  $("chat-new-message").hidden = !draft.pending;
  $("chat-new-message").disabled = chatSending;
  $("chat-media").disabled = chatSending || Boolean(draft.pending);
  $("chat-send-result").textContent = draft.result;
}
async function selectChat(item) {
  if (chatTarget && chatKey(chatTarget) === chatKey(item)) { chatTarget = item; renderChatHeading(); return; }
  chatTarget = item; chatRows = []; chatHasMore = false; chatPollingPaused = false;
  chatHistoryRequest++; chatHistoryBusy = false;
  $("chat-welcome").hidden = true; $("chat-active").hidden = false;
  $("chat-message-list").replaceChildren(); $("chat-load-older").hidden = true;
  renderChatHeading(); updateChatComposer();
  document.querySelectorAll(".chat-session").forEach(node => node.setAttribute("aria-current", String(node.dataset.chatKey === chatKey(item))));
  await loadChatHistory();
}
function renderChatHeading() {
  $("chat-title").textContent = chatName(chatTarget);
  $("chat-subtitle").textContent = `${botLabel(chatTarget.bot_id)} · ${chatTarget.source === "qq_group" ? "群 " + chatTarget.group_id : "用户 " + chatTarget.user_id}`;
  $("chat-group-note").hidden = chatTarget.source !== "qq_group";
  $("chat-send-scope").textContent = chatTarget.source === "qq_group" ? "以机器人身份发送给整个群" : "以机器人身份发送给该用户";
}
async function loadSendTargets() {
  const request = ++sendTargetsRequest, botId = $("send-bot").value, source = $("send-source").value;
  if (chatScope !== chatScopeKey()) { chatScope = chatScopeKey(); clearChatSelection(); }
  $("send-targets-body").replaceChildren(); $("send-targets-empty").hidden = false;
  $("send-targets-empty").textContent = botId ? "正在加载会话…" : "请选择机器人。";
  $("send-target-count").textContent = ""; $("send-target-page").textContent = "";
  $("send-target-prev").disabled = true; $("send-target-next").disabled = true;
  if (!botId) { clearChatSelection(); return; }
  try {
    const query = new URLSearchParams({source, page:String(sendTargetsPage), search:$("chat-search").value.trim()});
    const data = await api(`/bots/${botId}/send-targets?${query}`);
    if (request !== sendTargetsRequest || chatScopeKey() !== `${botId}:${source}`) return;
    $("send-target-count").textContent = `${data.total} 个${source === "qq_group" ? "群聊" : "私聊"}`;
    $("send-target-page").textContent = `${data.page} / ${Math.max(1, Math.ceil(data.total / data.page_size))}`;
    $("send-target-prev").disabled = data.page <= 1;
    $("send-target-next").disabled = data.page * data.page_size >= data.total;
    $("send-targets-empty").hidden = Boolean(data.items.length);
    $("send-targets-empty").textContent = $("chat-search").value.trim() ? "没有匹配的会话。" : "暂无记录，等待用户私聊或在群里 @机器人。";
    for (const item of data.items) {
      const node = button("", () => selectChat(item)); node.className = "chat-session";
      node.dataset.chatKey = chatKey(item);
      node.setAttribute("aria-current", String(Boolean(chatTarget && chatKey(chatTarget) === chatKey(item))));
      const avatar = chatAvatar(item.source === "qq_group" ? "" : chatUserAvatarUrl(item), item.source === "qq_group" ? "群" : chatName(item), "chat-session-avatar");
      const info = element("span", undefined, "chat-session-info");
      info.append(element("strong", chatName(item)), element("span", item.preview, "chat-session-preview"), element("small", date(item.received_at)));
      node.append(avatar, info); $("send-targets-body").append(node);
    }
    const selected = data.items.find(item => chatTarget && chatKey(item) === chatKey(chatTarget)) || data.items[0];
    if (selected) await selectChat(selected); else clearChatSelection();
  } catch(e) {
    if (request === sendTargetsRequest) { $("send-targets-empty").textContent = "加载失败：" + e.message; clearChatSelection(); }
  }
}
function chatAtBottom() {
  const node = $("chat-timeline"); return node.scrollHeight - node.scrollTop - node.clientHeight < 100;
}
function appendChatBubble(row, outgoing, content) {
  const article = element("article", undefined, outgoing ? "chat-message outgoing" : "chat-message incoming");
  const avatar = outgoing ? chatBotAvatar(row.bot_id) : chatAvatar(chatUserAvatarUrl(row), row.user_remark || row.platform_nickname || row.bound_name || row.contact_name || "用户", "chat-message-avatar");
  const body = element("div", undefined, "chat-message-body");
  const sender = outgoing ? botLabel(row.bot_id) : row.user_remark || row.platform_nickname || row.bound_name || row.contact_name || row.user_id;
  const origin = outgoing ? ({manual:" · 后台发送", scheduled:" · 定时发送", incoming:" · 机器人回复"})[row.origin] : "";
  const voiceOutput = outgoing && (row.reply_kind === "audio" || row.media_kinds.includes("audio"));
  if (!outgoing && row.voice_input) {
    const original = (row.question || "").replace(/\n?【附件 \d+ 个】$/, "").trim();
    content = (original ? original + "\n" : "") + "语音消息：" + (row.asr_text || "未识别到可用文本");
  } else if (voiceOutput) {
    // 旧自动/定时记录有正文，旧手动音频只有文件摘要，不能当成合成文本。
    content = "语音消息：" + (row.tts_text || (row.origin !== "manual" ? row.answer : "") || "未保存语音文本");
  }
  body.append(element("div", sender + origin, "chat-message-sender"), element("div", content, "chat-bubble"));
  if (outgoing && (row.reply_kind === "audio" || row.media_kinds.length)) {
    body.append(element("small", "含" + [...new Set([...(row.reply_kind === "audio" ? ["audio"] : []), ...row.media_kinds])].map(k => k === "audio" ? "语音" : "图片").join(" / ") + " · 历史原文件未保存", "chat-media-note"));
  }
  const status = outgoing ? " · " + (row.status === "replied" ? "已发送" : labels[row.status] || row.status) : "";
  const meta = element("small", date(outgoing ? row.updated_at : row.received_at) + status + ` · #${row.id}`, "chat-message-meta");
  if (outgoing && row.status !== "replied") meta.classList.add("chat-status-alert");
  body.append(meta);
  if (outgoing && row.error_message) body.append(element("p", row.error_message, "chat-message-error"));
  article.append(avatar, body); $("chat-message-list").append(article);
}
function renderChatRows() {
  $("chat-message-list").replaceChildren();
  for (const row of chatRows) {
    if (row.origin === "incoming") appendChatBubble(row, false, row.question || "[无文字内容]");
    if (row.answer || row.origin !== "incoming" || row.status !== "replied") {
      appendChatBubble(row, true, row.answer || (["pending","processing"].includes(row.status) ? "正在处理…" : "没有已保存的回复内容"));
    }
  }
}
async function loadChatHistory({older = false, poll = false} = {}) {
  if (!chatTarget || (poll && chatHistoryBusy)) return;
  const target = chatTarget, key = chatKey(target), request = ++chatHistoryRequest;
  const timeline = $("chat-timeline"), oldHeight = timeline.scrollHeight, oldTop = timeline.scrollTop;
  const wasAtBottom = chatAtBottom(); chatHistoryBusy = true;
  $("chat-load-older").disabled = true;
  if (!poll) $("chat-history-notice").textContent = "正在读取消息…";
  try {
    const before = older && chatRows.length ? "?" + new URLSearchParams({before_id:String(chatRows[0].id)}) : "";
    const data = await api(`/bots/${target.bot_id}/records/${target.id}/messages${before}`);
    if (request !== chatHistoryRequest || !chatTarget || chatKey(chatTarget) !== key) return;
    if (older) {
      const rows = new Map([...data.items, ...chatRows].map(row => [row.id,row]));
      chatRows = [...rows.values()].sort((a,b)=>a.id-b.id); chatHasMore = data.has_more;
    } else if (poll && chatRows.length && data.items.some(row => row.id === chatRows[chatRows.length-1].id)) {
      const rows = new Map([...chatRows, ...data.items].map(row => [row.id,row]));
      chatRows = [...rows.values()].sort((a,b)=>a.id-b.id);
    } else { chatRows = data.items; chatHasMore = data.has_more; }
    renderChatRows(); chatPollingPaused = false;
    $("chat-history-notice").textContent = chatRows.length ? "仅显示已保存的消息；历史媒体以摘要展示。" : "这段会话还没有可显示的消息。";
    $("chat-load-older").hidden = !chatHasMore;
    if (older) timeline.scrollTop = oldTop + timeline.scrollHeight - oldHeight;
    else if (!poll || wasAtBottom) timeline.scrollTop = timeline.scrollHeight;
  } catch(e) {
    if (request === chatHistoryRequest) { chatPollingPaused = true; $("chat-history-notice").textContent = "消息加载失败：" + e.message + "。请点击刷新消息重试。"; }
  } finally {
    if (request === chatHistoryRequest) { chatHistoryBusy = false; $("chat-load-older").disabled = false; }
  }
}
$("chat-text").oninput = () => { if (chatTarget && !chatDraft().pending) chatDraft().text = $("chat-text").value; };
$("chat-text").onkeydown = event => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault(); if (!chatSending) $("chat-composer").requestSubmit();
  }
};
$("chat-composer").onsubmit = async event => {
  event.preventDefault(); if (!chatTarget || chatSending || sendPending) return;
  const target = {...chatTarget}, key = chatKey(target), draft = chatDraft(target);
  if (!draft.pending) {
    const content = $("chat-text").value.trim(); if (!content) return;
    draft.text = content; draft.pending = {requestId:createRequestId(),content};
  }
  const pending = draft.pending;
  chatSending = true; draft.result = "正在发送，请稍候…"; updateChatComposer();
  try {
    const result = await api(`/bots/${target.bot_id}/records/${target.id}/send`, {method:"POST",
      body:JSON.stringify({content:pending.content,request_id:pending.requestId})});
    draft.result = `#${result.record_id} · ${result.status === "replied" ? "QQ 已确认发送" : labels[result.status] || result.status}。${result.message || ""}`;
    if (result.status === "replied") { draft.text = ""; draft.pending = null; }
    else draft.result += " 核对本次结果会沿用原请求，不会再次发送；要另发消息请点击开始新消息。";
    if (chatTarget && chatKey(chatTarget) === key) await loadChatHistory();
  } catch(e) {
    draft.result = e.message + "。结果未确认，内容与请求标识已保留；请先核对本次结果，避免重复发送。";
  } finally {
    chatSending = false;
    if (chatTarget) updateChatComposer();
  }
};
$("chat-new-message").onclick = () => {
  if (!chatTarget || chatSending) return;
  if (!confirm("上一条消息可能已送达，请先核对历史或 QQ。开始新消息会使用新的请求标识，确定继续？")) return;
  chatDrafts.set(chatKey(chatTarget), {text:"",pending:null,result:""}); updateChatComposer(); $("chat-text").focus();
};
$("chat-media").onclick = () => {
  if (!chatTarget || chatSending || sendPending) return;
  composeMessage(chatTarget);
  $("send-form").elements.message_kind.value = "image"; updateSendKind();
};
$("chat-group-note").onclick = () => { if (chatTarget) editGroupNote(chatTarget); };
$("chat-refresh").onclick = () => { void loadChatHistory(); };
$("chat-load-older").onclick = () => { void loadChatHistory({older:true}); };
function changeChatScope(source) {
  chatTabsState.set(chatScopeKey(), {page:sendTargetsPage, search:$("chat-search").value});
  $("send-source").value = source;
  const saved = chatTabsState.get(chatScopeKey());
  sendTargetsPage = saved?.page || 1; $("chat-search").value = saved?.search || "";
  for (const [id,value] of [["chat-private-tab","qq_c2c"],["chat-group-tab","qq_group"]]) {
    const selected = value === source; $(id).setAttribute("aria-selected", String(selected)); $(id).tabIndex = selected ? 0 : -1;
  }
  $("chat-session-panel").setAttribute("aria-labelledby", source === "qq_c2c" ? "chat-private-tab" : "chat-group-tab");
  void loadSendTargets();
}
for (const [id,source] of [["chat-private-tab","qq_c2c"],["chat-group-tab","qq_group"]]) {
  $(id).onclick = () => changeChatScope(source);
  $(id).onkeydown = event => {
    if (!["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? "qq_c2c" : event.key === "End" ? "qq_group" : source === "qq_c2c" ? "qq_group" : "qq_c2c";
    changeChatScope(next); $(next === "qq_c2c" ? "chat-private-tab" : "chat-group-tab").focus();
  };
}
$("send-bot").onchange = () => { sendTargetsPage = 1; $("chat-search").value = ""; void loadSendTargets(); };
$("chat-search-form").onsubmit = event => { event.preventDefault(); sendTargetsPage = 1; void loadSendTargets(); };
$("refresh-send-targets").onclick = () => { void loadSendTargets(); if (chatTarget) void loadChatHistory(); };
$("send-target-prev").onclick = () => { sendTargetsPage--; void loadSendTargets(); };
$("send-target-next").onclick = () => { sendTargetsPage++; void loadSendTargets(); };
document.addEventListener("qbot-message-sent", event => {
  if (chatTarget && Number(chatTarget.bot_id) === Number(event.detail.botId)) void loadChatHistory();
});
document.addEventListener("qbot-profile-loaded", event => {
  document.querySelectorAll("[data-chat-bot-avatar]").forEach(node => {
    if (Number(node.dataset.chatBotAvatar) === Number(event.detail.botId)) node.replaceWith(chatBotAvatar(event.detail.botId));
  });
});
document.addEventListener("qbot-logout", () => { botAppIds.clear(); botAvatarUrls.clear(); chatDrafts.clear(); chatTabsState.clear(); sendTargetsRequest++; clearChatSelection(); });
setInterval(() => {
  if (ui.page === "messages" && !$("workspace").hidden && !document.hidden && chatTarget && !chatPollingPaused && !chatHistoryBusy && chatAtBottom()) void loadChatHistory({poll:true});
}, 10000);
