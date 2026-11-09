"use strict";
(() => {
  const section = $("knowledge-page");
  section.innerHTML = `
    <div class="page-heading"><h1>知识库</h1></div>
    <div class="knowledge-toolbar"><label>智能体<select id="knowledge-bot"></select></label><button id="knowledge-refresh" type="button">刷新</button></div>
    <p id="knowledge-error" class="error" role="alert"></p>
    <div class="knowledge-grid"><form id="knowledge-config" class="knowledge-panel"><fieldset id="knowledge-config-fields" disabled>
      <h2>知识库设置</h2>
      <label class="checkbox"><input type="checkbox" name="enabled">启用知识库</label>
      <label>回答模式<select name="mode"><option value="assist">辅助回答</option><option value="strict">仅依据知识库</option></select></label>
      <label>Embedding 基础地址<input name="base_url" type="url" maxlength="2048" placeholder="https://服务地址/v1"></label>
      <label>Embedding 模型<input name="model_name" maxlength="200" placeholder="填写向量模型名称"></label>
      <label>API Key<input name="api_key" type="password" autocomplete="new-password" placeholder="留空保留已保存密钥"></label>
      <p class="muted">更换模型或密钥后需重新处理文件。配置或文件生效变化会重置近期对话。</p><button class="primary" type="submit">保存设置</button>
    </fieldset></form><div>
      <div class="knowledge-panel"><h2>文件</h2>
        <form id="knowledge-upload"><label>选择文件<input id="knowledge-file" type="file" accept=".pdf,.docx,.xlsx,.txt,.md,.csv" required></label>
          <p class="muted">单文件最多30 MB；文件可供此智能体的全部聊天对象检索。解析索引会产生模型费用。</p>
          <button id="knowledge-upload-button" class="primary" type="submit">上传并处理</button></form>
        <div id="knowledge-files" aria-live="polite"></div>
        <div class="knowledge-actions"><button id="knowledge-prev" type="button">上一页</button><span id="knowledge-page-info"></span><button id="knowledge-next" type="button">下一页</button></div>
      </div>
      <form id="knowledge-search" class="knowledge-panel"><h2>检索试用</h2><label>问题<textarea name="query" rows="3" maxlength="4000" required></textarea></label>
        <button class="primary" type="submit">检索（调用向量模型）</button><div id="knowledge-results" aria-live="polite"></div></form>
    </div></div>`;
  const bot = $("knowledge-bot"), form = $("knowledge-config"), fields = $("knowledge-config-fields");
  const statuses = {queued:"排队中", processing:"处理中", ready:"可用", failed:"失败", stale:"待重建", deleting:"删除中", cleanup_failed:"清理失败"};
  let generation = 0, listRequest = 0, page = 1, total = 0, busy = false;
  const node = (tag, text, cls) => {const el = document.createElement(tag); if (text != null) el.textContent = text; if (cls) el.className = cls; return el;};
  const visible = seq => seq === generation && !section.hidden && !$("workspace").hidden;
  const fail = error => {$("knowledge-error").textContent = error.message || String(error);};
  const base = () => {if (!bot.value) throw new Error("请选择智能体"); return `/knowledge/${bot.value}`;};
  const lock = value => {busy = value; bot.disabled = value; $("knowledge-refresh").disabled = value; fields.disabled = value || !bot.value; $("knowledge-upload-button").disabled = value || !bot.value;};

  async function list(seq = generation) {
    const request = ++listRequest;
    const path = base();
    const data = await api(`${path}/files?page=${page}`);
    if (!visible(seq) || request !== listRequest) return;
    total = data.total;
    const container = $("knowledge-files"); container.replaceChildren();
    if (!data.items.length) container.append(node("p", "暂无文件", "knowledge-meta"));
    for (const file of data.items) {
      const card = node("article", null, "knowledge-file");
      card.append(node("strong", file.filename), node("div", `${statuses[file.status] || file.status} · ${(file.size_bytes / 1024).toFixed(1)} KB · ${file.chunk_count} 个片段`, "knowledge-meta"));
      card.append(node("div", `索引累计：${file.embedding_calls} 次调用 · 已知 ${file.embedding_tokens} token · 未知 ${file.unknown_calls} 次`, "knowledge-meta"));
      if (file.error) card.append(node("p", file.error, "error"));
      const actions = node("div", null, "knowledge-actions");
      if (!["deleting", "cleanup_failed"].includes(file.status)) {
        const link = node("a", "下载原文件"); link.href = `/api/admin${path}/files/${file.id}/download`; link.setAttribute("download", file.filename); actions.append(link);
      }
      for (const [action, label] of [["retry", file.status === "cleanup_failed" ? "重试清理" : "重新处理"], ["delete", "删除"]]) {
        if (action === "retry" && !["ready", "failed", "stale", "cleanup_failed"].includes(file.status)) continue;
        if (action === "delete" && file.status === "deleting") continue;
        const button = node("button", label); button.type = "button";
        button.onclick = async () => {
          if (busy) return;
          try {
            await ElementPlus.ElMessageBox.confirm(action === "delete" ? "删除后停止检索并清理原文件，近期对话将重新开始。" : file.status === "cleanup_failed" ? "重试清理此文件？" : "重新处理会产生模型费用，近期对话将重新开始。", label);
          } catch {return;}
          if (!visible(seq) || base() !== path || busy) return;
          lock(true); button.disabled = true;
          try {await api(`${path}/files/${file.id}${action === "retry" ? "/retry" : ""}`, {method:action === "retry" ? "POST" : "DELETE"}); await list(seq);}
          catch(error) {if (visible(seq)) fail(error);} finally {if (seq === generation) lock(false); button.disabled = false;}
        };
        actions.append(button);
      }
      card.append(actions); container.append(card);
    }
    $("knowledge-page-info").textContent = `${page} / ${Math.max(1, Math.ceil(total / 20))} 页 · ${total} 个文件`;
    $("knowledge-prev").disabled = page <= 1;
    $("knowledge-next").disabled = page * 20 >= total;
  }

  async function load() {
    const seq = ++generation;
    lock(true); $("knowledge-error").textContent = ""; $("knowledge-files").replaceChildren(); $("knowledge-results").replaceChildren(); form.reset();
    try {
      const data = await api("/bots");
      if (!visible(seq)) return;
      const previous = bot.value;
      bot.replaceChildren(new Option("请选择智能体", ""), ...data.items.map(item => new Option(`${item.name} #${item.id}`, item.id)));
      bot.value = data.items.some(item => String(item.id) === previous) ? previous : String(data.items[0]?.id || "");
      if (!bot.value) return;
      const cfg = await api(base() + "/config");
      if (!visible(seq)) return;
      form.elements.enabled.checked = cfg.enabled;
      for (const key of ["mode", "base_url", "model_name"]) form.elements[key].value = cfg[key];
      form.elements.api_key.placeholder = cfg.has_api_key ? "已保存，留空保留" : "填写 API Key";
      await list(seq);
    } catch(error) {if (visible(seq)) fail(error);} finally {if (seq === generation) lock(false);}
  }

  form.onsubmit = async event => {
    event.preventDefault(); if (busy) return;
    const seq = generation, path = base(), values = Object.fromEntries(new FormData(form)); values.enabled = form.elements.enabled.checked;
    lock(true); $("knowledge-error").textContent = "";
    try {await api(path + "/config", {method:"PUT", body:JSON.stringify(values)}); if (visible(seq)) {form.elements.api_key.value = ""; toast("设置已保存，近期对话重新开始"); await list(seq);}}
    catch(error) {if (visible(seq)) fail(error);} finally {if (seq === generation) lock(false);}
  };
  $("knowledge-upload").onsubmit = async event => {
    event.preventDefault(); if (busy) return;
    const file = $("knowledge-file").files[0];
    if (!file || file.size === 0 || file.size > 30 * 1024 * 1024) {fail(new Error("请选择不超过30 MB的非空文件")); return;}
    const seq = generation, path = base(); lock(true); $("knowledge-error").textContent = "";
    try {await api(path + "/files?" + new URLSearchParams({filename:file.name}), {method:"POST", headers:{"Content-Type":"application/octet-stream"}, body:file});
      if (visible(seq)) {$("knowledge-file").value = ""; page = 1; toast("已上传并加入处理队列"); await list(seq);}}
    catch(error) {if (visible(seq)) fail(error);} finally {if (seq === generation) lock(false);}
  };
  $("knowledge-search").onsubmit = async event => {
    event.preventDefault(); if (busy) return;
    const seq = generation, path = base(), query = event.currentTarget.elements.query.value.trim();
    if (!query) return;
    const button = event.currentTarget.querySelector("button"); button.disabled = true; lock(true);
    $("knowledge-results").replaceChildren(); $("knowledge-error").textContent = "";
    try {
      const result = await api(path + "/search", {method:"POST", body:JSON.stringify({query})});
      if (!visible(seq)) return;
      const out = $("knowledge-results");
      if (result.status !== "ok") out.append(node("p", result.status === "failed" ? result.error : "没有找到相关资料", "knowledge-meta"));
      for (const item of result.items) {const card = node("article", null, "knowledge-result"); card.append(node("strong", `${item.filename} · ${item.location}`), node("p", item.content)); out.append(card);}
      out.append(node("p", `本次向量用量：${result.embedding_tokens == null ? "未知或未调用" : result.embedding_tokens + " token"}`, "knowledge-meta"));
    } catch(error) {if (visible(seq)) fail(error);} finally {button.disabled = false; if (seq === generation) lock(false);}
  };
  bot.onchange = () => {if (!busy) {page = 1; void load();}};
  $("knowledge-refresh").onclick = () => {if (!busy) void load();};
  $("knowledge-prev").onclick = () => {if (!busy && page > 1) {page--; list().catch(fail);}};
  $("knowledge-next").onclick = () => {if (!busy && page * 20 < total) {page++; list().catch(fail);}};
  new MutationObserver(() => {
    if (section.hidden) {++generation; form.elements.api_key.value = ""; lock(false);} else void load();
  }).observe(section, {attributes:true, attributeFilter:["hidden"]});
  document.addEventListener("qbot-logout", () => {++generation; form.reset(); $("knowledge-file").value = ""; $("knowledge-files").replaceChildren(); $("knowledge-results").replaceChildren(); lock(false);});
})();
