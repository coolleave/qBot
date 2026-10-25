"""有界 WAV → 腾讯 SILK 编码；子进程隔离原生编码器并限制执行时间。"""
import asyncio
import io
import subprocess
import sys
import wave

from qbot.media import MAX_MEDIA_BYTES, MediaUpload, validate_media


def _convert(data: bytes) -> bytes:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "qbot.audio_codec"], input=data,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("语音转码超时，尚未上传或发送") from None
    if result.returncode:
        raise ValueError("语音转码失败，请确认已安装 silk-python；WAV 需为单声道 16 位 PCM，采样率 8/12/16/24 kHz")
    return result.stdout


async def to_private_audio(media: MediaUpload) -> MediaUpload:
    if media.kind != "audio" or media.extension != ".wav":
        return media
    data = await asyncio.to_thread(_convert, media.data)
    return validate_media("audio", media.filename[:-4] + ".silk", data)


def main() -> None:
    import pysilk

    data = sys.stdin.buffer.read(MAX_MEDIA_BYTES + 1)
    if not data or len(data) > MAX_MEDIA_BYTES:
        raise ValueError("invalid size")
    with wave.open(io.BytesIO(data), "rb") as audio:
        rate = audio.getframerate()
        if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                or audio.getcomptype() != "NONE" or rate not in (8000, 12000, 16000, 24000)
                or audio.getnframes() * 2 > MAX_MEDIA_BYTES):
            raise ValueError("unsupported WAV")
        expected = audio.getnframes() * 2
        pcm = audio.readframes(audio.getnframes())
    if not pcm or len(pcm) != expected:
        raise ValueError("incomplete PCM")
    # 编码器以 20 ms 为一帧，末帧补静音，避免丢失最后一个字的尾音。
    frame_bytes = rate // 50 * 2
    pcm += b"\x00" * (-len(pcm) % frame_bytes)
    output = io.BytesIO()
    pysilk.encode(io.BytesIO(pcm), output, rate, 24000, tencent=True)
    encoded = output.getvalue()
    validate_media("audio", "tts.silk", encoded)
    sys.stdout.buffer.write(encoded)


if __name__ == "__main__":
    main()
