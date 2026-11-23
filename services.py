import json
import re
from datetime import datetime, date

from database import now_text, audit


def parse_iso_day(value):
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError, AttributeError):
        return None


def days_left(value):
    target = parse_iso_day(value)
    return (target - date.today()).days if target else None


# ---------------- 标书正文 Markdown：模型稿统一格式 + 历史纯文本归一化 ----------------
LEGACY_HEAD_RE = re.compile(r"^(\d+(?:\.\d+){0,2})[ \u3000]+(\S[^\n]{0,28})$")
MD_TITLE_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s")
MD_BLOCK_RE = re.compile(r"(?m)^\s{0,3}(?:[-*+]\s|\d+\.\s|\||>|```)")
MD_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")
HEADING_NUMBER_RE = re.compile(
    r"^(?:\d+(?:\.\d+){0,5}(?:[\s、.．]+|$)|[一二三四五六七八九十百]+[、.．]\s*|"
    r"[（(][一二三四五六七八九十\d]+[）)]\s*)+")


def _strip_heading_number(content):
    return HEADING_NUMBER_RE.sub("", (content or "").strip()).strip(" \u3000-—:：")


def _section_title_keys(section):
    if section is None:
        return set()
    try:
        title = str(section["title"] or "").strip()
        code = str(section["code"] or "").strip()
    except (TypeError, KeyError, IndexError):
        return set()
    full = " ".join(x for x in (code, title) if x)
    return {re.sub(r"\s+", "", x) for x in (title, full) if x}


def normalize_markdown(text, section=None, base_level=2):
    """章节正文归一化为 Markdown（导出 .md 与页面预览共用同一口径）。

    base_level 为本章标题在 Markdown 中的层级（1 级章节=2，对应 `##`）；
    章内小标题至少比本章深一级，避免与章节标题同级导致 Word 目录错乱。
    """
    text = (text or "").strip()
    if not text:
        return ""
    min_level = min(max(int(base_level or 2) + 1, 3), 6)
    smallest = "#" * min_level
    text = re.sub(rf"(?m)^([#]{{1,{min_level - 1}}})(\s+)", smallest + r"\2", text)
    keep_blocks = bool(MD_TITLE_RE.search(text) or MD_BLOCK_RE.search(text))
    keys = _section_title_keys(section)
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            out.append("")
            continue
        m = MD_HEADING_RE.match(raw)
        if m:
            content = _strip_heading_number(m.group(2))
            marks = m.group(1)
            if len(marks) < min_level:
                marks = smallest
            if not content or (keys and re.sub(r"\s+", "", content) in keys):
                continue
            out.append(f"{marks} {content}")
            continue
        if keys and re.sub(r"\s+", "", line) in keys:
            continue
        m = LEGACY_HEAD_RE.match(line)
        if m and not re.search(r"[。；：，、,;:!?！？]$", line):
            content = _strip_heading_number(line)
            if not content or (keys and re.sub(r"\s+", "", content) in keys):
                continue
            out += ["", f"{smallest} {content}", ""]  # 历史编号标题 → 无编号章内小标题
        elif keep_blocks:
            out.append(raw.rstrip())           # 已有 MD 块：列表/表格/引用原样保留
        else:
            out += [line, ""]                 # 纯文本稿逐行分段
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def render_bid_markdown(bid, sections):
    """整本标书 → Markdown（导出 .md / 全书预览共用）。

    章节标题只输出纯标题，不手写章节编号；层级由 # 数量承载，
    Word 模板按 Heading 样式自动生成编号。章内正文沿用 content_ai，
    只归一化块级结构，不注入重复编号；摘要块不导出。
    """
    def cell(value):
        return str(value or "—").replace("|", "\\|").replace("\n", " ")

    lines = [
        f"# {bid['name']}（预审版）", "",
        "| 项目信息 | 内容 |", "| --- | --- |",
        f"| 项目名称 | {cell(bid['name'])} |",
        f"| 业主单位 | {cell(bid['owner'])} |",
        f"| 堆型/类型 | {cell(bid['reactor_type'])} |",
        f"| 投标截止 | {cell(bid['due_date'])} |",
        "| 版本 | 预审版（Markdown，供转 Word） |", "",
        "> **内部预审提示**：本文档含 AI 辅助内容，必须经授权人员人工终审后方可对外。", "",
        "---", "",
    ]
    for s in sections:
        body = (s["content_ai"] or "").strip().split("【本章要点摘要】")[0].strip()
        if not body:
            raw = (s["content"] or "").strip()
            skeleton = any(k in raw for k in ("【本节编写要点】", "【技术规格要求", "【要求】", "【来源】"))
            body = "【待补充：本节正文尚未编制】" if skeleton or not raw else raw
        try:
            raw_level = int(s["level"] or 1)
        except (KeyError, IndexError, TypeError, ValueError):
            raw_level = 1
        md_level = min(max(raw_level + 1, 2), 6)   # 1 级章节 → ##，2 级 → ### …
        # head = f"{s['code']} {s['title']}"
        head = f"{s['title']}"
        norm = normalize_markdown(body, s, base_level=md_level)
        lines += [f"{'#' * md_level} {head}", "", norm, "", "---", ""]
    lines.append("> 文档结束 · 由中核西仪文档与标书智能工作台生成")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def generate_section_text(code, bid):
    project = bid["name"]
    if code == "2.2":
        return (
            "2.2.4 国产化与自主可控\n"
            f"本项目{project}国产化率达到 100%：探测器、前置放大、数据采集、集中显示软件均为自主研制，"
            "核心元器件选用国产工业级器件并完成替代验证。此前为华龙一号首堆研制的辐射监测系统已实现 "
            "100% 自主化，本项目在其基础上进一步优化通讯协议与自诊断功能，可有效降低后续备件依赖与全生命周期维护成本。"
        )
    if code == "2.5":
        return (
            "2.5.1 服务响应机制\n"
            "建立 7×24 小时服务响应机制，一般问题 2 小时内响应、24 小时内提出处理意见；重大故障由项目经理组织专项组，"
            "4 小时内启动远程诊断，并根据现场需要安排工程师到场。\n\n"
            "2.5.2 备件与培训保障\n"
            "按关键度分级建立备件清单，提供不少于两年的备件保障；在设备交付、现场开通和质保期内分阶段开展操作与维护培训，"
            "提交培训教材、维护手册及常见故障处置卡。"
        )
    if code == "1.3":
        return (
            "同类项目经验补充\n"
            "结合本项目堆型、监测通道和交付范围，从已投运项目中筛选同平台、同探测原理、同验收标准的业绩，"
            "逐项附合同关键页、验收证书及用户联系人，确保业绩证明可核验、可追溯。"
        )
    return (
        f"本节围绕{project}编制。项目实施坚持安全第一、质量第一，依据招标技术规格书逐项建立响应关系，"
        "明确设计输入、验证方法、交付物和责任人。对关键参数及偏离项实行人工复核，所有对外承诺经授权人员终审后生效。"
    )


