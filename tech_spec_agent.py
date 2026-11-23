# -*- coding: utf-8 -*-
"""技术规格书独立解析 Agent —— 从技术规格书归纳"技术方案编写目录"并对齐技术标。

与招标解析 Agent（tender_agent）的关系：
  招标解析 关注"会不会废标 / 值多少分"（资格、评分、★、废标、交付期…）；
  技术规格 关注"技术方案怎么写"（供货范围、功能、性能、接口、标准、验收…），
  两者共用同一抽取管线（extract_file_blocks），因此页码口径完全一致。

管线：
  Step1 文本抽取        复用 tender_agent.extract_file_blocks（PDF 版面 / Word 结构与真实分页）
  Step2 规格书区段定位  找"第X章 供货要求/技术规格/技术要求"章，截到"投标文件格式/评标办法"等章前
  Step3 规格条目抽取    条款编号 / 子系统 / 1）2）子项 / 物资清单表 / 数值指标 / 标准引用 / ★必须项
  Step4 编写目录归纳    13 类主题 → 技术方案标准骨架；子系统超 4 条自动单列一章
  Step5 技术标映射      与标书编制已有技术标章节做"章节角色 + 专业域"双匹配，给强映射/弱映射/新建

所有条目都带原文页码与判定依据（signals），可回看原文；不做无依据的编造。
"""
import json
import re
from collections import OrderedDict
from pathlib import Path

from tender_agent import extract_file_blocks

BASE_DIR = Path(__file__).resolve().parent
PROFILE_FILE = BASE_DIR / "data" / "tech_outline_profiles.json"

# ---------------- Step2 规格书区段 ----------------

SPEC_CHAPTER_KEYS = ("供货要求", "技术规格", "技术规范", "技术要求", "技术条件", "采购需求", "技术需求", "供货范围")
SPEC_END_KEYS = ("投标文件格式", "评标办法", "合同条款", "投标人须知", "招标公告")

_CHAPTER_RE = re.compile(r"^第([一二三四五六七八九十百\d]+)章\s*(.*)$")
_NUM_RE = re.compile(r"^(\d+(?:\.\d+){0,3})[\s、.．]*([^\d\s].*)$")
_SUB_RE = re.compile(r"^(\d{1,2})[）)]\s*(.+)$")


def _is_toc_line(text):
    """目录行：章标题后跟页号（常见制表符或尾部数字），不是正文标题。"""
    return "\t" in text or bool(re.search(r"[\.．·…]{2,}\s*\d+\s*$", text)) or bool(
        re.search(r"^第[一二三四五六七八九十百\d]+章.*\S\s+\d{1,3}$", text))


def _locate_spec_region(blocks):
    """返回 (start, end, region_title, note)。
    未命中技术规格类章节时，退回全文（并在 note 中如实说明，不假装定位成功）。"""
    start = end = None
    title = ""
    for i, b in enumerate(blocks):
        text = (b.get("text") or "").strip()
        if not text or _is_toc_line(text):
            continue
        m = _CHAPTER_RE.match(text)
        if not m:
            continue
        rest = m.group(2)
        if any(k in rest for k in SPEC_CHAPTER_KEYS) and start is None:
            start, title = i, text[:40]
            continue
        if start is not None and any(k in rest for k in SPEC_END_KEYS):
            end = i
            break
    if start is None:
        # 独立技术规格书常以「1-1 标的名称：…」或「技术要求 / 技术参数表」开头，没有"第X章"。
        for i, b in enumerate(blocks):
            text = (b.get("text") or "").strip()
            if b.get("kind") == "table" or not text or _is_toc_line(text):
                continue
            if re.match(r"^\d+\s*[-—－]\s*\d+\s*标的名称", text) or re.match(
                    r"^(技术要求|技术规格|技术参数与性能指标|采购需求)\s*[：:]?", text):
                start, title = i, text[:40]
                end = None  # 独立规格书起点即全文起点，沿用下方"至文末"逻辑
                break
    if start is None:
        return 0, len(blocks), "", "未定位到独立的技术规格/供货要求章节，按全文技术类条款抽取"
    page_from = blocks[start].get("page") or 1
    note = f"定位技术规格书区段「{title}」"
    if end is not None and end > start:
        page_to = blocks[end].get("page") or page_from
        note += f"（第 {page_from}—{page_to} 页，含以下章节前）"
    else:
        note += f"（自第 {page_from} 页起至文末）"
        end = len(blocks)
    return start, end, title, note


# ---------------- Step3 规格条目 ----------------

