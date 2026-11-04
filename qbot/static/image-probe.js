"use strict";
const imageProbeStates = new Map();
function addImageProbe(card, item) {
  const prompt = element("textarea"), submit = button("开始生图", start), fresh = button("新建测试", reset);
  const status = element("p", "", "muted"), preview = element("img", undefined, "image-probe-preview"), download = element("a", "下载图片");
  prompt.placeholder = "输入图片提示词"; prompt.maxLength = 2000; prompt.rows = 4;
  prompt.setAttribute("aria-label", "生图提示词"); status.setAttribute("role", "status");
  preview.alt = "测试生成的图片"; preview.hidden = download.hidden = true;
  const actions = element("div", undefined, "actions"); actions.append(submit, fresh, download);
  card.append(element("h3", "测试生图"), prompt, actions, status, preview);
  let state = imageProbeStates.get(item.id), timer = null, version = 0, url = null;
  function release() {
    clearTimeout(timer); version++;
    if (url) URL.revokeObjectURL(url);
    url = null; preview.removeAttribute("src"); download.removeAttribute("href");
    preview.hidden = download.hidden = true;
  }
  function render() {
    prompt.value = state?.prompt || "";
    prompt.disabled = Boolean(state && !["succeeded", "failed"].includes(state.status));
    submit.disabled = !item.has_api_key || state?.busy === true;
    fresh.disabled = state?.busy === true;
    submit.textContent = state && !["succeeded", "failed"].includes(state.status) ? "查询 / 恢复任务" : "开始生图";
  }
  async function poll() {
    if (!state || !card.isConnected || !$("provider-dialog").open) return;
    const current = ++version, id = state.id;
    clearTimeout(timer);
    try {
      const result = await api(`/providers/${item.id}/image-jobs/${id}`);
      if (current !== version || state?.id !== id) return;
      state.status = result.status; state.accepted = true; render();
      if (result.status === "running") {
        status.textContent = "正在生成图片…";
        timer = setTimeout(() => void poll(), 2000);
      } else if (result.status === "succeeded") {
        const response = await fetch(`/api/admin/providers/${item.id}/image-jobs/${id}/result`, { credentials: "same-origin", cache: "no-store" });
        if (!response.ok) { if (response.status === 401) loggedOut(); throw new Error("图片读取失败或已过期"); }
        const blob = await response.blob();
        if (current !== version || state?.id !== id) return;
        if (url) URL.revokeObjectURL(url);
        url = URL.createObjectURL(blob); preview.src = download.href = url;
        download.download = blob.type === "image/png" ? "generated.png" : "generated.jpg";
        preview.hidden = download.hidden = false; status.textContent = "生成成功";
      } else status.textContent = result.error || "生成失败";
    } catch (e) {
      if (current === version) status.textContent = `${e.message}；可查询原任务，不会自动重新生图。`;
    }
  }
  async function start() {
    if (state?.busy) return;
    if (state?.accepted && !["succeeded", "failed"].includes(state.status)) {
      await poll(); return;
    }
    if (!state || ["succeeded", "failed"].includes(state.status)) {
      const text = prompt.value.trim();
      if (!text) { status.textContent = "请输入提示词"; return; }
      release();
      state = { id: createRequestId(), prompt: text, status: "submitting", busy: false };
      imageProbeStates.set(item.id, state);
    }
    state.busy = true; render(); status.textContent = "正在提交任务…";
    try {
      // 响应丢失时沿用UUID；查询或重开窗口不自动发起新的付费任务。
      const result = await api(`/providers/${item.id}/image-jobs`, { method: "POST", body: JSON.stringify({ request_id: state.id, prompt: state.prompt }) });
      state.status = result.status; state.accepted = true;
      state.busy = false; render(); await poll();
    } catch (e) {
      status.textContent = `${e.message}；再次点击将沿用原请求标识。`;
    } finally { state.busy = false; render(); }
  }
  function reset() {
    if (state?.busy) return;
    if (state && !["succeeded", "failed"].includes(state.status)
        && !confirm("原任务可能仍在生成。新建测试不会取消原任务，并可能额外计费，是否继续？")) return;
    release(); state = null; imageProbeStates.delete(item.id); status.textContent = ""; render();
  }
  card.addEventListener("provider-close", release);
  card.addEventListener("provider-open", () => { render(); if (state) void poll(); });
  render();
}
document.addEventListener("qbot-logout", () => imageProbeStates.clear());