def calculate_progress(conn, bid_id):
    rows = conn.execute("SELECT status, content FROM sections WHERE bid_id=?", (bid_id,)).fetchall()
    if not rows:
        return 0
    total = 0.0
    for row in rows:
        if row["status"] == "已完成":
            total += 1
        elif row["status"] == "编制中":
            total += 0.6
        elif row["content"].strip():
            total += 0.25
    return round(total / len(rows) * 100)


def run_compliance(conn, bid_id):
    bid = conn.execute("SELECT * FROM bids WHERE id=?", (bid_id,)).fetchone()
    if not bid:
        return None
    checks = []

    upcoming = []
    for cert in conn.execute("SELECT * FROM certificates ORDER BY expiry_date").fetchall():
        left = days_left(cert["expiry_date"])
        if left is not None and 0 <= left <= 90:
            upcoming.append(f"{cert['name']}（{cert['expiry_date']}，剩 {left} 天）")
        elif left is not None and left < 0:
            upcoming.append(f"{cert['name']}（已过期）")
    checks.append({
        "rule": "资质证书有效期核验",
        "status": "warn" if upcoming else "ok",
        "description": "；".join(upcoming) + "，需附续证受理单或换证计划。" if upcoming else "全部资质在投标截止日有效，扫描件完整。",
        "penalty": 2 if upcoming else 0,
    })

    missing = max(0, bid["deviation_total"] - bid["deviation_responded"])
    checks.append({
        "rule": "技术偏离表逐条响应",
        "status": "warn" if missing else "ok",
        "description": f"技术规格书 {bid['deviation_total']} 条参数中 {missing} 条未响应，请补齐偏离表。" if missing else "技术参数已逐条响应，无遗漏。",
        "penalty": 4 if missing else 0,
    })

    sections = conn.execute("SELECT code,title,content FROM sections WHERE bid_id=? ORDER BY sort_order", (bid_id,)).fetchall()
    aliases = ["西安中核核仪器股份有限公司", "中核西仪", "262厂"]
    findings = []
    for section in sections:
        for alias in aliases:
            count = section["content"].count(alias)
            if count:
                findings.append(f"{section['code']} 节“{alias}”{count} 处")
    checks.append({
        "rule": "暗标要求检查",
        "status": "warn" if findings else "ok",
        "description": "检出公司标识：" + "；".join(findings) + "，请统一替换为“投标人”。" if findings else "未检出公司名称、简称或曾用名。",
        "penalty": 4 if findings else 0,
    })

    missing_pac = conn.execute("SELECT name FROM documents WHERE category='业绩案例' AND has_pac=0").fetchall()
    checks.append({
        "rule": "业绩证明材料完整性",
        "status": "warn" if missing_pac else "ok",
        "description": "以下业绩缺少 PAC/验收证明：" + "；".join(row["name"] for row in missing_pac) if missing_pac else "业绩合同与 PAC/验收证明均已关联。",
        "penalty": 2 if missing_pac else 0,
    })

    unnamed = [s["code"] for s in sections if not s["title"].strip()]
    checks.append({
        "rule": "目录与页码一致性",
        "status": "warn" if unnamed else "ok",
        "description": f"章节 {','.join(unnamed)} 标题为空，请修正目录。" if unnamed else f"目录 {len(sections)} 个章节标题完整，导出时自动重排页码。",
        "penalty": 2 if unnamed else 0,
    })

    checks.append({
        "rule": "签字盖章检查",
        "status": "ok" if bid["signature_complete"] else "warn",
        "description": "法人授权委托书、投标函和报价表签章信息已确认。" if bid["signature_complete"] else "法人授权委托书及报价表尚未确认签字盖章，请在提交前完成。",
        "penalty": 2 if not bid["signature_complete"] else 0,
    })

    page_limit = 400
    valid_pages = 0 < bid["page_count"] <= page_limit
    checks.append({
        "rule": "排版与装订要求",
        "status": "ok" if valid_pages else "warn",
        "description": f"正文 {bid['page_count']} 页，符合不超过 {page_limit} 页的要求。" if valid_pages else f"当前记录为 {bid['page_count']} 页，请更新页数并核对不超过 {page_limit} 页。",
        "penalty": 0 if valid_pages else 2,
    })

    score = max(0, 100 - sum(item["penalty"] for item in checks))
    result = {
        "bid_id": bid_id,
        "bid_name": bid["name"],
        "score": score,
        "summary": f"共检查 {len(checks)} 类规则，{sum(1 for c in checks if c['status'] == 'warn')} 项需整改",
        "can_submit": score == 100,
        "checks": checks,
        "created_at": now_text(),
    }
    conn.execute(
        "INSERT INTO compliance_runs(bid_id,score,result_json,created_at) VALUES(?,?,?,?)",
        (bid_id, score, json.dumps(result, ensure_ascii=False), now_text()),
    )
    audit(conn, "执行合规校验", "bid", bid_id, f"合规分 {score}")
    conn.commit()
    return result