# 主题分类：按加权关键词打分（数字越大越强），命中最高分者为该条目的编写主题。
CATEGORY_RULES = (
    ("标准规范", (("GB/T", 3), ("GB ", 3), ("GB5", 3), ("HAF", 3), ("EJ/T", 3), ("IEC", 3),
                  ("ISO", 2), ("标准", 1), ("规范", 1), ("强制性", 2))),
    ("性能指标", (("≥", 2), ("≤", 2), ("精度", 3), ("准确率", 3), ("响应时间", 3), ("响应速度", 2),
                  ("偏差", 2), ("误差", 2), ("分辨率", 2), ("并发", 2), ("性能", 1), ("毫秒", 1))),
    ("接口集成", (("协议", 3), ("接口", 2), ("OPC", 3), ("Modbus", 3), ("RTSP", 3), ("ONVIF", 2),
                  ("28181", 3), ("DCS", 2), ("数据交互", 2), ("集成", 2), ("对接", 2), ("通信", 2))),
    ("设备选型", (("服务器", 2), ("交换机", 2), ("摄像头", 2), ("摄像机", 2), ("传感器", 2), ("硬盘", 2),
                  ("线缆", 2), ("互感器", 2), ("采集箱", 2), ("机柜", 2), ("物料", 2), ("清单", 2),
                  ("品牌", 2), ("数量", 2))),
    ("环境与供电", (("温度", 2), ("湿度", 2), ("防护等级", 3), ("IP6", 2), ("供电", 3), ("电源", 2),
                    ("AC220", 3), ("DC24", 3), ("EMC", 2), ("电磁兼容", 3), ("防雷", 2), ("机房", 2),
                    ("海拔", 2), ("环境条件", 3), ("防腐", 2))),
    ("安全防护", (("权限", 2), ("日志", 2), ("备份", 2), ("冗余", 2), ("隔离", 2), ("入侵", 2),
                  ("加密", 2), ("安全", 1), ("防护", 1), ("泄露", 1))),
    ("质量与合规", (("质量体系", 3), ("ISO9001", 3), ("正品", 3), ("授权", 2), ("合格证", 3),
                    ("检测报告", 2), ("追溯", 2), ("国产化", 3), ("自主可控", 3), ("返修", 2), ("翻新", 2),
                    ("注册证", 3), ("经营许可证", 3), ("医疗器械", 3), ("NMPA", 3), ("二类", 2), ("三类", 2))),
    ("试验验收", (("试验", 2), ("检验", 2), ("验收", 3), ("测试", 1), ("检定", 3), ("标定", 3),
                  ("验证", 2), ("报告", 2), ("出厂", 2), ("联调", 3), ("试运行", 3), ("鉴定", 2))),
    ("培训售后", (("培训", 3), ("售后", 2), ("质保", 4), ("运维", 2), ("备件", 3), ("维保", 2),
                  ("服务响应", 2), ("服务体系", 2))),
    ("实施交付", (("安装", 2), ("调试", 3), ("实施", 2), ("进度", 2), ("工期", 3), ("交付", 2),
                  ("到货", 2), ("施工", 1), ("组织机构", 2))),
    ("需求与范围", (("供货范围", 4), ("覆盖范围", 3), ("项目名称", 2), ("采购类型", 2), ("建设目标", 3),
                    ("适用范围", 2), ("运行模式", 2), ("采购内容", 2), ("采购一批", 2), ("概述", 1), ("概况", 1))),
    ("总体架构", (("系统组成", 3), ("总体组成", 3), ("总体架构", 3), ("建设内容", 2), ("架构", 2),
                  ("子系统", 1), ("组成", 1), ("总体", 1))),
    ("功能设计", (("功能", 2), ("操作", 1), ("显示", 1), ("报警", 1), ("诊断", 1), ("分析", 1),
                  ("组态", 1), ("界面", 1), ("管理", 1), ("演练", 2), ("评分", 1))),
)

STD_RE = re.compile(r"\b(?:GB/T|GB|HAF|EJ/T|IEC|ISO|JGJ|DL/T)\s?\d[\w.\-/]*")
NUM_RE = re.compile(r"(?:[≥≤<>±]\s*\d|\d+(?:\.\d+)?\s*(?:%|℃|MPa|kPa|kV|V|A|Hz|秒|s\b|ms|TB|GB|PPM|ppm))")
MUST_RE = re.compile(r"★|▲|※|必须|不得|严禁|禁止|否决|废标|无效")
QTY_RE = re.compile(r"\d{1,5}\s*(?:台|套|块|个|只|件|批|项|组)(?![工作日月年人小])")
DEVICE_RE = re.compile(r"服务器|交换机|摄像头|摄像机|传感器|硬盘|线缆|互感器|采集箱|机柜|工控机|工作站|平台")
CATEGORY_ORDER = tuple(c for c, _ in CATEGORY_RULES)

# 领域模块：技术规格常以「一张大表 + 序号区间」描述产品能力，章节名藏在单元格内。
# 规则按顺序优先匹配（专用模块在前，通用模块在后），命中后成为目录的一级章节候选。
MODULE_RULES = (
    ("儿童骨龄辅助评估", ("骨龄", "骨骺", "桡骨", "尺骨", "GP图谱", "RUS-CHN", "TW3", "生长发育")),
    ("乳腺MR辅助诊断", ("乳腺", "BI-RADS", "乳腺MR", "钼靶")),
    ("肝脏MR智能辅助诊断", ("肝脏", "肝囊肿", "肝血管瘤", "肝结节", "肝脏MR")),
    ("颅脑MR与CT-FFR评估", ("颅脑", "脑区", "TOF-MRA", "CSVD", "Willis", "CT-FFR", "冠脉", "脑卒中")),
    ("服务器与部署环境", ("CPU", "内存", "GPU", "显卡", "硬盘", "冗余电源", "机架式", "千兆RJ45", "服务器配置")),
    ("数据管理与接口服务", ("数据管理", "接口服务", "数据备份", "HIS", "PACS", "RIS", "DICOM转发", "接口对接")),
    ("服务团队与验收资质", ("服务团队", "项目经理", "培训计划", "验收标准", "验收方案", "注册证",
                       "经营许可证", "业绩", "售后服务", "质保期")),
    ("平台基本要求与总体架构", ("基本要求", "平台架构", "B/S架构", "Web登陆", "浏览器", "多院区",
                         "本地化部署", "总体架构", "系统架构")),
    ("影像标注与AI辅助能力", ("标注", "分割", "CT肺结节", "影像数据", "DICOM", "MPR", "病灶", "AI自动", "算法")),
)


def _module_of(text):
    text = text or ""
    # 供货范围总述里会顺带列举所有产品名，先按总述处理，避免被第一个产品名带偏。
    if any(k in text for k in ("标的名称", "供货范围", "放射影像标注平台 1 套", "放射影像标注平台（包含）")):
        return "平台基本要求与总体架构"
    for name, keywords in MODULE_RULES:
        if any(k.lower() in text.lower() for k in keywords):
            return name
    return None


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").replace("\u3000", " ")).strip()


