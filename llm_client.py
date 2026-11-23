"""大模型客户端（OpenAI 兼容协议）。

配置来源优先级：环境变量（XNIC_LLM_BASE_URL / XNIC_LLM_API_KEY / XNIC_LLM_MODEL）
> data/llm.json 中当前默认配置（页面"大模型配置"写入，密钥仅存本机）。

兼容 OpenAI / DeepSeek / 通义（compatible-mode）/ Kimi / 智谱 / 本地 vLLM、Ollama 等
任何提供 POST {base_url}/chat/completions 的服务。未配置密钥时不伪造调用，由上层降级。

data/llm.json 支持两种结构：
1. 旧版单配置：{"base_url": ..., "model": ..., "api_key": ...}（读写时自动兼容）；
2. 新版多配置：{"version": 2, "active_id": "...", "models": [{id,name,base_url,model,api_key,...}]}。
"""

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DEFAULTS = {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "api_key": "",
    "temperature": 0.35,
    "max_tokens": 4000,
    # 非流式长文（约 900~2400 字）在慢模型上可能超过 120s；实测 qwen3.8-max
    # 关闭思考后 29.5s 完成，放宽到 180s 给其他模型留余量。
    "timeout": 180,
    # None=自动识别 / True=强制关闭深度思考 / False=保持模型默认
    "disable_thinking": None,
}

PROFILE_FIELDS = ("name", "base_url", "model", "api_key", "temperature",
                  "max_tokens", "timeout", "disable_thinking")

# 百炼 OpenAI 兼容模式：Qwen3 系列（含 qwen3.8-max）默认开启混合思考，
# 非流式调用须等"思考过程 + 正文"全部生成完才返回，长文写作极易超时。
# enable_thinking 为 HTTP 请求体顶层参数（非 OpenAI 标准），仅对这些服务注入。
AUTO_NO_THINK_MODELS = ("qwen3",)


def thinking_disabled(cfg=None):
    """本次请求是否注入 enable_thinking=false。"""
    cfg = cfg or load_config()
    v = cfg.get("disable_thinking")
    if v is True or v == "1":
        return True
    if v is False or v == "0":
        return False
    host = str(cfg.get("base_url", "")).lower()
    model = str(cfg.get("model", "")).lower()
    on_dashscope = "dashscope" in host or "aliyuncs.com" in host
    return on_dashscope and model.startswith(AUTO_NO_THINK_MODELS)


class LLMError(RuntimeError):
    """retryable=False 表示配置/格式类错误，重试没有意义，应立即失败。"""

    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


def config_path():
    custom = os.environ.get("XNIC_LLM_CONFIG")
    return Path(custom) if custom else BASE_DIR / "data" / "llm.json"


def _read_store():
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _float_in_range(value, default, low, high):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if low <= n <= high else default


def _positive_int(value, default):
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _tristate(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return None


def _normalise_profile(raw, fallback_id=None, fill_defaults=True):
    raw = raw if isinstance(raw, dict) else {}
    base_url = str(raw.get("base_url") or "").strip().rstrip("/")
    model = str(raw.get("model") or "").strip()
    if fill_defaults:
        base_url = base_url or DEFAULTS["base_url"]
        model = model or DEFAULTS["model"]
    return {
        "id": str(raw.get("id") or fallback_id or uuid.uuid4().hex[:12]),
        "name": (str(raw.get("name") or "").strip() or model or "默认模型"),
        "base_url": base_url,
        "model": model,
        "api_key": str(raw.get("api_key") or "").strip(),
        "temperature": _float_in_range(
            raw.get("temperature", DEFAULTS["temperature"]), DEFAULTS["temperature"], 0.0, 1.0),
        "max_tokens": _positive_int(raw.get("max_tokens"), DEFAULTS["max_tokens"]),
        "timeout": _positive_int(raw.get("timeout"), DEFAULTS["timeout"]),
        "disable_thinking": _tristate(raw.get("disable_thinking")),
    }


def _profiles_from_store(store):
    """读取配置列表；旧版扁平结构视作唯一配置，不改写文件。"""
    models = store.get("models")
    if isinstance(models, list) and models:
        profiles = [_normalise_profile(item) for item in models if isinstance(item, dict)]
        active_id = str(store.get("active_id") or "")
        if not any(p["id"] == active_id for p in profiles):
            active_id = profiles[0]["id"] if profiles else ""
        return profiles, active_id
    if any(k in store for k in PROFILE_FIELDS):
        legacy = {k: store[k] for k in PROFILE_FIELDS if k in store}
        legacy["id"] = str(store.get("active_id") or "legacy")
        legacy["name"] = store.get("name") or legacy.get("model") or "默认模型"
        return [_normalise_profile(legacy)], legacy["id"]
    return [], ""


def _write_store(profiles, active_id):
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"version": 2, "active_id": active_id, "models": profiles},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _apply_env(cfg, apply_env=True):
    cfg = dict(cfg)
    if apply_env:
        for env, key in (("XNIC_LLM_BASE_URL", "base_url"), ("XNIC_LLM_API_KEY", "api_key"),
                         ("XNIC_LLM_MODEL", "model")):
            if os.environ.get(env):
                cfg[key] = os.environ[env].strip()
        v = os.environ.get("XNIC_LLM_DISABLE_THINKING", "").strip().lower()
        if v in ("0", "1"):
            cfg["disable_thinking"] = v == "1"
    cfg["base_url"] = str(cfg.get("base_url") or DEFAULTS["base_url"]).rstrip("/")
    cfg["model"] = str(cfg.get("model") or DEFAULTS["model"])
    return cfg