# ---------------- 二期（1）招标解析 ----------------

TENDER_CATEGORIES = ("资格条件", "评分办法", "★条款", "废标条款", "技术参数", "交付期", "保证金", "份数装订", "澄清截止")
AUTO_CONFIRM_CONFIDENCE = 85  # ≥85 自动确认，低于则标黄待人工确认

_IMPORTANT_KEYWORDS = (
    ("废标条款", ("废标", "否决其投标", "否决投标", "拒收")),
    ("★条款", ("★",)),
    ("资格条件", ("资格", "资质要求", "投标人须具备")),
    ("评分办法", ("评分", "得满分", "分值", "评标办法")),
    ("保证金", ("保证金",)),
    ("份数装订", ("份数", "装订", "正本", "密封")),
    ("澄清截止", ("澄清", "截止时间", "开标时间")),
    ("交付期", ("交付", "交货期", "工期", "安装调试")),
)


def _classify_line(line):
    """按行业术语库与关键词强度对单行分类，返回 (category, score_value, confidence)。"""
    stripped = line.strip()
    for category, keywords in _IMPORTANT_KEYWORDS:
        hit = next((k for k in keywords if k in stripped), None)
        if hit:
            score_value = None
            m = re.search(r"(\d+(?:\.\d+)?)\s*分", stripped)
            if m:
                score_value = round(float(m.group(1)))
            confidence = 95 if category in ("废标条款", "★条款") else 78 + min(14, len(hit) * 6)
            return category, score_value, confidence
    return None, None, None


