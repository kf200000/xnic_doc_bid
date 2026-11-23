"""技术标编写 Agent：把"批量编写"从同步长请求升级为可观测、可自检、可纠错的任务。

流程（与设计文档一致）：
  1) 前置闸门（app.py 接口层）：模型密钥 / 运行锁 / 存在待写章节；
  2) 规划 plan()：圈定章节（幂等：有技术要求且未编写）+ 预估，落 compose_jobs 任务单；
  3) 逐节循环 run_job()：装配五层上下文 → 生成（429/超时指数退避重试，重试时自动
     降级压缩上下文）→ 自检闸门 check() → 不合格带反馈重写 1 次 →
     落库（content_ai + summary_ai 回写）；
  4) 对齐审核：写完自动跑 tech_audit（纵向对齐 + 横向一致性），对齐分写回任务单；
  5) 汇总：耗时 / tokens / 重写次数 / 失败清单，写审计日志。

铁律不变：缺事实【待补充】占位禁止编造；单节失败不中断；覆盖正文默认关闭；
摘要随正文同次产出（零额外调用），成为下一章的前章上下文。
"""

import json
import re
import time

import composer
import llm_client
import tech_audit
from database import audit, connect, now_text

MAX_RETRY = 2                 # 429/超时等瞬时错误的重试次数
BACKOFF = (2, 6)              # 指数退避间隔（秒）
FLUFF_RE = re.compile(r"我方完全响应|绝对满足|国际一流|行业领先")

# 超时降级：重试时摘掉"参考层"，只留角色卡 + 基线卡 + 本节定位 + 本节要求 + 写作要求。
# 实测提示词约 2.3k 字符（1.5k tokens）并非超时主因，但换慢模型/长章节时压缩仍有兜底价值。
BLOCK_HEADS = ("【项目信息】", "【全书基线卡】", "【规格指标库】", "【统一术语】",
               "【本次编写章节】", "【章节族谱", "【前后章节衔接】",
               "【本节须逐条回应的技术要求】", "【写作要求】")
DROP_PREFIX = ("【规格指标库】", "【统一术语】", "【章节族谱", "【前后章节衔接】")


def compact_messages(messages):
    """按块摘除上下文参考层；块内以【…】开头的小标题继承所属块的保留/摘除状态。"""
    out = []
    for msg in messages:
        if msg["role"] != "user":
            out.append(dict(msg))
            continue
        keep, skip = [], False
        for line in msg["content"].split("\n"):
            if any(line.startswith(h) for h in BLOCK_HEADS):
                skip = any(line.startswith(d) for d in DROP_PREFIX)
            if not skip:
                keep.append(line)
        out.append({**msg, "content": "\n".join(keep)})
    return out


def plan(conn, bid_id, group="技术标", overwrite=False, section_ids=None):
    """规划：圈定待写章节（不调模型）。幂等——重复发起只会跳过已写章节。"""
    sections = conn.execute(
        "SELECT * FROM sections WHERE bid_id=? AND group_name=? ORDER BY sort_order",
        (bid_id, group)).fetchall()
    wanted = set(section_ids) if section_ids is not None else None
    targets, skipped = [], []
    for s in sections:
        if wanted is not None and s["id"] not in wanted:
            continue
        reqs = composer.section_requirements(conn, s)
        if not reqs:
            skipped.append({"code": s["code"], "title": s["title"], "reason": "本节无技术要求"})
            continue
        if (s["content_ai"] or "").strip() and not overwrite:
            skipped.append({"code": s["code"], "title": s["title"], "reason": "已有正文（未覆盖）"})
            continue
        targets.append({"section_id": s["id"], "code": s["code"], "title": s["title"],
                        "req_count": len(reqs)})
    # 实测 qwen3.8-max 关闭思考后单节约 30s；含自检重写按 45s/节估算更接近真实
    return {"sections": targets, "skipped": skipped,
            "estimate_seconds": len(targets) * 45}


