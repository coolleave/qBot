"""后台上传媒体：有界内存、文件头检查，不接受本地路径或远程 URL。"""
import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath

# Base64 后低于官方二进制上传的 5M 限制，并留出 JSON 开销。
MAX_MEDIA_BYTES = 3_500_000


@dataclass(frozen=True)
class MediaUpload:
    kind: str
    filename: str
    data: bytes
    extension: str

    @property
    def summary(self) -> str:
        label = "图片" if self.kind == "image" else "语音"
        return f"【{label}】{self.filename}（{len(self.data)} 字节）"

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def check_source(self, source: str) -> None:
        if source == "qq_c2c" and self.kind == "audio" and self.extension != ".silk":
            raise ValueError("QQ 私聊语音目前按官方文档仅支持 SILK；MP3/WAV 仅用于群聊，暂不自动转码")


def validate_media(kind: str, filename: str, data: bytes) -> MediaUpload:
    if not data or len(data) > MAX_MEDIA_BYTES:
        raise ValueError("请选择非空且不超过 3.5 MB 的文件")
    name = PurePosixPath(filename.replace("\\", "/")).name
    if not name or len(name) > 150 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("文件名无效或过长")
    ext = PurePosixPath(name).suffix.lower()
    valid = False
    if kind == "image":
        valid = ((ext == ".png" and len(data) >= 33 and data.startswith(b"\x89PNG\r\n\x1a\n")
                  and data[12:16] == b"IHDR")
                 or (ext in (".jpg", ".jpeg") and data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9")))
    elif kind == "audio":
        valid = ((ext == ".silk" and len(data) > 12 and (data.startswith(b"#!SILK_V3") or data.startswith(b"\x02#!SILK_V3")))
                 or (ext == ".wav" and len(data) >= 44 and data[:4] == b"RIFF" and data[8:12] == b"WAVE")
                 or (ext == ".mp3" and len(data) >= 10 and
                     (data.startswith(b"ID3") or (data[0] == 255 and data[1] & 0xE0 == 0xE0))))
    if not valid:
        raise ValueError("文件类型与文件头不匹配：图片支持 PNG/JPG，语音支持 SILK，群聊另支持 MP3/WAV")
    return MediaUpload(kind, name, data, ext)
