"""普通聊天的输入预算；只裁剪本次请求，不修改已保存的历史或记忆。"""
import math
import re
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate


class ContextBudgetExceeded(ValueError):
    pass


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
    target = extended_budget if needs_extended_context(text) else normal_budget
    before = estimate_tokens(prompt.invoke(inputs).to_messages())
    result = dict(inputs)
    history = inputs.get("history", [])
    # 只选择完整问答对，不能让孤立的助手回复失去对应问题。
    pairs = []
    for index in range(len(history) - 1):
        if isinstance(history[index], HumanMessage) and isinstance(history[index + 1], AIMessage):
            pairs.append(history[index:index + 2])
    recent = pairs[-3:]
    result["history"] = [message for pair in recent for message in pair]
    memory_lines = memory_units(inputs.get("memory", []))
    protected = [line for line in memory_lines if line.startswith("同群主人近期原话（用户资料）：")]
    result["memory"] = [HumanMessage(content="\n".join(protected))] if protected else []
    # 主人参考属于行为边界，保持完整；摘要和普通长期资料才参与裁剪。
    required = estimate_tokens(prompt.invoke(result).to_messages())
    if required > extended_budget:
        if metrics is not None:
            metrics["context_budget"] = {"estimated": True, "status": "exceeded",
                "before_tokens": before, "required_tokens": required, "target_tokens": extended_budget}
        raise ContextBudgetExceeded("必要上下文超过预算")
    target = min(extended_budget, max(target, required))
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
        trial = {**result, "memory": [HumanMessage(content="\n".join(candidate))]}
        if estimate_tokens(prompt.invoke(trial).to_messages()) <= target:
            selected_lines = candidate
            result["memory"] = trial["memory"]
    kept_pairs = list(recent)
    for pair in reversed(pairs[:-3]):
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
            "target_tokens": target, "before_tokens": before,
            "after_tokens": estimate_tokens(prompt.invoke(result).to_messages()),
            "parts": parts, "history_turns_before": len(pairs),
            "history_turns_after": len(kept_pairs), "duplicate_memory_units": duplicates,
        }
    return result
