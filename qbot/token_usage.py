"""提取供应商实际返回的 token 用量，缺失值保持未知。"""


def extract_token_usage(message: object) -> dict[str, int]:
    usage = getattr(message, "usage_metadata", None)
    metadata = getattr(message, "response_metadata", None)
    fallback = metadata.get("token_usage", {}) if isinstance(metadata, dict) else {}
    result: dict[str, int] = {}
    for name, alias in (("input_tokens", "prompt_tokens"),
                        ("output_tokens", "completion_tokens"),
                        ("total_tokens", "total_tokens")):
        value = usage.get(name) if isinstance(usage, dict) else None
        if value is None and isinstance(fallback, dict):
            value = fallback.get(alias)
        if type(value) is int and 0 <= value <= 9223372036854775807:
            result[name] = value
    if "total_tokens" not in result and "input_tokens" in result and "output_tokens" in result:
        total = result["input_tokens"] + result["output_tokens"]
        if total <= 9223372036854775807:
            result["total_tokens"] = total
    return result