def _classify(text, allow_device=True):
    """返回 (category, score, hit_keyword)；无命中返回 (None, 0, "")。
    allow_device=False 时，纯叙述句即使提到"服务器/交换机"也不归入设备选型（多为引用平台条件），
    避免把"核心硬件优先国产化"这类合规条款误收进供货清单。"""
    scores = {}
    hits = {}
    for category, kws in CATEGORY_RULES:
        score, hit = 0, ""
        for kw, weight in kws:
            if kw in text:
                score += weight * min(text.count(kw), 2)
                if not hit:
                    hit = kw
        scores[category], hits[category] = score, hit
    if not allow_device:
        scores["设备选型"] = 0
    # 硬件清单特征（设备名 + 数量单位）直接判为设备选型
    if DEVICE_RE.search(text) and QTY_RE.search(text):
        scores["设备选型"] += 8
        hits.setdefault("设备选型", "设备名+数量")
    best = max(CATEGORY_ORDER, key=lambda c: scores.get(c, 0))
    if scores.get(best, 0) <= 0:
        return None, 0, ""
    return best, scores[best], hits[best]


def _split_title(text):
    """去掉条款编号后拆标题与正文：'5.3 故障诊断：正文…' → ('故障诊断', '正文…')。"""
    t = _norm(text)
    t = re.sub(r"^[（(]?\d{1,3}[）)、.．]\s*", "", t).strip()
    t = re.sub(r"^\d+(?:\.\d+){0,3}[\s、.．]*", "", t).strip()
    t = t.lstrip("）)、.． ")
    if "：" in t[:40]:
        head, tail = t.split("：", 1)
        return head.strip("。；，, ")[:40] or tail[:18], tail.strip()
    if "。" in t[:40]:
        head = t.split("。", 1)[0]
        return head[:40], t
    if len(t) <= 26:
        return t, t
    head = re.split(r"[（(，；,]", t)[0].strip()
    if len(head) < 4:
        head = t[:26]
    return head[:40], t


def _looks_like_heading(text):
    """短标题：有编号且无句读；'X.Y 标题：正文' 属于条款而非标题。"""
    t = text.strip()
    if len(t) > 46 or re.search(r"[。；]", t):
        return False
    if _CHAPTER_RE.match(t):
        return True
    if re.match(r"^[一二三四五六七八九十]+、", t):
        return True
    m = _NUM_RE.match(t)
    if not m:
        return False
    rest = m.group(2).strip()
    if rest.startswith(("）", ")")):   # 1）2）属于子项，不作为层级标题
        return False
    if "：" in rest or ":" in rest:
        return False
    if len(rest) <= 22:
        return True
    return bool(re.search(r"(物料清单|设备清单|一览表|需求表|供货清单)", rest))


def _table_item(text, page):
    """物资/参数表行 → 设备选型或性能指标条目；表头与合计行跳过。"""
    cells = [c.strip() for c in text.split("|") if c.strip()]
    if not cells or len(cells) < 2:
        return None
    flat = _norm(" ".join(cells))
    if "合计" in flat or "序号" in flat or "物料名称" in flat and "单位" in flat:
        return None
    names = [(i, c) for i, c in enumerate(cells)
             if not re.fullmatch(r"[\d.\s]+", c)
             and "详见" not in c
             and c not in ("台", "套", "块", "个", "只", "件", "批", "项", "组")
             and 2 <= len(c) <= 30]
    if not names:
        return None
    name_idx, name = next(((i, c) for i, c in names if DEVICE_RE.search(c)), names[0])
    qty, unit = None, ""
    for i, c in enumerate(cells[name_idx + 1:], start=name_idx + 1):
        if re.fullmatch(r"\d{1,5}(?:\.\d+)?", c):
            qty = c
            if i + 1 < len(cells) and cells[i + 1] in ("套", "台", "块", "个", "只", "件", "批", "项", "组"):
                unit = cells[i + 1]
            break
    if qty:
        title = f"供货清单：{name}"
        content = f"{name}　数量 {qty} {unit}".strip() + (f"；{flat[:160]}" if len(flat) > len(name) + 6 else "")
        category = "设备选型"
        signals = ["物资清单行", f"数量 {qty}{unit}"]
    else:
        category, score, hit = _classify(flat)
        if not category or not NUM_RE.search(flat):
            return None
        title = f"参数表：{name}"
        content = flat[:300]
        signals = [f"表格参数行（{hit}）"]
    must = bool(MUST_RE.search(flat))
    return {"title": title, "content": content[:300], "page": page, "category": category,
            "must": must, "signals": signals + (["★/必须标记"] if must else [])}


_SPEC_FLAG_RE = re.compile(r"^[★▲※]?$")
_SPEC_SERIAL_RE = re.compile(r"^\d{1,3}(?:[-－]\d{1,3})?$")
_EMBEDDED_HEAD_RE = re.compile(r"^\s*(\d{1,2})[.．、]\s*([^\n\d]{2,20})\s*(?:\n|$)")


def _spec_table_cells(block):
    cells = block.get("cells")
    if cells:
        return [str(c or "").strip() for c in cells]
    return [c.strip() for c in (block.get("text") or "").split("|") if c.strip()]


def _is_spec_table_header(cells):
    flat = " ".join(cells[:4])
    return ("参数性质" in flat and "序号" in flat) or (
        "技术参数" in flat and "序号" in flat and "性能指标" in flat)