def check(ctx, text):
    """自检闸门（本地计算，不调模型、零成本）：篇幅 / 逐条响应 / 占位 / 空话 / 复述父章。

    返回 (passed, issues)；issues 会被拼进重写反馈。
    """
    issues = []
    reqs = ctx["requirements"]
    # 篇幅不再是硬闸门：默认 800 字只是建议值，允许章节偏短甚至留白，
    # 避免为凑字触发重写（多一次模型调用 + 长文超时风险）与套话灌水。
    # 仅当正文明显敷衍（<150 字）才提示补充要点。
    if len(text) < 150:
        issues.append(f"正文过短（{len(text)} 字）：本章可留白，但至少逐条列出要求与响应要点")
    comp = tech_audit._compact(text)
    titled = [(r, tech_audit._compact(r["title"])) for r in reqs]
    titled = [(r, t) for r, t in titled if len(t) >= 6]
    if titled:
        hit = sum(1 for _r, t in titled if t in comp)
        if hit < len(titled) * 0.5:
            miss = [r["title"] for r, t in titled if t not in comp][:3]
            issues.append(f"要求条款标题命中率 {hit}/{len(titled)} 低于 50%"
                          f"（未命中如：{'、'.join(miss)}），疑似未逐条响应")
    todos = len(re.findall(r"【待补充[：:]", text))
    if todos > max(4, len(reqs)):
        issues.append(f"【待补充】占位 {todos} 处偏多")
    sentences = re.split(r"[。；\n]", text)
    fluff = len(FLUFF_RE.findall(text))
    if fluff and fluff / max(1, len(sentences)) > 0.15:
        issues.append(f"空话表态句 {fluff} 处（如“我方完全响应”），缺少实质内容")
    parent = ctx.get("parent_body") or ""
    if parent and len(comp) > 300:
        pcomp = tech_audit._compact(parent)
        windows = [comp[i:i + 6] for i in range(0, len(comp) - 5, 6)]
        if windows:
            r6 = sum(1 for w in windows if w in pcomp) / len(windows)
            if r6 > 0.35:
                issues.append(f"与父章节正文 6 字片段重合率 {r6:.0%} 偏高，疑似越界复述，应收窄到本节范围")
    return (not issues), issues


def _chat_retry(messages, stats):
    """模型调用 + 退避重试（2s、6s）。

    - 只重试瞬时错误（429 / 超时 / 连接失败）；鉴权、地址、返回格式错误立即抛出，
      避免同一错误白等三次并重复扣费；
    - 第 2 次起自动降级：摘掉上下文参考层、收紧 max_tokens、放宽 timeout，
      而不是把同一条请求原样重发。
    """
    last = None
    for attempt in range(MAX_RETRY + 1):
        stats["llm_calls"] += 1
        use, kwargs = messages, {}
        if attempt:
            use = compact_messages(messages)
            kwargs = {"max_tokens": 2600, "timeout": 240}
            stats["degraded"] = stats.get("degraded", 0) + 1
        try:
            return llm_client.chat(use, **kwargs)
        except llm_client.LLMError as exc:
            last = exc
            if not getattr(exc, "retryable", True) or attempt >= MAX_RETRY:
                break
            stats["retries"] += 1
            time.sleep(BACKOFF[attempt])
    raise last


