"""技术标目录树 ↔ 原技术规格书：对齐审核引擎。

在标书编制页左侧技术目录树上逐节核验，产出可逐条签认的"审核项"：

  1) 目录对齐：技术规格编写目录的每一节是否落到技术标章节（未映射 / 映射章节已删除）；
  2) 条目响应：每条规格要求（含 ★ 必须条款）在正文里是否有实质响应，而非仅复述标题；
  3) 指标落文：数量、精度、带宽、标准号等指标值是否出现在正文，防止"响应了但没给数"；
  4) 成文质量：空章节、篇幅不足、占位符未补、模板稿未重写、状态未更新；
  5) 人工签认：审核项可"确认 / 忽略"并留痕，复核后不再计入扣分。

设计原则：只做"疑点定位 + 证据引用"（原文页码、缺失关键词），不做自动判定；
所有结论都能追溯到具体条款或章节，最终由技术负责人复核签字。
"""

import json
import re
import unicodedata
from datetime import datetime

HIGH, MID, LOW = "high", "mid", "low"
LEVEL_LABEL = {HIGH: "高", MID: "中", LOW: "低"}

KIND_LABEL = {
    "empty": "本章节未成文",
    "short": "篇幅不足以覆盖要求",
    "template": "模板稿待重写",
    "must_miss": "★必须条款未响应",
    "req_miss": "要求条目疑似未响应",
    "num_miss": "指标值未落入正文",
    "todo": "占位内容待补",
    "status": "章节状态未更新",
    "unmapped": "规格要求未映射章节",
    "dangling": "映射章节已不存在",
    "orphan": "技术标章节无规格映射",
    "xref": "交叉引用失效",
    "num_conflict": "数值口径冲突",
    "commit_conflict": "承诺口径不一致",
}

# 结构性问题在总分里额外扣分（覆盖率的扣分已体现在 req_ratio / must_ratio 里）
PENALTY = {"empty": 5, "dangling": 5, "unmapped": 2, "template": 2, "short": 1,
           "num_conflict": 4, "commit_conflict": 2, "xref": 1}

# 横向一致性（正文 ↔ 正文）：交叉引用、数值口径、承诺口径
XREF_RE = re.compile(r"(?:详见|参见|见|参考)第?\s*([0-9]+(?:\.[0-9]+){1,3})\s*节")
COUNT_UNITS = {"台", "块", "路", "套", "通道", "个", "只", "面", "台套", "根", "对", "芯"}
COMMIT_RES = [
    ("质保期", re.compile(r"(?:质保期|保修期)\s*(?:为|不少于|不低于)?\s*(\d+)\s*年")),
    ("故障响应时效", re.compile(r"响应(?:时间|时效)?\s*(?:不超过|≤)?\s*(\d+)\s*(?:小时|分钟)")),
    ("现场到达时效", re.compile(r"(\d+)\s*小时(?:以)?内?到达现场")),
]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

UNIT_PATTERN = (
    "台套|台|套|块|个|只|根|条|张|米|毫米|厘米|mm|cm|km|kg|吨|t|TB|GB|MB|Gbps|Mbps|"
    "MHz|GHz|Hz|℃|°C|%|伏|V|安|A|瓦|W|kW|kWh|小时|时|分钟|min|秒|ms|s|天|日|周|个月|月|年|"
    "路|核|项|次|人|页|份|级|dB|MPa|kPa|Pa|lx|帧|fps|点|通道|芯|对|万小时"
)
METRIC_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(" + UNIT_PATTERN + r")")
IDENT_RE = re.compile(r"IP\d{2}|HAF\s?\d{3}|HAD\s?\d{3}|GB\s?/?\s?T\s?\d+[\.\d\-]*|"
                      r"IEC\s?\d+[\.\d\-]*|IEEE\s?\d+[\.\d\-]*|EJ\s?/?\s?T?\s?\d+[\.\d\-]*|"
                      r"\d{3,4}[PpKk]|\d+×\d+|\d+\.\d+级")

UNIT_ALIAS = {"台套": ("台套", "台"), "时": ("小时", "时"), "小时": ("小时", "h", "时"),
              "秒": ("秒", "s"), "min": ("分钟", "min"), "分钟": ("分钟", "min"),
              "°C": ("℃", "°C"), "℃": ("℃", "°C"), "%": ("%", "％"),
              "个月": ("个月", "月")}


