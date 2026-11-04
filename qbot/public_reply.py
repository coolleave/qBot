"""面向聊天对象的模型正文校验；诊断异常不得包含模型原文。"""
import re
from langchain_core.messages import BaseMessage


class UnsafeModelReply(ValueError):
    """模型没有提供可安全发送的正式正文。"""


_REASONING_TAG = re.compile(r"<\s*(/?)\s*(think|thinking|analysis|reasoning)\b[^>]*>", re.I)
_INTERNAL_FIELD = re.compile(r"\b(?:family_role|role_label|is_owner|owner_version|owner_user_id)\b[\s\"'“”]*[:=：]", re.I)
_INTERNAL_CONTEXT = re.compile(r"当前发送者[：:]|同群主人近期原话|后台选择结果[：:]|系统提示(?:里|中|明确|要求|说)|记忆资料[：:]")
_DELIBERATION = (
    re.compile(r"(?:我|我们)(?:需要|应该|应当|必须|最好).{0,35}(?:判断用户|回应|回复|拒绝|遵守|回避|回答|人设)"),
    re.compile(r"(?:用户(?:问的是|的问题是|当前的问题|之前说)|我应该如何回应|为了符合|再看一下)"),
    re.compile(r"(?:可以(?:直接|简单)?(?:说|回答|回应)[：:]|或者[，,].{0,24}(?:我可以|更简单|因为|结合人设)|这样既.{0,40}又遵守)"),
)


def public_reply_text(message: BaseMessage | str) -> str:
    """只从正式文本提取答案；不读取additional_kwargs中的推理内容。"""
    content = message if isinstance(message, str) else message.content
    if isinstance(content, list):
        content = "".join(part if isinstance(part, str) else part.get("text", "")
                          for part in content if isinstance(part, str) or
                          (isinstance(part, dict) and part.get("type") == "text"
                           and isinstance(part.get("text"), str)))
    if not isinstance(content, str):
        raise UnsafeModelReply("模型未返回正文")
    # 标签有明确边界才剔除，缺失边界时不猜测哪一段是最终答案。
    pieces, stack, cursor = [], [], 0
    for match in _REASONING_TAG.finditer(content):
        if not stack:
            pieces.append(content[cursor:match.start()])
        tag = match[2].lower()
        if match[1]:
            if not stack or stack.pop() != tag:
                raise UnsafeModelReply("模型推理与正文边界异常")
        else:
            stack.append(tag)
        cursor = match.end()
    if stack:
        raise UnsafeModelReply("模型推理段未结束")
    pieces.append(content[cursor:])
    text = "".join(pieces).strip()
    if re.search(r"<\s*/?\s*(?:think|thinking|analysis|reasoning)\b", text, re.I):
        raise UnsafeModelReply("模型推理标签不完整")
    if not text:
        raise UnsafeModelReply("模型仅返回推理或空正文")
    fields = len(_INTERNAL_FIELD.findall(text))
    internal = bool(_INTERNAL_CONTEXT.search(text))
    deliberation = sum(bool(pattern.search(text)) for pattern in _DELIBERATION)
    if fields >= 2 or (fields and internal) or (internal and deliberation) or deliberation >= 2:
        raise UnsafeModelReply("模型正文包含内部分析，已阻止发送")
    return text


def history_reply_text(text: str) -> str:
    """历史异常回复不再喂给模型；原始数据库记录保留供管理员排查。"""
    if text.strip() == "这次问题和近期对话太长了，请缩短问题，或发送 /reset 清空近期对话后重试。":
        return ""

    try:
        return public_reply_text(text)
    except UnsafeModelReply:
        return ""
