/* 采音线程：连续重采样为16kHz单声道PCM，每100ms交给主线程。 */
class VoiceCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.weight = 0;
    this.sum = 0;
    this.block = new ArrayBuffer(3200);
    this.view = new DataView(this.block);
    this.offset = 0;
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const sample of channel) {
      let left = 1;
      while (left > 0.000001) {
        const part = Math.min(left, this.ratio - this.weight);
        this.sum += sample * part;
        this.weight += part;
        left -= part;
        if (this.weight >= this.ratio - 0.000001) {
          const value = Math.max(-1, Math.min(1, this.sum / this.ratio));
          this.view.setInt16(this.offset, Math.round(value * (value < 0 ? 32768 : 32767)), true);
          this.offset += 2;
          this.weight = this.sum = 0;
          if (this.offset === 3200) {
            this.port.postMessage(this.block, [this.block]);
            this.block = new ArrayBuffer(3200);
            this.view = new DataView(this.block);
            this.offset = 0;
          }
        }
      }
    }
    return true;
  }
}
registerProcessor("qbot-voice-capture", VoiceCapture);