def _write_section(conn, section, instructions, stats):
    """单节 Agent 循环：装配→生成→自检→（不合格）带反馈重写 1 次→落库。"""
    ctx = composer.build_context(conn, section)
    parts = section["code"].split(".")
    parent = None
    if len(parts) > 1:
        parent = conn.execute(
            "SELECT content_ai FROM sections WHERE bid_id=? AND code=?",
            (section["bid_id"], ".".join(parts[:-1]))).fetchone()
    ctx["parent_body"] = (parent["content_ai"] or "") if parent else ""

    text, summary, passed, issues = "", "", False, []
    rewritten = False
    deg_before = stats.get("degraded", 0)
    for attempt in range(2):                      # 初稿 + 最多 1 次重写
        inst = instructions
        if attempt:
            stats["rewrites"] += 1
            rewritten = True
            inst = (instructions + "\n" if instructions else "") + (
                "上一稿自检未通过：" + "；".join(issues) +
                "。请重写：逐条回应全部技术要求并保留条款标题，篇幅达标，涉及未提供的事实仍用【待补充】占位。")
        messages = composer.build_messages(ctx)
        if inst:
            messages[-1]["content"] += f"\n\n【补充编写要求】{inst}"
        resp = _chat_retry(messages, stats)
        usage = resp.get("usage") or {}
        stats["tokens_prompt"] += int(usage.get("prompt_tokens") or 0)
        stats["tokens_completion"] += int(usage.get("completion_tokens") or 0)
        stats["model"] = resp.get("model") or stats["model"]
        text, summary = composer.split_summary(resp["text"])
        passed, issues = check(ctx, text)
        if passed:
            break
    if not text.strip():
        raise ValueError("生成内容为空")
    conn.execute(
        """UPDATE sections SET content_ai=?, summary_ai=?, ai_model=?, ai_written_at=?, ai_generated=1,
           status='编制中', updated_at=? WHERE id=?""",
        (text, summary, stats["model"] or "", now_text(), now_text(), section["id"]))
    # 锚点章：第一个写成章节的摘要写入全书基线卡，后续章节向其收敛
    bid = conn.execute("SELECT baseline_card FROM bids WHERE id=?", (section["bid_id"],)).fetchone()
    if bid and not (bid["baseline_card"] or "").strip() and summary:
        conn.execute("UPDATE bids SET baseline_card=?, updated_at=? WHERE id=?",
                     (summary[:600], now_text(), section["bid_id"]))
    from services import calculate_progress
    progress = calculate_progress(conn, section["bid_id"])
    conn.execute("UPDATE bids SET progress=?, updated_at=? WHERE id=?",
                 (progress, now_text(), section["bid_id"]))
    conn.commit()                                # 逐节提交：中断不丢已写内容
    return {"text": text, "summary": summary, "requirements": len(ctx["requirements"]),
            "must": ctx["must_count"], "rewritten": rewritten, "issues": issues,
            "degraded": stats.get("degraded", 0) - deg_before}


