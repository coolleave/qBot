"use strict";
let scheduledTemplatePage = 1, scheduledTemplateRequest = 0;
let scheduledTemplateCatalog = {libraries:[], prompt_styles:[]};
let scheduledTemplateId = null, scheduledTemplateBusy = false, scheduledTemplatePicker = null;
let scheduledTemplateOpening = false, scheduledTemplateImageUrl = "";
const templateForm = $("scheduled-template-form");
function templateOutputLabel(workflow) {
  const output = {text:"文字", audio:"语音", text_audio:"文字＋语音", none:"仅图片"}[workflow.output];
  return (workflow.content_source === "ai" ? "AI 生成 · " : "固定内容 · ") + output +
    (workflow.image_source !== "none" && workflow.output !== "none" ? "＋图片" : "");
}
async function loadScheduledTemplates() {
  const request = ++scheduledTemplateRequest;
  $("scheduled-template-list").replaceChildren();
  $("new-scheduled-template").disabled = true;
  $("scheduled-template-notice").textContent = "正在读取模板…";
  $("scheduled-template-prev").disabled = true; $("scheduled-template-next").disabled = true;
  try {
    const data = await api("/scheduled-templates?" + new URLSearchParams({page:String(scheduledTemplatePage), search:$("scheduled-template-query").value.trim()}));
    if (request !== scheduledTemplateRequest) return;
    scheduledTemplateCatalog = data.catalog;
    $("new-scheduled-template").disabled = false;
    $("scheduled-template-page").textContent = `第 ${data.page} / ${Math.max(1, Math.ceil(data.total / data.page_size))} 页，共 ${data.total} 个模板`;
    $("scheduled-template-prev").disabled = data.page <= 1;
    $("scheduled-template-next").disabled = data.page * data.page_size >= data.total;
    $("scheduled-template-notice").textContent = data.items.length ? "" : "暂无匹配模板，可点击新增模板。";
    for (const item of data.items) {
      const card = element("div", undefined, "card"), actions = element("div", undefined, "actions");
      card.append(element("h3", item.name), element("p", templateOutputLabel(item.config.workflow), "muted"));
      card.append(element("p", item.config.workflow.content_source === "ai" ?
        (item.config.workflow.custom_prompt || "使用预设文案风格").slice(0, 140) : item.config.content || "图片消息"));
      if (item.image_filename) card.append(element("small", "图片：" + item.image_filename));
      if (scheduledTemplatePicker) actions.append(button("套用到当前任务", () => applyScheduledTemplate(item.id)));
      actions.append(button("查看 / 编辑", () => editScheduledTemplate(item.id)), button("删除", async () => {
        if (scheduledTemplateBusy || !confirm(`删除模板“${item.name}”？已保存任务不受影响。`)) return;
        scheduledTemplateBusy = true;
        try { await api(`/scheduled-templates/${item.id}`, {method:"DELETE"}); await loadScheduledTemplates(); }
        finally { scheduledTemplateBusy = false; }
      }));
      card.append(actions); $("scheduled-template-list").append(card);
    }
  } catch(e) { if (request === scheduledTemplateRequest) $("scheduled-template-notice").textContent = "读取失败：" + e.message; }
}
async function openScheduledTemplates(slot = null) {
  scheduledTemplatePicker = slot ? {slot, target:greetingTarget} : null;
  scheduledTemplatePage = 1; $("scheduled-template-query").value = "";
  $("scheduled-templates-dialog").showModal();
  await loadScheduledTemplates();
}
function updateTemplateFields() {
  const f = templateForm.elements, imageOnly = f.output.value === "none";
  if (imageOnly) {
    f.content_source.value = "fixed";
    if (f.image_source.value === "none") f.image_source.value = "upload";
  }
  f.content_source.disabled = imageOnly;
  const ai = !imageOnly && f.content_source.value === "ai", audio = ["audio","text_audio"].includes(f.output.value);
  $("template-content-field").hidden = ai || imageOnly; f.content.required = !ai && !imageOnly;
  $("template-ai-fields").hidden = !ai;
  f.custom_prompt.required = ai && !f.prompt_style.value;
  $("template-library-field").hidden = f.image_source.value !== "library"; f.library_id.required = f.image_source.value === "library";
  $("template-image-fields").hidden = f.image_source.value !== "upload";
  $("template-voice-fields").hidden = !audio; f.tts_provider_id.required = audio;
}
function clearTemplatePreview() {
  if (scheduledTemplateImageUrl) URL.revokeObjectURL(scheduledTemplateImageUrl);
  scheduledTemplateImageUrl = "";
  $("template-image-preview").removeAttribute("src"); $("template-image-preview").hidden = true;
}
async function editScheduledTemplate(id = null) {
  if (scheduledTemplateOpening || scheduledTemplateBusy) return;
  scheduledTemplateOpening = true;
  try {
    const [data, providerData] = await Promise.all([
      id ? api(`/scheduled-templates/${id}`) : Promise.resolve({name:"", content:"", workflow:{content_source:"fixed", image_source:"none", output:"text"}, voice:"Bunny"}),
      api("/providers")
    ]);
    if (!$("scheduled-templates-dialog").open) return;
    scheduledTemplateId = id; templateForm.reset(); clearTemplatePreview();
    $("scheduled-template-error").textContent = "";
    $("scheduled-template-title").textContent = id ? "查看 / 编辑模板" : "新增模板";
    const f = templateForm.elements;
    f.library_id.replaceChildren(new Option("请选择素材库", ""), ...scheduledTemplateCatalog.libraries.map(x=>new Option(x.name,x.id)));
    f.prompt_style.replaceChildren(new Option("自定义提示词", ""), ...(scheduledTemplateCatalog.prompt_styles || []).map(x=>new Option(x.name,x.id)));
    f.tts_provider_id.replaceChildren(new Option("请选择 TTS 供应商", ""), ...providerData.items.filter(x=>x.model_type === "tts" && x.enabled).map(x=>new Option(x.name,x.id)));
    for (const key of ["content_source","image_source","output","library_id","prompt_style","custom_prompt"]) f[key].value = data.workflow[key] || "";
    for (const key of ["name","content","voice"]) f[key].value = data[key] || "";
    if (data.tts_provider_id && ![...f.tts_provider_id.options].some(x=>x.value === String(data.tts_provider_id))) f.tts_provider_id.add(new Option("原供应商不可用，请重新选择", data.tts_provider_id));
    f.tts_provider_id.value = data.tts_provider_id || "";
    $("template-image-name").textContent = data.image_filename ? "已保存：" + data.image_filename : "尚未上传图片";
    if (data.image_base64) {
      $("template-image-preview").src = "data:" + (/\.png$/i.test(data.image_filename) ? "image/png" : "image/jpeg") + ";base64," + data.image_base64;
      $("template-image-preview").hidden = false;
    }
    updateTemplateFields(); $("scheduled-template-editor").showModal();
  } finally { scheduledTemplateOpening = false; }
}
async function applyScheduledTemplate(id) {
  const picker = scheduledTemplatePicker;
  if (!picker || scheduledTemplateBusy || greetingRunning) return;
  scheduledTemplateBusy = true;
  try {
    const data = await api(`/scheduled-templates/${id}`);
    if (!$("greeting-dialog").open || greetingTarget !== picker.target || scheduledTemplatePicker !== picker) return;
    const slot = picker.slot, f = $("greeting-form").elements;
    // 仅复制内容；日期、星期、启用状态、接收人和任务标识保持原值。
    fillGreetingWorkflow(slot, data.workflow);
    f[slot + "_delivery_mode"].value = "custom";
    f[slot + "_content"].value = data.content;
    f[slot + "_voice"].value = data.voice;
    const tts = f[slot + "_tts_provider_id"];
    if (data.tts_provider_id && ![...tts.options].some(x=>x.value === String(data.tts_provider_id))) tts.add(new Option("模板供应商（保存时校验）", data.tts_provider_id));
    tts.value = data.tts_provider_id || "";
    f[slot + "_image_file"].value = "";
    f[slot + "_remove_image"].checked = !data.image_base64 || data.workflow.image_source !== "upload";
    if (data.image_base64 && data.workflow.image_source === "upload") greetingTemplateImages.set(slot, {image_base64:data.image_base64,image_filename:data.image_filename});
    else greetingTemplateImages.delete(slot);
    $(slot + "-image-name").textContent = data.image_filename ? "模板图片待保存：" + data.image_filename : "模板无上传图片";
    $(slot + "-preview").textContent = data.content;
    greetingDirty = true; updateGreetingMedia(slot);
    $("scheduled-templates-dialog").close(); toast("模板已套用，请保存任务后生效");
  } finally { scheduledTemplateBusy = false; }
}
templateForm.onsubmit = async event => {
  event.preventDefault(); if (scheduledTemplateBusy) return;
  const f = templateForm.elements, id = scheduledTemplateId;
  const values = {name:f.name.value.trim(), content:f.content.value.trim(), voice:f.voice.value.trim(),
    tts_provider_id:Number(f.tts_provider_id.value) || null, remove_image:f.remove_image.checked,
    workflow:{version:1, content_source:f.content_source.value, image_source:f.image_source.value, output:f.output.value,
      library_id:f.library_id.value || null, prompt_style:f.prompt_style.value || null, custom_prompt:f.custom_prompt.value.trim()}};
  const controls = [...templateForm.querySelectorAll("button,input,textarea,select")], states = controls.map(x=>x.disabled);
  scheduledTemplateBusy = true; controls.forEach(x=>x.disabled=true); $("scheduled-template-error").textContent = "";
  try {
    const file = f.image_file.files[0];
    if (file) {
      if (!file.size || file.size > 3500000) throw new Error("图片必须非空且不超过3.5MB");
      if (values.remove_image) throw new Error("不能同时删除和上传图片");
      values.image_base64 = await greetingImageBase64(file); values.image_filename = file.name;
    }
    await api(id ? `/scheduled-templates/${id}` : "/scheduled-templates", {method:id ? "PUT" : "POST", body:JSON.stringify(values)});
    $("scheduled-template-editor").close(); toast("模板已保存，已有任务不受影响"); await loadScheduledTemplates();
  } catch(e) { $("scheduled-template-error").textContent = e.message; }
  finally { scheduledTemplateBusy = false; controls.forEach((x,i)=>x.disabled=states[i]); }
};
for (const key of ["content_source","output","image_source","prompt_style"]) templateForm.elements[key].onchange = updateTemplateFields;
templateForm.elements.image_file.onchange = () => {
  clearTemplatePreview(); const file = templateForm.elements.image_file.files[0];
  if (!file) return;
  if (!file.size || file.size > 3500000 || !/\.(png|jpe?g)$/i.test(file.name)) {
    templateForm.elements.image_file.value = ""; $("scheduled-template-error").textContent = "请选择非空且不超过3.5MB的PNG/JPG图片"; return;
  }
  templateForm.elements.remove_image.checked = false;
  $("template-image-name").textContent = "待保存：" + file.name;
  scheduledTemplateImageUrl = URL.createObjectURL(file);
  $("template-image-preview").src = scheduledTemplateImageUrl; $("template-image-preview").hidden = false;
};
$("template-image-preview").onerror = () => { $("scheduled-template-error").textContent = "图片无法预览，请检查图片文件"; };
$("scheduled-template-editor").addEventListener("cancel", event => { if (scheduledTemplateBusy) event.preventDefault(); });
$("scheduled-template-editor").addEventListener("close", clearTemplatePreview);
$("scheduled-templates-dialog").addEventListener("close", () => { scheduledTemplateRequest++; scheduledTemplatePicker = null; });
$("manage-scheduled-templates").onclick = () => openScheduledTemplates().catch(e=>toast(e.message));
$("new-scheduled-template").onclick = () => editScheduledTemplate().catch(e=>toast(e.message));
$("scheduled-template-search").onsubmit = event => { event.preventDefault(); scheduledTemplatePage=1; void loadScheduledTemplates(); };
$("scheduled-template-prev").onclick = () => { scheduledTemplatePage--; void loadScheduledTemplates(); };
$("scheduled-template-next").onclick = () => { scheduledTemplatePage++; void loadScheduledTemplates(); };
for (const slot of ["morning","noon","evening","other"]) $(slot + "-choose-template").onclick = () => openScheduledTemplates(slot).catch(e=>toast(e.message));