def parse_tender_text(text, term_hints=None):
    """对纯文本招标文件做规则抽取：定位页码与段落、按类别归档、给置信度。
    页码按每页约 40 行估算；置信度低于阈值的条目由前端标黄待人工确认。"""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    extracts = []
    page = 1
    for idx, line in enumerate(lines):
        page = idx // 40 + 1
        category, score_value, confidence = _classify_line(line)
        if not category:
            continue
        star = "★" in line
        title = re.sub(r"^[★\s\d\.\、）)（(]+", "", line.strip())[:40] or line[:40]
        extracts.append({
            "category": category,
            "title": title,
            "content": line.strip()[:300],
            "page": page,
            "para": f"第 {page} 页 第 {idx + 1} 行",
            "score_value": score_value,
            "confidence": confidence,
            "status": "已确认" if confidence >= AUTO_CONFIRM_CONFIDENCE else "待确认",
            "is_star": star,
        })
    return extracts


def build_demo_extracts(conn, bid):
    """无文本附件（PDF/Word 登记）时的演示解析档案：
    评分办法条目取自该标书的评分矩阵，其余八类按堆型/产品线上下文生成。"""
    rows = conn.execute(
        "SELECT item, score, section_code FROM scoring WHERE bid_id=? ORDER BY sort_order", (bid["id"],)
    ).fetchall()
    reactor = bid["reactor_type"] or "核电站"
    name = bid["name"]
    extracts = []
    for idx, row in enumerate(rows):
        extracts.append({
            "category": "评分办法", "title": row["item"],
            "content": f"{row['item']}：按招标文件评标办法对应条款评分，满分 {row['score']} 分。",
            "page": 45 + idx, "para": f"评标办法 3.{idx + 1}",
            "score_value": row["score"], "confidence": 90 + min(6, idx),
            "status": "已确认",
        })
    generic = [
        ("资格条件", "民用核安全设备许可", f"投标人须具备有效的民用核安全设备制造许可证，许可范围涵盖 {reactor} 堆型标的设备类别。", 12, "投标人须知 2.1.3", 96),
        ("资格条件", "类似业绩", "近五年至少 2 项同类成套供货业绩，须附合同与验收证明。", 14, "投标人须知 2.1.6", 74),
        ("★条款", "安全级设计", f"★安全级监测通道须按 IE 级要求设计鉴定，适应 {reactor} 堆型工况。", 88, "技术规格书 4.5", 93),
        ("废标条款", "负偏离", "对★条款存在负偏离的，否决其投标。", 28, "投标人须知 15.4", 94),
        ("废标条款", "逾期送达", "逾期送达或未按要求密封的投标文件作否决处理。", 26, "投标人须知 15.2", 97),
        ("技术参数", "通讯协议", "系统通讯须提供完整协议文本及接口定义。", 130, "技术规格书 5.9", 68),
        ("交付期", "设备交付", "合同生效后 20 个月内完成全部设备交付。", 101, "合同条款 6.1", 91),
        ("保证金", "投标保证金", "投标保证金金额与到账时间要求。", 30, "投标人须知 16.1", 93),
        ("份数装订", "正副本份数", "正本 1 份、副本 4 份、电子版 2 份，逐页编码。", 31, "投标人须知 17.1", 76),
        ("澄清截止", "澄清截止", f"{name} 澄清问题截止时间与答复时限要求。", 18, "投标人须知 9.1", 96),
    ]
    for category, title, content, page, para, conf in generic:
        extracts.append({
            "category": category, "title": title, "content": content,
            "page": page, "para": para, "score_value": None,
            "confidence": conf, "status": "已确认" if conf >= AUTO_CONFIRM_CONFIDENCE else "待确认",
        })
    return extracts