def _parse_spec_table_row(block, page):
    """三列规格表行：参数性质 | 序号 | 技术参数与性能指标。

    这类行的要求正文通常远超 30 字，且多为功能描述（无数值），旧 `_table_item`
    会因"找不到短名称"直接丢弃。这里按列位解析，保留每一行要求。
    """
    cells = _spec_table_cells(block)
    if len(cells) < 3 or _is_spec_table_header(cells):
        return None
    flag, serial, body = cells[0].strip(), cells[1].strip(), cells[2].strip()
    if not body or not _SPEC_SERIAL_RE.match(serial) or not _SPEC_FLAG_RE.match(flag):
        return None
    if any(k in body for k in ("合计", "小计", "备注：序号")):
        return None

    evidence = ""
    m = re.search(r"证明材料[：:]\s*(.+)$", body)
    if m:
        evidence = _norm(m.group(1))
        body = body[:m.start()].strip()

    module_hint = ""
    hm = _EMBEDDED_HEAD_RE.match(body)
    if hm:
        module_hint = hm.group(2).strip()
        body = body[hm.end():].strip()

    title, content = _split_title(body or module_hint or f"规格条目 {serial}")
    if module_hint and len(title) > 24:
        title = module_hint
    category, _, hit = _classify(f"{title} {content}", allow_device=True)
    if not category:
        category, hit = "功能设计", "技术描述"

    must = bool(flag) or bool(MUST_RE.search(body))
    signals = ["规格表行", f"序号 {serial}"]
    if flag:
        signals.append(f"参数性质 {flag}")
    if module_hint:
        signals.append(f"内嵌小标题「{module_hint}」")
    if evidence:
        signals.append("证明材料")
    if hit:
        signals.append(f"主题词「{hit}」")
    if must:
        signals.append("★/必须标记")
    return {"code": serial, "title": title[:40], "content": content[:300], "page": page,
            "category": category, "must": must, "subsystem": None, "module_hint": module_hint,
            "source": "spec_table", "evidence": evidence, "signals": signals,
            "std": sorted(set(STD_RE.findall(body)))}


def _extract_items(blocks, stats=None):
    """从规格书区段抽取条目；同时返回子系统清单（用于目录单列）。

    stats 可选，用于回填表格行数/保留数/模块列表等解析质量指标。
    """
    items, subsystems, modules = [], [], []
    stats = stats if isinstance(stats, dict) else {}
    stats.setdefault("table_rows", 0)
    stats.setdefault("spec_rows", 0)
    stats.setdefault("dropped_rows", 0)
    stats.setdefault("modules", modules)
    cur_topic = ""
    cur_subsystem, cur_subsystem_level = None, None
    pending_star = False
    last_module = None
    seen = set()

    def add(item):
        nonlocal last_module
        content = item.get("content") or ""
        key = (item.get("code") or "") + re.sub(
            r"[\s★▲※、。，；：（）()\-—0-9A-Za-z]", "", content)[:70]
        if len(re.sub(r"\d+", "", key)) < 6 or key in seen:
            return
        seen.add(key)
        text = f"{item.get('title', '')} {content} {item.get('module_hint', '')}"
        module = item.get("module") or _module_of(text)
        if not module and item.get("module_hint"):
            module = _module_of(item["module_hint"]) or item["module_hint"]
        if not module and last_module:
            module = last_module  # 规格表按序号连续描述同一模块，继承上文
        if module:
            item["module"] = module
            if module not in modules:
                modules.append(module)
            last_module = module
        items.append(item)

    for b in blocks:
        text = (b.get("text") or "").strip()
        page = b.get("page") or 1
        if not text or _is_toc_line(text):
            continue
        if text in ("★", "▲", "※"):
            pending_star = True
            continue
        is_star = pending_star or bool(re.search("[★▲※]", text))
        pending_star = False

        if b.get("kind") == "table":
            stats["table_rows"] += 1
            row = _parse_spec_table_row(b, page)
            if not row:
                row = _table_item(text, page)
            if row:
                if b.get("cells") and row.get("code") and _SPEC_SERIAL_RE.match(str(row["code"])):
                    stats["spec_rows"] += 1
                row["must"] = row["must"] or is_star
                if is_star and "★/必须标记" not in row["signals"]:
                    row["signals"].append("★/必须标记")
                row["subsystem"] = cur_subsystem
                add(row)
            else:
                stats["dropped_rows"] += 1
            continue

        # 标题 / 子系统
        if _looks_like_heading(text):
            m = _NUM_RE.match(text)
            level = m.group(1).count(".") + 1 if m else 0
            heading = m.group(2).strip() if m else re.sub(r"^[一二三四五六七八九十]+、", "", text)
            if cur_subsystem_level is not None and level and level <= cur_subsystem_level:
                cur_subsystem, cur_subsystem_level = None, None
            if any(k in heading for k in SPEC_END_KEYS):
                cur_subsystem, cur_subsystem_level = None, None
            is_subsystem = (level == 2 and "系统" in heading and len(heading) <= 30
                            and not re.search(r"(要求|清单|条件|组成|指标)", heading))
            if is_subsystem:
                name = re.sub(r"[（(].*?[）)]", "", heading).strip()
                cur_subsystem, cur_subsystem_level = name, level
                if name not in subsystems:
                    subsystems.append(name)
            else:
                cur_topic = heading[:30]
            continue

        m = _NUM_RE.match(text)
        sub = _SUB_RE.match(text)
        # 2）硬件物资要求（…）/ 1）功能要求：属于小标题，不是要求本身
        if sub:
            body = sub.group(2).strip()
            is_sub_head = len(text) <= 40 and (
                re.match(r"^(功能|软件|硬件|设备|系统|接口|性能|培训|服务|其他)[^，。；]{0,12}要求", body)
                or (body.rstrip("：:").endswith(("）", ")")) and "：" not in body))
            if is_sub_head:
                cur_topic = re.split(r"[：:]", body)[0][:30]
                continue
        if m:
            raw_title, raw_body = _split_title(text)
            code = m.group(1)
            title = raw_title or text[:18]
            content = raw_body if len(raw_body) >= 6 else text
            signals = ["条款编号"]
        elif sub:
            raw_title, raw_body = _split_title(text)
            code = ""
            title = raw_title or text[:18]
            content = raw_body if len(raw_body) >= 6 else text
            signals = ["子项编号"]
        else:
            if len(text) < 8:
                continue
            code = ""
            title, content = _split_title(text)
            signals = ["段落条款"]

        flat = f"{title} {content}"
        allow_device = bool(QTY_RE.search(flat)) or ("硬件" in cur_topic or "物资" in cur_topic)
        category, score, hit = _classify(flat, allow_device=allow_device)
        if "供货范围" in title:      # 供货范围条款归入项目理解，即便其中罗列了设备数量
            category, hit = "需求与范围", "供货范围"
        if not category:
            if not (NUM_RE.search(flat) or STD_RE.search(flat) or is_star):
                continue
            category, hit, score = "功能设计", "技术描述", 1
        std = STD_RE.findall(flat)
        numeric = bool(NUM_RE.search(flat))
        must = is_star or bool(MUST_RE.search(flat))
        if hit:
            signals.append(f"主题词「{hit}」")
        if numeric:
            signals.append("含量化指标")
        if std:
            signals.append("引用标准 " + "/".join(sorted(set(std))[:2]))
        if must:
            signals.append("★/必须标记")
        if cur_subsystem:
            signals.append(f"子系统：{cur_subsystem}")
        add({"code": code, "title": title[:40], "content": content[:300], "page": page,
             "category": category, "must": must, "subsystem": cur_subsystem,
             "topic": cur_topic, "signals": signals, "std": sorted(set(std))})

    return items, subsystems