def _norm(text):
    """全角转半角 + 去多余空白，便于指标值比对。"""
    return re.sub(r"[ \t\u3000]+", "", unicodedata.normalize("NFKC", text or ""))


def _compact(text):
    return re.sub(r"[\W_]+", "", _norm(text).lower())


def _metrics(text):
    """抽取指标值（数量/精度/带宽/标准号），先剔掉页码、年份、条款编号等噪声。"""
    t = unicodedata.normalize("NFKC", text or "")
    t = re.sub(r"第\s*\d+\s*页", " ", t)
    t = re.sub(r"(19|20)\d{2}\s*年", " ", t)
    out, seen = [], set()
    for m in METRIC_RE.finditer(t):
        raw = m.group(0).replace(" ", "")
        value, unit = m.group(1), m.group(2)
        if value in ("0", "1", "2", "3") and unit in ("个", "项", "次", "人", "份", "页"):
            continue
        if raw not in seen:
            seen.add(raw)
            out.append({"raw": raw, "value": value, "unit": unit})
    for m in IDENT_RE.finditer(t):
        raw = re.sub(r"\s", "", m.group(0))
        if raw not in seen:
            seen.add(raw)
            out.append({"raw": raw, "value": "", "unit": "", "ident": True})
    return out


def _in_body(metric, body_norm):
    raw = metric["raw"]
    if raw in body_norm:
        return True
    if metric.get("ident"):
        return raw.lower() in body_norm.lower()
    for alias in UNIT_ALIAS.get(metric["unit"], ()):
        if metric["value"] + alias in body_norm:
            return True
    # 数值本身出现且单位以别的方式表达（如 26 台 vs 26 路）→ 视为"数值已给"，交人工复核单位
    return False


def _metric_only_value(metric, body_norm):
    return bool(metric["value"]) and re.search(r"(?<![\d.])" + re.escape(metric["value"]) + r"(?![\d.])",
                                               body_norm) is not None


def _title_hit(title, body_compact):
    """条款小标题是否在正文出现（正文按条款逐条响应时，标题通常原样保留）。"""
    for seg in re.split(r"[：:，,、；;（）()/\s]+", title or ""):
        c = _compact(seg)
        if len(c) >= 2 and c in body_compact:
            return True
    return False


def _coverage(title, content, body_compact, body_norm):
    """判断一条要求是否在正文中有实质响应。

    四路证据，任一成立即视为"已响应"：
      ① 条款小标题在正文出现，且要求中的指标未被漏掉（正文按条响应时会保留标题）；
      ② 指标/标准号全部落入正文（说明给了响应值）；
      ③ 3 字片段命中率 ≥ 0.40（中文短语级重合，容忍"必须/均为"这类同义替换）；
      ④ 6 字连续片段命中率 ≥ 0.25（长段落级复用）。
    另有"部分对应"（weak）档：短语命中 28%–40%，不单独报审核项但计入明细。
    """
    text = " ".join(filter(None, [title or "", content or ""]))
    c = _compact(text)
    if not c:
        return "ok", ""
    metrics = _metrics(text)
    metrics_hit = [m for m in metrics if _in_body(m, body_norm)]
    metrics_all = bool(metrics) and len(metrics_hit) == len(metrics)

    if len(c) <= 12:
        if c in body_compact:
            return "ok", ""
        return ("weak" if metrics_hit else "miss"), c

    if _title_hit(title, body_compact) and (not metrics or metrics_hit):
        return "ok", "标题命中"
    if metrics_all:
        return "ok", f"指标全部落入正文（{len(metrics)} 项）"

    grams = [c[i:i + 3] for i in range(len(c) - 2)]
    r3 = sum(1 for g in grams if g in body_compact) / len(grams) if grams else 0.0
    windows = [c[i:i + 6] for i in range(0, len(c) - 5, 6)]
    r6 = sum(1 for w in windows if w in body_compact) / len(windows) if windows else 0.0

    detail = f"短语命中 {r3:.0%} / 片段命中 {r6:.0%}"
    if r3 >= 0.40 or r6 >= 0.25:
        return "ok", detail
    return ("weak" if r3 >= 0.28 else "miss"), detail


def _finding(key, level, kind, message, section=None, outline=None, detail=""):
    return {
        "key": key,
        "level": level,
        "level_label": LEVEL_LABEL[level],
        "kind": kind,
        "kind_label": KIND_LABEL.get(kind, kind),
        "message": message,
        "detail": detail,
        "section_id": section["id"] if section else None,
        "section_code": section["code"] if section else "",
        "section_title": section["title"] if section else "",
        "outline_id": outline["id"] if outline else None,
        "outline_code": outline["code"] if outline else "",
        "outline_title": outline["title"] if outline else "",
        "page": None,
        "state": "open",
        "note": "",
        "checked_by": "",
        "checked_at": "",
    }


def _load_marks(conn, bid_id):
    try:
        rows = conn.execute("SELECT * FROM tech_audit_marks WHERE bid_id=?", (bid_id,)).fetchall()
    except Exception:  # noqa: BLE001 - 老库未迁移时不影响审核主流程
        return {}
    marks = {}
    for r in rows:
        marks[r["item_key"]] = {"state": r["verdict"], "note": r["note"] or "",
                                "checked_by": r["user"] or "", "checked_at": r["updated_at"] or ""}
    return marks


def _pid(row):
    """取行的 parent_id；兼容 dict / sqlite3.Row / 历史无该列的数据。"""
    try:
        return row["parent_id"]
    except (KeyError, IndexError, TypeError):
        return None


def _subtree(secs_by_code, section):
    """本节 + 全部下级章节（父章节的正文常写在子节里，覆盖判定要合并看）。

    沿 parent_id 递归取结构真值；历史/手工数据缺 parent_id 时按 code 前缀兜底。
    """
    by_id = {s["id"]: s for s in secs_by_code.values()}
    kids = {}
    for s in secs_by_code.values():
        pid = _pid(s)
        if pid is None:
            pcode = ".".join(s["code"].split(".")[:-1])
            p = secs_by_code.get(pcode)
            pid = p["id"] if p else None
        if pid is not None and pid != s["id"]:
            kids.setdefault(pid, []).append(s["id"])
    out, stack, seen = [section], [section["id"]], {section["id"]}
    while stack:
        pid = stack.pop()
        for cid in kids.get(pid, []):
            if cid not in seen:
                seen.add(cid)
                stack.append(cid)
                out.append(by_id[cid])
    return out


