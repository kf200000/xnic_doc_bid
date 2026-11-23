# -*- coding: utf-8 -*-
"""招标解析 Agent —— 多步管线引擎（真实解析版）。

管线（每一步都可观测、留痕，产出执行轨迹报告）：
  Step1 格式识别与文本抽取  PDF（PyMuPDF，含表格检测）/ Word（python-docx，段落+表格交错）/ TXT·MD 直读
  Step2 章节定位            识别"第X章 / X.Y / 评标办法"等结构，为后续分类提供上下文
  Step3 条目抽取与分类      规则引擎：关键词强度 + 结构特征（编号/★/表格分值列）+ 术语库同义词
  Step4 一致性校验          分值合计 vs 标称总分、★/废标条款计数、重复条目、空结果告警
  Step5 置信度评估          综合多信号打分，≥85% 自动确认，其余标黄转人工复核

可选 LLM 增强：Step3 的分类结果可通过 ``classify_with_llm`` 钩子替换为模型判定，
未配置模型时自动回退到本地规则引擎（离线可跑）。
"""
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None
try:
    import docx  # python-docx
except ImportError:  # pragma: no cover
    docx = None

from database import now_text

AUTO_CONFIRM_CONFIDENCE = 85
TEXT_SUFFIXES = (".txt", ".md", ".csv", ".json", ".xml")
MAX_CHARS = 400000

# ---------------- Step1 文本抽取 ----------------

def _decode_text(raw):
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def _overlap(a, b):
    """两个矩形 (x0,y0,x1,y1) 是否相交。"""
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def extract_pdf_blocks(path):
    """PDF → 块列表 [{page, text, kind}]；kind: para / heading / table。
    表格区域用 find_tables 检测，普通文本块剔除与表格重叠的部分避免重复抽取。"""
    blocks, tables_total = [], 0
    with fitz.open(path) as pdf:
        page_total = pdf.page_count
        for pno, page in enumerate(pdf, start=1):
            tabs = []
            try:
                tabs = page.find_tables()
            except Exception:
                tabs = []
            for tab in (tabs.tables if hasattr(tabs, "tables") else tabs):
                try:
                    rows = tab.extract()
                except Exception:
                    continue
                tables_total += 1
                for r in rows:
                    cells = [str(c or "").strip() for c in r if str(c or "").strip()]
                    if cells:
                        blocks.append({"page": pno, "text": " | ".join(cells), "kind": "table"})
            bboxes = [tuple(tab.bbox) for tab in (tabs.tables if hasattr(tabs, "tables") else tabs)]
            for b in page.get_text("blocks"):
                x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], (b[4] or "").strip()
                if not text or any(_overlap((x0, y0, x1, y1), box) for box in bboxes):
                    continue
                for line in text.splitlines():
                    line = line.strip()
                    if line:
                        blocks.append({"page": pno, "text": line, "kind": "para"})
    return blocks, page_total


# 老式 OLE2 .doc 不能直接被 python-docx 读取；借本机 Word/WPS 子进程转成 .docx。
DOC_CONVERTER = Path(__file__).resolve().parent / "tools" / "doc2docx.py"
DOC_CACHE_DIR = Path(__file__).resolve().parent / "data" / "doccache"