def has_env_override():
    """是否有环境变量覆盖了页面配置（页面仅提示，不改变优先级）。"""
    return any(os.environ.get(key) for key in
               ("XNIC_LLM_BASE_URL", "XNIC_LLM_API_KEY", "XNIC_LLM_MODEL",
                "XNIC_LLM_DISABLE_THINKING"))


def load_config(profile_id=None, apply_env=True):
    """当前生效配置；profile_id 可指定某套配置（用于分别测试）。"""
    profiles, active_id = _profiles_from_store(_read_store())
    wanted = profile_id or active_id
    cfg = dict(DEFAULTS)
    if wanted:
        found = next((p for p in profiles if p["id"] == wanted), None)
        if found:
            cfg.update(found)
    return _apply_env(cfg, apply_env)


def list_profiles():
    """配置列表（密钥只回显掩码），供页面渲染模型清单。"""
    profiles, active_id = _profiles_from_store(_read_store())
    result = []
    for p in profiles:
        view = dict(p)
        view["api_key"] = masked_key(p)
        view["api_key_set"] = bool(p["api_key"])
        view["configured"] = is_configured(p)
        view["active"] = p["id"] == active_id
        result.append(view)
    return result


def get_active_profile_id():
    return _profiles_from_store(_read_store())[1]


def profile_config(profile_id=None, apply_env=False):
    """取指定配置原文；不指定则取默认配置。默认不套环境变量，便于"分别测试"。"""
    profiles, active_id = _profiles_from_store(_read_store())
    wanted = profile_id or active_id
    if not wanted:
        return load_config(apply_env=apply_env)
    found = next((p for p in profiles if p["id"] == wanted), None)
    if not found:
        raise ValueError("模型配置不存在或已被删除")
    return _apply_env({**DEFAULTS, **found}, apply_env)


def config_from_values(values):
    """用表单草稿构造临时配置（测试连接用，不落盘）。"""
    return _apply_env({**DEFAULTS, **_normalise_profile(values)}, apply_env=False)


def overlay_config(cfg, values):
    """在已有配置上叠加表单改动；空 api_key 表示保留原密钥。"""
    merged = dict(cfg or {})
    for key in PROFILE_FIELDS:
        if key not in values:
            continue
        value = values[key]
        if key == "api_key" and not str(value or "").strip():
            continue
        if key == "disable_thinking" and value == "":
            continue
        merged[key] = value
    return _apply_env({**DEFAULTS, **_normalise_profile(merged)}, apply_env=False)


def upsert_profile(values, profile_id=None):
    """新增或更新一套配置；profile_id 为空表示新增。"""
    values = values or {}
    profiles, active_id = _profiles_from_store(_read_store())
    if profile_id:
        index = next((i for i, p in enumerate(profiles) if p["id"] == profile_id), -1)
        if index < 0:
            raise ValueError("模型配置不存在或已被删除")
        merged = dict(profiles[index])
        for key in PROFILE_FIELDS:
            if key not in values:
                continue
            value = values[key]
            # 编辑时密钥留空表示沿用原密钥，避免误清空
            if key == "api_key" and not str(value or "").strip():
                continue
            merged[key] = value
        profile = _normalise_profile(merged, fallback_id=profile_id, fill_defaults=False)
        if not profile["base_url"] or not profile["model"]:
            raise ValueError("接口地址和模型名不能为空")
        profiles[index] = profile
    else:
        profile = _normalise_profile(values, fill_defaults=False)
        if not profile["base_url"] or not profile["model"]:
            raise ValueError("接口地址和模型名不能为空")
        profiles.append(profile)
        if not active_id:
            active_id = profile["id"]
    if not active_id and profiles:
        active_id = profiles[0]["id"]
    _write_store(profiles, active_id)
    return profile


def set_active_profile(profile_id):
    profiles, _ = _profiles_from_store(_read_store())
    if not any(p["id"] == profile_id for p in profiles):
        raise ValueError("模型配置不存在或已被删除")
    _write_store(profiles, profile_id)
    return load_config()


def delete_profile(profile_id):
    profiles, active_id = _profiles_from_store(_read_store())
    left = [p for p in profiles if p["id"] != profile_id]
    if len(left) == len(profiles):
        raise ValueError("模型配置不存在或已被删除")
    if active_id == profile_id:
        active_id = left[0]["id"] if left else ""
    _write_store(left, active_id)
    return active_id