# ---------------- Step4 技术方案编写目录 ----------------

# 技术方案标准骨架：主题 → 目录项 → 对应技术标角色
SKELETON = (
    {"slug": "scope", "title": "项目理解与供货范围", "cats": ("需求与范围",), "role": "总体方案",
     "hint": "先说清「买什么、建什么、界到哪里」，再谈方案——评委据此判断是否读懂了项目。"},
    {"slug": "arch", "title": "系统总体架构与组成", "cats": ("总体架构",), "role": "总体方案",
     "hint": "给出系统组成框图、子系统划分与数据流，标明独立建设/共用服务器等边界约束。"},
    {"slug": "sub", "title": "分系统设计方案", "cats": ("功能设计",), "role": "设计方案",
     "hint": "按规格书子系统逐项展开功能与操作流程，避免用通用平台介绍替代针对性设计。"},
    {"slug": "perf", "title": "性能指标响应与保证", "cats": ("性能指标",), "role": "new",
     "hint": "逐条对表格化响应：指标值—响应值—验证方式，负偏离须显式说明。"},
    {"slug": "dev", "title": "设备选型与供货清单", "cats": ("设备选型",), "role": "new",
     "hint": "按物料清单逐项列型号、产地、数量，国产化与正品承诺在此处呼应。"},
    {"slug": "if", "title": "接口与系统集成方案", "cats": ("接口集成",), "role": "new",
     "hint": "协议、点表、与既有 DCS/第三方系统的边界与安全接入步骤必须写实。"},
    {"slug": "std", "title": "标准规范符合性", "cats": ("标准规范",), "role": "new",
     "hint": "逐项列明执行标准与版本，标注强制性条款的符合性证据。"},
    {"slug": "safe", "title": "安全防护与可靠性设计", "cats": ("安全防护",), "role": "new",
     "hint": "权限、日志、备份、冗余、故障隔离——共用服务器场景是本项重点。"},
    {"slug": "env", "title": "环境适应性与供电设计", "cats": ("环境与供电",), "role": "new",
     "hint": "温湿度、防护等级、供电制式与备用电源切换，按现场条件逐项响应。"},
    {"slug": "qa", "title": "质量保证与正品承诺", "cats": ("质量与合规",), "role": "质量",
     "hint": "质量体系、原厂正品与软件授权证据链，附合格证/检测报告清单。"},
    {"slug": "test", "title": "试验检验与验收方案", "cats": ("试验验收",), "role": "new",
     "hint": "出厂检验、现场联调、试运行与验收判据要可执行、可签认。"},
    {"slug": "impl", "title": "实施进度与组织保障", "cats": ("实施交付",), "role": "实施",
     "hint": "按交付期倒排里程碑，明确安装调试窗口与不停产约束。"},
    {"slug": "service", "title": "培训与售后运维保障", "cats": ("培训售后",), "role": "售后",
     "hint": "培训对象/课时/考核，响应时限与备件保障，与质保期条款逐条对齐。"},
)
FALLBACK = {"slug": "other", "title": "其他技术规格响应", "cats": (), "role": "new",
            "hint": "未能归入上述主题的规格条目，逐条响应并注明原文页码。"}

ROLE_KEYWORDS = {
    "总体方案": ("总体方案", "总体设计", "项目理解", "概述", "技术方案"),
    "设计方案": ("设计方案", "技术方案", "分系统", "子系统", "技术响应"),
    "质量": ("质量保证", "质量体系", "核安全文化", "质量管理"),
    "实施": ("实施", "进度", "组织", "交付计划"),
    "售后": ("售后", "服务", "备件", "运维", "质保"),
}

DOMAIN_STOP = ("质量保证", "质量体系", "核安全文化", "质量管理", "项目实施", "进度计划", "组织保障",
               "售后服务", "备件保障", "服务保障", "系统总体", "总体方案", "总体设计", "分系统",
               "子系统", "设计方案", "技术方案", "技术响应", "项目理解", "概述", "实施", "进度",
               "计划", "售后", "服务", "备件", "保障", "质量", "系统", "设计", "方案", "技术",
               "平台", "管理", "响应", "说明", "文件", "工程", "与", "及", "的")


