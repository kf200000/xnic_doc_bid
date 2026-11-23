import os
import re
import shutil
import sqlite3
import uuid
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = Path(os.environ.get("XNIC_DB_PATH", str(DATA_DIR / "xnic.db")))
UPLOAD_DIR = Path(os.environ.get("XNIC_UPLOAD_DIR", str(BASE_DIR / "uploads")))
DOC_UPLOAD_ROOT = "documents"
UNCATEGORIZED_FOLDER = "未分类"


def safe_upload_category(category):
    """把分类名转换为安全的目录名，兼容 Windows 非法字符。"""
    name = str(category or "").strip()
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name or UNCATEGORIZED_FOLDER


def new_document_upload_relpath(category, original_filename=""):
    """返回文档库附件在 UPLOAD_DIR 下的相对路径（POSIX 风格）。"""
    suffix = Path(str(original_filename or "")).suffix[:12]
    rel = Path(DOC_UPLOAD_ROOT) / safe_upload_category(category) / f"{uuid.uuid4().hex}{suffix}"
    return rel.as_posix()


def resolve_upload_path(stored_path):
    """解析附件相对路径；兼容旧扁平文件名，拒绝越出 UPLOAD_DIR。"""
    if not stored_path:
        return None
    try:
        candidate = (UPLOAD_DIR / str(stored_path)).resolve()
    except (OSError, RuntimeError):
        return None
    if UPLOAD_DIR.resolve() not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    security_level TEXT NOT NULL DEFAULT '内部',
    department TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    version TEXT NOT NULL DEFAULT 'V1.0',
    updated_at TEXT NOT NULL,
    expiry_date TEXT NOT NULL DEFAULT '长期',
    reference_count INTEGER NOT NULL DEFAULT 0,
    original_filename TEXT,
    stored_path TEXT,
    has_pac INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS certificates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    certificate_no TEXT NOT NULL,
    issuer TEXT NOT NULL,
    expiry_date TEXT NOT NULL,
    owner TEXT NOT NULL,
    scan_complete INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    owner TEXT NOT NULL,
    reactor_type TEXT NOT NULL,
    due_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '资料收集',
    progress INTEGER NOT NULL DEFAULT 0,
    page_count INTEGER NOT NULL DEFAULT 0,
    signature_complete INTEGER NOT NULL DEFAULT 0,
    deviation_total INTEGER NOT NULL DEFAULT 0,
    deviation_responded INTEGER NOT NULL DEFAULT 0,
    baseline_card TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bid_id INTEGER NOT NULL REFERENCES bids(id) ON DELETE CASCADE,
    parent_id INTEGER REFERENCES sections(id) ON DELETE CASCADE,
    level INTEGER NOT NULL DEFAULT 1,
    code TEXT NOT NULL,
    title TEXT NOT NULL,
    group_name TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '未开始',
    sort_order INTEGER NOT NULL,
    ai_generated INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    UNIQUE(bid_id, group_name, code)
);
CREATE TABLE IF NOT EXISTS scoring (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bid_id INTEGER NOT NULL REFERENCES bids(id) ON DELETE CASCADE,
    item TEXT NOT NULL,
    score INTEGER NOT NULL,
    section_code TEXT NOT NULL,
    sort_order INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bid_id INTEGER NOT NULL REFERENCES bids(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    similarity INTEGER NOT NULL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS compliance_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bid_id INTEGER NOT NULL REFERENCES bids(id) ON DELETE CASCADE,
    score INTEGER NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '受控',
    stored_path TEXT,
    original_filename TEXT,
    sha256 TEXT,
    file_size INTEGER NOT NULL DEFAULT 0,
    change_note TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(doc_id, version)
);
CREATE TABLE IF NOT EXISTS document_refs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    bid_id INTEGER,
    section_id INTEGER,
    section_code TEXT NOT NULL DEFAULT '',
    user TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS security_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    from_level TEXT NOT NULL DEFAULT '',
    to_level TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    applicant TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '待审批',
    approver TEXT,
    decided_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sections_bid ON sections(bid_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_scoring_bid ON scoring(bid_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category);
CREATE INDEX IF NOT EXISTS idx_docver_doc ON document_versions(doc_id, id);
CREATE INDEX IF NOT EXISTS idx_docrefs_doc ON document_refs(doc_id, id);
CREATE TABLE IF NOT EXISTS tender_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bid_id INTEGER NOT NULL REFERENCES bids(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT '招标文件',
    filename TEXT NOT NULL,
    page_count INTEGER NOT NULL DEFAULT 0,
    declared_total INTEGER,
    status TEXT NOT NULL DEFAULT '已解析',
    version_label TEXT NOT NULL DEFAULT '基准版',
    parent_id INTEGER,
    stored_path TEXT,
    uploaded_by TEXT NOT NULL DEFAULT '',
    uploaded_at TEXT NOT NULL,
    locked_at TEXT
);
CREATE TABLE IF NOT EXISTS tender_extracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL REFERENCES tender_files(id) ON DELETE CASCADE,
    bid_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    page INTEGER,
    para TEXT NOT NULL DEFAULT '',
    score_value INTEGER,
    confidence INTEGER NOT NULL DEFAULT 90,
    status TEXT NOT NULL DEFAULT '待确认',
    change_kind TEXT,
    confirmed_by TEXT,
    confirmed_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS term_dict (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    synonyms TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE(term)
);
CREATE TABLE IF NOT EXISTS tech_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id INTEGER NOT NULL UNIQUE REFERENCES tender_files(id) ON DELETE CASCADE,
    bid_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT '已解析',
    item_count INTEGER NOT NULL DEFAULT 0,
    req_count INTEGER NOT NULL DEFAULT 0,
    must_count INTEGER NOT NULL DEFAULT 0,
    outline_count INTEGER NOT NULL DEFAULT 0,
    page_from INTEGER,
    page_to INTEGER,
    page_source TEXT NOT NULL DEFAULT '',
    region_title TEXT NOT NULL DEFAULT '',
    report_json TEXT,
    applied_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tech_outline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spec_id INTEGER NOT NULL REFERENCES tech_specs(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    code TEXT NOT NULL DEFAULT '',
    level INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL,
    brief TEXT NOT NULL DEFAULT '',
    categories TEXT NOT NULL DEFAULT '',
    req_count INTEGER NOT NULL DEFAULT 0,
    must_count INTEGER NOT NULL DEFAULT 0,
    child_req_count INTEGER NOT NULL DEFAULT 0,
    child_must_count INTEGER NOT NULL DEFAULT 0,
    page_from INTEGER,
    page_to INTEGER,
    requirements TEXT NOT NULL DEFAULT '[]',
    section_id INTEGER,
    mapping_kind TEXT NOT NULL DEFAULT 'new',
    mapping_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tech_outline_spec ON tech_outline(spec_id, seq);
CREATE TABLE IF NOT EXISTS tech_audit_marks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bid_id INTEGER NOT NULL REFERENCES bids(id) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    verdict TEXT NOT NULL DEFAULT 'confirmed',
    note TEXT NOT NULL DEFAULT '',
    user TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(bid_id, item_key)
);
CREATE INDEX IF NOT EXISTS idx_tech_audit_bid ON tech_audit_marks(bid_id);
CREATE TABLE IF NOT EXISTS compose_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bid_id INTEGER NOT NULL REFERENCES bids(id) ON DELETE CASCADE,
    group_name TEXT NOT NULL DEFAULT '技术标',
    status TEXT NOT NULL DEFAULT 'running',
    total INTEGER NOT NULL DEFAULT 0,
    done_n INTEGER NOT NULL DEFAULT 0,
    failed_n INTEGER NOT NULL DEFAULT 0,
    skipped_n INTEGER NOT NULL DEFAULT 0,
    current_code TEXT,
    current_title TEXT,
    model TEXT,
    tokens_prompt INTEGER NOT NULL DEFAULT 0,
    tokens_completion INTEGER NOT NULL DEFAULT 0,
    llm_calls INTEGER NOT NULL DEFAULT 0,
    retries INTEGER NOT NULL DEFAULT 0,
    rewrites INTEGER NOT NULL DEFAULT 0,
    instructions TEXT NOT NULL DEFAULT '',
    overwrite INTEGER NOT NULL DEFAULT 0,
    section_ids TEXT NOT NULL DEFAULT '',
    error TEXT,
    result TEXT NOT NULL DEFAULT '[]',
    audit_score INTEGER,
    audit_verdict TEXT,
    audit_open TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_compose_jobs_bid ON compose_jobs(bid_id, id);
CREATE INDEX IF NOT EXISTS idx_tender_files_bid ON tender_files(bid_id, id);
CREATE INDEX IF NOT EXISTS idx_tender_extracts_file ON tender_extracts(file_id, category);
CREATE INDEX IF NOT EXISTS idx_secreq_doc ON security_requests(doc_id, status);
"""

DOC_COLUMNS = {
    "product_line": "TEXT NOT NULL DEFAULT ''",
    "reactor_tags": "TEXT NOT NULL DEFAULT ''",
    "sha256": "TEXT",
    "file_size": "INTEGER NOT NULL DEFAULT 0",
    "mime_type": "TEXT NOT NULL DEFAULT ''",
    "version_status": "TEXT NOT NULL DEFAULT '受控'",
    "status": "TEXT NOT NULL DEFAULT '正常'",
    "last_access_at": "TEXT",
    "access_count": "INTEGER NOT NULL DEFAULT 0",
    "content_text": "TEXT NOT NULL DEFAULT ''",
    "remark": "TEXT NOT NULL DEFAULT ''",
    "deleted_at": "TEXT",
    "deleted_by": "TEXT NOT NULL DEFAULT ''",
    "delete_reason": "TEXT NOT NULL DEFAULT ''",
}

DEFAULT_SECTIONS = [
    ("1.1", "投标函", "商务标"),
    ("1.2", "资质证明文件", "商务标"),
    ("1.3", "同类项目业绩", "商务标"),
    ("2.1", "评分标准索引表", "技术标"),
    ("2.2", "技术条款偏离表", "技术标"),
    ("2.3", "系统总体方案", "技术标"),
    ("2.4", "项目实施与进度计划", "技术标"),
    ("2.5", "售后服务与备件保障", "技术标"),
    ("3.1", "报价汇总表", "报价标"),
]

DEFAULT_SECTIONS_old = [
    ("1.1", "投标函", "商务标"),
    ("1.2", "资质证明文件", "商务标"),
    ("1.3", "同类项目业绩", "商务标"),
    ("2.1", "系统总体方案", "技术标"),
    ("2.2", "辐射监测系统（KRT）设计方案", "技术标"),
    ("2.3", "质量保证与核安全文化", "技术标"),
    ("2.4", "项目实施与进度计划", "技术标"),
    ("2.5", "售后服务与备件保障", "技术标"),
    ("3.1", "报价汇总表", "报价标"),
]

DEFAULT_SCORING = [
    ("投标报价", 25, "3.1"),
    ("技术方案完整性", 15, "2.1"),
    ("辐射监测系统设计方案", 14, "2.2"),
    ("同类项目业绩", 10, "1.3"),
    ("质量保证与核安全文化", 10, "2.3"),
    ("项目实施与进度计划", 9, "2.4"),
    ("售后服务与备件保障", 8, "2.5"),
    ("国产化率与自主可控", 5, "2.2"),
    ("资质与财务状况", 4, "1.2"),
]

SEED_DOCUMENTS = [
    ("民用核安全设备设计许可证（机械设备 / 电气）", "资质证照", "内部", "质量管理部", "王工", "V2", "2026-03-12", "2027-06-30", 38, 1),
    ("民用核安全设备制造许可证", "资质证照", "内部", "质量管理部", "王工", "V3", "2026-05-08", "2027-06-30", 41, 1),
    ("消防产品强制性认证证书（3C）· 火灾报警控制器", "资质证照", "内部", "质量管理部", "李工", "V5", "2026-01-20", "2026-12-31", 22, 1),
    ("华龙一号福清5号机组 KRT 辐射监测系统 出厂验收报告", "业绩案例", "内部", "核仪器事业部", "赵工", "V1", "2025-11-23", "长期", 17, 1),
    ("田湾5号机组 KRT 辐射监测系统 合同及 PAC 证书", "业绩案例", "内部", "经营计划部", "钱工", "V1", "2025-09-15", "长期", 14, 0),
    ("三门3号机组 CAP1000 RMS 联合供货业绩说明", "业绩案例", "内部", "核仪器事业部", "孙工", "V2", "2026-06-02", "长期", 11, 1),
    ("巴基斯坦恰希玛 P300 辐射监测系统 业绩证明", "业绩案例", "内部", "国际业务部", "周工", "V1", "2024-12-10", "长期", 6, 1),
    ("KRT 辐射监测系统技术方案（华龙一号堆型）", "技术方案", "内部", "核仪器事业部", "赵工", "V3.2", "2026-08-19", "长期", 52, 1),
    ("核电站专用火灾报警与联动控制系统技术方案", "技术方案", "内部", "智慧消防事业部", "李工", "V4.1", "2026-07-30", "长期", 33, 1),
    ("实物保护系统（PPS）标准方案与设备清单", "技术方案", "秘密", "安防事业部", "吴工", "V2.0", "2026-04-11", "长期", 9, 1),
    ("2026 年度投标文件范本 · 商务标", "投标文件", "内部", "经营计划部", "张工", "2026版", "2026-01-05", "2026-12-31", 45, 1),
    ("HAF003 核电厂质量保证安全规定（现行版）", "标准规范", "公开", "质量管理部", "王工", "现行", "2023-06-01", "长期", 28, 1),
    ("辐射监测系统抗震计算书（华龙一号）", "图纸与计算书", "秘密", "核仪器事业部", "赵工", "V1.3", "2026-02-17", "长期", 4, 1),
]

SEED_CERTIFICATES = [
    ("民用核安全设备设计许可证", "国核安证字 S-2xxx", "国家核安全局", "2027-06-30", "质量管理部"),
    ("民用核安全设备制造许可证", "国核安证字 Z-1xxx", "国家核安全局", "2027-06-30", "质量管理部"),
    ("辐射安全许可证", "陕环辐证 0xxx", "陕西省生态环境厅", "2026-12-31", "安全环保部"),
    ("消防产品 3C 认证证书", "CCCF-2xxx", "消防产品合格评定中心", "2026-12-31", "质量管理部"),
    ("医疗器械生产企业许可证", "陕药监械生产许 2xxx", "陕西省药品监督管理局", "2028-03-15", "核医学事业部"),
    ("建筑智能化工程设计与施工一级", "D261xxxxxx", "陕西省住房和城乡建设厅", "2027-11-20", "安防事业部"),
    ("制造计量器具许可证", "陕制 0xxx", "陕西省市场监督管理局", "2027-08-01", "质量管理部"),
    ("高新技术企业证书", "GR2xxx", "陕西省科学技术厅", "2027-12-05", "科技管理部"),
]

SEED_BIDS = [
    ("漳州核电 3、4 号机组 辐射监测系统（KRT）", "中核工程", "华龙一号", "2026-11-18", "编制中", 68, 386, 0, 86, 83),
    ("徐大堡 1、2 号机组 火灾报警及消防联动系统", "中核工程", "VVER", "2026-11-25", "资料收集", 25, 0, 0, 0, 0),
    ("某核设施实物保护系统改造（二期）", "中核四〇四", "核设施", "2026-12-05", "待决策", 8, 0, 0, 0, 0),
    ("阿尔及利亚核技术应用项目 通信/火警/实物保护设备", "中核工程（国际）", "出口项目", "2026-12-12", "编制中", 42, 0, 0, 0, 0),
    ("昌江小堆 辐射监测与核应急系统", "海南核电", "小堆", "2026-10-30", "已提交", 100, 0, 1, 0, 0),
    ("田湾 7、8 号机组 KRT 辐射监测系统", "江苏核电", "VVER", "2026-09-20", "已中标", 100, 0, 1, 0, 0),
]

SEED_CONTENT = {
    "1.1": "致：中国核电工程有限公司\n\n我方已仔细研究漳州核电 3、4 号机组辐射监测系统（KRT）招标文件的全部内容，愿意以人民币（大写）【待报价审批后填入】元的总价，按合同约定完成本项目全部工作。\n\n我方承诺投标有效期为开标之日起 90 日历天，并保证在收到中标通知书后 30 日内与贵方签订合同。",
    "1.2": "本节附具以下资质证明文件扫描件（系统已自动核验有效期）：\n\n① 民用核安全设备设计许可证\n② 民用核安全设备制造许可证\n③ 辐射安全许可证\n④ 消防产品 3C 认证\n⑤ 医疗器械生产企业许可证\n⑥ 建筑智能化工程设计与施工一级资质\n\n系统提示：消防 3C 认证将于 2026-12-31 到期，请确认续证受理单扫描件已上传。",
    "1.3": "近五年承担核电站辐射监测系统项目 12 项，覆盖华龙一号、CAP1000、VVER、快堆等堆型。代表性业绩如下：\n\n· 福清 5、6 号机组 KRT 系统（华龙一号首堆，120 余台套，已通过出厂验收并投运）\n· 田湾 5、6 号机组 KRT 系统（136 台套，分批出厂验收）\n· 三门 3、4 号机组 RMS 系统（CAP1000 全国产化首套，与国核自仪联合供货）",
    "2.1": "本方案针对漳州核电 3、4 号机组（华龙一号堆型）的辐射监测需求，按照“独立监测、集中显示、分级报警”的原则构建 KRT 系统，满足 HAF003 及招标技术规格书的全部要求。\n\n系统采用成熟平台 + 项目化配置的设计思路，硬件平台沿用福清 5、6 号机组验证过的国产化架构，软件按本项目测点表重新组态，兼顾可靠性与交付周期。",
    "2.2": "2.2.1 系统组成\nKRT 系统由区域辐射监测、工艺辐射监测、排出流监测、辐射报警与集中显示四个子系统构成，共设监测通道 118 路，其中安全级通道 36 路，均按 IE 级要求设计与鉴定。\n\n2.2.2 探测器选型与布置\n区域监测采用国产化 GM 计数管与电离室组合方案，量程覆盖 1μSv/h—10Sv/h；工艺监测按介质与温度压力条件选用 NaI 探测器与闪烁体探测器。现场安装调试由西安中核核仪器股份有限公司负责，中核西仪提供全过程技术支持。探测器布置遵循“可达性、代表性、可维护性”三原则，避开高剂量热点与检修通道。\n\n2.2.3 抗震与鉴定要求\n安全级设备按 SL-2 地震工况进行抗震计算与试验鉴定，机柜采用后扩底锚栓固定，抗震计算书见附件。非安全级设备按常规工业标准设计，但安装方式与安全级保持一致，便于后续改造升级。",
    "2.3": "项目执行公司依据 HAF003 建立的质量保证体系，设置独立的质量控制点（H/W 点），关键工序实施见证与停工待检。\n\n公司坚持“安全第一、质量第一”的核安全文化，项目组全员完成核安全文化培训并考核合格；对不符合项实行分级管理与闭环跟踪，确保不带病交付。",
    "2.4": "合同生效后 12 个月内完成全部设备交付，18 个月内完成现场安装调试。关键里程碑：设计冻结（T+2 月）、长周期物项下单（T+1 月）、出厂验收（T+10 月）、现场开通（T+16 月）。",
    "2.5": "",
    "3.1": "本节由报价模块生成，需经价格库取数与审批后填入。\n\n关联交易定价提示：本项目业主为中核集团下属单位，报价需引用“集团内基准价版本 R2026-03”，改价须经经营计划部与财务联合审批并留痕。",
}

SEED_STATUS = {
    "1.1": "已完成", "1.2": "已完成", "1.3": "编制中", "2.1": "已完成",
    "2.2": "编制中", "2.3": "已完成", "2.4": "已完成", "2.5": "未开始", "3.1": "未开始",
}


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def create_default_sections(conn, bid_id, with_seed_content=False):
    for order, (code, title, group_name) in enumerate(DEFAULT_SECTIONS, start=1):
        content = SEED_CONTENT.get(code, "") if with_seed_content else ""
        status = SEED_STATUS.get(code, "未开始") if with_seed_content else ("编制中" if content else "未开始")
        conn.execute(
            "INSERT INTO sections(bid_id,parent_id,level,code,title,group_name,content,status,sort_order,updated_at) VALUES(?,NULL,1,?,?,?,?,?,?,?)",
            (bid_id, code, title, group_name, content, status, order, now_text()),
        )
    for order, (item, score, section_code) in enumerate(DEFAULT_SCORING, start=1):
        conn.execute(
            "INSERT INTO scoring(bid_id,item,score,section_code,sort_order) VALUES(?,?,?,?,?)",
            (bid_id, item, score, section_code, order),
        )


def seed(conn):
    if conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0:
        for row in SEED_DOCUMENTS:
            conn.execute(
                """INSERT INTO documents(name,category,security_level,department,owner,version,updated_at,expiry_date,reference_count,has_pac,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (*row, now_text()),
            )
    if conn.execute("SELECT COUNT(*) FROM certificates").fetchone()[0] == 0:
        for row in SEED_CERTIFICATES:
            conn.execute(
                "INSERT INTO certificates(name,certificate_no,issuer,expiry_date,owner,created_at) VALUES(?,?,?,?,?,?)",
                (*row, now_text()),
            )
    if conn.execute("SELECT COUNT(*) FROM bids").fetchone()[0] == 0:
        for row in SEED_BIDS:
            cur = conn.execute(
                """INSERT INTO bids(name,owner,reactor_type,due_date,status,progress,page_count,signature_complete,deviation_total,deviation_responded,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (*row, now_text(), now_text()),
            )
            bid_id = cur.lastrowid
            create_default_sections(conn, bid_id, with_seed_content=(bid_id == 1))
        materials = [
            (1, "KRT 辐射监测系统技术方案（华龙一号）V3.2", 86, "2026-08-19 · 核仪器事业部"),
            (1, "漳州 1、2 号机组 KRT 技术应答表", 82, "2026-03-04 · 核仪器事业部"),
            (1, "福清 5、6 号机组出厂验收报告", 74, "2025-11-23 · 经营计划部"),
        ]
        conn.executemany(
            "INSERT INTO materials(bid_id,name,similarity,source,updated_at) VALUES(?,?,?,?,?)",
            [(*m, now_text()) for m in materials],
        )
    conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('historical_bids','62')")
    conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('historical_wins','38')")
    conn.commit()


def _infer_meta(name):
    """按文档名称推断产品线与适用堆型（仅用于历史数据回填）。"""
    if "消防" in name or "火灾" in name:
        return "消防报警", ""
    if "实物保护" in name:
        return "实物保护", "核设施"
    if "辐射监测" in name or "KRT" in name or "RMS" in name:
        reactors = []
        for item in ("华龙一号", "CAP1000", "VVER", "快堆", "小堆"):
            if item in name:
                reactors.append(item)
        if "田湾" in name and "VVER" not in reactors:
            reactors.append("VVER")
        if "恰希玛" in name or "巴基斯坦" in name:
            reactors.append("出口项目")
        if "福清" in name and "华龙一号" not in reactors:
            reactors.append("华龙一号")
        return "辐射监测（KRT）", ",".join(dict.fromkeys(reactors))
    if "医疗器械" in name:
        return "核医学", ""
    if "通信" in name:
        return "通信", ""
    return "共用", ""


def _version_parts(version):
    match = re.match(r"^[Vv]?(\d+)(?:\.(\d+))?$", str(version).strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2) or 0)


REF_SEED = [
    ("KRT 辐射监测系统技术方案", 1, "2.2", "赵工", "2026-08-20 09:12:00"),
    ("KRT 辐射监测系统技术方案", 6, "2.2", "赵工", "2026-09-02 15:40:00"),
    ("出厂验收报告", 1, "1.3", "钱工", "2026-08-25 10:05:00"),
    ("田湾5号机组", 6, "1.3", "钱工", "2026-09-08 11:20:00"),
    ("火灾报警与联动控制", 2, "2.1", "李工", "2026-10-05 14:30:00"),
    ("投标文件范本", 1, "1.1", "张工", "2026-08-18 09:00:00"),
    ("消防产品强制性认证", 2, "1.2", "李工", "2026-10-06 16:12:00"),
    ("CAP1000", 1, "1.3", "孙工", "2026-07-28 10:40:00"),
]

STALE_SEED = [
    ("HAF003", "2024-05-20 09:00:00"),
    ("恰希玛", "2024-08-14 10:30:00"),
    ("实物保护系统", "2024-10-09 08:20:00"),
]


def migrate(conn):
    """补齐文档库管理所需字段，并对历史数据做一次性回填（幂等）。"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
    for column, ddl in DOC_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE documents ADD COLUMN {column} {ddl}")

    conn.execute("UPDATE documents SET security_level='商密' WHERE security_level IN ('秘密','机密')")
    for row in conn.execute("SELECT id,name,product_line,reactor_tags FROM documents").fetchall():
        line, reactors = _infer_meta(row["name"])
        if not row["product_line"]:
            conn.execute("UPDATE documents SET product_line=? WHERE id=?", (line, row["id"]))
        if not row["reactor_tags"] and reactors:
            conn.execute("UPDATE documents SET reactor_tags=? WHERE id=?", (reactors, row["id"]))

    if conn.execute("SELECT value FROM settings WHERE key='doc_lib_v2'").fetchone():
        conn.commit()
        return

    now = now_text()
    for doc in conn.execute("SELECT * FROM documents ORDER BY id").fetchall():
        conn.execute("UPDATE documents SET version_status='受控', access_count=? WHERE id=?", (doc["reference_count"], doc["id"]))
        last_access = doc["last_access_at"] or doc["updated_at"] or now
        conn.execute("UPDATE documents SET last_access_at=? WHERE id=?", (last_access, doc["id"]))
        if conn.execute("SELECT COUNT(*) c FROM document_versions WHERE doc_id=?", (doc["id"],)).fetchone()["c"]:
            continue
        conn.execute(
            """INSERT INTO document_versions(doc_id,version,status,stored_path,original_filename,sha256,file_size,change_note,created_by,created_at)
               VALUES(?,?,'受控',?,?,?,?,?,?,?)""",
            (doc["id"], doc["version"], doc["stored_path"], doc["original_filename"], doc["sha256"],
             doc["file_size"], "初始入库", doc["owner"] or "系统", doc["updated_at"] or now),
        )
        parts = _version_parts(doc["version"])
        if parts:
            major, minor = parts
            for step in (1, 2):
                if minor - step < 0:
                    continue
                older = f"V{major}.{minor - step}"
                conn.execute(
                    """INSERT OR IGNORE INTO document_versions(doc_id,version,status,change_note,created_by,created_at)
                       VALUES(?,?,'历史',?,?,?)""",
                    (doc["id"], older, f"历史修订版（{older}）", doc["owner"] or "系统",
                     f"2025-0{max(1, 6 - step)}-1{step} 10:00:00"),
                )

    if conn.execute("SELECT COUNT(*) c FROM document_refs").fetchone()["c"] == 0:
        for keyword, bid_id, code, user, created in REF_SEED:
            doc = conn.execute("SELECT id FROM documents WHERE name LIKE ? ORDER BY id LIMIT 1", (f"%{keyword}%",)).fetchone()
            if not doc or not conn.execute("SELECT id FROM bids WHERE id=?", (bid_id,)).fetchone():
                continue
            section = conn.execute("SELECT id FROM sections WHERE bid_id=? AND code=?", (bid_id, code)).fetchone()
            conn.execute(
                "INSERT INTO document_refs(doc_id,bid_id,section_id,section_code,user,created_at) VALUES(?,?,?,?,?,?)",
                (doc["id"], bid_id, section["id"] if section else None, code, user, created),
            )

    for keyword, stamp in STALE_SEED:
        conn.execute("UPDATE documents SET last_access_at=? WHERE name LIKE ?", (stamp, f"%{keyword}%"))

    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('doc_lib_v2','1')")
    conn.commit()


def _cert_category(name):
    """按证书名称推断类别（用于历史数据回填）。"""
    if "核安全设备" in name:
        return "核安全设备许可"
    if "辐射安全" in name:
        return "辐射安全许可"
    if "消防" in name or "3C" in name:
        return "消防认证"
    if "医疗器械" in name:
        return "医疗器械许可"
    if "智能化" in name or "工程设计" in name:
        return "工程资质"
    if "计量" in name:
        return "计量许可"
    if "高新技术企业" in name:
        return "企业信用资质"
    return "其他资质"


CERT_COLUMNS = {
    "category": "TEXT NOT NULL DEFAULT ''",
    "scope": "TEXT NOT NULL DEFAULT ''",
    "remark": "TEXT NOT NULL DEFAULT ''",
    "original_filename": "TEXT",
    "stored_path": "TEXT",
    "sha256": "TEXT",
    "file_size": "INTEGER",
    "status": "TEXT NOT NULL DEFAULT '正常'",
    "updated_at": "TEXT",
    "deleted_at": "TEXT",
    "deleted_by": "TEXT",
    "delete_reason": "TEXT NOT NULL DEFAULT ''",
}


def migrate_certs(conn):
    """补齐资质证照模块字段并回填类别（幂等）。"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(certificates)").fetchall()}
    for column, ddl in CERT_COLUMNS.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE certificates ADD COLUMN {column} {ddl}")
    now = now_text()
    for row in conn.execute("SELECT id,name,category,updated_at,created_at FROM certificates").fetchall():
        if not row["category"]:
            conn.execute("UPDATE certificates SET category=? WHERE id=?", (_cert_category(row["name"]), row["id"]))
        if not row["updated_at"]:
            conn.execute("UPDATE certificates SET updated_at=? WHERE id=?", (row["created_at"] or now, row["id"]))
    conn.commit()


TENDER_EXTRACT_CATEGORIES = (
    "资格条件", "评分办法", "★条款", "废标条款", "技术参数", "交付期", "保证金", "份数装订", "澄清截止",
)

PHASE2_TERMS = [
    ("KRT", "辐射监测系统,RMS,Radiation Monitoring System", "核电厂辐射监测系统（法国源术语 KRT，VVER 机组沿用 RMS）"),
    ("PPS", "实物保护系统,Physical Protection System", "核设施实物保护系统"),
    ("PAC", "初步验收证书,Performance Acceptance Certificate", "业绩双要件之一：合同 + 验收证明"),
    ("FAT", "出厂验收,Factory Acceptance Test,IFC", "设备出厂验收测试"),
    ("SL-2", "运行安全地震震动,SSE", "核抗震设计基准工况"),
    ("IE级", "安全级,1E级,Safety Class 1E", "电气设备核安全分级"),
    ("HAF003", "核电厂质量保证安全规定", "核质保法规，投标方案引用其现行有效版本"),
]

# 演示种子：bid 1（漳州核电 3、4 号机组 KRT）招标文件抽取
# 元组：(category, title, content, page, para, score_value, confidence, status)
PHASE2_BASELINE_EXTRACTS = [
    ("资格条件", "民用核安全设备制造许可证",
     "投标人须具备有效的民用核安全设备制造许可证，许可范围须涵盖标的设备类别（辐射监测仪表）及相应核安全级别。",
     12, "投标人须知 2.1.3", None, 96, "已确认"),
    ("资格条件", "质量管理体系认证",
     "投标人须通过 GB/T 19001 / ISO 9001 质量管理体系认证，证书在有效期内且覆盖标的范围。",
     13, "投标人须知 2.1.4", None, 92, "已确认"),
    ("资格条件", "类似业绩要求",
     "近五年（2021 年至今）至少具有 2 项核电站辐射监测系统成套供货业绩，须同时提供合同关键页及验收（投运）证明复印件。",
     14, "投标人须知 2.1.6", None, 78, "待确认"),
    ("评分办法", "投标报价",
     "以通过初步评审的有效最低投标报价为基准价得满分，其余按基准价占其报价比例扣减。",
     45, "评标办法 3.1", 25, 97, "已确认"),
    ("评分办法", "技术方案完整性",
     "方案结构完整、逻辑清晰，覆盖招标技术规格书全部要求，无明显缺项。",
     46, "评标办法 3.2.1", 15, 95, "已确认"),
    ("评分办法", "辐射监测系统设计方案",
     "按探测原理先进性、通道配置合理性、智能化与自诊断水平评分。",
     47, "评标办法 3.2.2", 14, 96, "已确认"),
    ("评分办法", "同类项目业绩",
     "每提供 1 项满足要求的类似业绩得 2 分，最多得 10 分，须附合同与验收证明。",
     48, "评标办法 3.2.3", 10, 94, "已确认"),
    ("评分办法", "质量保证与核安全文化",
     "按质保大纲完整性、H/W 控制点设置、核安全文化培训与考核体系评分。",
     49, "评标办法 3.2.4", 10, 93, "已确认"),
    ("评分办法", "项目实施与进度计划",
     "按进度计划合理性、长周期物项管控措施、里程碑设置可行性评分。",
     50, "评标办法 3.2.5", 9, 92, "已确认"),
    ("评分办法", "售后服务与备件保障",
     "按服务响应机制、备件保障清单、培训方案完备性评分。",
     51, "评标办法 3.2.6", 8, 90, "已确认"),
    ("评分办法", "国产化率与自主可控",
     "国产化率 ≥95% 得满分，每降低 5% 扣 1 分，扣完为止。",
     52, "评标办法 3.2.7", 5, 91, "已确认"),
    ("评分办法", "资质与财务状况",
     "资质证书齐全、近三年财务指标良好、无重大违法记录。",
     52, "评标办法 3.2.8", 4, 89, "已确认"),
    ("★条款", "抗震等级",
     "★安全级设备须按 SL-2 运行安全地震震动进行抗震计算与鉴定，随投标文件提交抗震计算书。",
     88, "技术规格书 4.3", None, 95, "已确认"),
    ("★条款", "核安全级别",
     "★安全级监测通道须按 IE 级（1E）要求设计、鉴定，并具备相应鉴定试验报告。",
     89, "技术规格书 4.5", None, 93, "已确认"),
    ("★条款", "交付周期",
     "★合同生效后 18 个月内完成全部设备交付。",
     101, "合同条款 6.1", None, 72, "待确认"),
    ("废标条款", "逾期送达",
     "逾期送达或者未按要求密封的投标文件，将被拒收并作否决投标处理。",
     26, "投标人须知 15.2", None, 98, "已确认"),
    ("废标条款", "保证金",
     "投标保证金金额不足或未从投标人基本账户转出的，否决其投标。",
     27, "投标人须知 15.3", None, 97, "已确认"),
    ("废标条款", "负偏离",
     "对招标文件中标注★号的条款存在负偏离的，作否决投标处理。",
     28, "投标人须知 15.4", None, 94, "已确认"),
    ("废标条款", "授权文件",
     "无法定代表人授权委托书、或授权范围与期限不覆盖投标有效期的，否决其投标。",
     29, "投标人须知 15.6", None, 88, "已确认"),
    ("技术参数", "量程覆盖",
     "区域γ剂量率监测通道量程须覆盖 1μSv/h—10Sv/h，工艺监测通道按介质条件选型并覆盖全量程。",
     120, "技术规格书 5.2", None, 85, "已确认"),
    ("技术参数", "通讯协议",
     "系统通讯采用 TCP/IP 协议，投标人须提供完整协议文本及接口定义。",
     133, "技术规格书 5.9", None, 68, "待确认"),
    ("交付期", "现场安装调试",
     "设备到货后 3 个月内完成现场安装调试，并具备系统投运条件。",
     102, "合同条款 6.2", None, 91, "已确认"),
    ("保证金", "投标保证金",
     "投标保证金金额为 80 万元，须在投标截止时间前从基本账户一次性足额转出。",
     30, "投标人须知 16.1", None, 93, "已确认"),
    ("份数装订", "正副本与电子版",
     "纸质投标文件正本 1 份、副本 4 份，电子版 2 份（U 盘），均须逐页编码。",
     31, "投标人须知 17.1", None, 76, "待确认"),
    ("澄清截止", "澄清问题截止",
     "澄清问题须于 2026-11-08 17:00 前以书面形式提出；澄清答复将在投标截止 3 日前发出，答复内容为招标文件组成部分。",
     18, "投标人须知 9.1", None, 96, "已确认"),
]

# 演示种子：澄清答疑第 1 号（差异版），change_kind 标注变更类型
PHASE2_CLARIFY_EXTRACTS = [
    ("★条款", "交付周期（澄清修改）",
     "★合同生效后完成全部设备交付的期限由 18 个月调整为 20 个月。",
     101, "澄清答复 Q&A 第 12 条", None, 70, "待确认", "修改"),
    ("废标条款", "电子标书命名（新增）",
     "电子标书文件未按“项目编号_标段_投标人简称”规则命名、或加解密失败的，否决其投标。",
     2, "澄清答复补充条款", None, 82, "待确认", "新增"),
    ("技术参数", "通讯协议（澄清修改）",
     "通讯协议由 TCP/IP 明确为 Modbus TCP，投标人须提供完整协议文本及寄存器定义表。",
     133, "澄清答复 Q&A 第 21 条", None, 66, "待确认", "修改"),
]


def migrate_tender(conn):
    """二期·招标解析：建表演示数据一次性回填（幂等）。"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(tender_files)").fetchall()}
    if "parse_report" not in existing:
        conn.execute("ALTER TABLE tender_files ADD COLUMN parse_report TEXT")
    ext_cols = {row["name"] for row in conn.execute("PRAGMA table_info(tender_extracts)").fetchall()}
    if "signals" not in ext_cols:
        conn.execute("ALTER TABLE tender_extracts ADD COLUMN signals TEXT")
    # 招标文件登记记录：备注 + 作废（软删除）三件套，保留记录与附件以便审计追溯
    for col, ddl in (
        ("remark", "ALTER TABLE tender_files ADD COLUMN remark TEXT"),
        ("deleted_at", "ALTER TABLE tender_files ADD COLUMN deleted_at TEXT"),
        ("deleted_by", "ALTER TABLE tender_files ADD COLUMN deleted_by TEXT"),
        ("delete_reason", "ALTER TABLE tender_files ADD COLUMN delete_reason TEXT"),
    ):
        if col not in existing:
            conn.execute(ddl)
    # 技术规格目录：父章与子章节的计数分离（父章只统计本章直接要求，避免同步后重复列）
    outline_cols = {row["name"] for row in conn.execute("PRAGMA table_info(tech_outline)").fetchall()}
    for col, ddl in (
        ("child_req_count", "ALTER TABLE tech_outline ADD COLUMN child_req_count INTEGER NOT NULL DEFAULT 0"),
        ("child_must_count", "ALTER TABLE tech_outline ADD COLUMN child_must_count INTEGER NOT NULL DEFAULT 0"),
    ):
        if outline_cols and col not in outline_cols:
            conn.execute(ddl)
    if conn.execute("SELECT value FROM settings WHERE key='tender_v1'").fetchone():
        conn.commit()
        return
    now = now_text()
    if conn.execute("SELECT COUNT(*) c FROM term_dict").fetchone()["c"] == 0:
        for term, synonyms, note in PHASE2_TERMS:
            conn.execute(
                "INSERT INTO term_dict(term,synonyms,note,created_at) VALUES(?,?,?,?)",
                (term, synonyms, note, now),
            )
    if conn.execute("SELECT COUNT(*) c FROM tender_files").fetchone()["c"] == 0:
        bid = conn.execute("SELECT * FROM bids WHERE id=1").fetchone()
        if bid:
            cur = conn.execute(
                """INSERT INTO tender_files(bid_id,kind,filename,page_count,declared_total,status,version_label,
                   uploaded_by,uploaded_at) VALUES(1,'招标文件',?,386,100,'已解析','基准版','投标经理 · 演示数据',?)""",
                (f"{bid['name']}招标文件（演示）.pdf", now),
            )
            base_id = cur.lastrowid
            for category, title, content, page, para, score, conf, status in PHASE2_BASELINE_EXTRACTS:
                conn.execute(
                    """INSERT INTO tender_extracts(file_id,bid_id,category,title,content,page,para,score_value,
                       confidence,status,confirmed_by,confirmed_at,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (base_id, 1, category, title, content, page, para, score, conf, status,
                     "解析引擎" if status == "已确认" else None, now if status == "已确认" else None, now),
                )
            cur2 = conn.execute(
                """INSERT INTO tender_files(bid_id,kind,filename,page_count,declared_total,status,version_label,
                   parent_id,uploaded_by,uploaded_at) VALUES(1,'澄清答疑',?,3,100,'已解析','差异版',?, '投标经理 · 演示数据',?)""",
                ("漳州核电 KRT 招标文件澄清答疑第 1 号（演示）.pdf", base_id, now),
            )
            for category, title, content, page, para, score, conf, status, change in PHASE2_CLARIFY_EXTRACTS:
                conn.execute(
                    """INSERT INTO tender_extracts(file_id,bid_id,category,title,content,page,para,score_value,
                       confidence,status,change_kind,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (cur2.lastrowid, 1, category, title, content, page, para, score, conf, status, change, now),
                )
    conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('tender_v1','1')")
    conn.commit()


def migrate_sections_ai(conn):
    """章节正文拆分：content 保存原文技术要求，content_ai 保存大模型/人工编写的正文。"""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(sections)").fetchall()}
    for col, ddl in (
        ("content_ai", "ALTER TABLE sections ADD COLUMN content_ai TEXT NOT NULL DEFAULT ''"),
        ("ai_model", "ALTER TABLE sections ADD COLUMN ai_model TEXT NOT NULL DEFAULT ''"),
        ("ai_written_at", "ALTER TABLE sections ADD COLUMN ai_written_at TEXT NOT NULL DEFAULT ''"),
        ("summary_ai", "ALTER TABLE sections ADD COLUMN summary_ai TEXT NOT NULL DEFAULT ''"),
    ):
        if col not in cols:
            conn.execute(ddl)
    # 一次性回填：历史章节若 content 是已写好的草稿（不含"编写要点/技术规格要求/原文要求骨架"标记），
    # 视为原有正文迁到 content_ai，content 原样保留以备对照。
    # 注意：由"插入本节/转子目录"生成的【来源】【要求】骨架不是正文，绝不能回填成 content_ai（否则会被当成已编写）。
    conn.execute(
        """UPDATE sections SET content_ai=content
           WHERE content_ai='' AND content!='' AND ai_model=''
             AND content NOT LIKE '%【本节编写要点】%' AND content NOT LIKE '%【技术规格要求%'
             AND content NOT LIKE '%【要求】%' AND content NOT LIKE '%【来源】%'""")
    # bids 基线卡（锚点章提炼的方案主线与关键承诺，供全书章节共享）
    bid_cols = {row["name"] for row in conn.execute("PRAGMA table_info(bids)").fetchall()}
    if "baseline_card" not in bid_cols:
        conn.execute("ALTER TABLE bids ADD COLUMN baseline_card TEXT NOT NULL DEFAULT ''")
    # 编写任务单：续跑需要记住本次的章节范围与覆盖开关
    job_cols = {row["name"] for row in conn.execute("PRAGMA table_info(compose_jobs)").fetchall()}
    for col, ddl in (
        ("overwrite", "ALTER TABLE compose_jobs ADD COLUMN overwrite INTEGER NOT NULL DEFAULT 0"),
        ("section_ids", "ALTER TABLE compose_jobs ADD COLUMN section_ids TEXT NOT NULL DEFAULT ''"),
    ):
        if col not in job_cols:
            conn.execute(ddl)
    # 服务重启：上次未完成的编写任务标记为中断（paused 保留，仍可继续）；进入时提示可续跑
    conn.execute(
        """UPDATE compose_jobs SET status='interrupted', finished_at=datetime('now','localtime'),
           error='服务重启，任务中断（已写章节保留）'
           WHERE status IN ('running','canceling','pausing')""")
    conn.commit()


def migrate_sections_tree(conn):
    """目录树结构真值：parent_id 指向父章节，level 为树深度（组根 = 1）。

    历史库按 code 前缀回填；之后由 app._renumber_sections 与各新增接口维护。
    每次启动做幂等自愈：清悬空 parent_id、补未挂父的历史行、按父链重算 level。
    索引必须放这里（不能放 SCHEMA），否则老库补列前建索引会报「列不存在」。
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(sections)").fetchall()}
    if "parent_id" not in cols:
        conn.execute("ALTER TABLE sections ADD COLUMN parent_id INTEGER REFERENCES sections(id) ON DELETE CASCADE")
    if "level" not in cols:
        conn.execute("ALTER TABLE sections ADD COLUMN level INTEGER NOT NULL DEFAULT 1")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sections_parent ON sections(parent_id, sort_order)")

    rows = conn.execute(
        "SELECT id, bid_id, group_name, code, parent_id, level FROM sections ORDER BY sort_order").fetchall()
    by_code = {(r["bid_id"], r["group_name"], r["code"]): r["id"] for r in rows}
    own_bid = {r["id"]: r["bid_id"] for r in rows}
    own_group = {r["id"]: r["group_name"] for r in rows}
    valid_ids = set(own_bid)
    # 1) 清理悬空 / 自指 / 跨标书 / 跨分组的 parent_id。
    #    跨分组必须清理：技术标同步后可能存在 code='1'、'2'，商务标 1.1 不能挂到技术标根下。
    for r in rows:
        pid = r["parent_id"]
        if pid is not None and (pid not in valid_ids or pid == r["id"] or
                                own_bid.get(pid) != r["bid_id"] or
                                own_group.get(pid) != r["group_name"]):
            conn.execute("UPDATE sections SET parent_id=NULL WHERE id=?", (r["id"],))
    # 2) 历史行按「同标书 + 同分组」的 code 前缀回填直接父；找不到父即为组根
    current = conn.execute(
        "SELECT id, bid_id, group_name, code, parent_id FROM sections ORDER BY sort_order").fetchall()
    for r in current:
        if r["parent_id"] is not None:
            continue
        pcode = ".".join(r["code"].split(".")[:-1])
        pid = by_code.get((r["bid_id"], r["group_name"], pcode))
        if pid and pid != r["id"]:
            conn.execute("UPDATE sections SET parent_id=? WHERE id=?", (pid, r["id"]))
    # 3) level 由父链重算，与 _renumber_sections 写入的树深度口径一致
    fresh = conn.execute("SELECT id, parent_id, level FROM sections").fetchall()
    parent_of = {r["id"]: r["parent_id"] for r in fresh}
    for r in fresh:
        depth, cur, seen = 1, r["id"], set()
        while parent_of.get(cur) is not None and cur not in seen:
            seen.add(cur)
            cur = parent_of[cur]
            depth += 1
        if r["level"] != depth:
            conn.execute("UPDATE sections SET level=? WHERE id=?", (depth, r["id"]))
    conn.commit()


def migrate_sections_unique_scope(conn):
    """章节编码改为「同一标书 + 同一分组内唯一」，并保留所有历史扩展列。"""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sections'").fetchone()
    sql = (row["sql"] if row else "") or ""
    if "UNIQUE(bid_id, group_name, code)" in sql or "UNIQUE(bid_id, code)" not in sql:
        return
    info = conn.execute("PRAGMA table_info(sections)").fetchall()
    names = [c["name"] for c in info]
    defs = []
    for col in info:
        if col["name"] == "id":
            defs.append('"id" INTEGER PRIMARY KEY AUTOINCREMENT')
            continue
        piece = f'"{col["name"]}" {col["type"] or "TEXT"}'
        if col["notnull"]:
            piece += " NOT NULL"
        if col["dflt_value"] is not None:
            piece += f" DEFAULT {col['dflt_value']}"
        defs.append(piece)
    defs += [
        "FOREIGN KEY(bid_id) REFERENCES bids(id) ON DELETE CASCADE",
        "FOREIGN KEY(parent_id) REFERENCES sections(id) ON DELETE CASCADE",
        "UNIQUE(bid_id, group_name, code)",
    ]
    collist = ",".join(f'"{name}"' for name in names)
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("DROP INDEX IF EXISTS idx_sections_bid")
        conn.execute("DROP INDEX IF EXISTS idx_sections_parent")
        conn.execute("ALTER TABLE sections RENAME TO sections_old")
        conn.execute(f"CREATE TABLE sections ({','.join(defs)})")
        conn.execute(f"INSERT INTO sections({collist}) SELECT {collist} FROM sections_old")
        conn.execute("DROP TABLE sections_old")
        conn.execute("CREATE INDEX idx_sections_bid ON sections(bid_id, sort_order)")
        conn.execute("CREATE INDEX idx_sections_parent ON sections(parent_id, sort_order)")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


def _delete_if_unreferenced(conn, stored_path):
    """附件不再被任何业务表引用时删除物理文件。"""
    if not stored_path:
        return False
    for table in ("documents", "document_versions", "certificates", "tender_files"):
        if conn.execute(f"SELECT 1 FROM {table} WHERE stored_path=? LIMIT 1", (stored_path,)).fetchone():
            return False
    path = resolve_upload_path(stored_path)
    if not path:
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def _legacy_document_upload_refs(conn):
    """列出旧扁平格式的文档库附件引用：(doc_id, category, stored_path, is_version)。"""
    prefix = f"{DOC_UPLOAD_ROOT}/%"
    refs = []
    for row in conn.execute(
        """SELECT id AS doc_id, category, stored_path FROM documents
           WHERE stored_path IS NOT NULL AND stored_path <> '' AND stored_path NOT LIKE ?""",
        (prefix,),
    ).fetchall():
        refs.append((row["doc_id"], row["category"], row["stored_path"], False))
    for row in conn.execute(
        """SELECT v.doc_id, d.category, v.stored_path
           FROM document_versions v JOIN documents d ON d.id=v.doc_id
           WHERE v.stored_path IS NOT NULL AND v.stored_path <> '' AND v.stored_path NOT LIKE ?""",
        (prefix,),
    ).fetchall():
        refs.append((row["doc_id"], row["category"], row["stored_path"], True))
    return refs


def migrate_document_upload_paths(conn):
    """一次性迁移：文档库旧扁平附件迁到 uploads/documents/<分类>/，幂等。"""
    if conn.execute("SELECT 1 FROM settings WHERE key='doc_upload_categories'").fetchone():
        return
    migrated = {}
    changed = set()
    for doc_id, category, old_path, is_version in _legacy_document_upload_refs(conn):
        key = (old_path, safe_upload_category(category))
        rel = migrated.get(key)
        if rel is None:
            source = resolve_upload_path(old_path)
            if not source:
                continue
            rel = new_document_upload_relpath(category, source.name)
            target = UPLOAD_DIR / rel
            if target.resolve() == source.resolve():
                migrated[key] = old_path
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            migrated[key] = rel
        if is_version:
            conn.execute(
                "UPDATE document_versions SET stored_path=? WHERE doc_id=? AND stored_path=?",
                (rel, doc_id, old_path),
            )
        else:
            conn.execute(
                "UPDATE documents SET stored_path=? WHERE id=? AND stored_path=?",
                (rel, doc_id, old_path),
            )
        changed.add(old_path)
    for old_path in changed:
        _delete_if_unreferenced(conn, old_path)
    conn.execute(
        "INSERT OR REPLACE INTO settings(key,value) VALUES('doc_upload_categories',?)",
        (now_text(),),
    )
    conn.commit()


def relocate_document_attachments(conn, doc_id, old_category, new_category):
    """文档改分类时，把当前及历史版本附件迁到新分类目录，返回新的相对路径。"""
    if safe_upload_category(old_category) == safe_upload_category(new_category):
        return []
    doc = conn.execute("SELECT stored_path FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not doc:
        return []
    paths = {doc["stored_path"]}
    paths.update(row["stored_path"] for row in conn.execute(
        "SELECT stored_path FROM document_versions WHERE doc_id=?", (doc_id,)))
    mapping = {}
    for old_path in sorted(p for p in paths if p):
        source = resolve_upload_path(old_path)
        if not source:
            continue
        rel = new_document_upload_relpath(new_category, source.name)
        target = UPLOAD_DIR / rel
        if target.exists() and target.resolve() != source.resolve():
            rel = new_document_upload_relpath(new_category, source.name)
            target = UPLOAD_DIR / rel
        if target.resolve() != source.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        mapping[old_path] = rel
    for old_path, rel in mapping.items():
        conn.execute(
            "UPDATE documents SET stored_path=? WHERE id=? AND stored_path=?",
            (rel, doc_id, old_path),
        )
        conn.execute(
            "UPDATE document_versions SET stored_path=? WHERE doc_id=? AND stored_path=?",
            (rel, doc_id, old_path),
        )
    for old_path in mapping:
        _delete_if_unreferenced(conn, old_path)
    return list(mapping.values())


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        seed(conn)
        migrate(conn)
        migrate_certs(conn)
        migrate_tender(conn)
        migrate_sections_ai(conn)
        migrate_sections_tree(conn)
        migrate_sections_unique_scope(conn)
        migrate_document_upload_paths(conn)


def audit(conn, action, entity_type, entity_id=None, detail=""):
    conn.execute(
        "INSERT INTO audit_logs(action,entity_type,entity_id,detail,created_at) VALUES(?,?,?,?,?)",
        (action, entity_type, entity_id, detail, now_text()),
    )