def doc_to_docx(path):
    """返回 (converted_docx_path, note)；转换失败返回 (None, 原因)。"""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError as exc:
        return None, f"无法读取源文件：{exc}"
    DOC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.md5(f"{path}|{stat.st_size}|{stat.st_mtime}".encode("utf-8")).hexdigest()[:10]
    dst = DOC_CACHE_DIR / f"{path.stem}_{digest}.docx"
    if dst.exists() and dst.stat().st_size > 1024:
        return dst, "doc→docx（缓存）"
    try:
        proc = subprocess.run([sys.executable, str(DOC_CONVERTER), str(path), str(dst)],
                              capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None, "doc→docx 转换超时（120s）"
    except OSError as exc:
        return None, f"doc→docx 转换启动失败：{exc}"
    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size < 1024:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        return None, f"doc→docx 转换失败（code={proc.returncode}）：{detail or '无可用 Word/WPS'}"
    return dst, "doc→docx（本机排版引擎）"


_CHAPTER_HEAD_RE = re.compile(r"^(第[一二三四五六七八九十百\d]+章)")


def _docx_default_size(d):
    """styles.xml docDefaults 的默认字号（磅）；取不到按 10.5。"""
    try:
        for rel in d.part.rels.values():
            if rel.reltype.endswith("/styles"):
                xml = rel.target_part.blob.decode("utf-8", "ignore")
                m = re.search(r"<w:docDefaults>.*?<w:sz w:val=\"(\d+)\"", xml, re.S)
                if m:
                    return int(m.group(1)) / 2
    except Exception:
        pass
    return 10.5


def _para_size_pt(paragraph, default_pt):
    """段落渲染字号：取 run 显式字号的最大值，无则用默认。"""
    try:
        sizes = [r.font.size.pt for r in paragraph.runs if r.font.size is not None]
        if sizes:
            return max(sizes)
    except Exception:
        pass
    return default_pt


def _layout_lines(text, size_pt):
    """按 A4 版面估算段落占用行数（每行字数 = 可用宽度 / 字宽）。"""
    import math
    chars_per_line = max(10, int(155 / (size_pt * 0.3528)))
    if not text:
        return 1
    return max(1, math.ceil(len(text) / chars_per_line))


# ---------------- Word 真实分页：借用本机排版引擎 ----------------
# Word 的 .docx 不存页码（分页由渲染决定），任何"估算"都只能逼近。
# 只要本机有 Word / WPS，就用它的排版引擎转一份 PDF，按 PDF 真实页取页码——
# 与甲方打印出来的纸质文件逐页一致；没有排版引擎时，才降级为版面估算。

PDF_CACHE_DIR = Path(__file__).parent / "data" / "pdfcache"
PDF_CACHE_TTL_DAYS = 30


def docx_to_pdf(path, timeout=240):
    """docx → PDF（真实分页），带磁盘缓存。返回 (pdf_path | None, 说明文案)。"""
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return None, "源文件不可读"
    key = f"{path.stem[:24]}_{st.st_size}_{int(st.st_mtime)}"
    key = re.sub(r"[^\w\-.]+", "_", key)[:80]
    cached = PDF_CACHE_DIR / f"{key}.pdf"
    if cached.exists() and cached.stat().st_size > 1024:
        return cached, "命中已缓存的排版结果"
    if os.name != "nt":
        return None, "非 Windows 环境，无 Office 排版引擎"
    tool = Path(__file__).parent / "tools" / "docx2pdf.py"
    if not tool.exists():
        return None, "缺少 docx2pdf 转换工具"
    tmp = cached.with_suffix(".part.pdf")
    try:
        PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if tmp.exists():
            tmp.unlink()
    except OSError:
        pass
    cmd = [sys.executable, str(tool), str(path), str(tmp)]
    creationflags = 0x08000000  # CREATE_NO_WINDOW：不弹控制台
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              creationflags=creationflags if os.name == "nt" else 0)
    except subprocess.TimeoutExpired:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None, f"排版引擎超时（>{timeout}s）"
    except OSError as exc:
        return None, f"无法启动排版引擎：{exc}"
    if proc.returncode != 0 or not tmp.exists():
        err = (proc.stderr or b"").decode("gb18030", "ignore").strip().splitlines()
        reason = err[-1][:80] if err else f"退出码 {proc.returncode}"
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None, f"排版引擎不可用（{reason}）"
    try:
        tmp.replace(cached)
    except OSError:
        return tmp, "排版完成"
    _purge_pdf_cache()
    return cached, "排版完成"


