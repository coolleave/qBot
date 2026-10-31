"""清理模型回复的常见 Markdown 外观，保留代码正文和普通链接。"""
import re

_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})([^`~]*)$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


def plain_text_reply(text: str) -> str:
    """按行区分代码围栏，避免把代码中的乘号、下划线等当作格式删除。"""
    lines = []
    fence_char, fence_size = "", 0
    for line in text.splitlines():
        fence = _FENCE.match(line)
        if fence_char:
            if fence and fence[1][0] == fence_char and len(fence[1]) >= fence_size and not fence[2].strip():
                fence_char, fence_size = "", 0
            else:
                lines.append(line)
            continue
        if fence:
            fence_char, fence_size = fence[1][0], len(fence[1])
            continue
        if _TABLE_SEPARATOR.fullmatch(line) or re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*", line):
            continue
        line = re.sub(r"^\s{0,3}#{1,6}\s+(.+?)(?:\s+#+)?\s*$", r"\1", line)
        line = re.sub(r"^\s{0,3}(?:>\s*)+", "", line)
        line = re.sub(r"^\s*[-+*]\s+(?:\[[ xX]\]\s*)?", "", line)
        line = re.sub(r"^\s*(\d+)\.\s+", r"\1、", line)
        # 先保留行内代码，防止其中的标记被当成强调或链接清理。
        parts = re.split(r"(`+[^`\n]+`+)", line)
        for index, part in enumerate(parts):
            if index % 2:
                parts[index] = part.strip("`")
                continue
            part = re.sub(r"!?\[([^]\n]*)\]\((https?://[^\s)]+)\)",
                          lambda match: f"{match[1]}（{match[2]}）" if match[1] else match[2], part)
            for marker in ("***", "___", "**", "__", "~~", "*", "_"):
                escaped = re.escape(marker)
                part = re.sub(r"(?<![A-Za-z0-9_])" + escaped + r"(?=\S)(.+?)(?<=\S)" + escaped + r"(?![A-Za-z0-9_])", r"\1", part)
            parts[index] = part
        line = "".join(parts)
        if line.strip().startswith("|") and line.strip().endswith("|"):
            line = "；".join(cell.strip() for cell in line.strip().strip("|").split("|"))
        lines.append(line)
    return "\n".join(lines).strip()
