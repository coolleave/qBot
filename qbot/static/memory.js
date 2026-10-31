"use strict";
(() => {
  const section = $("memory-page");
  section.innerHTML = `
    <div class="page-heading"><div><h1>记忆管理</h1><p class="muted">保留有用的信息，按预算异步整理。群聊默认以主人发言为主要来源。</p></div></div>
    <div class="memory-controls memory-toolbar"><label>机器人<select id="memory-bot"></select></label><button id="memory-refresh" type="button">刷新</button></div>
    <p id="memory-error" class="error" role="alert"></p>
    <div class="memory-layout"><aside class="memory-sidebar" aria-label="记忆配置与用量"><form id="memory-config" class="memory-panel"><fieldset id="memory-config-fields">
      <div class="memory-panel-heading"><h2>记忆设置</h2><p class="muted">控制记忆的保存方式与整理节奏</p></div><div class="memory-form-section"><h3>保存与整理</h3><label class="checkbox"><input name="enabled" type="checkbox">启用持久化记忆</label>
      <label class="checkbox"><input name="automatic" type="checkbox">启用自动整理（产生模型费用）</label>
      <label>独立整理模型<select name="provider_id"></select></label>
      <label class="checkbox"><input name="budget_compatible" type="checkbox">已确认模型采用字节级分词并支持输出上限</label>
      <p class="muted">输入按 UTF-8 字节保守预留，实际用量另行统计。不会切换到聊天或备用模型。</p>
      </div><div class="memory-form-section"><h3>每日预算</h3><label>每天最多调用次数<input name="daily_calls" type="number" min="1" max="60" required></label>
      <label>每天 token 配额<input name="daily_tokens" type="number" min="1000" max="180000" required></label>
      </div><div class="memory-form-section"><h3>整理频率</h3><label>每批触发消息数<input name="batch_messages" type="number" min="2" max="20" required></label>
      <label>同会话冷却（分钟）<input name="cooldown_minutes" type="number" min="5" max="1440" required></label>
      <label>闲置整理（分钟）<input name="idle_minutes" type="number" min="5" max="1440" required></label>
      </div><p class="muted memory-hint">全局每天最多60次、180000 token。未知调用保留预算，不自动重试。保存设置会使旧整理任务和近期摘要失效，已有长期记忆保留。</p>
      <button class="primary" type="submit">保存设置</button></fieldset></form>
      <div class="memory-panel"><div class="memory-panel-heading"><h2>今日用量</h2><p class="muted">预算占用与模型实际返回量分开展示</p></div><div id="memory-budget" class="memory-budget" aria-live="polite"></div><p id="memory-paused" class="error"></p></div>
    </aside><div class="memory-main">
      <div class="memory-panel"><div class="memory-panel-heading"><h2>记忆条目</h2><p class="muted">查找、确认和维护每个会话的长期记忆</p></div><div class="memory-controls memory-filters"><label>会话<select id="memory-conversation"></select></label>
        <label>状态<select id="memory-status"><option value="active">有效</option><option value="pending">待确认</option><option value="superseded">已替代</option><option value="forgotten">已遗忘</option><option value="">全部</option></select></label>
        <label>关键词<input id="memory-query" maxlength="100" placeholder="搜索记忆内容"></label><button id="memory-search" type="button">查询</button></div>
        <div class="memory-actions"><button id="memory-new" type="button" class="primary">新增记忆</button><button id="memory-organize" type="button">请求整理</button><button id="memory-backfill" type="button">历史回填</button></div>
        <p class="muted">新增、整理和回填需选择具体会话。回填仅处理最多100条，不绕过遗忘边界或日预算。</p>
        <div id="memory-items" aria-live="polite"></div><div class="memory-actions memory-pagination"><button id="memory-prev" type="button">上一页</button><span id="memory-page-info"></span><button id="memory-next" type="button">下一页</button></div>
      </div>
      <div class="memory-panel"><div class="memory-panel-heading"><h2>整理运行记录</h2><p class="muted">查看最近批次、模型用量与待处理状态</p></div><p id="memory-backlog" class="muted"></p><div id="memory-runs" class="memory-table-wrap"></div><div id="memory-states"></div></div>
    </div></div>`;
  const dialog = document.createElement("dialog");
  dialog.id = "memory-item-dialog";
  dialog.innerHTML = `<form id="memory-item-form"><h2>保存记忆</h2><input name="id" type="hidden"><input name="conversation_id" type="hidden">
    <label>范围<select name="scope"><option value="personal">此会话个人记忆</option><option value="group">当前群共享</option></select></label>
    <label>类别<select name="category"><option value="note">笔记</option><option value="preference">偏好</option><option value="fact">事实</option><option value="goal">目标</option><option value="event">事件</option><option value="request">长期要求</option></select></label>
    <label>事实键（可留空）<input name="fact_key" maxlength="80"></label><label>内容<textarea name="content" maxlength="600" rows="5" required></textarea></label>
    <label>到期时间（本地时间，可留空）<input name="expires_at" type="datetime-local"></label><p class="muted">保存待确认条目表示管理员确认其内容。修改会重新开始相关近期对话。</p>
    <p id="memory-item-error" class="error"></p><div class="dialog-actions"><button id="memory-item-cancel" type="button">取消</button><button type="submit" class="primary">保存</button></div></form>`;
  document.body.append(dialog);
  const bot = $("memory-bot"), conversation = $("memory-conversation"), configForm = $("memory-config"), itemForm = $("memory-item-form");
  let generation = 0, page = 1, total = 0, items = [], editingBot = null;
  const node = (tag, text, cls) => { const value = document.createElement(tag); if (text != null) value.textContent = text; if (cls) value.className = cls; return value; };
  const statusNames = {active:"有效", pending:"待确认", superseded:"已替代", forgotten:"已遗忘", reserved:"已预留", calling:"调用中", result:"待提交", applied:"已完成", failed:"失败", call_unknown:"调用未知", invalidated:"已失效", retry_requested:"已请求重试", cancelled:"已取消"};
  function fail(error) { $("memory-error").textContent = error.message || String(error); }
  function target() { if (!bot.value) throw new Error("请选择机器人"); return `/memory/${bot.value}`; }
  function selectedConversation() { if (!conversation.value) throw new Error("请选择具体会话"); return Number(conversation.value); }
  async function load() {
    const seq = ++generation;
    $("memory-error").textContent = "";
    $("memory-config-fields").disabled = true;
    $("memory-items").replaceChildren(); $("memory-runs").replaceChildren();
    const [botsData, providerData] = await Promise.all([api("/bots"), api("/providers")]);
    if (seq !== generation || section.hidden) return;
    const previous = bot.value;
    bot.replaceChildren(new Option("请选择机器人", ""));
    for (const entry of botsData.items) bot.add(new Option(`${entry.name} #${entry.id}`, entry.id));
    bot.value = botsData.items.some(entry => String(entry.id) === previous) ? previous : String(botsData.items[0]?.id || "");
    if (!bot.value) return;
    const base = target();
    const [cfg, convs] = await Promise.all([api(base + "/config"), api(base + "/conversations")]);
    if (seq !== generation || section.hidden) return;
    for (const key of ["enabled", "automatic", "budget_compatible"]) configForm.elements[key].checked = Boolean(cfg[key]);
    for (const key of ["daily_calls", "daily_tokens", "batch_messages", "cooldown_minutes", "idle_minutes"]) configForm.elements[key].value = cfg[key];
    const select = configForm.elements.provider_id;
    select.replaceChildren(new Option("不使用整理模型", ""));
    for (const entry of providerData.items.filter(value => value.model_type === "chat" && value.enabled)) select.add(new Option(`${entry.name} · ${entry.model_name}`, entry.id));
    select.value = cfg.provider_id || "";
    $("memory-paused").textContent = cfg.paused_reason || "";
    const oldConversation = conversation.value;
    conversation.replaceChildren(new Option("全部会话", ""));
    for (const entry of convs.items) conversation.add(new Option(`#${entry.id} ${entry.source === "qq_group" ? "群 " + entry.group_id : "私聊"} · ${entry.contact_name || entry.platform_nickname || entry.user_id}`, entry.id));
    if (convs.items.some(entry => String(entry.id) === oldConversation)) conversation.value = oldConversation;
    $("memory-config-fields").disabled = false;
    await refreshContent(seq);
  }
  async function refreshContent(seq = generation) {
    const base = target();
    const params = new URLSearchParams({status:$("memory-status").value, page:String(page), query:$("memory-query").value});
    if (conversation.value) params.set("conversation_id", conversation.value);
    const [result, stats] = await Promise.all([api(base + "/items?" + params), api(base + "/status")]);
    if (seq !== generation || section.hidden) return;
    items = result.items; total = result.total;
    const list = $("memory-items"); list.replaceChildren();
    if (!items.length) list.append(node("p", "当前筛选下暂无记忆。可调整筛选，或选择具体会话后新增记忆。", "memory-empty"));
    for (const item of items) {
      const card = node("article", null, "memory-card");
      const heading = node("div", null, "memory-card-heading");
      const tags = node("div", null, "memory-tags");
      const categoryNames = {note:"笔记", preference:"偏好", fact:"事实", goal:"目标", event:"事件", request:"长期要求"};
      tags.append(node("span", statusNames[item.status] || item.status, `memory-tag memory-status-${item.status}`));
      tags.append(node("span", item.scope === "group" ? "群共享" : "个人记忆", "memory-tag"));
      tags.append(node("span", categoryNames[item.category] || item.category || "笔记", "memory-tag"));
      heading.append(tags, node("span", `#${item.id} · 会话 ${item.conversation_id}`, "memory-meta"));
      card.append(heading);
      card.append(node("div", `更新于 ${date(item.updated_at)}`, "memory-meta"));
      card.append(node("p", item.content || "正文已清除"));
      card.append(node("div", `主体：${item.subject_id}；来源：${item.origin}；到期：${item.expires_at ? date(item.expires_at) : "长期"}`, "memory-meta"));
      const actions = node("div", null, "memory-actions");
      if (item.status !== "forgotten") {
        const edit = node("button", item.status === "pending" ? "确认 / 编辑" : "编辑"); edit.type = "button"; edit.onclick = () => editItem(item); actions.append(edit);
        const remove = node("button", "遗忘", "memory-danger"); remove.type = "button"; remove.onclick = async () => {
          try { await ElementPlus.ElMessageBox.confirm("将清除记忆正文并重新开始相关近期对话；群共享记忆会影响全群会话。原始审计聊天记录保留。", "遗忘记忆", {type:"warning"}); }
          catch { return; }
          remove.disabled = true;
          try { await api(base + `/items/${item.id}`, {method:"DELETE"}); await refreshContent(); } catch (error) { fail(error); } finally { remove.disabled = false; }
        }; actions.append(remove);
      }
      for (const id of JSON.parse(item.sources || "[]").slice(-3)) { const source = node("button", `来源 #${id}`); source.type = "button"; source.onclick = () => showDetail(id).catch(fail); actions.append(source); }
      card.append(actions); list.append(card);
    }
    $("memory-page-info").textContent = `第 ${page} 页 · 共 ${total} 条`;
    $("memory-prev").disabled = page <= 1; $("memory-next").disabled = page * 20 >= total;
    const budget = $("memory-budget");
    budget.replaceChildren(node("p", `${stats.date} · 北京时间`, "memory-meta"));
    const metrics = node("div", null, "memory-metrics");
    const number = value => value == null ? "未知" : Number(value).toLocaleString("zh-CN");
    for (const [label, value, detail] of [
      ["今日调用", number(stats.bot.calls), "本机器人 · 次"],
      ["预算占用", number(stats.bot.budget_used), "包含未结算预留 · token"],
      ["实际返回", number(stats.bot.actual_tokens), "供应商返回 · token"],
      ["未知调用", number(stats.bot.unknown_calls), "保留预算 · 次"],
    ]) {
      const metric = node("div", null, "memory-metric");
      metric.append(node("span", label), node("strong", value), node("small", detail));
      metrics.append(metric);
    }
    budget.append(metrics, node("p", `全局调用 ${number(stats.global.calls)} / 60 次 · 预算 ${number(stats.global.budget_used)} / 180,000 token`, "memory-global-budget"));
    $("memory-backlog").textContent = stats.sources.map(entry => `${entry.status}: ${entry.count}`).join(" · ") || "暂无待处理消息";
    const table = node("table"), head = node("tr");
    for (const label of ["批次 / 会话", "时间", "模型", "状态", "实际 token", "说明", "操作"]) head.append(node("th", label));
    table.append(head);
    for (const run of stats.runs) {
      const row = node("tr");
      for (const value of [`#${run.id} / ${run.conversation_id}`, date(run.created_at), run.model_name, statusNames[run.status] || run.status, run.total_tokens ?? "未知", run.error]) row.append(node("td", value));
      const action = node("td");
      if (["failed", "call_unknown"].includes(run.status)) {
        const retry = node("button", "重新排队"); retry.type = "button"; retry.onclick = async () => {
          try { await ElementPlus.ElMessageBox.confirm("此批次可能已经计费，重新调用可能重复收费，仍受当天预算限制。是否重新排队？", "重试整理"); } catch { return; }
          retry.disabled = true;
          try { await api(base + `/runs/${run.id}/retry`, {method:"POST", body:JSON.stringify({accept_possible_charge:true})}); await refreshContent(); } catch (error) { fail(error); } finally { retry.disabled = false; }
        }; action.append(retry);
      }
      row.append(action); table.append(row);
    }
    $("memory-runs").replaceChildren(stats.runs.length ? table : node("p", "暂无整理运行记录。启用自动整理或请求整理后，可在这里查看处理结果。", "memory-empty"));
    $("memory-states").replaceChildren(...stats.states.filter(entry => entry.reason).map(entry => node("p", `会话 #${entry.conversation_id}：${entry.reason}${entry.next_run_at ? "；下次检查 " + date(entry.next_run_at) : ""}`, "muted")));
  }
  function editItem(item) {
    try {
      const cid = item?.conversation_id || selectedConversation();
      editingBot = bot.value;
      itemForm.reset(); $("memory-item-error").textContent = "";
      for (const key of ["id", "content", "fact_key"]) itemForm.elements[key].value = item?.[key] || "";
      itemForm.elements.conversation_id.value = cid;
      itemForm.elements.scope.value = item?.scope || "personal";
      itemForm.elements.category.value = item?.category || "note";
      if (item?.expires_at) { const value = new Date(item.expires_at + (/[zZ]|[+-]\d\d:\d\d$/.test(item.expires_at) ? "" : "Z")); value.setMinutes(value.getMinutes() - value.getTimezoneOffset()); itemForm.elements.expires_at.value = value.toISOString().slice(0,16); }
      dialog.showModal();
    } catch (error) { fail(error); }
  }
  itemForm.onsubmit = async event => {
    event.preventDefault(); const submit = itemForm.querySelector('[type="submit"]'); submit.disabled = true;
    const data = Object.fromEntries(new FormData(itemForm)), id = data.id; delete data.id;
    data.conversation_id = Number(data.conversation_id); data.expires_at = data.expires_at ? new Date(data.expires_at).toISOString() : null;
    try { await api(`/memory/${editingBot}/items` + (id ? `/${id}` : ""), {method:id ? "PUT" : "POST", body:JSON.stringify(data)}); dialog.close(); await refreshContent(); }
    catch (error) { $("memory-item-error").textContent = error.message; } finally { submit.disabled = false; }
  };
  configForm.onsubmit = async event => {
    event.preventDefault(); const fields = $("memory-config-fields"), data = {};
    for (const key of ["enabled", "automatic", "budget_compatible"]) data[key] = configForm.elements[key].checked;
    for (const key of ["daily_calls", "daily_tokens", "batch_messages", "cooldown_minutes", "idle_minutes"]) data[key] = Number(configForm.elements[key].value);
    data.provider_id = Number(configForm.elements.provider_id.value) || null; fields.disabled = true;
    try { await api(target() + "/config", {method:"PUT", body:JSON.stringify(data)}); toast("记忆设置已保存"); await load(); } catch (error) { fail(error); } finally { fields.disabled = false; }
  };
  $("memory-item-cancel").onclick = () => dialog.close();
  $("memory-new").onclick = () => editItem(null);
  $("memory-refresh").onclick = () => load().catch(fail);
  bot.onchange = () => { page = 1; conversation.value = ""; load().catch(fail); };
  $("memory-search").onclick = () => { page = 1; ++generation; refreshContent().catch(fail); };
  $("memory-prev").onclick = () => { if (page > 1) { page--; ++generation; refreshContent().catch(fail); } };
  $("memory-next").onclick = () => { if (page * 20 < total) { page++; ++generation; refreshContent().catch(fail); } };
  $("memory-organize").onclick = async () => { try { const data = await api(target() + "/organize", {method:"POST", body:JSON.stringify({conversation_id:selectedConversation()})}); toast(data.message); await refreshContent(); } catch (error) { fail(error); } };
  $("memory-backfill").onclick = async () => {
    let cid, base, after;
    try { cid = selectedConversation(); base = target(); } catch (error) { fail(error); return; }
    try { const result = await ElementPlus.ElMessageBox.prompt("从哪个聊天记录编号之后回填？最多加入100条，整理会消耗模型预算，群历史不会追认为主人发言。", "历史回填", {inputValue:"0", inputPattern:/^\d+$/, inputErrorMessage:"请输入非负整数"}); after = Number(result.value); } catch { return; }
    try { const result = await api(base + "/backfill", {method:"POST", body:JSON.stringify({conversation_id:cid, after_id:after, limit:100})}); toast(`已加入 ${result.queued_sources} 条来源`); await refreshContent(); } catch (error) { fail(error); }
  };
  new MutationObserver(() => { if (section.hidden) { ++generation; dialog.close(); } else load().catch(fail); }).observe(section, {attributes:true, attributeFilter:["hidden"]});
  document.addEventListener("qbot-logout", () => { ++generation; dialog.close(); items = []; $("memory-items").replaceChildren(); $("memory-runs").replaceChildren(); });
})();
