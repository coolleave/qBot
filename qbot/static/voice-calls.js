"use strict";
(() => {
  const section = $("voice-page");
  section.innerHTML = `
    <div class="page-heading"><div><span class="eyebrow">VOICE CALL</span><h1>语音通话</h1><p class="muted">和智能体聊一会儿。支持实时转写、语音回复和插话。</p></div><button id="voice-refresh" type="button">刷新配置</button></div>
    <div class="voice-layout"><form id="voice-form" class="voice-settings">
      <h2>通话设置</h2><fieldset id="voice-fields"><label>机器人<select id="voice-bot" required></select></label>
      <label class="checkbox"><input name="enabled" type="checkbox">启用网页通话</label>
      <label>聊天模型<select name="chat_provider_id"></select></label>
      <label>实时语音识别<select name="asr_provider_id"></select></label>
      <label>语音合成<select name="tts_provider_id"></select></label>
      <label>音色<input name="voice" list="tts-voices" value="Bunny" maxlength="100" required></label>
      <label>最长通话（秒）<input name="max_seconds" type="number" value="900" min="60" max="1800" required></label>
      <label>无人说话后结束（秒）<input name="idle_seconds" type="number" value="60" min="30" max="300" required></label>
      <button id="voice-save" class="primary" type="submit">保存配置</button></fieldset>
      <p class="voice-help">模型统一在「模型供应商」维护。实时识别请选择 asr_realtime；录音识别与 Filetrans 不适用于通话。保存不会调用模型。</p>
    </form><div class="voice-room"><header><div class="voice-orb" aria-hidden="true">♫</div><h2 id="voice-title">选择一个机器人</h2><div id="voice-state" class="voice-status" role="status">尚未通话</div><p id="voice-duration">00:00</p></header>
      <div class="voice-actions"><button id="voice-start" class="primary" type="button" disabled>开始通话</button><button id="voice-mute" type="button" disabled>静音</button><button id="voice-interrupt" type="button" disabled>打断</button><button id="voice-end" type="button" disabled>挂断</button></div>
      <p id="voice-error" class="voice-error" role="alert"></p><div id="voice-partial" class="voice-partial"></div><div id="voice-transcript" class="voice-transcript" aria-label="本次通话文字"></div>
      <p class="voice-help">当前以网页访客身份通话，不继承 QQ 用户身份。默认不保存录音，转写与回复会保存。开始通话会产生模型费用；远程访问需 HTTPS，建议先使用耳机。</p>
    </div></div><div class="voice-history"><h2>通话记录</h2><div id="voice-history-list" class="voice-history-list"></div><button id="voice-history-more" type="button" hidden>更早记录</button><pre id="voice-history-detail" class="voice-history-detail" hidden></pre></div>`;
  const form = $("voice-form"), fields = $("voice-fields"), bot = $("voice-bot");
  let loading = 0, saved = null, dirty = false, providerItems = [], historyCursor = null;
  let connection = null, audio = null, stream = null, capture = null, input = null, silentGain = null;
  let busy = false, ready = false, muted = false, currentCall = null, generation = 0, activeTurn = 0, discardedTurn = 0;
  let scheduledAt = 0, segment = null, sources = new Set(), rows = new Map(), heartbeat = null, timer = null, connectedAt = 0;
  let lastPong = 0, startupTimer = null;
  const status = text => { $("voice-state").textContent = text; };
  const error = text => { $("voice-error").textContent = text; };
  function controls() {
    fields.disabled = busy;
    $("voice-refresh").disabled = busy;
    $("voice-start").disabled = busy || dirty || !saved?.enabled || !bot.value;
    $("voice-end").disabled = !busy;
    $("voice-mute").disabled = $("voice-interrupt").disabled = !ready;
    $("voice-mute").textContent = muted ? "取消静音" : "静音";
    $("voice-mute").setAttribute("aria-pressed", String(muted));
  }
  function send(value) { if (connection?.readyState === WebSocket.OPEN) connection.send(JSON.stringify(value)); }
  function clearPlayback() {
    for (const source of sources) { source.onended = null; source.stop(); source.disconnect(); }
    sources.clear(); segment = null; scheduledAt = audio?.currentTime || 0;
  }
  async function stop(message = "通话已结束", notify = true, refresh = true) {
    ++generation;
    const call = currentCall;
    currentCall = null;
    if (notify) send({type:"hangup"});
    if (connection) { connection.onclose = connection.onerror = connection.onmessage = null; connection.close(); connection = null; }
    clearInterval(heartbeat); clearInterval(timer); clearTimeout(startupTimer);
    clearPlayback();
    if (capture) { capture.port.onmessage = null; capture.disconnect(); capture = null; }
    input?.disconnect(); input = null; silentGain?.disconnect(); silentGain = null;
    stream?.getTracks().forEach(track => track.stop()); stream = null;
    const oldAudio = audio; audio = null;
    if (oldAudio && oldAudio.state !== "closed") oldAudio.close().catch(() => {});
    busy = ready = muted = false; status(message); controls(); $("voice-partial").textContent = "";
    if (notify && call) { try { await api(`/voice-calls/${call}`, {method:"DELETE"}); } catch (e) { error(e.message); } }
    if (refresh && bot.value && !$("workspace").hidden) loadHistory().catch(e => error(e.message));
  }
  function addRow(id, role, text) {
    const key = `${id}:${role}`;
    if (!rows.has(key)) {
      const row = element("p"), label = element("strong", role === "user" ? "你" : "智能体"), body = element("span", "");
      row.append(label, body); $("voice-transcript").append(row); rows.set(key, body);
    }
    rows.get(key).textContent += text;
    $("voice-transcript").scrollTop = $("voice-transcript").scrollHeight;
  }
  function confirmPlayed() {
    if (segment && segment.ended && segment.pending === 0 && !segment.confirmed) {
      segment.confirmed = true;
      send({type:"played", turn_id:segment.turn, sentence_id:segment.id});
    }
  }
  function play(event) {
    if (!audio || event.turn_id !== activeTurn || event.turn_id <= discardedTurn || !segment || event.sentence_id !== segment.id) return;
    if (audio.state !== "running") throw new Error("浏览器已暂停音频，请保持页面前台后重新拨号");
    const raw = atob(event.pcm), bytes = new Uint8Array(raw.length);
    for (let i=0;i<raw.length;i++) bytes[i] = raw.charCodeAt(i);
    if (bytes.length % 2 || bytes.length > 9600) throw new Error("收到无效音频");
    const data = new DataView(bytes.buffer), buffer = audio.createBuffer(1, bytes.length / 2, 24000), samples = buffer.getChannelData(0);
    for (let i=0;i<samples.length;i++) samples[i] = data.getInt16(i*2, true) / 32768;
    scheduledAt = Math.max(audio.currentTime + 0.04, scheduledAt);
    if (scheduledAt - audio.currentTime > 8) throw new Error("播放积压过多，通话已停止，请检查网络");
    const source = audio.createBufferSource(), ownSegment = segment;
    source.buffer = buffer; source.connect(audio.destination); sources.add(source); ownSegment.pending++;
    source.onended = () => { sources.delete(source); source.disconnect(); ownSegment.pending--; if (segment === ownSegment) confirmPlayed(); };
    source.start(scheduledAt); scheduledAt += buffer.duration; status("正在说话");
  }
  function onEvent(event) {
    if (event.type === "ready") {
      clearTimeout(startupTimer); ready = true; connectedAt = Date.now(); lastPong = Date.now(); controls(); status("正在聆听");
      heartbeat = setInterval(() => { if (Date.now()-lastPong > 25000) stop("连接超时"); else send({type:"ping"}); }, 5000);
      timer = setInterval(() => { const seconds = Math.floor((Date.now()-connectedAt)/1000); $("voice-duration").textContent = `${String(Math.floor(seconds/60)).padStart(2,"0")}:${String(seconds%60).padStart(2,"0")}`; }, 1000);
    } else if (event.type === "pong") lastPong = Date.now();
    else if (event.type === "ended") stop(event.message, false);
    else if (event.type === "partial") $("voice-partial").textContent = event.text;
    else if (event.type === "transcript") { activeTurn = event.turn_id; $("voice-partial").textContent = ""; addRow(event.turn_id,"user",event.text); status("思考中"); }
    else if (event.type === "answer" && event.turn_id > discardedTurn) addRow(event.turn_id,"assistant",event.text);
    else if (event.type === "sentence" && event.turn_id > discardedTurn) segment = {turn:event.turn_id,id:event.sentence_id,pending:0,ended:false,confirmed:false};
    else if (event.type === "audio") play(event);
    else if (event.type === "sentence_end" && segment && event.turn_id === segment.turn && event.sentence_id === segment.id) { segment.ended = true; confirmPlayed(); }
    else if (event.type === "interrupted") { discardedTurn = Math.max(discardedTurn,event.turn_id); clearPlayback(); status("正在聆听"); }
    else if (event.type === "turn_end") status("正在聆听");
    else if (event.type === "listening") status("正在聆听");
    else if (event.type === "turn_error") { discardedTurn = Math.max(discardedTurn,event.turn_id); clearPlayback(); error(event.message); status("正在聆听"); }
    else if (event.type === "notice") error(event.message);
  }
  async function start() {
    if (busy) return;
    if (!isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) { error("请使用支持麦克风的现代浏览器，通过 HTTPS 或 localhost 访问"); return; }
    busy = true; ready = false; controls(); error(""); status("请求麦克风权限…");
    const ownGeneration = ++generation;
    rows.clear(); $("voice-transcript").replaceChildren(); $("voice-duration").textContent = "00:00"; activeTurn = discardedTurn = 0;
    try {
      audio = new AudioContext(); await audio.resume();
      const microphone = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      if (generation !== ownGeneration) { microphone.getTracks().forEach(track=>track.stop()); return; }
      stream = microphone;
      stream.getAudioTracks()[0].onended = () => stop("麦克风已断开");
      await audio.audioWorklet.addModule("/admin-assets/voice-capture.js");
      if (generation !== ownGeneration) return;
      input = audio.createMediaStreamSource(stream); capture = new AudioWorkletNode(audio,"qbot-voice-capture"); silentGain = audio.createGain(); silentGain.gain.value = 0;
      input.connect(capture); capture.connect(silentGain); silentGain.connect(audio.destination);
      capture.port.onmessage = event => {
        if (!ready || connection?.readyState !== WebSocket.OPEN) return;
        if (connection.bufferedAmount > 64000) { stop("音频上传积压，通话已停止"); return; }
        connection.send(muted ? new ArrayBuffer(event.data.byteLength) : event.data);
      };
      status("正在连接…");
      const result = await api("/voice-calls", {method:"POST",body:JSON.stringify({bot_id:Number(bot.value)})});
      if (generation !== ownGeneration) { await api(`/voice-calls/${result.call_id}`, {method:"DELETE"}); return; }
      currentCall = result.call_id;
      const url = new URL("/api/admin/voice-calls/connect", location.href); url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      connection = new WebSocket(url);
      connection.onopen = () => send({call_id:result.call_id,ticket:result.ticket});
      connection.onmessage = event => { try { onEvent(JSON.parse(event.data)); } catch (e) { error(e.message); stop("音频处理失败"); } };
      connection.onerror = () => { error("通话连接失败，请检查服务、登录状态和 HTTPS 配置"); stop("连接失败"); };
      connection.onclose = () => stop("连接已断开");
      startupTimer = setTimeout(() => stop("接通超时，请检查实时识别配置"), 35000);
    } catch (e) {
      if (generation !== ownGeneration) return;
      error(e.name === "NotAllowedError" ? "未获得麦克风权限，请在浏览器设置中允许" : e.message);
      await stop("未能开始通话");
    }
  }
  async function loadConfig() {
    const request = ++loading, id = bot.value; saved = null; controls();
    if (!id) return;
    const config = await api(`/voice-calls/config/${id}`);
    if (request !== loading || id !== bot.value || $("workspace").hidden) return;
    for (const [name,kind] of [["chat_provider_id","chat"],["asr_provider_id","asr_realtime"],["tts_provider_id","tts"]]) {
      const select = form.elements[name]; select.replaceChildren(new Option(kind === "chat" ? "跟随机器人 / 全局默认" : "请选择模型", ""));
      for (const provider of providerItems.filter(p=>p.model_type === kind && p.enabled && p.has_api_key)) select.add(new Option(`${provider.name} · ${provider.model_name}`,provider.id));
      if (config[name] && ![...select.options].some(option=>option.value === String(config[name]))) select.add(new Option("原模型不可用，请重新选择",config[name]));
    }
    for (const [key,value] of Object.entries(config)) { if (key === "enabled") form.elements[key].checked = value; else form.elements[key].value = value ?? ""; }
    saved = config; dirty = false; controls(); $("voice-title").textContent = bot.selectedOptions[0]?.textContent || "智能体";
    await loadHistory();
  }
  async function load() {
    if (busy) return;
    error(""); const old = bot.value, request = ++loading;
    const [robots,models] = await Promise.all([api("/bots"),api("/providers")]);
    if (request !== loading || busy || section.hidden || $("workspace").hidden) return;
    providerItems = models.items; bot.replaceChildren(...robots.items.map(item=>new Option(item.name,item.id)));
    if ([...bot.options].some(option=>option.value === old)) bot.value = old;
    if (!bot.value) { saved = null; controls(); error("请先在机器人管理中添加机器人"); return; }
    await loadConfig();
  }
  async function loadHistory(older = false) {
    const id = bot.value; if (!id) return;
    const suffix = older && historyCursor ? `?before=${encodeURIComponent(historyCursor)}` : "";
    const data = await api(`/voice-calls/history/${id}${suffix}`);
    if (bot.value !== id || $("workspace").hidden) return;
    if (!older) { $("voice-history-list").replaceChildren(); $("voice-history-detail").hidden = true; }
    const labels = {connecting:"连接中",active:"通话中",ended:"已结束",failed:"失败",interrupted:"异常中断"};
    for (const item of data.items) {
      $("voice-history-list").append(button(`${date(item.started_at)} · ${labels[item.status] || item.status} · ${item.reason || ""}`, async()=>{
        const detail = await api(`/voice-calls/history/${id}/${item.id}`);
        if (bot.value !== id || $("workspace").hidden) return;
        const parts = [JSON.stringify(detail.model_snapshot,null,2)];
        for (const turn of detail.turns) parts.push(`你：${turn.user}\n智能体：${turn.answer}\n确认播完：${turn.played.join("") || "无"}\n状态：${turn.status} · 用量：${Object.keys(turn.usage || {}).length ? JSON.stringify(turn.usage) : "未知"}${turn.error ? "\n"+turn.error : ""}`);
        $("voice-history-detail").textContent = parts.join("\n\n"); $("voice-history-detail").hidden = false;
      }));
    }
    if (!data.items.length && !older) $("voice-history-list").textContent = "暂无通话记录";
    historyCursor = data.items.at(-1)?.started_at || null; $("voice-history-more").hidden = data.items.length < 20;
  }
  form.oninput = () => { dirty = true; controls(); };
  form.onsubmit = async event => {
    event.preventDefault(); if (busy || !bot.value) return;
    const id = bot.value, values = Object.fromEntries(new FormData(form)); values.enabled = form.elements.enabled.checked;
    for (const key of ["chat_provider_id","asr_provider_id","tts_provider_id"]) values[key] = Number(values[key]) || null;
    for (const key of ["max_seconds","idle_seconds"]) values[key] = Number(values[key]);
    fields.disabled = true;
    try { await api(`/voice-calls/config/${id}`,{method:"PUT",body:JSON.stringify(values)}); saved = values; dirty = false; error(""); toast("通话配置已保存，下次拨号生效"); }
    catch (e) { error(e.message); }
    finally { fields.disabled = busy; controls(); }
  };
  bot.onchange = () => loadConfig().catch(e=>error(e.message));
  $("voice-refresh").onclick = () => load().catch(e=>error(e.message));
  $("voice-start").onclick = start;
  $("voice-end").onclick = () => stop();
  $("voice-mute").onclick = () => { muted = !muted; controls(); };
  $("voice-interrupt").onclick = () => { discardedTurn = Math.max(discardedTurn,activeTurn); clearPlayback(); send({type:"interrupt"}); status("正在聆听"); };
  $("voice-history-more").onclick = () => loadHistory(true).catch(e=>error(e.message));
  new MutationObserver(() => { if (section.hidden) { ++loading; if (busy) stop("已离开通话页面"); } else load().catch(e=>error(e.message)); }).observe(section,{attributes:true,attributeFilter:["hidden"]});
  document.addEventListener("qbot-logout",()=>{ ++loading; stop("已退出登录",false,false); saved = null; providerItems = []; rows.clear(); $("voice-transcript").replaceChildren(); $("voice-history-list").replaceChildren(); $("voice-history-detail").textContent=""; controls(); });
  window.addEventListener("pagehide",()=>stop("页面已关闭"));
})();