def _brief(item_list):
    if not item_list:
        return ""
    tops = "；".join(i["title"][:18] for i in item_list[:3])
    must = sum(1 for i in item_list if i["must"])
    text = f"须响应 {len(item_list)} 条技术规格" + (f"（★/必须 {must} 条）" if must else "") + f"：{tops}"
    return text[:120]


def _finalize_outline(outline):
    """汇总计数与页码区间（父章只统计本章直接要求，子章节单独统计，避免同步后重复）。"""
    def visit(node):
        reqs = node["requirements"]
        node["req_count"] = len(reqs)
        node["must_count"] = sum(1 for r in reqs if r["must"])
        pages = [r["page"] for r in reqs if r.get("page")]
        node["page_from"] = min(pages) if pages else None
        node["page_to"] = max(pages) if pages else None
        node["categories"] = sorted({r["category"] for r in reqs})
        for child in node["children"]:
            visit(child)
        node["child_req_count"] = sum(c["req_count"] for c in node["children"])
        node["child_must_count"] = sum(c["must_count"] for c in node["children"])
        if node["page_from"] is None and node["children"]:
            child_pages = [c["page_from"] for c in node["children"] if c["page_from"]]
            child_ends = [c["page_to"] for c in node["children"] if c["page_to"]]
            node["page_from"] = min(child_pages) if child_pages else None
            node["page_to"] = max(child_ends) if child_ends else None
        return node
    for node in outline:
        visit(node)
    return outline


def _module_role(name):
    if any(k in name for k in ("验收", "资质")):
        return "new"
    if "服务团队" in name:
        return "售后"
    if "总体架构" in name or "基本要求" in name:
        return "总体方案"
    return "设计方案"


def _build_module_outline(items):
    """表格型技术规格优先按领域模块成章，而不是塞进固定 13 类骨架。

    仅当"三列规格表"行足够多时启用；普通段落式规格仍走 13 类骨架，
    避免零星关键词（如"服务器 2 台"）把整套目录带偏。
    """
    table_items = [i for i in items if i.get("source") == "spec_table"]
    if len(table_items) < 5:
        return []
    buckets = OrderedDict()
    for item in items:
        module = item.get("module")
        if module:
            buckets.setdefault(module, []).append(item)
    kept = {name for name, reqs in buckets.items() if len(reqs) >= 2}
    if not kept:
        return []
    outline, seq = [], 0
    for name in buckets:
        if name not in kept:
            continue
        reqs = buckets[name]
        seq += 1
        children = []
        if len(reqs) >= 10:
            by_cat = OrderedDict()
            for r in reqs:
                by_cat.setdefault(r["category"], []).append(r)
            if len(by_cat) > 1:
                for ci, (cat, cat_items) in enumerate(by_cat.items(), start=1):
                    if len(cat_items) < 2:
                        continue
                    children.append({
                        "code": f"{seq}.{len(children) + 1}", "level": 2,
                        "title": f"{name}·{cat}", "role": "设计方案",
                        "brief": _brief(cat_items), "requirements": cat_items, "children": [],
                    })
        outline.append({
            "code": str(seq), "level": 1, "title": name, "role": _module_role(name),
            "brief": _brief(reqs), "requirements": [] if children else reqs, "children": children,
        })
    rest = [i for i in items if not i.get("module") or i["module"] not in kept]
    if rest:
        seq += 1
        outline.append({"code": str(seq), "level": 1, "title": FALLBACK["title"], "role": "new",
                        "brief": _brief(rest), "requirements": rest, "children": []})
    return _finalize_outline(outline)


def _build_outline(items, subsystems):
    """领域模块优先；无模块线索时退回 13 类主题骨架 + 子系统单列。"""
    module_outline = _build_module_outline(items)
    if module_outline:
        return module_outline
    big_sub = {s for s in subsystems if sum(1 for i in items if i.get("subsystem") == s) >= 2}
    buckets = {s["slug"]: [] for s in SKELETON}
    sub_buckets = {s: [] for s in big_sub}
    assigned = set()

    for idx, item in enumerate(items):
        sub = item.get("subsystem")
        if sub in big_sub and item["category"] in ("功能设计", "性能指标", "接口集成", "安全防护"):
            sub_buckets[sub].append(item)
            assigned.add(idx)
            continue
        for sk in SKELETON:
            if item["category"] in sk["cats"]:
                buckets[sk["slug"]].append(item)
                assigned.add(idx)
                break

    outline, seq = [], 0
    for sk in SKELETON:
        group = buckets[sk["slug"]]
        if sk["slug"] == "sub":
            # 分系统章：先放子系统的子项，再放未能单列的通用功能条目
            children = [(name, sub_buckets[name]) for name in subsystems if name in big_sub]
            own = group
            if not children and not own:
                continue
            seq += 1
            node = {"code": str(seq), "level": 1, "title": sk["title"], "role": sk["role"],
                    "brief": sk["hint"], "requirements": list(own), "children": []}
            for name, sub_items in children:
                node["children"].append({
                    "code": f"{seq}.{len(node['children']) + 1}", "level": 2,
                    "title": f"{name}技术方案", "role": "设计方案",
                    "brief": _brief(sub_items), "requirements": sub_items, "children": [],
                })
            outline.append(node)
            continue
        if not group:
            continue
        seq += 1
        outline.append({"code": str(seq), "level": 1, "title": sk["title"], "role": sk["role"],
                        "brief": _brief(group) or sk["hint"], "requirements": group, "children": []})

    rest = [i for idx, i in enumerate(items) if idx not in assigned]
    if rest:
        seq += 1
        outline.append({"code": str(seq), "level": 1, "title": FALLBACK["title"], "role": "new",
                        "brief": _brief(rest), "requirements": rest, "children": []})

    return _finalize_outline(outline)