def _purge_pdf_cache():
    """清理过期的 PDF 缓存（默认 30 天），避免磁盘无限增长。"""
    try:
        cutoff = time.time() - PDF_CACHE_TTL_DAYS * 86400
        for f in PDF_CACHE_DIR.glob("*.pdf"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except OSError:
        pass


def _norm_ws(text):
    """去掉全部空白（含全角空格），用于跨 PDF/Word 的文本比对。"""
    return re.sub(r"\s+", "", text or "")


def _pdf_page_texts(pdf_path, limit=600):
    """读取 PDF 每页文本（已去空白），用于把 Word 段落映射到真实页。"""
    pages = []
    with fitz.open(str(pdf_path)) as pdf:
        for pno, page in enumerate(pdf):
            if pno >= limit:
                break
            try:
                pages.append(_norm_ws(page.get_text("text")))
            except Exception:
                pages.append("")
    return pages


def _item_key(it):
    """取用于页面比对的文本：表格取首格（PDF 里没有" | "分隔），段落整段取前缀。"""
    text = it["text"]
    if it["kind"] == "table":
        cells = it.get("cells") or [c.strip() for c in text.split("|") if c.strip()]
        # "★ | 1 | 要求正文"：首格太短无法定位页码，用最长单元格作为锚点。
        text = max(cells, key=len) if cells else text
    key = _norm_ws(text)
    return key[:32]


def _map_items_to_pages(items, page_texts):
    """单调游标 + 局部窗口：把 Word 段落顺序映射到 PDF 真实页码（1 基）。

    逐段取前缀文本，在当前游标页及后续若干页搜索；命中即前进游标。
    个别段落抽不出文本（图片、域代码、异常排版）时不后退也不跳页，
    沿用当前游标——周边段落命中会把游标带回正确位置。
    """
    total = len(page_texts)
    cursor, pages, miss = 0, [], 0
    for it in items:
        key = _item_key(it)
        hit = None
        if len(key) >= 6 and total:
            span = 3 + min(miss // 4, 24)  # 连续未命中时逐步放宽窗口，追上大段图片/表格
            for p in range(cursor, min(total, cursor + span)):
                if key in page_texts[p]:
                    hit = p
                    break
        if hit is None:
            miss += 1
        else:
            cursor, miss = hit, 0
        pages.append(cursor + 1)
    return pages


def extract_docx_blocks(path):
    """Word → 块列表（段落与表格按文档顺序交错），页码尽量取真实排版结果。

    页码策略（按可靠性排序）：
      ① 真实排版分页 —— 调本机 Word/WPS 转 PDF，用 PDF 页做页码映射（与纸质文件一致）；
      ② 目录页码锚定 —— 无排版引擎时，用文档目录"第X章 …… N"校准章首页；
      ③ 显式分页符 + 版面高度估算 —— 锚点之间按字号行高累计推进。

    返回 (blocks, page_count, page_source)：
      page_source = "typeset"（真实排版）| "estimate"（版面估算，章级对齐、页级有偏差）
    """
    d = docx.Document(path)
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    default_pt = _docx_default_size(d)

    items = []  # {text, kind, page_break, size_pt}
    for child in d.element.body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, d)
            text = para.text.strip()
            page_break = any(br.get(qn("w:type")) == "page" for br in child.iter(qn("w:br")))
            sect_break = any(el.tag == qn("w:sectPr") for el in child.iter(qn("w:sectPr")))
            items.append({"text": text, "kind": "para",
                          "page_break": page_break or sect_break,
                          "size_pt": _para_size_pt(para, default_pt)})
        elif child.tag == qn("w:tbl"):
            table = Table(child, d)
            table_index = sum(1 for x in items if x.get("kind") == "table") + 1
            for row_index, r in enumerate(table.rows, start=1):
                # 保留空单元格，后续才能按「参数性质 | 序号 | 技术参数」列位解析。
                cells = [c.text.strip() for c in r.cells]
                visible = [c for c in cells if c]
                if visible:
                    items.append({"text": " | ".join(visible), "kind": "table", "cells": cells,
                                  "table_index": table_index, "row_index": row_index,
                                  "page_break": False, "size_pt": default_pt})

    def block_payload(it):
        return {k: it[k] for k in ("cells", "table_index", "row_index") if k in it}

    # ① 真实排版分页（首选）
    if fitz is not None:
        pdf_path, _note = docx_to_pdf(path)
        if pdf_path is not None:
            try:
                page_texts = _pdf_page_texts(pdf_path)
                if page_texts:
                    pages = _map_items_to_pages(items, page_texts)
                    blocks = [{"page": pg, "text": it["text"], "kind": it["kind"],
                               **block_payload(it)} for it, pg in zip(items, pages) if it["text"]]
                    return blocks, len(page_texts), "typeset"
            except Exception:
                pass  # 落到估算

    # ②③ 降级：目录锚定 + 分页符 + 版面估算
    toc_map = {}
    for it in items[:150]:
        m = _CHAPTER_HEAD_RE.match(it["text"])
        tail = re.search(r"(\d+)\s*$", it["text"])
        if m and tail and len(it["text"]) < 80:
            toc_map.setdefault(m.group(1), int(tail.group(1)))

    PAGE_HEIGHT_PT = 696  # A4 297mm - 上下边距约 50mm
    page, height_used = 1, 0.0
    blocks = []
    for it in items:
        text = it["text"]
        if it["kind"] == "para":
            # 章标题锚定（目录条目本身以数字结尾，跳过锚定防止自引用）
            m = _CHAPTER_HEAD_RE.match(text)
            if m and m.group(1) in toc_map and not re.search(r"\d+\s*$", text):
                anchored = toc_map[m.group(1)]
                if anchored != page:
                    page = anchored
                    height_used = 0.0
        if text:
            blocks.append({"page": page, "text": text, "kind": it["kind"], **block_payload(it)})
        size_pt = it["size_pt"]
        line_pt = size_pt * 1.55
        lines = _layout_lines(text, size_pt) if it["kind"] == "para" else 1
        height_used += lines * line_pt
        if height_used >= PAGE_HEIGHT_PT:
            page += 1
            height_used = 0.0
        if it["page_break"]:
            page += 1
            height_used = 0.0
    return blocks, max((b["page"] for b in blocks), default=1), "estimate"


def extract_file_blocks(path):
    """统一入口：返回 (blocks, page_count, source_kind, page_source)。
    source_kind: pdf / docx / text / unsupported
    page_source: typeset（真实排版分页）/ estimate（版面估算）/ exact（PDF 原生页码）"""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        if fitz is None:
            return [], None, "pdf-no-lib", "none"
        blocks, pages = extract_pdf_blocks(path)
        return blocks, pages, "pdf", "exact"
    if suffix == ".doc":
        converted, note = doc_to_docx(path)
        if converted is None:
            return [], None, "doc-convert-failed", note
        blocks, pages, page_source = extract_docx_blocks(converted)
        return blocks, pages, "doc", f"{page_source}；{note}"
    if suffix == ".docx":
        if docx is None:
            return [], None, "docx-no-lib", "none"
        blocks, pages, page_source = extract_docx_blocks(path)
        return blocks, pages, "docx", page_source
    if suffix in TEXT_SUFFIXES:
        text = _decode_text(path.read_bytes()[:MAX_CHARS])
        blocks = []
        for pno, chunk in enumerate(text.split("\f"), start=1):  # 支持分页符
            for line in chunk.splitlines():
                line = line.strip()
                if line:
                    blocks.append({"page": pno, "text": line, "kind": "para"})
        return blocks, None, "text", "exact"
    return [], None, "unsupported", "none"


# ---------------- Step2 章节定位 ----------------

_CHAPTER_RE = re.compile(r"^第[一二三四五六七八九十百\d]+章")
_SECTION_RE = re.compile(r"^[一二三四五六七八九十]+[、.]")
_NUMBERED_RE = re.compile(r"^[（(]?[一二三四五六七八九十\d]{1,3}[）)]?[、.\s]\s*\S")

SECTION_HINTS = (
    ("评分办法", ("评标办法", "评分办法", "评分标准", "评分细则", "评审标准")),
    ("资格条件", ("资格审查", "资格要求", "资质要求", "投标人须知", "商务条款")),
    ("废标条款", ("废标", "否决", "无效标", "投标无效")),
    ("技术参数", ("技术规格", "技术要求", "技术标准", "供货范围", "技术参数")),
    ("份数装订", ("密封和标记", "装订", "份数", "递交", "开标")),
    ("保证金", ("保证金",)),
    ("交付期", ("交付", "交货", "工期", "进度计划")),
    ("澄清截止", ("澄清", "答疑")),
)


def section_of(text):
    for cat, keys in SECTION_HINTS:
        if any(k in text for k in keys):
            return cat
    return None


# ---------------- Step3 条目抽取与分类 ----------------

# 分类规则按优先级排列：命中即返回（废标 > ★ > 保证金 > …）。
_RULES = (
    ("废标条款", ("废标", "否决其投标", "否决投标", "按无效标处理", "视为无效投标", "不予受理"), 97),
    ("★条款", ("★", "▲", "※"), 96),
    ("保证金", ("投标保证金", "保证金"), 92),
    ("份数装订", ("正本", "副本", "份数", "密封", "装订"), 88),
    ("澄清截止", ("澄清截止", "提问截止", "开标时间", "截止时间", "答疑截止"), 92),
    ("交付期", ("交货期", "交付期", "交付时间", "工期", "合同生效后"), 90),
    ("资格条件", ("资格", "资质", "许可证", "业绩要求", "投标人须具备", "投标人应具备", "近五年", "近三年"), 86),
    ("评分办法", ("评分", "分值", "得分", "评标", "满分", "权重"), 88),
    ("技术参数", ("技术参数", "技术指标", "精度", "量程", "协议", "标准要求"), 80),
)

_SCORE_PATTERNS = (
    re.compile(r"[（(]\s*(\d+(?:\.\d+)?)\s*分\s*[）)]"),
    re.compile(r"(\d+(?:\.\d+)?)\s*分(?![钟个条页值析项段歧布担])"),
    re.compile(r"满分\s*为?\s*(\d+(?:\.\d+)?)"),
)

_TERM_HINT_KEYS = ("krt", "rms", "辐射监测", "pps", "pac", "ie级", "sl-2", "安全级")


def _score_value(text):
    # 剥离日期时间模式，防止"09 时 30 分""2026 年 11 月"被识别为分值
    t = re.sub(r"\d{1,2}\s*[时点]\s*\d{1,2}\s*分", "", text)
    t = re.sub(r"\d{4}\s*年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?", "", t)
    t = re.sub(r"\d{1,2}\s*月\s*\d{1,2}\s*日", "", t)
    # "合计 X 分"/"总分 X 分"是叙述句（校验器另行比对），不作为单项分值
    if "合计" in text or "总分" in text:
        return None
    for pat in _SCORE_PATTERNS:
        for m in pat.finditer(t):
            try:
                v = float(m.group(1))  # 支持 0.5 分等小数分值
            except ValueError:
                continue
            if 0 < v <= 100:
                return v
    return None


def _classify(text, current_section, term_hints):
    """返回 (category, score_value, confidence, signals)。"""
    signals = []
    hit_category, base = None, 0
    for category, keywords, conf in _RULES:
        hit = next((k for k in keywords if k in text), None)
        if hit:
            hit_category, base = category, conf
            signals.append(f"关键词「{hit}」")
            break
    if hit_category is None:
        return None, None, None, signals

    score_value = _score_value(text)
    if score_value is not None and hit_category == "评分办法":
        signals.append(f"分值 {score_value}")
        base = min(97, base + 4)

    if _NUMBERED_RE.match(text) or _SECTION_RE.match(text):
        signals.append("编号条款")
        base = min(97, base + 3)

    # 章节上下文：分类与所在章节一致 / 冲突
    if current_section:
        if current_section == hit_category:
            signals.append(f"章节「{current_section}」一致")
            base = min(97, base + 3)
        elif hit_category in ("评分办法", "技术参数"):
            base = max(60, base - 12)
            signals.append(f"章节「{current_section}」弱相关")

    # 术语库同义词命中增强
    if term_hints and any(t["term"] in text or any(w in text for w in t["synonyms"].split("、") if w)
                          for t in term_hints):
        signals.append("术语库命中")

    # 短行降低置信度（多为截断）
    if len(text.strip()) < 8 and hit_category not in ("★条款", "废标条款"):
        base = max(60, base - 10)

    # 无结构证据的叙述句（无编号、无分值、非★/废标强标记）：降置信转人工复核，
    # 避免公告类叙述（"欢迎符合资格条件的投标人参加投标"）被高分自动确认
    has_evidence = bool(_NUMBERED_RE.match(text) or _SECTION_RE.match(text) or score_value is not None
                        or hit_category in ("★条款", "废标条款"))
    if not has_evidence:
        base = max(58, base - 12)
        signals.append("叙述句（弱证据）转人工复核")

    return hit_category, score_value, base, signals


def _title_of(text):
    t = re.sub(r"[\n\r]+", " ", text)
    t = re.sub(r"\s*、\s*", "、", t)
    t = re.sub(r"^[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]\s*", "", t.strip())
    t = re.sub(r"^[★▲※\s\d\.\、）)（(]+", "", t)
    t = re.sub(r"[、\s]+$", "", t)
    return (t[:40] or text[:40]).strip()


def classify_with_llm(blocks, term_hints):
    """可选 LLM 增强钩子：接入模型后按块判定类别，替换本地规则。
    返回 None 表示未配置模型，回退本地规则引擎。"""
    return None


# ---------------- Step4 一致性校验 ----------------

def _fmt_score(v):
    """分值显示：整数去掉 .0，小数最多保留两位。"""
    f = float(v)
    return int(f) if f.is_integer() else round(f, 2)


def _validate(extracts, declared_total):
    checks = []
    score_items = [e for e in extracts if e["category"] == "评分办法" and e["score_value"] is not None]
    total = round(sum(e["score_value"] for e in score_items), 2)
    if declared_total is not None and score_items:
        ok = abs(total - float(declared_total)) < 0.01
        checks.append({"name": "评分分值合计", "ok": ok,
                       "detail": f"抽取合计 {_fmt_score(total)} 分 / 标称 {_fmt_score(declared_total)} 分" + ("" if ok else "（请核对是否漏抽评分项）")})
    elif score_items:
        checks.append({"name": "评分分值合计", "ok": True, "detail": f"抽取合计 {_fmt_score(total)} 分（未登记标称总分，跳过比对）"})
    star = sum(1 for e in extracts if e["category"] == "★条款")
    checks.append({"name": "★条款识别", "ok": star > 0, "detail": f"识别 {star} 条★条款" + ("（未发现，请人工核查）" if star == 0 else "")})
    reject = sum(1 for e in extracts if e["category"] == "废标条款")
    checks.append({"name": "废标条款识别", "ok": reject > 0, "detail": f"识别 {reject} 条废标/否决条款"})
    seen, dup = {}, 0
    for e in extracts:
        key = re.sub(r"[\s★、。，；：（）()0-9A-Za-z]", "", e["content"])[:80]
        if len(key) > 20:
            if key in seen:
                dup += 1
            seen[key] = 1
    if dup:
        checks.append({"name": "重复条目", "ok": False, "detail": f"检出 {dup} 条疑似重复（跨页表格或分栏所致），请人工去重"})
    return checks


# ---------------- Agent 主入口 ----------------

def run_tender_agent(path, declared_total=None, term_hints=None):
    """执行完整解析管线，返回 {extracts, page_count, source, report}。
    report 为分步执行轨迹，供前端渲染 Agent 工作过程。"""
    t0 = time.perf_counter()
    steps = []

    # Step1 抽取
    t = time.perf_counter()
    blocks, page_count, source, page_source = extract_file_blocks(path)
    kind_label = {"pdf": "PDF 版面解析", "docx": "Word 结构解析", "doc": "老式 .doc（已转 docx）解析",
                  "text": "文本直读", "pdf-no-lib": "PDF（缺少 PyMuPDF）",
                  "docx-no-lib": "Word（缺少 python-docx）",
                  "doc-convert-failed": ".doc 转换失败（需本机 Word/WPS）",
                  "unsupported": "不支持的格式"}.get(source, source)
    tables = sum(1 for b in blocks if b["kind"] == "table")
    detail1 = f"{kind_label}：{len(blocks)} 个内容块（含表格 {tables} 个）"
    if source in ("pdf-no-lib", "docx-no-lib", "doc-convert-failed", "unsupported"):
        steps.append({"step": 1, "name": "格式识别与文本抽取", "status": "警告", "detail": detail1, "ms": 0})
        report = {"agent": "招标解析 Agent v2", "ran_at": now_text(), "steps": steps,
                  "summary": {"blocks": 0, "tables": 0, "sections": 0, "extracts": 0},
                  "fallback": "格式不可解析，转演示档案"}
        return {"extracts": [], "page_count": page_count, "source": source,
                "page_source": page_source, "report": report}
    text_chars = sum(len(b["text"]) for b in blocks)
    if text_chars < 40:
        steps.append({"step": 1, "name": "格式识别与文本抽取", "status": "警告",
                      "detail": f"{detail1}；文本层仅 {text_chars} 字符，疑似扫描件（无文本层），建议提供可搜索 PDF 或走 OCR",
                      "ms": int((time.perf_counter() - t) * 1000)})
        return {"extracts": [], "page_count": page_count, "source": source,
                "page_source": page_source,
                "report": {"agent": "招标解析 Agent v2", "ran_at": now_text(), "steps": steps,
                           "summary": {"blocks": len(blocks), "tables": tables, "sections": 0, "extracts": 0},
                           "fallback": "扫描件无文本层"}}
    page_note = f"，{page_count} 页" if page_count else ""
    if source in ("docx", "doc"):
        if str(page_source).startswith("typeset"):
            page_note = f"，{page_count} 页（页码取自 Word 排版引擎真实分页，与纸质文件一致）"
        else:
            page_note = (f"，约 {page_count} 页（未检测到 Office 排版引擎，页码按目录锚定 + "
                         f"分页符 + 版面估算，章级准确、页级有偏差）")
    steps.append({"step": 1, "name": "格式识别与文本抽取", "status": "完成",
                  "detail": f"{detail1}，文本 {text_chars} 字符{page_note}",
                  "ms": int((time.perf_counter() - t) * 1000)})

    # Step2 章节定位
    t = time.perf_counter()
    section_names, current = [], None
    for b in blocks:
        text = b["text"]
        if _CHAPTER_RE.match(text) or (_SECTION_RE.match(text) and len(text) < 30):
            current = text[:24]
            section_names.append(current)
            b["kind"] = "heading"
            b["section"] = None
            continue
        b["section"] = section_of(text) or (section_of(current) if current else None)
    steps.append({"step": 2, "name": "章节定位", "status": "完成",
                  "detail": f"识别 {len(section_names)} 个章节标题" + (f"：{' / '.join(section_names[:6])}" if section_names else ""),
                  "ms": int((time.perf_counter() - t) * 1000)})

    # Step3 分类抽取（可插拔 LLM：classify_with_llm 未配置时走本地规则）
    t = time.perf_counter()
    llm_result = classify_with_llm(blocks, term_hints)
    extracts, table_rows = [], 0
    seen_keys = set()
    score_table_mode = False  # 评分表表头命中后开启，后续表格行按评分项处理
    header_cols = None        # 表头列名（用于判定数字单元格是否分值列）
    for idx, b in enumerate(blocks, start=1):
        text = b["text"]
        if b["kind"] != "table":
            # 表格中断（出现段落/标题）→ 清除表头上下文，防止跨表残留
            header_cols = None
            score_table_mode = False
        if b["kind"] == "heading":
            continue  # 章节标题行本身不作为抽取条目
        if b["kind"] == "table":
            cells = [c.strip() for c in text.split("|") if c.strip()]
            flat = re.sub(r"[\n\r]+", " ", text)
            # 合计行不作为评分项（作为校验器比对依据）
            if "合计" in text:
                continue
            # 表头识别：含列名词且无数字 → 记录列名；评分类列名开启评分表模式
            if any(k in text for k in ("分值", "评分标准", "评审因素", "评审标准", "满分", "得分",
                                       "评分项", "序号", "条款号", "条款名称")) and not re.search(r"\d", text):
                header_cols = cells
                score_table_mode = any(k in text for k in ("分值", "评分标准", "满分", "得分", "评分项"))
                continue
            # 分值构成行拆条："商务部分：10分 / 技术部分：50分 / 投标报价：40分"多行单元格 → 逐项展开
            comp = [(n, v) for n, v in
                    re.findall(r"([\u4e00-\u9fa5A-Za-z（）()]{2,12})[：:]\s*(\d+(?:\.\d+)?)\s*分", text)
                    if "总分" not in n and "合计" not in n]
            if len(comp) >= 2:
                clause = next((re.sub(r"\s+", "", c) for c in cells
                               if not re.fullmatch(r"[\d.、\s]+", c) and 3 <= len(c) <= 20), cells[0])
                for name, v in comp:
                    try:
                        v = float(v)  # 小数分值（如 0.5）同样支持
                    except ValueError:
                        continue
                    if not 0 < v <= 100:
                        continue
                    key = re.sub(r"\s", "", f"{clause}{name}")[:60]
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    extracts.append({
                        "category": "评分办法", "title": f"{clause}·{name}",
                        "content": f"{clause}：{name} {_fmt_score(v)} 分", "page": b["page"],
                        "para": f"评标办法表 第 {table_rows + 1} 行（分值构成）", "score_value": v,
                        "confidence": 92, "status": "已确认", "signals": ["分值构成行拆条"],
                    })
                    table_rows += 1
                continue
            # 分值列判定：数字单元格的列头是分值类才认（防止"条款号/序号"列数字误判为分值）
            sv = None
            nums_idx = [(i, c) for i, c in enumerate(cells) if re.fullmatch(r"\d{1,3}(?:\.\d{1,2})?", c)]
            if header_cols:
                for i, c in nums_idx:
                    if i < len(header_cols) and any(k in header_cols[i] for k in ("分值", "满分", "权重", "得分", "评分")):
                        sv = float(c)
                        break
            elif score_table_mode and nums_idx:
                sv = float(nums_idx[-1][1])
            if sv is None:
                sv = _score_value(flat)
            if sv is not None and len(flat) > 6:
                row_no = table_rows + 1
                key = re.sub(r"\s", "", flat)[:60]
                if key not in seen_keys:
                    seen_keys.add(key)
                    table_rows += 1
                    title_cell = next((c for c in cells
                                       if not re.fullmatch(r"[\d.、\s]+", c) and len(c) >= 3), "")
                    extracts.append({
                        "category": "评分办法",
                        "title": _title_of(title_cell) or "评分项（表格行）",
                        "content": flat.replace(" | ", "；")[:300], "page": b["page"],
                        "para": f"评标办法表 第 {row_no} 行", "score_value": sv,
                        "confidence": 90, "status": "已确认",
                        "signals": ["表格分值列" if (header_cols or score_table_mode) else "表格含分值"],
                    })
            continue
        # 评分行优先识别：行首"（X分）"前缀，或位于评标办法章节且含分值 ——
        # 防止"（30分）技术参数响应"被"技术参数"关键词抢走、"（25分）类似业绩"漏抽
        score_lead = re.match(r"^[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]", text)
        if score_lead or (b.get("section") == "评分办法" and _score_value(text) is not None):
            sv = _score_value(text)
            conf = 93 if score_lead else 90
            signals = ["分值前缀" if score_lead else f"评标办法章节含分值 {sv}"]
            if _NUMBERED_RE.match(text):
                conf = min(97, conf + 3)
                signals.append("编号条款")
            key = re.sub(r"[\s★、。，；：（）()0-9A-Za-z]", "", text)[:60]
            if not (len(key) > 12 and key in seen_keys):
                seen_keys.add(key)
                extracts.append({
                    "category": "评分办法", "title": _title_of(text), "content": text[:300],
                    "page": b["page"], "para": f"第 {b['page']} 页 第 {idx} 块（评标办法章节）",
                    "score_value": sv, "confidence": min(97, conf),
                    "status": "已确认", "signals": signals,
                })
            continue
        category, sv, conf, signals = _classify(text, b.get("section"), term_hints)
        if not category:
            continue
        if len(text.strip()) < 6 and category not in ("★条款", "废标条款"):
            continue
        key = re.sub(r"[\s★、。，；：（）()0-9A-Za-z]", "", text)[:60]
        if len(key) > 12 and key in seen_keys:
            continue
        seen_keys.add(key)
        extracts.append({
            "category": category, "title": _title_of(text), "content": text[:300],
            "page": b["page"],
            "para": f"第 {b['page']} 页 第 {idx} 块" + (f"（{b['section']}章节）" if b.get("section") else ""),
            "score_value": sv, "confidence": conf,
            "status": "已确认" if conf >= AUTO_CONFIRM_CONFIDENCE else "待确认",
            "signals": signals,
        })
    engine = "本地规则引擎（关键词强度+结构特征+章节上下文）"
    if llm_result is not None:
        engine = "LLM 增强分类 + 本地规则兜底"
    steps.append({"step": 3, "name": "条目抽取与分类", "status": "完成",
                  "detail": f"{engine}：抽取 {len(extracts)} 条（表格行 {table_rows} 条），" 
                            f"置信度不足 {AUTO_CONFIRM_CONFIDENCE}% 待人工确认 {sum(1 for e in extracts if e['status'] == '待确认')} 条",
                  "ms": int((time.perf_counter() - t) * 1000)})

    # Step4 一致性校验
    t = time.perf_counter()
    checks = _validate(extracts, declared_total)
    failed = [c for c in checks if not c["ok"]]
    steps.append({"step": 4, "name": "一致性校验", "status": "警告" if failed else "完成",
                  "detail": "；".join(f"{c['name']}：{c['detail']}" for c in checks) or "无校验项",
                  "ms": int((time.perf_counter() - t) * 1000),
                  "checks": checks})

    # Step5 置信度评估
    t = time.perf_counter()
    high = sum(1 for e in extracts if e["status"] == "已确认")
    steps.append({"step": 5, "name": "置信度评估与人工复核分发", "status": "完成",
                  "detail": f"高置信自动确认 {high} 条，标黄转人工复核 {len(extracts) - high} 条",
                  "ms": int((time.perf_counter() - t) * 1000)})

    report = {
        "agent": "招标解析 Agent v2", "ran_at": now_text(), "steps": steps,
        "summary": {"blocks": len(blocks), "tables": tables, "sections": len(section_names),
                    "extracts": len(extracts), "total_ms": int((time.perf_counter() - t0) * 1000)},
    }
    return {"extracts": extracts, "page_count": page_count, "source": source,
            "page_source": page_source, "report": report}


# ---------------- 文档库复用：纯文本抽取 ----------------

def extract_document_text(raw, suffix):
    """供文档库全文检索复用：PDF/DOCX → 纯文本（截断 200k 字符）。"""
    import os
    import tempfile
    if suffix == ".pdf" and fitz is not None:
        try:
            with fitz.open(stream=raw, filetype="pdf") as pdf:
                return "\n".join(page.get_text("text") for page in pdf)[:200000]
        except Exception:
            return ""
    if suffix == ".docx" and docx is not None:
        import tempfile
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
                f.write(raw)
                tmp = f.name
            d = docx.Document(tmp)
            parts = [p.text for p in d.paragraphs]
            for table in d.tables:
                for r in table.rows:
                    parts.append("；".join(c.text for c in r.cells))
            return "\n".join(p for p in parts if p.strip())[:200000]
        except Exception:
            return ""
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    return ""
