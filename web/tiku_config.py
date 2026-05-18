import json


def build_effective_tiku_config(user_tiku_config, settings=None):
    conf = dict(user_tiku_config or {})
    global_conf = dict((settings or {}).get("tiku_config") or {})
    settings = settings or {}

    conf.setdefault("true_list", "正确,对,√,是")
    conf.setdefault("false_list", "错误,错,×,否,不对,不正确")
    conf.setdefault("submit", "false")
    conf.setdefault("cover_rate", "0.9")
    conf.setdefault("delay", "1.0")
    conf.setdefault("likeapi_search", "false")
    conf.setdefault("likeapi_model", "deepseek-v3")
    conf.setdefault("url", "")
    conf.setdefault("endpoint", "")
    conf.setdefault("key", "")
    conf.setdefault("model", "")
    conf.setdefault("http_proxy", "")
    conf.setdefault("min_interval_seconds", "3")
    conf.setdefault("siliconflow_key", "")
    conf.setdefault("siliconflow_model", "deepseek-ai/DeepSeek-V3")
    conf.setdefault("siliconflow_endpoint", "https://api.siliconflow.cn/v1/chat/completions")
    conf.setdefault("multi_model", "false")
    conf.setdefault("models", "[]")
    conf.setdefault("search_enabled", "false")
    conf.setdefault("search_max_results", "3")
    conf.setdefault("voting_strategy", "referee")
    conf.setdefault("referee_model_index", "0")
    conf.setdefault("parallel_query_workers", str(settings.get("ai_parallel_query_workers", 2)))
    conf.setdefault("parallel_only_large_sets", "true" if settings.get("ai_parallel_only_large_sets", True) else "false")

    for key in ("endpoint", "key", "model"):
        conf[key] = str(conf.get(key) or "").strip()

    model_configs = _parse_models(conf.get("models"))
    if model_configs:
        for model_conf in model_configs:
            if model_conf.get("provider") == "AI":
                for key in ("endpoint", "key", "model"):
                    model_conf[key] = str(model_conf.get(key) or "").strip()
        conf["models"] = json.dumps(model_configs, ensure_ascii=False)

    return conf


def _parse_models(raw):
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []
