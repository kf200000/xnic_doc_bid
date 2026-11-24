"""标书正文编写：把"章节路径 + 技术规格要求 + 评分关联"装配成写作上下文，
优先调用大模型（llm_client），未配置模型时降级为本地结构化撰写（明确标注，不冒充大模型）。
"""

import json
import re

import llm_client

SYSTEM_PROMPT = (
    "你是核工业（核电、核化工）投标技术方案编写专家，长期为中核西仪这类核仪器供应商编写技术标。"
    "写作铁律：\n"
    "1) 逐条响应招标技术规格要求，不遗漏、不合并关键要求；\n"
    "2) 严禁编造具体型号、业绩项目、证书编号、检测数值、人员姓名——凡需要但未提供的事实，"
    "一律写成【待补充：具体内容】占位，由责任人核实后填入；\n"
    "3) 突出核安全文化、HAF003 质保体系、国产化与自主可控、可追溯性；\n"
    "4) 中文书面语，条款化、可核查，不用营销套话，不写空泛表态；\n"
    
)

SECTION_REQ = '''重要输出要求】：
## 风格指南（所有章节必须遵守）
- **结构**：每个小节统一为三段式：① 背景/目的（1-2句） → ② 核心内容（正文） → ③ 小结/过渡（1句，可选）
- **语气**：客观、专业、简洁，使用被动语态和现在时，避免主观评价（如“我认为”）
- **术语**：全文统一使用术语表（如“用户”而非“使用者”）
- **列表**：有序列表用于步骤说明，无序列表用于并列要点
- **图表**：优先使用表格整理对比信息、流程图【使用 Mermaid】,架构图使用【drawio】,项目实施进度使用甘特图，并附带简要文字说明；
- **代码块**：所有代码示例必须标注语言，并包含注释
- **标点**：中文使用全角标点，英文与数字前后加空格（如“CPU 使用率”）
- **章节标题**：不输出任何 Markdown 标题标记（即生成的正文不包含 #、##），标题由程序在渲染时自动添加
**块级元素之间必须有空行**：
   - 表格、段落、标题、列表、代码块、引用、公式等块级元素之间必须用至少一个空行分隔。

## drawio 架构图规范（涉及系统架构时必须遵守）
- **触发条件**：章节内容涉及"总体架构、系统架构、平台架构、分层架构、技术架构、架构设计"时，正文中必须插入一个 drawio 代码块；无架构内容的章节不要输出该块。
- **代码块格式**：标记必须是 drawio，块内仅一行合法 JSON（可被 JSON.parse 解析），无注释、无换行、无多余文字；代码块前后各留一个空行。
- **JSON 结构**（照此结构替换 title / name / modules 内容）：
  {"title":"XX系统总体架构图","layers":[{"name":"用户层","modules":["临床医生","科研人员"]},{"name":"应用层","modules":["辅助诊断","报告生成"]},{"name":"平台服务层","modules":["模型推理","API网关"]},{"name":"数据层","modules":["影像存储","数据脱敏"]},{"name":"基础设施层","modules":["算力集群","容器云"]}],"sidebars":[{"name":"安全保障体系","side":"left"},{"name":"标准规范体系","side":"right"}]}
- **字段说明**：title 为图标题；layers 按视觉从上到下排列（如 用户层→应用层→平台服务层→数据层→基础设施层），每层 modules 为 2-8 个简短模块名（每个不超过 12 字）；sidebars 固定为左右两条，side 取 left / right。
- **禁止事项**：架构图不得用 Mermaid 格式输出；缺少 drawio 块的架构章节视为不合格。'''

CATEGORY_HINT = {
    "需求与范围": "先复述建设目标与供货/服务边界，再说明我方理解与总体思路",
    "总体架构": "给出分层架构与数据流，说明与既有系统的界面划分",
    "功能设计": "按功能点说明实现方式、输入输出、异常处理",
    "性能指标": "逐项给出响应值（满足/优于）与验证方法",
    "设备选型": "按清单说明选型依据、关键参数、国产化情况",
    "接口设计": "说明协议、点表/寄存器、联调与测试方法",
    "标准规范": "列出执行标准清单及符合性说明",
    "安全保密": "说明等保/保密/功能安全设计与落实措施",
    "环境供电": "说明环境适应性、供电与接地、抗震等设计",
    "质量保证": "说明质保体系、控制点（H/W 点）、不符合项闭环",
    "试验验收": "说明出厂/现场试验项目、判定准则、验收交付物",
    "实施进度": "说明里程碑、资源投入与进度保障措施",
    "培训售后": "说明培训对象与课时、响应时效、备件保障",
}


def _norm(text, limit=None):
    t = re.sub(r"\s+", " ", (text or "").replace("\u3000", " ")).strip()
    return t[:limit] if limit and len(t) > limit else t


SUMMARY_MARK = "【本章要点摘要】"

# 章节篇幅策略：默认 800 字，允许留白，不为凑字灌水。
# 旧策略 900~2400 字随要求条数线性膨胀，既拖慢非流式生成（长文易超时），
# 又迫使模型用套话填空；自检闸门也不再因篇幅不足强制重写。
DEFAULT_SECTION_CHARS = 800
MAX_SECTION_CHARS = 1200


def section_target(req_count):
    """本节建议字数：默认 800，要求条数多时温和上浮，封顶 1200。"""
    return max(DEFAULT_SECTION_CHARS,
               min(MAX_SECTION_CHARS, DEFAULT_SECTION_CHARS + 40 * max(0, req_count - 1)))

# 数量/指标抽取：词+数字+单位，用于构建全书唯一事实源（规格指标库）
NUM_TOKEN_RE = re.compile(
    r"[\u4e00-\u9fa5A-Za-z0-9]{0,8}\d+(?:\.\d+)?\s*(?:台|块|路|套|通道|个|只|面|GB|TB|MB|KB|Mbps|"
    r"ms|秒|小时|年|℃|°C|%|V|W|kW|IP\d{1,2}|mm|kg|dB)(?![\w])")


def split_summary(text):
    """剥离模型同次产出的「本章要点摘要」块（供下一章衔接）。返回 (正文, 摘要)。

    摘要随正文一次调用产出，落库时剥离存 summary_ai——零额外调用。
    """
    if not text:
        return "", ""
    m = re.search(r"【本章要点摘要】", text)
    if not m:
        return text.strip(), ""
    body = text[:m.start()].strip()
    rest = re.sub(r"\s+", " ", text[m.end():]).strip().lstrip("：:—- ").strip()
    return body, rest[:400]


MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
HEADING_NUMBER_RE = re.compile(
    r"^(?:\d+(?:\.\d+){0,5}(?:[\s、.．]+|$)|[一二三四五六七八九十百]+[、.．]\s*|"
    r"[（(][一二三四五六七八九十\d]+[）)]\s*)+")


def clean_generated_body(text, section=None):
    """清除模型常见格式噪音：复述本节大标题、标题内手写章节编号。

    章内层级仍保留 ### / ####，Word 转换时由参考模板按标题层级自动生成编号；
    这里只去掉正文里的重复标题和数字/中文序号，避免导出后出现「2.4.1 2.4.1 …」。
    """
    text = (text or "").strip()
    if not text:
        return ""
    def field(name):
        try:
            return str(section[name] or "").strip() if section is not None else ""
        except (TypeError, KeyError, IndexError):
            return ""
    title, code = field("title"), field("code")
    full = " ".join(x for x in (code, title) if x)
    keys = {re.sub(r"\s+", "", x) for x in (title, full) if x}
    out = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        m = MD_HEADING_RE.match(stripped)
        if m:
            content = HEADING_NUMBER_RE.sub("", m.group(2).strip()).strip(" \u3000-—:：")
            if not content or (keys and re.sub(r"\s+", "", content) in keys):
                continue
            out.append(f"{m.group(1)} {content}")
            continue
        if keys and re.sub(r"\s+", "", stripped) in keys:
            continue
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _first_paragraph(text):
    for part in re.split(r"\n\s*\n", text or ""):
        p = part.strip()
        if len(p) >= 30:
            return p
    return _norm(text, 200)


def _clip(text, limit):
    """按句截断：避免把摘要/首段切在半句话中间（"确保业主在评标、合同" 这类断句）。"""
    t = _norm(text, limit + 60)
    if len(t) <= limit:
        return t
    cut = t[:limit]
    for sep in ("。", "；", "，"):
        pos = cut.rfind(sep)
        if pos >= limit * 0.6:
            return cut[:pos + 1]
    return cut.rstrip("，、： ") + "…"


def _spec_metric_library(conn, bid_id, limit=24):
    """全书硬指标库：技术规格映射条目中的数量/指标值，★必须条款优先。

    注入提示词后作为唯一事实源——正文数值必须与此一致或更优，杜绝各章口径不一。
    """
    rows = conn.execute(
        """SELECT o.title AS otitle, o.requirements FROM tech_outline o
           JOIN tech_specs t ON t.id=o.spec_id WHERE t.bid_id=? ORDER BY o.seq""",
        (bid_id,)).fetchall()
    entries = []
    for row in rows:
        try:
            items = json.loads(row["requirements"] or "[]")
        except (TypeError, ValueError):
            continue
        for r in items:
            text = " ".join(filter(None, [str(r.get("title") or ""), str(r.get("content") or "")]))
            tokens = [m.group(0).strip() for m in NUM_TOKEN_RE.finditer(text)]
            if tokens:
                entries.append((bool(r.get("must")), _norm(r.get("title") or row["otitle"], 30), tokens))
    entries.sort(key=lambda x: not x[0])
    out, seen = [], set()
    for must, title, tokens in entries:
        if title in seen:
            continue
        seen.add(title)
        out.append(("★" if must else "") + title + "：" + "、".join(list(dict.fromkeys(tokens))[:5]))
        if len(out) >= limit:
            break
    return out


def _term_library(conn, limit=12):
    """统一术语表（行业术语库），全书用词一致。"""
    rows = conn.execute("SELECT term, synonyms FROM term_dict ORDER BY id LIMIT ?", (limit,)).fetchall()
    return [f"{r['term']}（{r['synonyms']}）" if r["synonyms"] else str(r["term"]) for r in rows]


def _pid(row):
    """取行的 parent_id；兼容 dict / sqlite3.Row / 历史无该列的数据。"""
    try:
        return row["parent_id"]
    except (KeyError, IndexError, TypeError):
        return None


def _ancestor_lines(sections, section):
    """祖链层：父章节摘要 + 范围边界指令 + 祖父章节精简定位。

    子章节必须参考父章节及父父章节：只写父章节分配的范围，
    父章节已承诺的数值与口径直接引用、不得改写。
    祖先沿 parent_id 上溯（结构真值）；直接父缺失即断链，不再把祖父误当父。
    """
    by_id = {s["id"]: s for s in sections}
    by_code = {s["code"]: s for s in sections}
    chain, cur, seen = [], section, {section["id"]}
    for _ in range(8):
        pid = _pid(cur)
        row = by_id.get(pid) if pid is not None else None
        if row is None:  # 历史/手工数据缺 parent_id：按 code 前缀兜底找直接父
            pcode = ".".join(cur["code"].split(".")[:-1])
            row = by_code.get(pcode)
        if row is None or row["id"] in seen:
            break
        chain.append(row)
        seen.add(row["id"])
        cur = row
    if not chain:
        return []
    lines = []
    parent = chain[0]
    summ = (parent["summary_ai"] or "").strip()
    from_body = False
    if not summ and (parent["content_ai"] or "").strip():
        # 单节 compose / 旧数据可能只有正文没有 summary_ai：用正文首段兜底，
        # 否则明明已成文的父章节会被误判"尚未成文"，子节就拿不到父章范围。
        summ = _first_paragraph(parent["content_ai"])
        from_body = True
    if summ:
        tag = "正文首段" if from_body else "本章摘要"
        lines.append(f"【父章节】{parent['code']} {parent['title']} 已成文（{tag}）：{_clip(summ, 300)}")
    else:  # 父章节未编写（单独补写子节）：降级用骨架要求主题界定范围，不阻塞
        topics = [r["title"] for r in parse_skeleton(parent["content"])][:6]
        scope = "；".join(_norm(t, 24) for t in topics) if topics else f"按《{parent['title']}》标题所界定的范围"
        lines.append(f"【父章节】{parent['code']} {parent['title']} 尚未成文，其覆盖范围：{scope}")
    lines.append(f"范围边界：本节是 {parent['code']} 的子节，只写分配给本节的内容；"
                 f"父章节已承诺的数值与口径直接引用，不得改写。")
    if len(chain) > 1:
        gp = chain[1]
        gsumm = _norm((gp["summary_ai"] or "").strip(), 100)
        lines.append(f"【祖父章节】{gp['code']} {gp['title']}"
                     + (f"——{gsumm}（仅作定位，本节不展开其内容）" if gsumm else "（仅作层级定位，不要展开其内容）"))
    return lines


def _neighbor_lines(sections, section):
    """邻章层：前一章摘要 + 已编写章节索引 + 后章预告，保证前后衔接、避免重复。"""
    try:
        idx = next(i for i, x in enumerate(sections) if x["id"] == section["id"])
    except StopIteration:
        return []
    lines = []
    prev = next((x for x in reversed(sections[:idx])
                 if (x["summary_ai"] or "").strip() or (x["content_ai"] or "").strip()), None)
    if prev:
        summ = (prev["summary_ai"] or "").strip() or _first_paragraph(prev["content_ai"])
        lines.append(f"【前一章】{prev['code']} {prev['title']} 摘要：{_clip(summ, 240)}")
    written = [x for x in sections[:idx]
               if (x["content_ai"] or "").strip() and (prev is None or x["id"] != prev["id"])]
    if written:
        lines.append("【已编写章节索引】（可按编号呼应，不要复述内容）："
                     + "；".join(f"{x['code']} {_norm(x['title'], 20)}" for x in written[-10:]))
    nxt = sections[idx + 1: idx + 7]
    if nxt:
        lines.append("【后续章节预告】（涉及下列主题时一句话带过并注明“见对应章节”，本章不展开）："
                     + "；".join(f"{x['code']} {_norm(x['title'], 24)}" for x in nxt))
    return lines


def _category_of(text):
    """复用技术规格解析的分类规则，保持与解析结果同一口径；失败时归入需求与范围。"""
    try:
        from tech_spec_agent import _classify
        category, _score, _hit = _classify(text)
        return category or "需求与范围"
    except Exception:  # noqa: BLE001 - 分类失败不影响编写主流程
        return "需求与范围"


def parse_skeleton(content):
    """解析"插入本节 / 转子目录"写入章节的原文要求骨架：

        【来源】2.2 系统总体方案 · 技术规格条目（原文第 104 页）
        【要求】……
        【响应提示】……

    这些条目只存在于 sections.content，不在 tech_outline 映射表里，
    编写时必须一并纳入上下文，否则大模型会误判"本节无技术要求"。
    """
    if not content or "【要求】" not in content:
        return []
    items = []
    for block in re.split(r"(?=【来源】)", content):
        m_req = re.search(r"【要求】([\s\S]*?)(?=\n\s*【|$)", block)
        if not m_req:
            continue
        req = _norm(m_req.group(1), 600)
        if len(req) < 4:
            continue
        src = _norm((re.search(r"【来源】(.+)", block) or [None, ""])[1] if re.search(r"【来源】(.+)", block) else "")
        pm = re.search(r"原文第\s*(\d+)\s*页", src)
        # 标题取要求原文首个分句，保证不同条目可区分
        head = re.split(r"[；。，,]", req)[0].strip("（）() ")[:36]
        items.append({
            "title": head or req[:24],
            "content": req,
            "count": 1,
            "page": int(pm.group(1)) if pm else None,
            "must": ("★" in req) or ("必须" in req) or ("不得" in req),
            "category": _category_of(req),
            "source": src,
        })
    return items


def section_requirements(conn, section):
    """本节须回应的技术要求，三个来源合并去重：

    ① tech_outline 映射（解析后同步/强映射章节）；
    ② 本节 content 里的原文要求骨架（"插入本节/转子目录"生成的子节）；
    ③ 本节无直接要求时，汇总子章节骨架里的要求（写父章节不至于空手）。
    同名条目合并（如供货清单多行），重复内容自动去重。
    """
    rows = conn.execute(
        """SELECT o.title AS outline_title, o.code AS outline_code, o.brief, o.requirements
           FROM tech_outline o WHERE o.section_id=? ORDER BY o.seq""",
        (section["id"],)).fetchall()
    merged, order = {}, []

    def add(title, content, page, must, category):
        title = _norm(title, 60) or "技术条款"
        if title not in merged:
            merged[title] = {"parts": [], "pages": [], "must": False, "category": category}
            order.append(title)
        item = merged[title]
        content = _norm(content, 600)
        if content and content not in item["parts"]:
            item["parts"].append(content)
        if page:
            item["pages"].append(page)
        item["must"] = item["must"] or bool(must)

    for row in rows:
        try:
            reqs = json.loads(row["requirements"] or "[]")
        except (TypeError, ValueError):
            reqs = []
        for r in reqs:
            add(r.get("title"), r.get("content"), r.get("page"), r.get("must"),
                r.get("category") or "功能设计")

    direct = len(order)
    for r in parse_skeleton(section["content"]):
        add(r["title"], r["content"], r["page"], r["must"], r["category"])

    if not order:  # 父章节：汇总全部后代章节的原文要求（沿 parent_id 递归）
        sid = section["id"] if "id" in section.keys() else None
        children = []
        if sid is not None:
            children = conn.execute(
                """WITH RECURSIVE sub(id) AS (
                       SELECT id FROM sections WHERE id=?
                       UNION ALL
                       SELECT s.id FROM sections s JOIN sub ON s.parent_id=sub.id
                   )
                   SELECT s.code,s.title,s.content FROM sections s JOIN sub ON s.id=sub.id
                   WHERE s.id!=? AND s.bid_id=? ORDER BY s.sort_order""",
                (sid, sid, section["bid_id"])).fetchall()
        if not children:  # 历史/手工数据无 parent_id：按 code 前缀兜底
            children = conn.execute(
                "SELECT code,title,content FROM sections WHERE bid_id=? AND code LIKE ? ORDER BY sort_order",
                (section["bid_id"], section["code"] + ".%")).fetchall()
        for child in children:
            for r in parse_skeleton(child["content"]):
                add(r["title"], r["content"], r["page"], r["must"], r["category"])

    items = []
    for title in order:
        m = merged[title]
        parts = [p for p in m["parts"] if p]
        if len(parts) <= 3:
            content = "；".join(parts)
        else:  # 清单类条目：只留前两行，其余指向清单表，避免正文堆砌重复行
            content = "；".join(parts[:2]) + f" 等共 {len(parts)} 行（明细见供货清单表：名称/单位/数量/技术要求编号）"
        items.append({
            "title": title,
            "content": content,
            "count": len(parts),
            "page": min(m["pages"]) if m["pages"] else None,
            "must": m["must"],
            "category": m["category"],
        })
    return items


def build_context(conn, section):
    bid = conn.execute("SELECT * FROM bids WHERE id=?", (section["bid_id"],)).fetchone()
    secs = conn.execute(
        "SELECT id,parent_id,code,title,content,content_ai,summary_ai FROM sections WHERE bid_id=? ORDER BY sort_order",
        (section["bid_id"],)).fetchall()
    code = section["code"]
    by_id = {s["id"]: s for s in secs}
    by_code = {s["code"]: s for s in secs}
    # 章节族谱路径：祖链 + 本节，按由根到本节排列（沿 parent_id 上溯）
    path, cur, seen = [f"{code} {section['title']}"], section, {section["id"]}
    for _ in range(8):
        pid = _pid(cur)
        row = by_id.get(pid) if pid is not None else None
        if row is None:  # 历史/手工数据缺 parent_id：按 code 前缀兜底
            pcode = ".".join(cur["code"].split(".")[:-1])
            row = by_code.get(pcode)
        if row is None or row["id"] in seen:
            break
        seen.add(row["id"])
        path.append(f"{row['code']} {row['title']}")
        cur = row
    path.reverse()
    # 下级章节：沿 parent_id 递归收集全部后代，保持原「所有下级」语义与 sort_order
    kids = {}
    for s in secs:
        pid = _pid(s)
        if pid is None:
            pcode = ".".join(s["code"].split(".")[:-1])
            p = by_code.get(pcode)
            pid = p["id"] if p else None
        if pid is not None:
            kids.setdefault(pid, []).append(s)
    desc_ids, stack = set(), [section["id"]]
    while stack:
        pid = stack.pop()
        for s in kids.get(pid, []):
            if s["id"] not in desc_ids:
                desc_ids.add(s["id"])
                stack.append(s["id"])
    children = [f"{s['code']} {s['title']}" for s in secs if s["id"] in desc_ids]
    scoring = conn.execute(
        "SELECT item,score FROM scoring WHERE bid_id=? AND section_code=? ORDER BY sort_order",
        (section["bid_id"], code)).fetchall()
    reqs = section_requirements(conn, section)
    return {
        "section": dict(section),
        "bid": dict(bid) if bid else {},
        "path": path,
        "children": children[:12],
        "requirements": reqs,
        "raw_count": sum(r.get("count", 1) for r in reqs),
        "must_count": sum(1 for r in reqs if r["must"]),
        "scoring": [dict(s) for s in scoring],
        "baseline_card": str((bid["baseline_card"] if bid else "") or ""),
        "metric_library": _spec_metric_library(conn, section["bid_id"]),
        "term_library": _term_library(conn),
        "ancestor_lines": _ancestor_lines(secs, section),
        "neighbor_lines": _neighbor_lines(secs, section),
    }


def build_messages(ctx):
    s, b = ctx["section"], ctx["bid"]
    system = SYSTEM_PROMPT
    role_bits = [x for x in (b.get("name"), b.get("owner"), b.get("reactor_type")) if x]
    if role_bits:
        system += ("\n6) 本项目角色：你以同一专家身份编写本书全部章节，全书口径、文风必须统一——"
                   f"项目：{b.get('name', '')}，业主：{b.get('owner', '')}，堆型/类型：{b.get('reactor_type', '')}。")
    lines = [
        "【项目信息】",
        f"项目名称：{b.get('name', '')}",
    #     f"业主单位：{b.get('owner', '')}；堆型/类型：{b.get('reactor_type', '')}；投标截止：{b.get('due_date', '')}",
    ]
    if ctx.get("baseline_card"):
        lines += ["", "【全书基线卡】（总体方案主线与关键承诺，各章向其收敛，不得偏离）", _norm(ctx["baseline_card"], 600)]
    if ctx.get("metric_library"):
        lines += ["", "【规格指标库】（全书唯一数值事实源：正文数值必须与此一致或更优，禁止另起口径）"]
        lines += [f"- {m}" for m in ctx["metric_library"]]
    if ctx.get("term_library"):
        lines += ["", "【统一术语】" + "；".join(ctx["term_library"])]
    lines += ["", f"【本次编写章节】{s['code']} {s['title']}（{s['group_name']}）"]
    if len(ctx["path"]) > 1:
        lines.append("章节路径：" + " > ".join(ctx["path"]))
    if ctx.get("ancestor_lines"):
        lines += ["", "【章节族谱 · 上级章节（必须遵循其范围与口径）】"] + ctx["ancestor_lines"]
    if ctx.get("neighbor_lines"):
        lines += ["", "【前后章节衔接】"] + ctx["neighbor_lines"]
    if ctx["children"]:
        lines.append("本章下设子节（分别成文，本章不重复展开）：" + "；".join(ctx["children"]))
    if ctx["scoring"]:
        lines.append("关联评分项：" + "；".join(f"{x['item']}（{x['score']} 分）" for x in ctx["scoring"]))
    # lines += ["", f"【本节须逐条回应的技术要求】共 {len(ctx['requirements'])} 条"
    #               f"（原始条款/清单行 {ctx['raw_count']} 项，★必须 {ctx['must_count']} 条）"]
    if ctx["requirements"]:
        for i, r in enumerate(ctx["requirements"], 1):
            mark = "★" if r["must"] else ""
            extra = f"（含 {r['count']} 行清单，需逐行响应）" if r.get("count", 1) > 1 else ""
            lines.append(f"{i}. {mark}{r['title']}（原文第 {r['page'] or '-'} 页，{r['category']}）{extra}：{r['content']}")
    else:
        lines.append("（本节无直接映射的技术条目，按章节标题与项目特点编写，重点覆盖我方技术思路与保障措施）")
    target = section_target(len(ctx["requirements"]))
    lines +=[f"请根据整体需求以及该小节的标题，撰写专业、详实的技术内容约 {target} 字（可少于此值，允许章节留白），正文内容为中文。",
            "紧扣标题，不要偏离；",]
    # lines += SECTION_REQ 不要更改
    lines += SECTION_REQ
    return [{"role": "system", "content": system},
            {"role": "user", "content": "\n".join(lines)}]


RESP_TEMPLATE = {
    "性能指标": "我方对上述指标作满足性承诺，设计阶段即按该指标作为验收判据，出厂与现场试验逐项验证并提交测试记录。",
    "设备选型": "选型遵循成熟可靠、国产优先原则，关键参数不低于要求值，供货前提交选型说明与合格证明。",
    "试验验收": "按本节要求编制试验大纲，明确试验项目、判定准则与见证方式，验收资料随交付一并提交。",
    "标准规范": "执行所列标准现行有效版本，如标准更新则以最新版本为准，并在设计文件中标注符合性条款。",
    "接口设计": "按约定协议实现接口，提供完整点表/寄存器定义与联调方案，配合完成系统联调与问题闭环。",
    "实施进度": "按里程碑倒排计划，关键路径设专人跟踪，风险项提前预警并制定赶工预案。",
    "培训售后": "分阶段开展操作与维护培训，明确响应时效与备件保障范围，服务过程留痕可追溯。",
    "质量保证": "纳入公司质保体系管理，设置 H/W 见证点，不符合项分级闭环，确保不带病交付。",
    "安全保密": "按等级保护与保密要求设计，权限分级、操作留痕、数据可审计。",
    "环境供电": "按现场环境与供电条件设计，满足温湿度、接地、抗震等适应性要求。",
}
DEFAULT_RESP = "我方完全响应上述要求，在方案设计与实施中逐项落实，并在交付资料中提供对应证据。"


def local_compose(ctx):
    """本地结构化撰写（非大模型）：同样输出 Markdown，保证降级稿与大模型稿导出/预览一致。"""
    s, b = ctx["section"], ctx["bid"]
    reqs = ctx["requirements"]
    out = ["### 1 概述", "",
           f"本节依据招标技术规格书及项目特点编写，适用于{b.get('name', '本项目')}"
           f"（业主：{b.get('owner', '—')}）。本节共响应技术规格要求 {len(reqs)} 项"
           f"（原始条款/清单行 {ctx['raw_count']} 项），其中 ★ 必须满足 {ctx['must_count']} 项，"
           "逐条响应情况见第 2 部分。"]
    if ctx["scoring"]:
        out += ["", "关联评分项：" + "；".join(f"{x['item']}（{x['score']} 分）" for x in ctx["scoring"])
                + "，本节内容按评分要点组织。"]
    out += ["", "### 2 技术要求响应", ""]
    if reqs:
        for i, r in enumerate(reqs, 1):
            mark = "★" if r["must"] else ""
            hint = CATEGORY_HINT.get(r["category"], "")
            resp = RESP_TEMPLATE.get(r["category"], DEFAULT_RESP).rstrip("。")
            tail = f"写作落点：{hint}。" if hint else ""
            out += [f"#### 2.{i} {mark}{r['title']}（原文第 {r['page'] or '-'} 页）", "",
                    f"- **要求**：{_norm(r['content'], 400) or '详见技术规格书对应条款。'}",
                    f"- **响应**：满足。{resp}。{tail}",
                    f"- **证明材料**：【待补充：{r['title']}的选型/检测证明】", ""]
            if r.get("count", 1) > 1:
                out += [f"> 本项含 {r['count']} 行清单，逐行规格与数量在供货清单表中一一对应，随投标文件提交。", ""]
    else:
        out += ["本节无直接映射的技术条目。我方按章节标题所述内容组织方案，重点说明技术思路、"
                "实施方法与保障措施；涉及具体参数承诺的，以技术规格书及澄清文件为准。", ""]
    out += ["### 3 保障措施", "",
            "本节内容纳入项目质保体系管理，设置见证点与停工待检点，责任人对响应内容逐条核实；"
            "涉及对外承诺的参数与交付物，经技术负责人复核、授权人终审后生效。",
            "", "### 4 本章小结", "",
            f"本章已对 {len(reqs)} 项技术规格要求作出响应（★{ctx['must_count']} 项全部满足），"
            "后续将按里程碑提交设计文件、试验记录与验收资料，确保可追溯、可核查。"]
    return clean_generated_body("\n".join(out), s)


def compose(conn, section, instructions="", ctx=None):
    """生成正文草稿：优先大模型，未配置则本地结构化。返回 dict（不写库，由调用方保存）。

    正文与「本章要点摘要」一次调用同时产出，summary 由调用方存 summary_ai，
    供后续章节作为前章上下文——零额外模型调用。
    """
    ctx = ctx or build_context(conn, section)
    cfg = llm_client.load_config()
    result = {"requirements": len(ctx["requirements"]), "must": ctx["must_count"]}
    if llm_client.is_configured(cfg):
        messages = build_messages(ctx)
        if instructions:
            messages[-1]["content"] += f"\n\n【补充编写要求】{instructions}"
        resp = llm_client.chat(messages)
        text, summary = split_summary(resp["text"])
        text = clean_generated_body(text, ctx["section"])
        result.update(text=text, summary=summary, provider="llm", model=resp["model"],
                      usage=resp.get("usage") or {}, elapsed=resp.get("elapsed"),
                      notice=f"已由大模型 {resp['model']} 生成，须人工终审后定稿")
        return result
    result.update(text=clean_generated_body(local_compose(ctx), ctx["section"]),
                  summary=f"（本地模板稿）本章响应 {len(ctx['requirements'])} 项要求，"
              f"★{ctx['must_count']} 项全部满足。", provider="template", model="本地结构化模板",
                  usage={}, elapsed=None,
                  notice="未配置大模型密钥，已用本地结构化模板生成初稿（非大模型）；"
                         "在“模型配置”填入密钥后可由大模型重写")
    return result
