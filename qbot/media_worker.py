"""隔离不可信图片、GIF 和语音解码；由调用方限制运行时长。"""
import base64
import io
import json
import sys
import warnings
import wave

MAX_INPUT = 8_000_000


class LimitedPCM(io.BytesIO):
    def write(self, data: bytes) -> int:
        if self.tell() + len(data) > 24000 * 2 * 60:
            raise ValueError("语音超过 60 秒")
        return super().write(data)


def audio_wav(data: bytes) -> bytes:
    output = LimitedPCM()
    if data.startswith((b"#!SILK_V3", b"\x02#!SILK_V3")):
        import pysilk
        pysilk.decode(io.BytesIO(data), output, 24000)
    else:
        import miniaudio
        stream = miniaudio.stream_memory(data, nchannels=1, sample_rate=24000,
                                        output_format=miniaudio.SampleFormat.SIGNED16, frames_to_read=2400)
        try:
            for chunk in stream:
                output.write(chunk.tobytes())
        finally:
            stream.close()
    pcm = output.getvalue()
    if not pcm:
        raise ValueError("语音没有可识别的音频")
    result = io.BytesIO()
    with wave.open(result, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(pcm)
    return result.getvalue()


def image_frames(data: bytes, allow_gif: bool) -> dict:
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 20_000_000
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    with Image.open(io.BytesIO(data)) as picture:
        if picture.format not in ("PNG", "JPEG", "WEBP", "GIF"):
            raise ValueError("图片格式仅支持 PNG/JPEG/WebP/GIF")
        animated = bool(getattr(picture, "is_animated", False))
        if animated and not allow_gif:
            raise ValueError("此机器人未开启动态表情理解")
        width, height = picture.size
        if width * height > 20_000_000:
            raise ValueError("图片像素过大")
        frames, elapsed, scanned = [], 0, 0
        # 不先访问 n_frames，避免为了计数遍历恶意动画。只扫描预算内帧。
        for index in range(101 if animated else 1):
            try:
                picture.seek(index)
            except EOFError:
                break
            if index == 100 or (index + 1) * width * height > 100_000_000:
                return {"frames": frames[:1], "notice": "动画超过处理预算，仅识别首帧"}
            frame = ImageOps.exif_transpose(picture.copy()).convert("RGB")
            frame.thumbnail((2048, 2048))
            out = io.BytesIO()
            frame.save(out, format="JPEG", quality=85)
            frames.append({"time_ms": elapsed, "data": base64.b64encode(out.getvalue()).decode()})
            elapsed += max(20, int(picture.info.get("duration", 100)))
            scanned += 1
        if not frames:
            raise ValueError("无法读取图片")
        if scanned > 4:
            times = [frames[-1]["time_ms"] * n / 3 for n in range(4)]
            chosen = sorted({min(range(scanned), key=lambda i: abs(frames[i]["time_ms"] - t)) for t in times})
            frames = [frames[i] for i in chosen]
        unique = []
        for frame in frames:
            if not any(f["data"] == frame["data"] for f in unique):
                unique.append(frame)
        return {"frames": unique, "notice": "同一动画的时序帧" if animated else "静态图片"}


def main() -> None:
    data = sys.stdin.buffer.read(MAX_INPUT + 1)
    if not data or len(data) > MAX_INPUT:
        raise ValueError("文件为空或超过 8 MB")
    if sys.argv[1] == "audio":
        sys.stdout.buffer.write(audio_wav(data))
    else:
        result = image_frames(data, sys.argv[1] == "gif")
        sys.stdout.buffer.write(json.dumps(result).encode())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # 不把解码器异常及用户媒体写入日志。
        sys.exit(2)