# ---------------- Step5 技术标映射 ----------------

def _domain_tokens(title):
    """章节标题中的"专业域"词：去掉通用角色词后剩下的产品/系统名（如 辐射监测、KRT）。"""
    t = title or ""
    for w in DOMAIN_STOP:
        t = t.replace(w, " ")
    toks = re.findall(r"[A-Za-z]{2,6}|[\u4e00-\u9fa5]{2,6}", t)
    return [x for x in toks if x not in DOMAIN_STOP]


def _map_outline(outline, sections, spec_text):
    """给每个目录项找标书编制中的技术标章节：强映射 / 弱映射 / 新建。
    弱映射 = 章节角色对上了，但章节标题里的专业域在规格书中找不到（多为别的产品线章节），
    此时建议新建章节，避免"KRT 设计方案"套到辅助系统项目上。"""
    tech = [s for s in sections if (s.get("group_name") or "") == "技术标"]
    flat = list(_all_nodes(outline))

    for node in flat:
        roles = ROLE_KEYWORDS.get(node["role"], ())
        best, best_score = None, 0
        for s in tech:
            title = s.get("title") or ""
            hit = sum(1 for k in roles if k in title)
            if node["role"] == "new":
                hit = 3 if title.strip() == node["title"].strip() else 0
            if node["role"] == "设计方案" and "系统" in title:
                hit += 1
            if hit > best_score:
                best, best_score = s, hit
        if not best:
            node.update(section_id=None, mapping_kind="new",
                        mapping_note="技术标中无对应章节，同步时新建")
            continue
        domain = _domain_tokens(best["title"])
        overlap = [t for t in domain if t in spec_text]
        if node["role"] != "new" and domain and not overlap:
            node.update(section_id=None, mapping_kind="weak",
                        mapping_note=f"建议复用「{best['code']} {best['title']}」角色匹配，但专业域不符（{('、'.join(domain))[:18]}），建议新建")
        else:
            node.update(section_id=best["id"], mapping_kind="strong",
                        mapping_note=f"映射到「{best['code']} {best['title']}」" +
                                     (f"（专业域命中：{overlap[0]}）" if overlap else ""))
    return outline


def _all_nodes(nodes):
    for node in nodes or []:
        yield node
        yield from _all_nodes(node.get("children") or [])


def _load_outline_profiles():
    try:
        data = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    profiles = data.get("profiles") if isinstance(data, dict) else None
    if isinstance(profiles, list):
        return [p for p in profiles if isinstance(p, dict)]
    return [data] if isinstance(data, dict) and data.get("nodes") else []


def _match_outline_profile(items):
    text = " ".join(f"{i.get('title', '')} {i.get('content', '')}" for i in items)
    for profile in _load_outline_profiles():
        keys = profile.get("match_all") or []
        if keys and all(k in text for k in keys):
            return profile
    return None


def _serial_of(requirement):
    try:
        return int(str((requirement or {}).get("code") or "").strip())
    except (TypeError, ValueError):
        return None


def _apply_outline_profile(items, profile):
    if not profile or not profile.get("nodes"):
        return []
    index = {}
    for item in items:
        serial = _serial_of(item)
        if serial is not None:
            index[serial] = item

    def explicit_requirement(node):
        base = dict(node.get("requirement") or {})
        source = index.get(node.get("source_serial")) or index.get(_serial_of(base))
        if source:
            for key in ("page", "category", "signals", "evidence", "std"):
                base.setdefault(key, source.get(key))
            base["must"] = bool(base.get("must", source.get("must", False)))
            if not base.get("content"):
                base["content"] = source.get("content", "")
        base.setdefault("evidence", "")
        base.setdefault("signals", [])
        base.setdefault("category", "需求与范围")
        base.setdefault("page", None)
        return base

    def placeholder_requirement(node):
        return {"code": node.get("placeholder_code", ""), "title": node["title"],
                "content": node.get("placeholder_text", "原文未展开，需补充。"),
                "page": None, "category": "原文缺失", "must": False,
                "signals": ["原文未展开"], "evidence": "", "placeholder": True}

    def short_title(requirement):
        text = str(requirement.get("title") or requirement.get("content") or "").strip()
        text = re.sub(r"^(支持|提供|可|应|需|具备|采用|实现|内置|自动)+", "", text)
        text = re.sub(r"[。；;].*$", "", text).strip(" ，,：:")
        return (text[:26] or "技术要求").strip()

    def build(node):
        reqs = []
        if node.get("placeholder"):
            reqs.append(placeholder_requirement(node))
        elif node.get("requirement"):
            reqs.append(explicit_requirement(node))
        else:
            for low, high in node.get("ranges") or []:
                for serial in range(int(low), int(high) + 1):
                    if serial in index:
                        reqs.append(index[serial])
        level = int(node.get("level") or 1)
        children = [build(child) for child in node.get("children") or []]
        # 四级展开：区间节点若未满四级，逐条要求生成下一级目录项。
        # 显式要求/占位节点本身已是目录项，不再重复生成子项。
        if reqs and level < 4 and not node.get("placeholder") and not node.get("requirement"):
            for i, requirement in enumerate(reqs, start=1):
                children.append({"code": f"{node['code']}.{i}", "level": level + 1,
                                 "title": short_title(requirement), "role": "new", "brief": "",
                                 "requirements": [requirement], "children": [], "profile": True,
                                 "requirement_node": True})
            reqs = []
        return {"code": node["code"], "level": level, "title": node["title"],
                "role": _module_role(node["title"]), "brief": "", "requirements": reqs,
                "children": children, "profile": True}

    outline = _finalize_outline([build(n) for n in profile["nodes"]])

    def fill_brief(node):
        for child in node["children"]:
            fill_brief(child)
        if node["children"]:
            all_reqs = []
            for child in node["children"]:
                all_reqs.extend(child["requirements"])
                for grand in child["children"]:
                    all_reqs.extend(grand["requirements"])
            node["brief"] = _brief(all_reqs)
        else:
            node["brief"] = _brief(node["requirements"])

    for node in outline:
        fill_brief(node)
    return outline


def _profile_evidence_index(items, profile=None):
    """附：★/▲/证明材料/原文缺失索引，随解析报告返回，不改变条目原文。"""
    def codes(prefix, test):
        return [str(i.get("code") or "") for i in items if test(i) and str(i.get("code") or "")]

    index = {
        "must": codes("", lambda i: i.get("must")),
        "screenshot": codes("", lambda i: "软件截图" in str(i.get("evidence") or "")),
        "certificate": codes("", lambda i: "证书复印件" in str(i.get("evidence") or "")),
        "enterprise_letter": codes("", lambda i: "企业承诺" in str(i.get("evidence") or "")),
        "missing": [],
    }
    for node in profile.get("nodes", []) if profile else []:
        stack = [node]
        while stack:
            current = stack.pop()
            stack.extend(current.get("children") or [])
            if current.get("placeholder"):
                index["missing"].append(str(current.get("placeholder_code") or ""))
    return {key: list(dict.fromkeys(value)) for key, value in index.items()}


# ---------------- Agent 主入口 ----------------

def run_tech_spec_agent(path, sections=None):
    """解析技术规格书 → {items, outline, report, page_count, page_source, region}。"""
    sections = sections or []
    blocks, page_count, source, page_source = extract_file_blocks(path)
    steps = [{"step": 1, "name": "技术规格书文本抽取", "status": "完成" if blocks else "警告",
              "detail": f"{len(blocks)} 个内容块" + (f"，{page_count} 页" if page_count else ""),
              "ms": 0}]
    if not blocks:
        return {"items": [], "outline": [], "page_count": page_count, "page_source": page_source,
                "region": {"title": "", "page_from": None, "page_to": None},
                "report": {"agent": "技术规格解析 Agent v1", "steps": steps,
                           "summary": {"reqs": 0, "outline": 0},
                           "fallback": "无可解析文本（扫描件需 OCR 或提供可搜索版本）"}}

    start, end, region_title, region_note = _locate_spec_region(blocks)
    spec_blocks = blocks[start:end]
    pages = [b.get("page") for b in spec_blocks if b.get("page")]
    steps.append({"step": 2, "name": "技术规格书区段定位", "status": "完成",
                  "detail": region_note, "ms": 0})

    stats = {}
    items, subsystems = _extract_items(spec_blocks, stats)
    cat_count = {}
    for it in items:
        cat_count[it["category"]] = cat_count.get(it["category"], 0) + 1
    table_note = (f"；规格表行 {stats.get('spec_rows', 0)}/{stats.get('table_rows', 0)}"
                  if stats.get("table_rows") else "")
    steps.append({"step": 3, "name": "规格条目抽取",
                  "status": "完成" if items else "警告",
                  "detail": f"抽取 {len(items)} 条技术规格（★/必须 {sum(1 for i in items if i['must'])} 条）"
                            + table_note
                            + ("：" + "、".join(f"{k} {v}" for k, v in sorted(cat_count.items(), key=lambda x: -x[1])[:6]) if items else "；未发现技术条款，请核对文件是否为技术规格书"),
                  "ms": 0})

    outline = _build_outline(items, subsystems)
    modules = stats.get("modules") or []
    profile = _match_outline_profile(items)
    if profile:
        templated = _apply_outline_profile(items, profile)
        if templated:
            outline = templated
    group_note = (f"套用目录模板「{profile.get('name', '未命名')}」" if profile else
                  ("领域模块成章：" + "、".join(modules[:6]) if modules else
                   f"子系统单列：{'、'.join(subsystems[:4]) if subsystems else '无'}"))
    flat_nodes = list(_all_nodes(outline))
    steps.append({"step": 4, "name": "技术方案编写目录归纳", "status": "完成" if outline else "警告",
                  "detail": f"归纳 {sum(1 for n in flat_nodes if n['level'] == 1)} 章／{len(flat_nodes)} 节，"
                            f"覆盖 {len(items)} 条规格；{group_note}",
                  "ms": 0})

    spec_text = " ".join(f"{i['title']}{i['content']}" for i in items)
    _map_outline(outline, sections, spec_text)
    flat = flat_nodes
    strong = sum(1 for n in flat if n["mapping_kind"] == "strong")
    weak = sum(1 for n in flat if n["mapping_kind"] == "weak")
    steps.append({"step": 5, "name": "技术标章节映射",
                  "status": "完成",
                  "detail": f"强映射 {strong} 节（复用已有技术标章节）／弱映射 {weak} 节（专业域不符，建议新建）"
                            f"／新建 {len(flat) - strong - weak} 节；同步后写入标书编制技术标",
                  "ms": 0})

    report = {"agent": "技术规格解析 Agent v2", "steps": steps,
              "summary": {"blocks": len(blocks), "reqs": len(items), "must": sum(1 for i in items if i["must"]),
                          "outline": len(flat), "sections_mapped": strong, "subsystems": len(subsystems),
                          "table_rows": stats.get("table_rows", 0), "spec_rows": stats.get("spec_rows", 0),
                          "modules": modules, "profile": profile.get("name") if profile else "",
                          "max_level": max([n["level"] for n in flat_nodes] or [0])},
              "region": {"title": region_title, "page_from": min(pages) if pages else None,
                         "page_to": max(pages) if pages else None},
              "evidence_index": _profile_evidence_index(items, profile)}
    return {"items": items, "outline": outline, "page_count": page_count,
            "page_source": page_source, "source": source,
            "region": report["region"], "report": report}
