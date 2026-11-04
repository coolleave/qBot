"""普通聊天的输入预算；只裁剪本次请求，不修改已保存的历史或记忆。"""
import math
import re
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate


def estimate_tokens(messages: list[BaseMessage]) -> int:
    """无网络的跨模型粗估：ASCII约3字符/token，非ASCII约1.5 token/字符。"""
    total = 0
    for message in messages:
        value = message.content if isinstance(message.content, str) else str(message.content)
        ascii_count = sum(ord(char) < 128 for char in value)
        total += 6 + math.ceil(ascii_count / 3 + (len(value) - ascii_count) * 1.5)
    return total


def needs_extended_context(text: str) -> bool:
    return len(text) > 300 or bool(re.search(
        r"之前|以前|上次|记得|回忆|还记|刚才|前面|总结|详细|分析|比较|为什么|那[他她它]|继续", text))


def memory_units(messages: list[BaseMessage]) -> list[str]:
    """多行摘要/条目作为整体保留，避免裁掉后半段的条件或否定语句。"""
    units = []
    for message in messages:
        for line in str(message.content).splitlines():
            if not units or re.match(r"^(?:【当前会话|会话摘要：|#\d+ 主体=|同群主人近期原话（用户资料）：)", line):
                units.append(line)
            else:
                units[-1] += "\n" + line
    return units


def budget_inputs(prompt: ChatPromptTemplate, inputs: dict, text: str, normal_budget: int,
                  extended_budget: int, metrics: dict | None = None) -> dict:
    """优先固定消息和最近三轮，随后记忆与更早历史；预算不足按完整单元省略。"""
    extended_budget = max(normal_budget, extended_budget)
    configured_target = extended_budget if needs_extended_context(text) else normal_budget
    before = estimate_tokens(prompt.invoke(inputs).to_messages())
    result = dict(inputs)
    history = inputs.get("history", [])
    # 只选择完整问答对，不能让孤立的助手回复失去对应问题。
    pairs = []
    for index in range(len(history) - 1):
        if isinstance(history[index], HumanMessage) and isinstance(history[index + 1], AIMessage):
            pairs.append(history[index:index + 2])
    result["history"] = []
    memory_lines = memory_units(inputs.get("memory", []))
    protected = [line for line in memory_lines if line.startswith("同群主人近期原话（用户资料）：")]
    result["memory"] = [HumanMessage(content="\n".join(protected))] if protected else []
    result["memory_rules"] = inputs.get("memory_rules", []) if protected else []
    required = estimate_tokens(prompt.invoke(result).to_messages())
    # 这是性能优化目标，不是模型上下文容量；长人设不能让所有后续短消息被拒绝。
    target = max(configured_target, required + min(2000, normal_budget // 2))
    recent = []
    for pair in reversed(pairs[-3:]):
        candidate = [message for group in [pair] + recent for message in group]
        trial = {**result, "history": candidate}
        if estimate_tokens(prompt.invoke(trial).to_messages()) > target:
            break
        recent.insert(0, pair)
        result["history"] = candidate
    seen = set(protected)
    selected_lines = list(protected)
    duplicates = 0
    history_text = [" ".join(str(message.content).split()) for message in result["history"]]
    for line in memory_lines:
        if line in protected:
            continue
        key = " ".join(line.split())
        if not key:
            continue
        if key in seen or key in history_text:
            duplicates += 1
            continue
        seen.add(key)
        candidate = selected_lines + [line]
        trial = {**result, "memory": [HumanMessage(content="\n".join(candidate))],
                 "memory_rules": inputs.get("memory_rules", [])}
        if estimate_tokens(prompt.invoke(trial).to_messages()) <= target:
            selected_lines = candidate
            result["memory"] = trial["memory"]
            result["memory_rules"] = trial["memory_rules"]
    kept_pairs = list(recent)
    # 最近一轮放不下时，不跳过它改取更旧记录，以免短追问接错上下文。
    older_pairs = pairs[:-3] if len(recent) == min(3, len(pairs)) else []
    for pair in reversed(older_pairs):
        candidate = [message for group in [pair] + kept_pairs for message in group]
        trial = {**result, "history": candidate}
        if estimate_tokens(prompt.invoke(trial).to_messages()) > target:
            break
        kept_pairs.insert(0, pair)
        result["history"] = candidate
    if metrics is not None:
        parts = {key: estimate_tokens(value) for key, value in result.items() if isinstance(value, list)}
        fixed_inputs = {key: [] for key in result}
        parts["fixed_rules"] = estimate_tokens(prompt.invoke(fixed_inputs).to_messages())
        metrics["context_budget"] = {
            "estimator": "ascii_div3_nonascii_mul1.5_v1", "estimated": True,
            "configured_target_tokens": configured_target, "required_tokens": required,
            "target_raised": target > configured_target,
            "target_tokens": target, "before_tokens": before,
            "after_tokens": estimate_tokens(prompt.invoke(result).to_messages()),
            "parts": parts, "history_turns_before": len(pairs),
            "history_turns_after": len(kept_pairs), "duplicate_memory_units": duplicates,
        }
    return result
