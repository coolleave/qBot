"use strict";
// 独立统计页面；复用后台的鉴权请求和安全 DOM 文本渲染。
let tokenStatsRequest = 0, tokenOptionsRequest = 0, tokenOptionsPage = 1;
let tokenInitialized = false;
const tokenForm = $("token-filter");

function resetTokenDates() {
  const today = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
  $("token-end").value = today;
  $("token-start").value = new Date(Date.parse(today + "T00:00:00Z") - 29 * 86400000).toISOString().slice(0, 10);
}
function invalidateTokenStats() {
  tokenStatsRequest++;
  $("token-results").hidden = true;
  $("token-status").textContent = "筛选已变更，点击应用筛选查看统计。";
}
function conversationLabel(item) {
  const source = {qq_group:"群聊", qq_c2c:"私聊", local:"本地"}[item.source] || item.source;
  return `#${item.id ?? item.conversation_id} · ${botLabel(item.bot_id)} · ${source}${item.contact_name ? " · " + item.contact_name : ""}${item.source === "qq_group" ? " · 群 " + item.group_id : ""} · 用户 ${item.user_id}`;
}
function replaceTokenOptions(select, placeholder, entries, preserve = true) {
  const selected = select.value;
  select.replaceChildren(new Option(placeholder, ""), ...entries);
  if (preserve && Array.from(select.options).some(option => option.value === selected)) select.value = selected;
}
async function loadTokenOptions() {
  const request = ++tokenOptionsRequest;
  const params = new URLSearchParams({page:String(tokenOptionsPage), search:$("token-search").value.trim()});
  if ($("token-bot").value) params.set("bot_id", $("token-bot").value);
  $("token-options-info").textContent = "正在加载会话…";
  $("token-options-prev").disabled = true;
  $("token-options-next").disabled = true;
  try {
    const data = await api("/token-stats/options?" + params);
    if (request !== tokenOptionsRequest) return false;
    const selected = $("token-conversation").selectedOptions[0];
    const retained = selected?.value ? new Option(selected.textContent, selected.value) : null;
    replaceTokenOptions($("token-bot"), "全部机器人", data.bots.map(item => new Option(botLabelFromOption(item), item.id)));
    replaceTokenOptions($("token-model"), "全部历史模型", data.models.map(name => new Option(name, name)));
    const entries = data.conversations.map(item => new Option(conversationLabel(item), item.id));
    // 翻页或搜索不会悄悄清空已经选择的会话。
    if (retained && !entries.some(option => option.value === retained.value)) entries.unshift(retained);
    replaceTokenOptions($("token-conversation"), "全部对话", entries);
    $("token-options-info").textContent = `共 ${data.total} 个匹配对话 · 第 ${data.page} / ${Math.max(1, Math.ceil(data.total / data.page_size))} 页`;
    $("token-options-prev").disabled = data.page <= 1;
    $("token-options-next").disabled = data.page * data.page_size >= data.total;
    return true;
  } catch (error) {
    if (request === tokenOptionsRequest) $("token-options-info").textContent = "会话加载失败：" + error.message;
    return false;
  }
}
function botLabelFromOption(item) {
  return `${botNames.get(Number(item.id)) || item.name} #${item.id}`;
}
async function openTokenStats() {
  if (!tokenInitialized) { resetTokenDates(); tokenInitialized = true; }
  invalidateTokenStats();
  if (await loadTokenOptions()) await loadTokenStats();
  else $("token-status").textContent = "筛选选项加载失败，请刷新重试。";
}
async function loadTokenStats() {
  const request = ++tokenStatsRequest;
  $("token-results").hidden = true;
  if (!tokenForm.reportValidity()) return;
  const params = new URLSearchParams();
  for (const [name, value] of new FormData(tokenForm)) if (value) params.set(name, value);
  $("token-status").textContent = "正在汇总用量…";
  try {
    const data = await api("/token-stats?" + params);
    if (request !== tokenStatsRequest) return;
    renderTokenStats(data);
    $("token-results").hidden = false;
    $("token-status").textContent = `${data.start} 至 ${data.end}（北京时间） · ${data.summary.records} 条记录` +
      (data.summary.records === 0 ? " · 当前范围没有记录。" : data.summary.known_records === 0 ? " · 当前范围没有已知总用量。" : " · 图表已更新，点击排行可继续筛选。");
  } catch (error) {
    if (request === tokenStatsRequest) $("token-status").textContent = "统计加载失败：" + error.message;
  }
}
function renderTokenStats(data) {
  const summary = data.summary;
  $("token-cards").replaceChildren();
  const cards = [["已知总 Token", tokenNumber(summary.total_tokens)], ["已知输入 Token", tokenNumber(summary.input_tokens)],
    ["已知输出 Token", tokenNumber(summary.output_tokens)], ["用量记录覆盖率", summary.records ? `${(summary.known_records / summary.records * 100).toFixed(1)}%` : "—"]];
  for (const [title, value] of cards) {
    const card = element("div", undefined, "card token-metric");
    card.append(element("p", title, "muted"), element("strong", value));
    if (title === "用量记录覆盖率") card.append(element("small", `${summary.known_records} / ${summary.records} 条有总用量（含未调用模型记录）`));
    $("token-cards").append(card);
  }
  const days = new Map(data.daily.map(day => [day.day, day]));
  const daily = [];
  for (let cursor = Date.parse(data.start + "T00:00:00Z"), end = Date.parse(data.end + "T00:00:00Z"); cursor <= end; cursor += 86400000) {
    const day = new Date(cursor).toISOString().slice(0, 10);
    daily.push(days.get(day) || {day, records:0, known_records:0, input_tokens:null, output_tokens:null, total_tokens:null});
  }
  renderTokenTrend(daily);
  renderTokenRank("token-bot-rank", data.bots, item => botLabel(item.bot_id), async item => {
    $("token-bot").value = String(item.bot_id); $("token-conversation").value = "";
    tokenOptionsPage = 1; $("token-search").value = ""; invalidateTokenStats();
    if (await loadTokenOptions()) await loadTokenStats();
  });
  renderTokenRank("token-model-rank", data.models, item => item.model_name_snapshot || "未记录模型", async item => {
    if (!item.model_name_snapshot) return;
    $("token-model").value = item.model_name_snapshot; await loadTokenStats();
  });
  renderTokenRank("token-conversation-rank", data.conversations, conversationLabel, async item => {
    const select = $("token-conversation"), id = String(item.conversation_id);
    if (!Array.from(select.options).some(option => option.value === id)) select.add(new Option(conversationLabel(item), id));
    select.value = id; await loadTokenStats();
  });
}
function renderTokenRank(id, items, label, action) {
  const root = $(id); root.replaceChildren();
  if (!items.length) { root.append(element("p", "当前范围暂无记录", "muted")); return; }
  const max = Math.max(1, ...items.map(item => item.total_tokens ?? 0));
  for (const item of items) {
    const row = element("div", undefined, "token-rank-row");
    const name = button(label(item), () => action(item)); name.classList.add("token-rank-name");
    name.title = label(item);
    if (id === "token-model-rank" && !item.model_name_snapshot) name.disabled = true;
    const track = element("div", undefined, "token-track"), bar = element("div", undefined, "token-bar");
    bar.style.width = `${(item.total_tokens ?? 0) / max * 100}%`; track.append(bar);
    row.append(name, element("span", `${tokenNumber(item.total_tokens)} token`, "token-rank-value"), track,
      element("small", `输入 ${tokenNumber(item.input_tokens)} · 输出 ${tokenNumber(item.output_tokens)} · ${item.known_records}/${item.records} 条有总量`, "muted"));
    root.append(row);
  }
}
function renderTokenTrend(days) {
  const root = $("token-trend"); root.replaceChildren(); $("token-days").replaceChildren();
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  const width = Math.max(800, days.length * 18), height = 240, left = 75, plotHeight = 155;
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`); svg.style.minWidth = width + "px";
  svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "每日已知总 Token 柱状图，详细数值见下方每日数值表");
  function shape(tag, attrs, text) {
    const node = document.createElementNS(svgNS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    if (text !== undefined) node.textContent = text;
    svg.append(node); return node;
  }
  const max = Math.max(1, ...days.map(day => day.total_tokens ?? 0));
  for (const fraction of [0, 0.5, 1]) {
    const y = 20 + plotHeight * (1 - fraction);
    shape("line", {x1:left, x2:width - 15, y1:y, y2:y, stroke:"#e1e7e4"});
    shape("text", {x:left - 8, y:y + 4, "text-anchor":"end", fill:"#75827d", "font-size":11}, Math.round(max * fraction).toLocaleString());
  }
  const step = (width - left - 20) / days.length;
  days.forEach((day, index) => {
    const x = left + index * step + step * 0.15;
    const value = day.total_tokens, size = value == null ? 0 : value / max * plotHeight;
    const rect = shape("rect", {x, y:20 + plotHeight - size, width:step * 0.7, height:Math.max(2, size),
      fill:value == null ? (day.records ? "#c7a46b" : "#dce4df") : "#167660"});
    const title = document.createElementNS(svgNS, "title");
    title.textContent = `${day.day}：${day.records ? tokenNumber(value) + " token" : "无记录"}；${day.known_records}/${day.records} 条有总量`; rect.append(title);
    if (index % Math.max(1, Math.ceil(days.length / 12)) === 0 || index === days.length - 1)
      shape("text", {x, y:198, fill:"#75827d", "font-size":11}, day.day.slice(5));
    const row = element("tr");
    for (const text of [day.day, ...[day.input_tokens, day.output_tokens, day.total_tokens].map(v => day.records ? tokenNumber(v) : "无记录"), `${day.known_records}/${day.records}`]) row.append(element("td", text));
    $("token-days").append(row);
  });
  root.append(svg, element("p", "绿色：已知用量（含0） · 金色：有记录但总量未知 · 灰色：无记录。柱形只表示已知部分；可横向滚动并悬停查看数值。", "muted"));
}
tokenForm.onchange = invalidateTokenStats;
tokenForm.onsubmit = event => { event.preventDefault(); void loadTokenStats(); };
$("token-bot").onchange = async event => {
  event.stopPropagation(); invalidateTokenStats();
  $("token-conversation").replaceChildren(new Option("全部对话", ""));
  $("token-model").replaceChildren(new Option("全部历史模型", ""));
  $("token-search").value = ""; tokenOptionsPage = 1;
  await loadTokenOptions();
};
$("token-refresh").onclick = () => { void openTokenStats(); };
$("token-reset").onclick = () => {
  tokenForm.reset(); resetTokenDates(); $("token-search").value = ""; tokenOptionsPage = 1;
  void openTokenStats();
};
$("token-search-button").onclick = () => { tokenOptionsPage = 1; void loadTokenOptions(); };
$("token-search").onkeydown = event => { if (event.key === "Enter") { event.preventDefault(); $("token-search-button").click(); } };
$("token-options-prev").onclick = () => { tokenOptionsPage--; void loadTokenOptions(); };
$("token-options-next").onclick = () => { tokenOptionsPage++; void loadTokenOptions(); };