def _job_update(job_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE compose_jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
        conn.commit()


def _finish(job_id, status, stats, items, error=None,
            audit_score=None, audit_verdict=None, audit_open=None):
    _job_update(
        job_id, status=status, finished_at=now_text(),
        done_n=sum(1 for i in items if i.get("status") == "written"),
        failed_n=sum(1 for i in items if i.get("status") == "failed"),
        tokens_prompt=stats.get("tokens_prompt", 0),
        tokens_completion=stats.get("tokens_completion", 0),
        llm_calls=stats.get("llm_calls", 0), retries=stats.get("retries", 0),
        rewrites=stats.get("rewrites", 0), model=stats.get("model") or "",
        result=json.dumps(items, ensure_ascii=False), error=error,
        audit_score=audit_score, audit_verdict=audit_verdict,
        audit_open=audit_open)


def run_job(bid_id, job_id, group="技术标", overwrite=False, instructions="", section_ids=None,
            resume=False):
    """后台线程主循环：逐节执行，单节失败不中断；收尾跑对齐审核并写审计日志。

    resume=True 表示"暂停/中断后续跑"：沿用同一任务单，从 DB 恢复本轮的章节范围、
    已完成清单与统计，只跑尚未处理的章节，进度条与任务编号保持连续。
    """
    stats = {"llm_calls": 0, "retries": 0, "rewrites": 0, "degraded": 0,
             "tokens_prompt": 0, "tokens_completion": 0, "model": ""}
    items = []
    if resume:
        with connect() as conn:
            prev = conn.execute("SELECT * FROM compose_jobs WHERE id=?", (job_id,)).fetchone()
        if prev:
            group = prev["group_name"] or group
            overwrite = bool(prev["overwrite"])
            instructions = prev["instructions"] or ""
            try:
                section_ids = json.loads(prev["section_ids"] or "") or None
            except (TypeError, ValueError):
                section_ids = None
            try:
                items = json.loads(prev["result"] or "[]")
            except (TypeError, ValueError):
                items = []
            for key in ("llm_calls", "retries", "rewrites", "tokens_prompt", "tokens_completion"):
                stats[key] = prev[key] or 0
            stats["model"] = prev["model"] or ""
    handled = {i.get("section_id") for i in items if i.get("section_id")}
    try:
        with connect() as conn:
            targets = [e for e in plan(conn, bid_id, group, overwrite, section_ids)["sections"]
                       if e["section_id"] not in handled]
        for entry in targets:
            with connect() as conn:
                job = conn.execute("SELECT status FROM compose_jobs WHERE id=?", (job_id,)).fetchone()
                if not job or job["status"] != "running":   # pausing / canceling / interrupted
                    if job and job["status"] == "pausing":
                        _finish(job_id, "paused", stats, items,
                                error="用户暂停（已完成部分保留，可点「继续编写」续跑）")
                    else:
                        _finish(job_id, "canceled", stats, items,
                                error="任务已停止（已完成部分保留）")
                    return
            _job_update(job_id, current_code=entry["code"], current_title=entry["title"])
            with connect() as conn:
                section = conn.execute("SELECT * FROM sections WHERE id=?",
                                       (entry["section_id"],)).fetchone()
                item = {"code": entry["code"], "title": entry["title"],
                        "section_id": entry["section_id"],
                        "requirements": entry["req_count"], "status": "writing"}
                try:
                    result = _write_section(conn, section, instructions, stats)
                    item.update(status="written", chars=len(result["text"]),
                                summary=bool(result["summary"]), requirements=result["requirements"],
                                must=result["must"], rewritten=result["rewritten"],
                                issues=result["issues"], degraded=result["degraded"])
                except Exception as exc:  # noqa: BLE001 - 单节失败不中断，原因留档供补写
                    item.update(status="failed", error=str(exc)[:200])
            items.append(item)
            _job_update(job_id, result=json.dumps(items, ensure_ascii=False),
                        done_n=sum(1 for i in items if i["status"] == "written"),
                        failed_n=sum(1 for i in items if i["status"] == "failed"),
                        tokens_prompt=stats["tokens_prompt"],
                        tokens_completion=stats["tokens_completion"],
                        llm_calls=stats["llm_calls"], retries=stats["retries"],
                        rewrites=stats["rewrites"], model=stats["model"] or "")
        # 收尾：对齐审核（纵向 + 横向一致性）+ 审计日志
        with connect() as conn:
            rep = tech_audit.audit(conn, bid_id)
            summary = rep["summary"] if rep else {}
            ok = sum(1 for i in items if i["status"] == "written")
            audit(conn, "技术标编写Agent", "bid", bid_id,
                  f"任务 #{job_id}：生成 {ok} 节，失败 {len(items) - ok} 节，"
                  f"重写 {stats['rewrites']} 次，超时降级 {stats.get('degraded', 0)} 次，"
                  f"tokens {stats['tokens_prompt'] + stats['tokens_completion']}"
                  + (f"，对齐分 {summary.get('score')}（{summary.get('verdict')}）" if summary else ""))
            conn.commit()
        _finish(job_id, "done", stats, items,
                audit_score=(summary or {}).get("score"),
                audit_verdict=(summary or {}).get("verdict"),
                audit_open=json.dumps((summary or {}).get("open") or {}, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 - 环境级失败（密钥失效等）：定格任务单
        _finish(job_id, "failed", stats, items, error=str(exc)[:300])


def job_view(conn, bid_id, job_id):
    row = conn.execute("SELECT * FROM compose_jobs WHERE id=? AND bid_id=?",
                       (job_id, bid_id)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["items"] = json.loads(d.pop("result") or "[]")
    except (TypeError, ValueError):
        d["items"] = []
    raw_open = d.get("audit_open")
    try:
        d["audit_open"] = json.loads(raw_open) if raw_open else {}
    except (TypeError, ValueError):
        d["audit_open"] = {}
    return d