def audit(conn, bid_id, spec_id=None):
    bid = conn.execute("SELECT * FROM bids WHERE id=?", (bid_id,)).fetchone()
    if not bid:
        return None
    sections = conn.execute("SELECT * FROM sections WHERE bid_id=? ORDER BY sort_order",
                            (bid_id,)).fetchall()
    sec_by_code = {s["code"]: s for s in sections}
    sec_by_id = {s["id"]: s for s in sections}
    tech_sections = [s for s in sections if s["group_name"] == "技术标"]

    if spec_id:
        spec = conn.execute("SELECT * FROM tech_specs WHERE id=? AND bid_id=?", (spec_id, bid_id)).fetchone()
    else:
        spec = conn.execute(
            """SELECT ts.* FROM tech_specs ts JOIN tender_files tf ON tf.id=ts.file_id
               WHERE ts.bid_id=? AND tf.deleted_at IS NULL ORDER BY ts.id DESC LIMIT 1""",
            (bid_id,)).fetchone()
    findings = []
    units = []              # 审核单元：目录项 / 插入本节生成的骨架要求
    unmapped_nodes = []
    dangling_nodes = []
    body_cache = {}

    def body_of(section):
        if section["id"] in body_cache:
            return body_cache[section["id"]]
        texts = []
        for s in _subtree(sec_by_code, section):
            texts.append(s["content_ai"] or "")
        raw = "\n".join(texts)
        body_cache[section["id"]] = (raw, _compact(raw), _norm(raw))
        return body_cache[section["id"]]

    # ---------- 1. 技术规格编写目录 ↔ 技术标章节 ----------
    if spec:
        try:
            report = json.loads(spec["report_json"] or "{}")
        except (TypeError, ValueError):
            report = {}
        for o in conn.execute("SELECT * FROM tech_outline WHERE spec_id=? ORDER BY seq", (spec["id"],)):
            sid = o["section_id"]
            section = sec_by_id.get(sid) if sid else None
            if not sid:
                unmapped_nodes.append(o["code"] + " " + o["title"])
                f = _finding(f"unmapped:{o['id']}", HIGH, "unmapped",
                             f"「{o['code']} {o['title']}」{o['req_count']} 条规格要求未对应技术标章节，正文无处响应",
                             outline=o)
                f["page"] = o["page_from"]
                findings.append(f)
                continue
            if not section:
                dangling_nodes.append(o["code"] + " " + o["title"])
                f = _finding(f"dangling:{o['id']}", HIGH, "dangling",
                             f"「{o['code']} {o['title']}」映射的章节 #{sid} 已不存在（可能已被删除），{o['req_count']} 条要求失去落点",
                             outline=o)
                f["page"] = o["page_from"]
                findings.append(f)
                continue
            try:
                reqs = json.loads(o["requirements"] or "[]")
            except (TypeError, ValueError):
                reqs = []
            units.append({"key": f"outline:{o['id']}", "outline": o, "section": section,
                          "requirements": reqs, "source": "目录映射"})
        spec_info = {
            "id": spec["id"], "file_id": spec["file_id"], "region_title": spec["region_title"],
            "req_count": spec["req_count"], "must_count": spec["must_count"],
            "outline_count": spec["outline_count"], "applied_at": spec["applied_at"],
            "steps": len(report.get("steps") or []),
        }
        filename = conn.execute("SELECT filename FROM tender_files WHERE id=?", (spec["file_id"],)).fetchone()
        spec_info["filename"] = filename["filename"] if filename else ""
    else:
        spec_info = None

    # ---------- 2. "插入本节 / 整块转子目录"生成的骨架要求 ----------
    for s in tech_sections:
        if not (s["content"] or "").strip():
            continue
        try:
            from composer import parse_skeleton
            skel = parse_skeleton(s["content"])
        except Exception:  # noqa: BLE001
            skel = []
        if skel:
            units.append({"key": f"skel:{s['id']}", "outline": None, "section": s,
                          "requirements": skel, "source": "插入本节"})

    # ---------- 3. 逐单元核验条目响应 ----------
    must_total = must_miss_total = 0
    for unit in units:
        section, reqs, o = unit["section"], unit["requirements"], unit["outline"]
        raw, compact, norm = body_of(section)
        keybase = unit["key"].replace(":", "_")
        label = f"{o['code']} {o['title']}" if o else f"{section['code']} {section['title']}"
        if not reqs:   # 目录项无明细：用标题当一条要求，避免漏检
            reqs = [{"title": o["title"] if o else section["title"], "content": o["brief"] if o else "",
                     "page": (o["page_from"] if o else None), "must": bool(o and o["must_count"])}]
        missing, must_missing, miss_metrics, weak = [], [], [], 0
        covered = 0
        unit_must = unit_must_ok = 0
        for idx, r in enumerate(reqs):
            r_title = str(r.get("title") or "")
            r_content = str(r.get("content") or "")
            is_must = bool(r.get("must"))
            if is_must:
                must_total += 1
                unit_must += 1
            state, why = _coverage(r_title, r_content, compact, norm)
            if state in ("ok", "weak"):
                covered += 1
                if state == "weak":
                    weak += 1
                if is_must:
                    unit_must_ok += 1
                # 要求已响应，但指标值没写进正文（如"满足要求"却没给数量）仍单独提示
                for m in _metrics(r_content + " " + r_title):
                    if not _in_body(m, norm):
                        miss_metrics.append({"raw": m["raw"], "title": r_title.strip(),
                                             "page": r.get("page"),
                                             "value_only": _metric_only_value(m, norm)})
                continue
            title = r_title.strip() or _compact(r_content)[:24]
            page = r.get("page")
            if is_must:
                must_miss_total += 1
                must_missing.append((idx, title, page))
                f = _finding(f"must:{keybase}:{idx}", HIGH, "must_miss",
                             f"★必须条款未在正文响应：{title}" + (f"（原文第 {page} 页）" if page else ""),
                             section=section, outline=o,
                             detail=(r_content[:160] + f"　【{why}】").strip())
                f["page"] = page
                findings.append(f)
            else:
                missing.append((idx, title, page))
        unit.update({"req_total": len(reqs), "req_covered": covered, "req_partial": weak,
                     "must_total": unit_must, "must_covered": unit_must_ok})

        if missing:
            ratio = len(missing) / max(1, len(reqs))
            if len(missing) >= 2 or ratio >= 0.25:
                names = "、".join(f"{t}（第 {p} 页）" if p else t for _i, t, p in missing[:3])
                more = f" 等 {len(missing)} 条" if len(missing) > 3 else ""
                lvl = MID if len(missing) <= 3 else HIGH
                f = _finding(f"req:{keybase}", lvl, "req_miss",
                             f"{len(missing)}/{len(reqs)} 条要求未见明确响应：{names}{more}",
                             section=section, outline=o,
                             detail=f"来源：{unit['source']} · 仅提示复核，正文可能换用同义表述")
                f["page"] = missing[0][2]
                findings.append(f)

        if miss_metrics:
            # 同一数值在正文出现但单位不同 → 降级为"待核实"，不直接判缺
            hard = [m for m in miss_metrics if not m["value_only"]]
            if hard:
                vals = "、".join(dict.fromkeys(m["raw"] for m in hard))[:90]
                f = _finding(f"num:{keybase}", MID, "num_miss",
                             f"指标值未在正文出现：{vals}",
                             section=section, outline=o,
                             detail="要求逐项给出响应值；若采用等效表述请在审核项中确认并备注")
                f["page"] = hard[0]["page"]
                findings.append(f)

    # ---------- 4. 章节成文质量 ----------
    mapped_subtree = set()
    for u in units:
        for s in _subtree(sec_by_code, u["section"]):
            mapped_subtree.add(s["id"])
    for s in tech_sections:
        own = (s["content_ai"] or "")
        body_len = len(own)
        subtree_len = sum(len((x["content_ai"] or "")) for x in _subtree(sec_by_code, s))
        req_total = sum(u.get("req_total", 0) for u in units if u["section"]["id"] == s["id"])
        has_children = any(_pid(c) == s["id"] for c in sections)
        if not has_children:  # 历史/手工数据无 parent_id：按 code 前缀兜底
            has_children = any(c["code"].startswith(s["code"] + ".") for c in sections)
        if req_total and not own.strip() and not (has_children and subtree_len):
            findings.append(_finding(f"empty:{s['id']}", HIGH, "empty",
                                     f"本节映射 {req_total} 条规格要求，但正文仍为空（未成文）",
                                     section=s))
        if s["id"] in mapped_subtree or req_total:
            todos = len(re.findall(r"【待补充[：:][^】]*】", own))
            if todos:
                samples = "；".join(list(dict.fromkeys(re.findall(r"【待补充[：:]([^】]{0,18})】?", own)))[:2])
                findings.append(_finding(f"todo:{s['id']}", LOW, "todo",
                                         f"{todos} 处【待补充】未填：{samples}…",
                                         section=s))
            if s["status"] == "未开始" and body_len >= 200:
                findings.append(_finding(f"status:{s['id']}", LOW, "status",
                                         "已有正文但章节状态仍为「未开始」", section=s))
        if (s["ai_model"] or "").startswith("本地结构化"):
            findings.append(_finding(f"template:{s['id']}", MID, "template",
                                     "正文由本地结构化模板生成（非大模型），实质方案需重写",
                                     section=s))
        if s["id"] not in mapped_subtree and not any(x["id"] in mapped_subtree for x in _subtree(sec_by_code, s)[1:]):
            findings.append(_finding(f"orphan:{s['id']}", LOW, "orphan",
                                     "本章节未对应任何技术规格要求（自主编写章节），请确认是否需补映射",
                                     section=s))

    # ---------- 4.5 横向一致性（正文 ↔ 正文） ----------
    # 单章不脱离整篇：各章之间的数值口径、承诺口径、交叉引用必须自洽
    own_cache, own_metric_cache = {}, {}

    def own_body(s):
        if s["id"] not in own_cache:
            raw = s["content_ai"] or ""
            own_cache[s["id"]] = (raw, _compact(raw), _norm(raw))
        return own_cache[s["id"]]

    def own_metrics(s):
        if s["id"] not in own_metric_cache:
            own_metric_cache[s["id"]] = _metrics(own_body(s)[0])
        return own_metric_cache[s["id"]]

    written = [s for s in tech_sections if (s["content_ai"] or "").strip()]

    # ① 交叉引用：正文引用的章节编号必须真实存在
    for s in written:
        raw = own_body(s)[0]
        for m in XREF_RE.finditer(raw):
            ref = m.group(1)
            if ref not in sec_by_code:
                findings.append(_finding(f"xref:{s['id']}:{ref}", MID, "xref",
                                         f"正文引用的章节 {ref} 不存在（“{m.group(0)}”），交叉引用断裂",
                                         section=s, detail="请核对章节编号，或补建对应章节"))

    # ② 数值口径：同一条款标题在多个章节出现时，同单位计数必须一致
    #    防误报三重锚定：仅比对"单对象条款"（计数指标 ≤2，清单类条款跳过）；
    #    冲突值必须出现在条款标题邻近 ±窗口内（说明在谈同一对象）；
    #    要求值已出现在窗口内则视为口径一致。
    for unit in units:
        for r in unit["requirements"]:
            r_title = str(r.get("title") or "")
            title_n = _norm(r_title)
            if len(title_n) < 4:
                continue
            req_metrics = [m for m in _metrics(f"{r_title} {r.get('content') or ''}")
                           if m["unit"] in COUNT_UNITS]
            if not req_metrics or len(req_metrics) > 2:
                continue
            for s in written:
                if s["id"] == unit["section"]["id"]:
                    continue
                raw, comp, norm = own_body(s)
                pos = norm.find(title_n)
                if pos < 0:
                    continue
                window = norm[max(0, pos - 60): pos + len(title_n) + 200]
                conflicts = []
                for rm in req_metrics:
                    if _in_body(rm, window):
                        continue
                    for bm in own_metrics(s):
                        if bm["unit"] == rm["unit"] and _num(bm["value"]) != _num(rm["value"]) \
                                and _num(bm["value"]) is not None and _in_body(bm, window):
                            conflicts.append(f"{bm['raw']}（要求 {rm['raw']}）")
                            break
                if conflicts:
                    findings.append(_finding(
                        f"numc:{s['id']}:{unit['key'].replace(':', '_')}",
                        HIGH, "num_conflict",
                        f"“{s['code']} {s['title']}”在条款“{r_title[:30]}”附近出现 {conflicts[0]}，口径不一致",
                        section=s, outline=unit["outline"],
                        detail="同一对象在不同章节给出了不同数值；以规格指标库为准统一口径后重写其一"
                               + (f"；另有 {conflicts[1:]}" if len(conflicts) > 1 else "")))

    # ③ 承诺口径：质保期 / 响应时效 / 到场时效等全书必须一致
    for label, pat in COMMIT_RES:
        hits = []
        for s in written:
            raw = re.sub(r"[ \t\u3000]", "", own_body(s)[0])
            m = pat.search(raw)
            if m:
                hits.append((m.group(1), s))
        if len({v for v, _ in hits}) > 1:
            base_v, base_s = hits[0]
            for v, s in hits[1:]:
                if _num(v) == _num(base_v):
                    continue
                findings.append(_finding(
                    f"commit:{label}:{s['id']}:{v}", MID, "commit_conflict",
                    f"「{label}」全书口径不一：{base_s['code']} 写 {base_v}、{s['code']} 写 {v}",
                    section=s,
                    detail="对外承诺值全书必须唯一，请统一后重写（不一致的承诺是评标质疑点）"))
                break

    # 空章节只报"未成文"一条，同类章节内的条目/指标/篇幅提示不再重复刷屏
    empty_dirs = {f["section_id"] for f in findings if f["kind"] == "empty" and f["section_id"]}
    empty_prefix = [f["section_code"] for f in findings if f["kind"] == "empty" and f["section_code"]]
    redundant = ("req_miss", "must_miss", "num_miss", "short", "todo", "status")
    findings = [f for f in findings if not (
        f["kind"] in redundant and (
            f["section_id"] in empty_dirs or
            any(f["section_code"] == c or f["section_code"].startswith(c + ".") for c in empty_prefix)
        )
    )]

    # ---------- 5. 人工签认状态 ----------
    marks = _load_marks(conn, bid_id)
    open_count = {HIGH: 0, MID: 0, LOW: 0}
    resolved = 0
    for f in findings:
        m = marks.get(f["key"])
        if m:
            f.update(m)
            if m["state"] in ("confirmed", "ignored"):
                resolved += 1
                continue
        open_count[f["level"]] += 1

    # ---------- 6. 汇总 ----------
    req_total = sum(u.get("req_total", 0) for u in units)
    req_covered = sum(u.get("req_covered", 0) for u in units)
    must_covered = max(0, must_total - must_miss_total)
    req_ratio = (req_covered / req_total) if req_total else 1.0
    must_ratio = (must_covered / must_total) if must_total else req_ratio
    base = 100 * (0.6 * req_ratio + 0.4 * must_ratio)
    penalty = 0
    for f in findings:
        if f["state"] != "open":
            continue
        penalty += PENALTY.get(f["kind"], 0)
    penalty = min(45, penalty)
    score = max(0, round(base - penalty))
    by_section = {}
    for s in tech_sections:
        by_section[s["id"]] = {
            "section_id": s["id"], "code": s["code"], "title": s["title"], "status": s["status"],
            "body_len": len(s["content_ai"] or ""), "req_total": 0, "req_covered": 0,
            "must_total": 0, "must_covered": 0,
            "high": 0, "mid": 0, "low": 0, "total": 0, "open": 0,
        }
    for u in units:
        row = by_section.get(u["section"]["id"])
        if not row:
            continue
        row["req_total"] += u.get("req_total", 0)
        row["req_covered"] += u.get("req_covered", 0)
        row["must_total"] += u.get("must_total", 0)
        row["must_covered"] += u.get("must_covered", 0)
    for f in findings:
        row = by_section.get(f["section_id"]) if f["section_id"] else None
        if not row:
            continue
        row[f["level"]] += 1
        row["total"] += 1
        if f["state"] == "open":
            row["open"] += 1
    unwritten = sum(1 for f in findings if f["kind"] == "empty" and f["state"] == "open")
    todo_open = sum(1 for f in findings if f["kind"] == "todo" and f["state"] == "open")
    consistency = {k: sum(1 for f in findings if f["kind"] == k and f["state"] == "open")
                   for k in ("xref", "num_conflict", "commit_conflict")}
    summary = {
        "score": score,
        "coverage_score": round(base),
        "penalty": penalty,
        "verdict": "可提交技术负责人复核" if score >= 85 and not open_count[HIGH]
                   else ("需补充后复核" if score >= 60 else "不可提交：存在未响应条款"),
        "req_total": req_total, "req_covered": req_covered,
        "req_missing": max(0, req_total - req_covered),
        "must_total": must_total, "must_covered": must_covered,
        "sections_total": len(tech_sections),
        "sections_unwritten": unwritten,
        "unmapped_nodes": len(unmapped_nodes),
        "dangling_nodes": len(dangling_nodes),
        "open": open_count,
        "open_total": open_count[HIGH] + open_count[MID] + open_count[LOW],
        "todo_open": todo_open,
        "consistency": consistency,
        "resolved": resolved,
        "units": len(units),
    }
    return {
        "bid": dict(bid),
        "spec": spec_info,
        "summary": summary,
        "findings": sorted(findings, key=lambda f: ({HIGH: 0, MID: 1, LOW: 2}[f["level"]],
                                                    f["section_code"] or "0", f["key"])),
        "by_section": list(by_section.values()),
        "unmapped_titles": unmapped_nodes[:12],
        "dangling_titles": dangling_nodes[:12],
        "levels": LEVEL_LABEL,
    }


def mark(conn, bid_id, item_key, verdict, note="", user=""):
    """审核项签认：confirmed（确认无误）/ ignored（忽略）/ open（重开）。"""
    if verdict == "open":
        conn.execute("DELETE FROM tech_audit_marks WHERE bid_id=? AND item_key=?", (bid_id, item_key))
        return "已重开该审核项"
    if verdict not in ("confirmed", "ignored"):
        raise ValueError("审核结论无效")
    conn.execute(
        """INSERT INTO tech_audit_marks(bid_id,item_key,verdict,note,user,updated_at)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(bid_id,item_key) DO UPDATE SET verdict=excluded.verdict,
             note=excluded.note,user=excluded.user,updated_at=excluded.updated_at""",
        (bid_id, item_key, verdict, note[:200], user[:40],
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    return "已确认" if verdict == "confirmed" else "已忽略"