def save_config(base_url=None, model=None, api_key=None, temperature=None,
                name=None, profile_id=None, make_active=None):
    """兼容旧调用：更新默认配置（无配置时新建）；传入 profile_id 则更新指定配置。"""
    profiles, active_id = _profiles_from_store(_read_store())
    target = profile_id or active_id
    payload = {}
    if base_url is not None:
        payload["base_url"] = base_url
    if model is not None:
        payload["model"] = model
    if api_key is not None:
        payload["api_key"] = api_key
    if temperature is not None:
        payload["temperature"] = temperature
    if name is not None:
        payload["name"] = name
    profile = upsert_profile(payload, profile_id=target)
    if make_active:
        set_active_profile(profile["id"])
    return load_config()


def is_configured(cfg=None):
    cfg = cfg or load_config()
    if cfg.get("api_key"):
        return True
    # 本地推理服务（Ollama / vLLM）通常不需要密钥
    host = cfg.get("base_url", "")
    return any(h in host for h in ("127.0.0.1", "localhost", "0.0.0.0"))


def masked_key(cfg=None):
    cfg = cfg or load_config()
    key = cfg.get("api_key") or ""
    if not key:
        return ""
    return f"{key[:4]}****{key[-4:]}" if len(key) > 10 else "****"


def chat(messages, temperature=None, max_tokens=None, timeout=None, cfg=None):
    """调用 /chat/completions，返回 {text, model, provider, usage, elapsed}。"""
    cfg = cfg if cfg is not None else load_config()
    url = cfg["base_url"]
    if not url.endswith("/chat/completions"):
        url = url + "/chat/completions"
    no_think = thinking_disabled(cfg)
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": cfg.get("temperature", 0.35) if temperature is None else temperature,
        "max_tokens": max_tokens or cfg.get("max_tokens", 3000),
        "stream": False,
    }
    # 百炼 Qwen3 混合思考模型：直接 HTTP 调用时把该参数放在请求体顶层即可
    if no_think:
        payload["enable_thinking"] = False
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {})},
        method="POST")
    started = time.time()
    wait = timeout or cfg.get("timeout", 180)
    try:
        with urllib.request.urlopen(req, timeout=wait) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "ignore")[:300]
        except Exception:  # noqa: BLE001 - 错误体读取失败不影响主错误信息
            detail = ""
        hint = {401: "密钥无效或已过期", 403: "无访问权限", 404: "接口地址不存在（请确认 base_url 与模型名）",
                429: "调用频率/额度受限"}.get(exc.code, "上游返回错误")
        # 仅限流可重试；鉴权/地址/模型名错误重试只会白等并重复扣费
        raise LLMError(f"大模型调用失败（{exc.code} {hint}）{('：' + detail) if detail else ''}",
                       retryable=(exc.code == 429)) from exc
    except urllib.error.URLError as exc:
        raise LLMError(f"无法连接大模型服务（{cfg['base_url']}）：{exc.reason}") from exc
    except TimeoutError as exc:
        why = ("已关闭深度思考仍超时" if no_think else
               "当前模型默认开启深度思考，非流式长文会显著变慢（建议关闭思考或换更快模型）")
        raise LLMError(f"大模型响应超时（等待超过 {wait}s；{why}），可增大 timeout 或缩短本章篇幅") from exc
    try:
        message = data["choices"][0]["message"]
        text = message.get("content")
        finish = data["choices"][0].get("finish_reason")
        # 推理模型（如 DeepSeek-R1/V4.1 系列）思考内容与正文分离；正文为空时以思考内容兜底提示
        reasoning = (message.get("reasoning_content") or "").strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("大模型返回格式异常，未取到正文内容", retryable=False) from exc
    text = (text or "").strip()
    if not text and reasoning:
        raise LLMError("模型只返回了思考过程、正文为空（max_tokens 被思考耗尽），请增大 max_tokens 后重试",
                       retryable=False)
    if not text and finish == "length":
        raise LLMError("生成长度不足被截断（finish=length），请增大 max_tokens 后重试",
                       retryable=False)
    return {"text": text, "model": data.get("model") or cfg["model"],
            "provider": "llm", "usage": data.get("usage") or {},
            "elapsed": round(time.time() - started, 1)}


def probe(cfg=None):
    """配置自检：发一条极短消息验证连通性与鉴权。

    注意：推理模型的思考内容同样消耗 max_tokens，探针必须放宽额度，
    否则 token 全被思考耗尽、正文为空，会被误判为"测试不通过"。
    """
    result = chat([{"role": "user", "content": "请只回复：连接正常"}],
                  max_tokens=512, timeout=60, cfg=cfg)
    return {"ok": True, "model": result["model"], "reply": result["text"][:60] or "（模型已响应）",
            "elapsed": result["elapsed"]}
