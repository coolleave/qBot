"use strict";
// 复用通话采集器生成16kHz PCM，直接封装WAV，避免MediaRecorder格式差异。
class VoiceCloneRecorder {
  static instances = new Set();
  static active = null;
  static resetAll() {
    for (const recorder of this.instances) { recorder.cancel(); recorder.clearSample(); }
  }
  static disposeAll() {
    for (const recorder of [...this.instances]) recorder.dispose();
  }
  constructor(onSample, onBusy) {
    this.onSample = onSample; this.onBusy = onBusy;
    this.state = "idle"; this.generation = 0; this.disabled = false; this.disposed = false;
    this.parts = []; this.bytes = 0; this.file = null;
    this.node = document.createElement("div"); this.node.className = "clone-recorder";
    const label = document.createElement("p"); label.textContent = "用平时说话的声音朗读，约20秒：";
    const script = document.createElement("blockquote");
    script.textContent = "你好，很高兴在这里和你见面。今天的阳光刚刚好，窗外吹来一阵轻柔的风。忙碌的时候，也别忘了喝杯水，放松一下。接下来，我想用自己的声音，和你分享生活里的小事。准备好了吗？我们慢慢聊。";
    this.button = document.createElement("button"); this.button.type = "button";
    this.button.textContent = "开始录音";
    this.button.onclick = () => {
      if (this.state === "starting") this.cancel();
      else if (this.state === "recording") void this.stop();
      else if (this.state === "idle") void this.start();
    };
    this.status = document.createElement("p"); this.status.className = "muted";
    this.status.setAttribute("role", "status");
    this.preview = document.createElement("audio"); this.preview.controls = true; this.preview.hidden = true;
    this.node.append(label, script, this.button, this.status, this.preview);
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      this.status.textContent = "网页录音需要HTTPS或localhost地址，请通过安全地址打开后台。";
      this.unavailable = true; this.button.disabled = true;
    }
    VoiceCloneRecorder.instances.add(this);
  }
  setDisabled(value) {
    this.disabled = value;
    this.button.disabled = value || this.unavailable || this.state === "stopping";
  }
  clearSample() {
    this.file = null; this.preview.pause();
    this.preview.removeAttribute("src"); this.preview.load(); this.preview.hidden = true;
    if (this.previewURL) URL.revokeObjectURL(this.previewURL);
    this.previewURL = null;
    this.onSample(null);
  }
  release() {
    clearTimeout(this.timer);
    if (this.capture) { this.capture.port.onmessage = null; this.capture.disconnect(); this.capture = null; }
    this.source?.disconnect(); this.source = null;
    this.gain?.disconnect(); this.gain = null;
    this.stream?.getTracks().forEach(track => track.stop()); this.stream = null;
    const context = this.context; this.context = null;
    if (context && context.state !== "closed") {
      void context.close().catch(error => console.warn("录音音频上下文关闭失败", error.name));
    }
    if (VoiceCloneRecorder.active === this) VoiceCloneRecorder.active = null;
  }
  cancel() {
    this.generation++; this.release(); this.parts = []; this.bytes = 0;
    this.state = "idle"; this.button.textContent = "开始录音"; this.onBusy(false);
    this.setDisabled(this.disabled);
    if (!this.unavailable) this.status.textContent = "录音已取消";
  }
  async start() {
    if (this.disabled || this.unavailable || this.disposed) return;
    if (VoiceCloneRecorder.active && VoiceCloneRecorder.active !== this) VoiceCloneRecorder.active.cancel();
    VoiceCloneRecorder.active = this;
    const generation = ++this.generation;
    this.clearSample(); this.parts = []; this.bytes = 0;
    this.state = "starting"; this.button.textContent = "取消录音";
    this.status.textContent = "请允许使用麦克风…"; this.onBusy(true);
    try {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio || !window.AudioWorkletNode) throw new Error("当前浏览器不支持网页录音，请升级浏览器");
      const context = new Audio(); this.context = context;
      await context.resume();
      if (generation !== this.generation) return;
      const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1,
        echoCancellation: true, noiseSuppression: true, autoGainControl: false}});
      if (generation !== this.generation) { stream.getTracks().forEach(track => track.stop()); return; }
      this.stream = stream;
      await context.audioWorklet.addModule("/admin-assets/voice-capture.js?v=clone-flush-1");
      if (generation !== this.generation) return;
      this.capture = new AudioWorkletNode(context, "qbot-voice-capture");
      this.source = context.createMediaStreamSource(stream);
      this.gain = context.createGain(); this.gain.gain.value = 0;
      this.capture.port.onmessage = event => {
        if (generation !== this.generation) return;
        if (event.data?.type === "flushed") { this.flushed?.(); return; }
        if (!(event.data instanceof ArrayBuffer)) return;
        const remaining = 16000 * 2 * 60 - this.bytes;
        const block = event.data.slice(0, Math.max(0, remaining));
        if (block.byteLength) { this.parts.push(block); this.bytes += block.byteLength; }
        if (this.state === "recording") {
          this.status.textContent = `正在录音 ${Math.floor(this.bytes / 32000)} / 60 秒`;
          if (this.bytes >= 16000 * 2 * 60) void this.stop();
        }
      };
      this.capture.onprocessorerror = () => { this.cancel(); this.status.textContent = "录音处理失败，请重新录制"; };
      stream.getAudioTracks().forEach(track => { track.onended = () => { if (this.state === "recording") void this.stop(); }; });
      this.source.connect(this.capture); this.capture.connect(this.gain); this.gain.connect(context.destination);
      this.state = "recording"; this.button.textContent = "停止录音";
      this.status.textContent = "正在录音 0 / 60 秒";
      this.timer = setTimeout(() => void this.stop(), 60000);
    } catch (error) {
      if (generation !== this.generation) return;
      this.cancel();
      this.status.textContent = error.name === "NotAllowedError" ? "麦克风权限被拒绝，请允许后重试" :
        error.name === "NotFoundError" ? "未找到可用麦克风" : "无法开始录音，请检查麦克风和浏览器支持";
    }
  }
  async stop() {
    if (this.state !== "recording") return;
    const generation = this.generation;
    this.state = "stopping"; this.button.disabled = true; clearTimeout(this.timer);
    this.status.textContent = "正在完成录音…";
    try {
      // 先取出Worklet未满100ms的尾部，避免最后一个字被截断。
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => { this.flushed = null; reject(new Error("flush timeout")); }, 1500);
        this.flushed = () => { clearTimeout(timeout); this.flushed = null; resolve(); };
        this.capture.port.postMessage({type: "flush"});
      });
      if (generation !== this.generation) return;
      this.release();
      const seconds = this.bytes / 32000;
      if (seconds < 5) throw new Error("录音不足5秒，请完整朗读后重试");
      const header = new ArrayBuffer(44), view = new DataView(header);
      const word = (offset, value) => [...value].forEach((char, i) => view.setUint8(offset + i, char.charCodeAt(0)));
      word(0, "RIFF"); view.setUint32(4, 36 + this.bytes, true); word(8, "WAVE"); word(12, "fmt ");
      view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
      view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true);
      view.setUint16(34, 16, true); word(36, "data"); view.setUint32(40, this.bytes, true);
      this.file = new File([header, ...this.parts], "voice-recording.wav", {type: "audio/wav"});
      this.previewURL = URL.createObjectURL(this.file); this.preview.src = this.previewURL; this.preview.hidden = false;
      this.status.textContent = `已录制 ${seconds.toFixed(1)} 秒，可先试听原声，再创建音色`;
      this.onSample(this.file);
    } catch (error) {
      if (generation === this.generation) this.status.textContent = this.bytes < 160000 ?
        "录音不足5秒，请完整朗读后重试" : "录音未完整结束，请重新录制";
    } finally {
      if (generation === this.generation) {
        this.release(); this.parts = []; this.state = "idle"; this.button.textContent = this.file ? "重新录音" : "开始录音";
        this.onBusy(false); this.setDisabled(this.disabled);
      }
    }
  }
  dispose() {
    this.disposed = true; this.cancel(); this.clearSample(); VoiceCloneRecorder.instances.delete(this);
  }
}
window.addEventListener("pagehide", () => VoiceCloneRecorder.resetAll());
document.addEventListener("qbot-logout", () => VoiceCloneRecorder.disposeAll());
