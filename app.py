import base64
import binascii
import hashlib
import html
import json
import logging
import mimetypes
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

from database import (BASE_DIR, UPLOAD_DIR, connect, init_db, now_text, audit, create_default_sections,
                      TENDER_EXTRACT_CATEGORIES, new_document_upload_relpath, resolve_upload_path,
                      relocate_document_attachments)
from services import (days_left, parse_iso_day, generate_section_text, calculate_progress, run_compliance,
                      parse_tender_text, build_demo_extracts, AUTO_CONFIRM_CONFIDENCE,
                      render_bid_markdown)
from tender_agent import (run_tender_agent, extract_document_text, extract_file_blocks,
                          docx_to_pdf, _fmt_score)
from tech_spec_agent import run_tech_spec_agent
import composer
import llm_client
import tech_audit
import tech_bid_agent

# Word 导出依赖用户提供的 WordChapter.py / MermaidNodeFixer.py（项目不修改这两个文件）。
# 主接口把中间文件写为相对路径 temp.md，故转换期间需在锁内临时切换工作目录。
_DOCX_LOCK = threading.Lock()
# 仅作可用性预检：DocumentConverter.__init__(reference_doc) 未被主接口使用，
# 实际模板由 WordChapter.py 内部 wordMedel 固定；换模板须同步改该文件。
DOCX_REFERENCE_DOC = Path("C:/work/aiwork/doc/default-reference_v2.0.docx")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
app.config["JSON_AS_ASCII"] = False

# 统一日志：文件（logs/app.log，按大小轮转）+ 终端 stderr，异常均带 traceback。
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"


def _setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(threadName)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(getattr(h, "_xnic_file_handler", False) for h in root.handlers):
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler._xnic_file_handler = True
        root.addHandler(file_handler)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler)
               for h in root.handlers):
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(formatter)
        root.addHandler(console)
    # Flask 默认只挂 stderr；移除后交给 root 统一输出，避免终端重复打印。
    for handler in list(app.logger.handlers):
        app.logger.removeHandler(handler)
    app.logger.setLevel(logging.INFO)
    app.logger.propagate = True


_setup_logging()


def log_api_error(exc, level=logging.ERROR):
    """把异常连同请求位置、完整堆栈写入 logs/app.log 并打印到终端。"""
    try:
        where = f"{request.method} {request.full_path or request.path}"
    except RuntimeError:
        where = "请求上下文外"
    app.logger.log(level, "接口错误 [%s]：%s", where, exc, exc_info=True)


ACTIVE_BID_STATUSES = ("编制中", "资料收集", "待决策")
SECURITY_LEVELS = ("公开", "内部", "商密")
SECURITY_RANK = {"公开": 0, "内部": 1, "商密": 2}
ZOMBIE_DAYS = 730
PRODUCT_LINES = ("辐射监测（KRT）", "消防报警", "实物保护", "通信", "核医学", "共用")
REACTOR_OPTIONS = ("华龙一号", "CAP1000", "VVER", "快堆", "小堆", "核设施", "出口项目")
DOC_STATUSES = ("正常", "待评估", "已归档", "已作废")


def as_dict(row):
    return dict(row) if row is not None else None


def as_list(rows):
    return [dict(row) for row in rows]


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ValueError("请求体必须为 JSON 对象")
    return data


@app.errorhandler(ValueError)
def handle_value_error(exc):
    log_api_error(exc, logging.WARNING)
    return jsonify(error=str(exc)), 400


@app.errorhandler(binascii.Error)
def handle_base64_error(exc):
    log_api_error(exc, logging.WARNING)
    return jsonify(error="附件 Base64 格式错误"), 400


@app.errorhandler(sqlite3.IntegrityError)
def handle_db_error(exc):
    log_api_error(exc)
    return jsonify(error=f"数据约束错误：{exc}"), 400


@app.errorhandler(413)
def handle_too_large(exc):
    log_api_error(exc, logging.WARNING)
    return jsonify(error="请求内容超过 12MB 限制"), 413


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    if isinstance(exc, HTTPException):
        return exc
    log_api_error(exc)
    return jsonify(error="服务内部错误，详情已写入 logs/app.log 并输出到终端"), 500


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok", time=now_text(), framework="Flask")


@app.get("/api/dashboard")
def dashboard():
    with connect() as conn:
        docs = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(reference_count),0) refs FROM documents WHERE deleted_at IS NULL"
        ).fetchone()
        doc_rows = conn.execute("SELECT * FROM documents WHERE deleted_at IS NULL").fetchall()
        certs = conn.execute("SELECT * FROM certificates ORDER BY expiry_date").fetchall()
        bids = as_list(conn.execute("SELECT * FROM bids ORDER BY due_date,id").fetchall())
        settings = {row["key"]: row["value"] for row in conn.execute("SELECT * FROM settings").fetchall()}
    active = [b for b in bids if b["status"] in ACTIVE_BID_STATUSES]
    warnings = []
    for cert in certs:
        left = days_left(cert["expiry_date"])
        if left is not None and left < 0:
            warnings.append({**dict(cert), "days_left": left, "level": "expired"})
        elif left is not None and left <= 90:
            warnings.append({**dict(cert), "days_left": left, "level": "warning"})
    historical_bids = int(settings.get("historical_bids", "0"))
    historical_wins = int(settings.get("historical_wins", "0"))
    win_rate = round(historical_wins / historical_bids * 100, 1) if historical_bids else 0
    doc_items = [doc_view(row) for row in doc_rows]
    return jsonify({
        "stats": {
            "documents": docs["c"], "references": docs["refs"], "certificates": len(certs),
            "warnings": len(warnings), "active_bids": len(active),
            "zombies": sum(1 for item in doc_items if item["is_zombie"]),
            "controlled_docs": sum(1 for item in doc_items if item["version_status"] == "受控"),
            "due_soon": sum(1 for b in active if days_left(b["due_date"]) is not None and days_left(b["due_date"]) <= 14),
            "win_rate": win_rate, "historical_bids": historical_bids, "historical_wins": historical_wins,
        },
        "upcoming_bids": active[:4], "warnings": warnings[:5], "bids": bids,
    })


def parse_dt(value):
    if not value:
        return None
    text = str(value)
    for fmt, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:size], fmt)
        except ValueError:
            continue
    return None


def doc_view(row, zombie_days=ZOMBIE_DAYS):
    item = as_dict(row)
    last = parse_dt(item.get("last_access_at") or item.get("updated_at") or item.get("created_at"))
    idle = max(0, (datetime.now() - last).days) if last else 0
    item["idle_days"] = idle
    item["reactors"] = [x for x in (item.get("reactor_tags") or "").split(",") if x]
    item["is_deleted"] = bool(item.get("deleted_at"))
    item["is_zombie"] = idle >= zombie_days and (item.get("status") or "正常") == "正常" and not item["is_deleted"]
    item["display_status"] = "待评估" if item["is_zombie"] else (item.get("status") or "正常")
    item["version_count"] = item.get("version_count") or 0
    return item


def cert_view(row):
    item = as_dict(row)
    left = days_left(item.get("expiry_date"))
    item["days_left"] = left
    item["is_deleted"] = bool(item.get("deleted_at"))
    item["has_attachment"] = bool(item.get("stored_path"))
    if item["is_deleted"]:
        item["display_state"] = "回收站"
    elif item.get("status") == "已作废":
        item["display_state"] = "已作废"
    elif left is None:
        item["display_state"] = "长期有效"
    elif left < 0:
        item["display_state"] = "已过期"
    elif left <= 90:
        item["display_state"] = f"{left} 天到期"
    else:
        item["display_state"] = "有效"
    return item


def next_version(current):
    match = re.match(r"^[Vv]?(\d+)(?:\.(\d+))?$", str(current or "").strip())
    if not match:
        return "V1.0"
    return f"V{int(match.group(1))}.{int(match.group(2) or 0) + 1}"


def extract_text(raw, filename):
    suffix = Path(filename).suffix.lower()
    if suffix in (".pdf", ".docx"):
        text = extract_document_text(raw, suffix)
        if text:
            return text[:200000]
        return ""
    if suffix not in (".txt", ".md", ".csv", ".json", ".html", ".htm", ".xml"):
        return ""
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)[:200000]
        except UnicodeDecodeError:
            continue
    return ""


@app.get("/api/documents")
def list_documents():
    started = time.perf_counter()
    keyword = request.args.get("q", "").strip()
    category = request.args.get("category", "")
    security = request.args.get("security", "")
    product_line = request.args.get("product_line", "")
    reactor = request.args.get("reactor", "")
    status = request.args.get("status", "")
    controlled = request.args.get("controlled", "")
    deleted = request.args.get("deleted", "")
    sort = request.args.get("sort", "updated")
    sql = """SELECT d.*, (SELECT COUNT(*) FROM document_versions v WHERE v.doc_id=d.id) version_count
             FROM documents d WHERE 1=1"""
    params = []
    if deleted == "1":
        sql += " AND d.deleted_at IS NOT NULL"
    else:
        sql += " AND d.deleted_at IS NULL"
    if keyword:
        sql += (" AND (d.name LIKE ? OR d.content_text LIKE ? OR d.reactor_tags LIKE ? OR d.remark LIKE ?"
                " OR d.department LIKE ? OR d.owner LIKE ?)")
        params.extend([f"%{keyword}%"] * 6)
    if category:
        sql += " AND d.category=?"
        params.append(category)
    if security:
        sql += " AND d.security_level=?"
        params.append(security)
    if product_line:
        sql += " AND d.product_line=?"
        params.append(product_line)
    order = {
        "updated": "d.updated_at DESC, d.id DESC",
        "refs": "d.reference_count DESC, d.id DESC",
        "name": "d.name ASC",
        "idle": "d.last_access_at ASC",
    }.get(sort, "d.updated_at DESC, d.id DESC")
    sql += f" ORDER BY {order}"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = [doc_view(row) for row in rows]
    if reactor:
        items = [item for item in items if reactor in item["reactors"]]
    if status:
        items = [item for item in items if item["display_status"] == status]
    if controlled == "1":
        items = [item for item in items if item["version_status"] == "受控"]
    return jsonify(items=items, total=len(items), elapsed_ms=round((time.perf_counter() - started) * 1000, 1))


@app.get("/api/documents/meta")
def document_meta():
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.*, (SELECT COUNT(*) FROM document_versions v WHERE v.doc_id=d.id) version_count
               FROM documents d WHERE d.deleted_at IS NULL"""
        ).fetchall()
        refs_total = conn.execute("SELECT COUNT(*) c FROM document_refs").fetchone()["c"]
        pending = conn.execute("SELECT COUNT(*) c FROM security_requests WHERE status='待审批'").fetchone()["c"]
        trash = conn.execute("SELECT COUNT(*) c FROM documents WHERE deleted_at IS NOT NULL").fetchone()["c"]
    items = [doc_view(row) for row in rows]

    def count_by(field):
        bucket = {}
        for item in items:
            key = item.get(field) or "未标注"
            bucket[key] = bucket.get(key, 0) + 1
        return bucket

    return jsonify(
        options={
            "categories": sorted({item["category"] for item in items}),
            "product_lines": list(PRODUCT_LINES),
            "reactors": list(REACTOR_OPTIONS),
            "security_levels": list(SECURITY_LEVELS),
        },
        stats={
            "total": len(items),
            "controlled": sum(1 for item in items if item["version_status"] == "受控"),
            "commercial": sum(1 for item in items if item["security_level"] == "商密"),
            "zombies": sum(1 for item in items if item["is_zombie"]),
            "refs": refs_total,
            "pending": pending,
            "trash": trash,
            "with_attachment": sum(1 for item in items if item["stored_path"]),
        },
        by_category=count_by("category"),
        by_security=count_by("security_level"),
        by_product_line=count_by("product_line"),
    )


@app.get("/api/documents/zombies")
def zombie_documents():
    threshold = request.args.get("days", default=ZOMBIE_DAYS, type=int)
    with connect() as conn:
        rows = conn.execute(
            """SELECT d.*, (SELECT COUNT(*) FROM document_versions v WHERE v.doc_id=d.id) version_count
               FROM documents d WHERE d.deleted_at IS NULL ORDER BY d.last_access_at ASC"""
        ).fetchall()
    items = [doc_view(row, threshold) for row in rows]
    items = [item for item in items if item["is_zombie"]]
    return jsonify(items=items, total=len(items), threshold=threshold)


@app.get("/api/documents/ref-stats")
def document_ref_stats():
    with connect() as conn:
        top = as_list(conn.execute(
            """SELECT d.id, d.name, d.category, d.product_line, d.security_level, d.reference_count,
                      (SELECT COUNT(*) FROM document_refs r WHERE r.doc_id=d.id) recent_refs,
                      (SELECT MAX(r.created_at) FROM document_refs r WHERE r.doc_id=d.id) last_ref_at
               FROM documents d WHERE d.deleted_at IS NULL
               ORDER BY d.reference_count DESC, d.id LIMIT 8"""
        ).fetchall())
        by_bid = as_list(conn.execute(
            """SELECT b.id, b.name, b.status, COUNT(r.id) refs
               FROM document_refs r JOIN bids b ON b.id=r.bid_id
               GROUP BY b.id ORDER BY refs DESC, b.id"""
        ).fetchall())
        recent = as_list(conn.execute(
            """SELECT r.id, r.created_at, r.user, r.section_code, d.id doc_id, d.name doc_name, b.name bid_name
               FROM document_refs r JOIN documents d ON d.id=r.doc_id
               LEFT JOIN bids b ON b.id=r.bid_id ORDER BY r.id DESC LIMIT 12"""
        ).fetchall())
        total = conn.execute("SELECT COUNT(*) c FROM document_refs").fetchone()["c"]
        covered = conn.execute("SELECT COUNT(DISTINCT doc_id) c FROM document_refs").fetchone()["c"]
        refs_sum = conn.execute(
            "SELECT COALESCE(SUM(reference_count),0) c FROM documents WHERE deleted_at IS NULL"
        ).fetchone()["c"]
    return jsonify(top=top, by_bid=by_bid, recent=recent, total_refs=total, docs_covered=covered,
                   cumulative=refs_sum)


@app.get("/api/documents/<int:doc_id>")
def document_detail(doc_id):
    with connect() as conn:
        if not conn.execute("SELECT id FROM documents WHERE id=?", (doc_id,)).fetchone():
            return jsonify(error="文档不存在"), 404
        conn.execute(
            "UPDATE documents SET last_access_at=?, access_count=access_count+1 WHERE id=?",
            (now_text(), doc_id),
        )
        row = conn.execute(
            """SELECT d.*, (SELECT COUNT(*) FROM document_versions v WHERE v.doc_id=d.id) version_count
               FROM documents d WHERE d.id=?""", (doc_id,)
        ).fetchone()
        versions = as_list(conn.execute(
            "SELECT * FROM document_versions WHERE doc_id=? ORDER BY id DESC", (doc_id,)
        ).fetchall())
        refs = as_list(conn.execute(
            """SELECT r.*, b.name bid_name, s.title section_title FROM document_refs r
               LEFT JOIN bids b ON b.id=r.bid_id LEFT JOIN sections s ON s.id=r.section_id
               WHERE r.doc_id=? ORDER BY r.id DESC LIMIT 20""", (doc_id,)
        ).fetchall())
        requests = as_list(conn.execute(
            "SELECT * FROM security_requests WHERE doc_id=? ORDER BY id DESC LIMIT 10", (doc_id,)
        ).fetchall())
        logs = as_list(conn.execute(
            """SELECT * FROM audit_logs WHERE entity_type='document' AND entity_id=?
               ORDER BY id DESC LIMIT 10""", (doc_id,)
        ).fetchall())
        approved = conn.execute(
            """SELECT id FROM security_requests WHERE doc_id=? AND kind='外发' AND status='已批准'
               ORDER BY id DESC LIMIT 1""", (doc_id,)
        ).fetchone()
        conn.commit()
    item = doc_view(row)
    return jsonify(
        **item, versions=versions, refs=refs, requests=requests, logs=logs,
        download_allowed=(not item["is_deleted"]) and (item["security_level"] != "商密" or bool(approved)),
        approved_request_id=approved["id"] if approved else None,
    )


@app.get("/api/documents/<int:doc_id>/download")
def download_document(doc_id):
    version_id = request.args.get("version_id", type=int)
    with connect() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            return jsonify(error="文档不存在"), 404
        if row["deleted_at"]:
            return jsonify(error="该文档在回收站中，请先恢复后再下载"), 409
        if row["security_level"] == "商密":
            req_id = request.args.get("req", type=int)
            approved = req_id and conn.execute(
                """SELECT id FROM security_requests WHERE id=? AND doc_id=? AND kind='外发' AND status='已批准'""",
                (req_id, doc_id),
            ).fetchone()
            if not approved:
                return jsonify(error="商密文档禁止直接下载，请先在“密级审批”中提交外发申请并获批准"), 403
        version = None
        if version_id:
            version = conn.execute(
                "SELECT * FROM document_versions WHERE id=? AND doc_id=?", (version_id, doc_id)
            ).fetchone()
            if not version:
                return jsonify(error="版本不存在"), 404
        stored_path = version["stored_path"] if version else row["stored_path"]
        filename = (version["original_filename"] if version else row["original_filename"]) or f"document-{doc_id}"
        if not stored_path:
            return jsonify(error="该版本未上传附件"), 404
        stored = resolve_upload_path(stored_path)
        if not stored:
            return jsonify(error="附件文件缺失或路径非法"), 404
        conn.execute(
            "UPDATE documents SET last_access_at=?, access_count=access_count+1 WHERE id=?",
            (now_text(), doc_id),
        )
        audit(conn, "下载文档", "document", doc_id, f"{filename} · 密级 {row['security_level']}")
        conn.commit()
    return send_file(stored, as_attachment=True, download_name=filename)


PREVIEW_TEXT_SUFFIX = (".txt", ".md", ".csv", ".json", ".log", ".xml", ".html", ".htm", ".yaml", ".yml", ".ini", ".srt", ".sql")
PREVIEW_IMAGE_SUFFIX = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


def preview_kind(filename):
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in PREVIEW_IMAGE_SUFFIX:
        return "image"
    if suffix in PREVIEW_TEXT_SUFFIX:
        return "text"
    if suffix in (".ofd",):
        return "ofd"
    return "unsupported"


def preview_target(conn, doc_id, version_id):
    """返回 (doc, version, stored_path, filename, approved_request_id)；错误时返回 (None, 响应, 状态码)。"""
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not doc:
        return None, jsonify(error="文档不存在"), 404
    if doc["deleted_at"]:
        return None, jsonify(error="该文档在回收站中，请先恢复后查看"), 409
    version = None
    if version_id:
        version = conn.execute(
            "SELECT * FROM document_versions WHERE id=? AND doc_id=?", (version_id, doc_id)
        ).fetchone()
        if not version:
            return None, jsonify(error="版本不存在"), 404
    stored_path = version["stored_path"] if version else doc["stored_path"]
    filename = (version["original_filename"] if version else doc["original_filename"]) or ""
    if not stored_path:
        return None, jsonify(error="该版本未上传附件，仅登记元数据", code="no_file"), 404
    approved = None
    if doc["security_level"] == "商密":
        approved = conn.execute(
            """SELECT id FROM security_requests WHERE doc_id=? AND kind='外发' AND status='已批准'
               ORDER BY id DESC LIMIT 1""", (doc_id,)
        ).fetchone()
        if not approved:
            return None, jsonify(
                error="商密文档禁止在线查看，请先在“密级审批”中提交外发申请并获批准",
                code="security_blocked",
            ), 403
    return (doc, version, stored_path, filename, approved["id"] if approved else None), None, 200


@app.get("/api/documents/<int:doc_id>/preview-meta")
def document_preview_meta(doc_id):
    version_id = request.args.get("version_id", type=int)
    with connect() as conn:
        target, error, status = preview_target(conn, doc_id, version_id)
        if error:
            return error, status
        doc, version, stored_path, filename, approved_id = target
        kind = preview_kind(filename)
        path = resolve_upload_path(stored_path)
        if not path:
            return jsonify(error="附件文件缺失，可能已被清理，请联系文档责任人"), 404
        size = (version["file_size"] if version else doc["file_size"]) or path.stat().st_size
        text = None
        if kind == "text":
            raw = path.read_bytes()[:400000]
            for encoding in ("utf-8", "gb18030"):
                try:
                    text = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if text is None:
                text = raw.decode("utf-8", errors="replace")
        audit(conn, "查看文档", "document", doc_id,
              f"{filename} · 版本 {version['version'] if version else doc['version']}")
        conn.execute(
            "UPDATE documents SET last_access_at=?, access_count=access_count+1 WHERE id=?",
            (now_text(), doc_id),
        )
        conn.commit()
        payload = {
            "kind": kind, "filename": filename,
            "version": version["version"] if version else doc["version"],
            "version_status": version["status"] if version else doc["version_status"],
            "size": size, "mime_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
            "security_level": doc["security_level"], "approved_request_id": approved_id,
            "doc_name": doc["name"], "updated_at": version["created_at"] if version else doc["updated_at"],
        }
        if text is not None:
            payload["text"] = text
        return jsonify(payload)


@app.get("/api/documents/<int:doc_id>/raw")
def document_raw(doc_id):
    version_id = request.args.get("version_id", type=int)
    with connect() as conn:
        target, error, status = preview_target(conn, doc_id, version_id)
        if error:
            return error, status
        doc, version, stored_path, filename, approved_id = target
        path = resolve_upload_path(stored_path)
        if not path:
            return jsonify(error="附件文件缺失"), 404
        conn.execute(
            "UPDATE documents SET last_access_at=?, access_count=access_count+1 WHERE id=?",
            (now_text(), doc_id),
        )
        audit(conn, "在线查看附件", "document", doc_id,
              f"{filename} · 版本 {version['version'] if version else doc['version']}")
        conn.commit()
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return send_file(path, mimetype=mime, as_attachment=False, download_name=filename or f"document-{doc_id}")


@app.post("/api/documents")
def create_document():
    data = body()
    name = str(data.get("name", "")).strip()
    category = str(data.get("category", "")).strip()
    if not name or not category:
        raise ValueError("文档名称和分类不能为空")
    security = str(data.get("security_level", "内部"))
    if security not in SECURITY_LEVELS:
        raise ValueError(f"密级必须为 {'/'.join(SECURITY_LEVELS)} 之一")
    reactors = data.get("reactors", data.get("reactor_tags", ""))
    if isinstance(reactors, list):
        reactors = ",".join(str(x).strip() for x in reactors if str(x).strip())
    reactors = str(reactors or "").strip()

    raw = None
    sha256 = None
    original_filename = None
    content_text = ""
    if data.get("content_base64"):
        raw = base64.b64decode(data["content_base64"], validate=True)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("附件不能超过 8MB")
        sha256 = hashlib.sha256(raw).hexdigest()
        original_filename = Path(str(data.get("original_filename", "upload.bin"))).name
        content_text = extract_text(raw, original_filename)

    with connect() as conn:
        if sha256:
            duplicate = conn.execute(
                "SELECT id,name FROM documents WHERE sha256=? LIMIT 1", (sha256,)
            ).fetchone()
            if duplicate and not data.get("force"):
                return jsonify(
                    error=f"该附件与《{duplicate['name']}》（ID {duplicate['id']}）SHA-256 完全一致，已阻止重复入库",
                    duplicate_id=duplicate["id"],
                ), 409
        stored_path = None
        if raw is not None:
            stored_path = new_document_upload_relpath(category, original_filename)
            target = UPLOAD_DIR / stored_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        version = str(data.get("version") or "V1.0").strip()
        cur = conn.execute(
            """INSERT INTO documents(name,category,security_level,department,owner,version,updated_at,expiry_date,
                   reference_count,original_filename,stored_path,has_pac,created_at,product_line,reactor_tags,sha256,
                   file_size,mime_type,version_status,status,last_access_at,access_count,content_text,remark)
               VALUES(?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?, '受控','正常',?,0,?,?)""",
            (name, category, security, str(data.get("department", "")), str(data.get("owner", "")), version,
             now_text(), str(data.get("expiry_date", "长期")), original_filename, stored_path,
             1 if data.get("has_pac", True) else 0, now_text(), str(data.get("product_line", "")), reactors,
             sha256, len(raw) if raw else 0, str(data.get("mime_type", "")), now_text(),
             content_text, str(data.get("remark", ""))),
        )
        doc_id = cur.lastrowid
        conn.execute(
            """INSERT INTO document_versions(doc_id,version,status,stored_path,original_filename,sha256,file_size,change_note,created_by,created_at)
               VALUES(?,?,'受控',?,?,?,?,?,?,?)""",
            (doc_id, version, stored_path, original_filename, sha256, len(raw) if raw else 0,
             str(data.get("change_note") or "首次入库"), str(data.get("owner") or "系统"), now_text()),
        )
        audit(conn, "上传文档", "document", doc_id, f"{name} · {security} · {version}")
        conn.commit()
        row = conn.execute(
            """SELECT d.*, (SELECT COUNT(*) FROM document_versions v WHERE v.doc_id=d.id) version_count
               FROM documents d WHERE d.id=?""", (doc_id,)
        ).fetchone()
    return jsonify(doc_view(row)), 201


DOC_EDIT_LABELS = {
    "name": "文档名称", "category": "分类", "product_line": "产品线", "department": "责任部门",
    "owner": "责任人", "expiry_date": "有效期", "remark": "摘要/关键词", "security_level": "密级",
}
EDITABLE_STATUSES = ("正常", "已归档")


def drop_orphan_files(conn, paths):
    """删除已无任何引用的附件，仍被其他文档/版本引用的文件保留。"""
    for stored in {p for p in paths if p}:
        still = conn.execute(
            """SELECT (SELECT COUNT(*) FROM documents WHERE stored_path=?)
                    + (SELECT COUNT(*) FROM document_versions WHERE stored_path=?) c""",
            (stored, stored),
        ).fetchone()["c"]
        if still:
            continue
        target = (UPLOAD_DIR / stored).resolve()
        if UPLOAD_DIR.resolve() in target.parents and target.exists():
            try:
                target.unlink()
            except OSError:
                pass


@app.patch("/api/documents/<int:doc_id>")
def update_document(doc_id):
    """修改文档元数据。逐字段留痕；密级只能上调，下调必须走降密审批。"""
    data = body()
    operator = str(data.get("operator") or "经营计划部 · 张").strip()
    note = str(data.get("change_note") or "").strip()
    with connect() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            return jsonify(error="文档不存在"), 404
        if doc["deleted_at"]:
            raise ValueError("回收站中的文档不能修改，请先恢复")
        if doc["status"] == "已作废" and str(data.get("status") or "") != "正常":
            raise ValueError("已作废文档为受控记录，只能由责任人恢复为“正常”后再修改其他信息")

        fields, params, changes = [], [], []
        new_category = None
        for key in ("name", "category", "product_line", "department", "owner", "expiry_date", "remark"):
            if key not in data:
                continue
            value = str(data.get(key) or "").strip()
            if key in ("name", "category") and not value:
                raise ValueError("文档名称和分类不能为空")
            if value == (doc[key] or ""):
                continue
            changes.append(f"{DOC_EDIT_LABELS[key]}：{doc[key] or '空'} → {value or '空'}")
            fields.append(f"{key}=?")
            params.append(value)
            if key == "category":
                new_category = value

        if "reactors" in data or "reactor_tags" in data:
            reactors = data.get("reactors", data.get("reactor_tags"))
            if isinstance(reactors, list):
                reactors = ",".join(str(x).strip() for x in reactors if str(x).strip())
            reactors = str(reactors or "").strip()
            if reactors != (doc["reactor_tags"] or ""):
                changes.append(f"适用堆型：{doc['reactor_tags'] or '空'} → {reactors or '空'}")
                fields.append("reactor_tags=?")
                params.append(reactors)

        if "has_pac" in data:
            pac = 1 if data.get("has_pac") else 0
            if pac != doc["has_pac"]:
                changes.append(f"PAC/验收证明：{'有' if doc['has_pac'] else '无'} → {'有' if pac else '无'}")
                fields.append("has_pac=?")
                params.append(pac)

        if "security_level" in data:
            level = str(data["security_level"])
            if level not in SECURITY_LEVELS:
                raise ValueError(f"密级必须为 {'/'.join(SECURITY_LEVELS)} 之一")
            if level != doc["security_level"]:
                if SECURITY_RANK[level] < SECURITY_RANK[doc["security_level"]]:
                    raise ValueError("密级下调必须走“降密申请”并由安全保密管理员审批，不能在编辑中直接修改")
                changes.append(f"密级：{doc['security_level']} → {level}（上调）")
                fields.append("security_level=?")
                params.append(level)

        if "status" in data:
            status = str(data["status"])
            if status not in EDITABLE_STATUSES:
                raise ValueError("状态只能改为正常或已归档；作废请使用“作废”处置（保留审计痕迹）")
            if status != doc["status"]:
                changes.append(f"状态：{doc['status']} → {status}")
                fields.append("status=?")
                params.append(status)

        if not fields:
            item = doc_view(doc)
            item.update(message="没有检测到字段变更", changed=[])
            return jsonify(item)

        fields.append("updated_at=?")
        params.extend([now_text(), doc_id])
        conn.execute(f"UPDATE documents SET {','.join(fields)} WHERE id=?", params)
        if new_category is not None:
            relocated = relocate_document_attachments(conn, doc_id, doc["category"], new_category)
            if relocated:
                changes.append(f"附件目录：{doc['category']} → {new_category}")
        if doc["status"] == "已作废" and str(data.get("status") or "") == "正常":
            conn.execute("UPDATE document_versions SET status='历史' WHERE doc_id=? AND status='作废'", (doc_id,))
            conn.execute(
                "UPDATE document_versions SET status='受控' WHERE doc_id=? AND version=?", (doc_id, doc["version"])
            )
            conn.execute("UPDATE documents SET version_status='受控' WHERE id=?", (doc_id,))
            changes.append("版本状态：作废 → 恢复为受控")
        audit(conn, "修改文档", "document", doc_id,
              f"{'；'.join(changes)}" + (f"；变更说明：{note}" if note else "") + f"；操作人：{operator}")
        conn.commit()
        row = conn.execute(
            """SELECT d.*, (SELECT COUNT(*) FROM document_versions v WHERE v.doc_id=d.id) version_count
               FROM documents d WHERE d.id=?""", (doc_id,)
        ).fetchone()
    item = doc_view(row)
    item.update(message=f"已保存 {len(changes)} 处修改，变更明细写入审计日志", changed=changes)
    return jsonify(item)


@app.delete("/api/documents/<int:doc_id>")
def delete_document(doc_id):
    """移入回收站（软删除）：保留版本链、引用记录与审计痕迹，可随时恢复。"""
    data = request.get_json(silent=True) or {}
    reason = str(data.get("reason") or "").strip()
    operator = str(data.get("operator") or "经营计划部 · 张").strip()
    force = bool(data.get("force"))
    with connect() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            return jsonify(error="文档不存在"), 404
        if doc["deleted_at"]:
            return jsonify(message="该文档已在回收站中", id=doc_id, already=True)
        active_refs = conn.execute(
            """SELECT COUNT(*) c FROM document_refs r JOIN bids b ON b.id=r.bid_id
               WHERE r.doc_id=? AND b.status IN (?,?,?)""",
            (doc_id, *ACTIVE_BID_STATUSES),
        ).fetchone()["c"]
        if active_refs and not force:
            return jsonify(
                error=f"该文档正被 {active_refs} 处在编标书引用，删除后这些引用会失去素材来源，请确认后再删除",
                code="referenced", active_refs=active_refs,
            ), 409
        conn.execute(
            "UPDATE documents SET deleted_at=?, deleted_by=?, delete_reason=? WHERE id=?",
            (now_text(), operator, reason, doc_id),
        )
        conn.execute(
            "UPDATE security_requests SET status='已驳回', approver='系统', decided_at=? WHERE doc_id=? AND status='待审批'",
            (now_text(), doc_id),
        )
        audit(conn, "移入回收站", "document", doc_id,
              f"{doc['name']}（{doc['version']}）；原因：{reason or '未填写'}；操作人：{operator}")
        conn.commit()
    return jsonify(
        message="已移入回收站：版本链与引用记录保留，可随时恢复；彻底删除需在回收站中二次确认",
        id=doc_id,
    )


@app.post("/api/documents/<int:doc_id>/restore")
def restore_document(doc_id):
    data = request.get_json(silent=True) or {}
    operator = str(data.get("operator") or "经营计划部 · 张").strip()
    with connect() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            return jsonify(error="文档不存在"), 404
        if not doc["deleted_at"]:
            return jsonify(message="该文档不在回收站中", id=doc_id, already=True)
        conn.execute(
            "UPDATE documents SET deleted_at=NULL, deleted_by='', delete_reason='', last_access_at=? WHERE id=?",
            (now_text(), doc_id),
        )
        audit(conn, "从回收站恢复", "document", doc_id, f"{doc['name']}；操作人：{operator}")
        conn.commit()
    return jsonify(message="已恢复，文档重新参与检索与引用统计", id=doc_id)


@app.delete("/api/documents/<int:doc_id>/purge")
def purge_document(doc_id):
    """彻底删除：仅限回收站内、且无引用证据链的文档；审计记录永久保留。"""
    data = request.get_json(silent=True) or {}
    if str(data.get("confirm") or "").strip() != "彻底删除":
        raise ValueError("彻底删除为不可恢复操作，请提交 confirm=“彻底删除”以确认")
    operator = str(data.get("operator") or "经营计划部 · 张").strip()
    with connect() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            return jsonify(error="文档不存在"), 404
        if not doc["deleted_at"]:
            raise ValueError("只能彻底删除回收站中的文档，请先移入回收站")
        refs = conn.execute("SELECT COUNT(*) c FROM document_refs WHERE doc_id=?", (doc_id,)).fetchone()["c"]
        if refs:
            return jsonify(
                error=f"该文档存在 {refs} 条引用记录（投标证据链），不能彻底删除；建议保留或改为归档",
                code="has_refs", refs=refs,
            ), 409
        paths = [doc["stored_path"]] + [
            row["stored_path"] for row in conn.execute(
                "SELECT stored_path FROM document_versions WHERE doc_id=?", (doc_id,)
            ).fetchall()
        ]
        conn.execute("DELETE FROM document_versions WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM security_requests WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        audit(conn, "彻底删除文档", "document", doc_id,
              f"{doc['name']}（{doc['version']}）永久删除；操作人：{operator}；审计记录保留")
        drop_orphan_files(conn, paths)
        conn.commit()
    return jsonify(message="已彻底删除，附件一并清理；审计日志保留该操作记录", id=doc_id)


@app.delete("/api/documents/<int:doc_id>/versions/<int:version_id>")
def delete_document_version(doc_id, version_id):
    data = request.get_json(silent=True) or {}
    reason = str(data.get("reason") or "").strip()
    operator = str(data.get("operator") or "经营计划部 · 张").strip()
    with connect() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            return jsonify(error="文档不存在"), 404
        version = conn.execute(
            "SELECT * FROM document_versions WHERE id=? AND doc_id=?", (version_id, doc_id)
        ).fetchone()
        if not version:
            return jsonify(error="版本不存在"), 404
        if version["status"] == "受控":
            raise ValueError("受控版本不能删除；如需替换请先将其他版本设为受控，再删除该历史版本")
        if version["version"] == doc["version"]:
            raise ValueError("该版本为文档当前版本，不能删除")
        conn.execute("DELETE FROM document_versions WHERE id=?", (version_id,))
        audit(conn, "删除历史版本", "document", doc_id,
              f"{version['version']}；原因：{reason or '未填写'}；操作人：{operator}")
        drop_orphan_files(conn, [version["stored_path"]])
        conn.commit()
    return jsonify(message=f"历史版本 {version['version']} 已删除", id=version_id)


@app.post("/api/documents/<int:doc_id>/versions")
def add_document_version(doc_id):
    data = body()
    with connect() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            return jsonify(error="文档不存在"), 404
        if doc["deleted_at"]:
            raise ValueError("回收站中的文档不能新增版本，请先恢复")
        if doc["status"] == "已作废":
            raise ValueError("已作废文档不能再新增版本")
        version = str(data.get("version") or "").strip() or next_version(doc["version"])
        if conn.execute("SELECT 1 FROM document_versions WHERE doc_id=? AND version=?", (doc_id, version)).fetchone():
            raise ValueError(f"版本 {version} 已存在，请使用其他版本号")
        raw = None
        sha256 = doc["sha256"]
        stored_path = doc["stored_path"]
        original_filename = doc["original_filename"]
        content_text = doc["content_text"]
        if data.get("content_base64"):
            raw = base64.b64decode(data["content_base64"], validate=True)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("附件不能超过 8MB")
            sha256 = hashlib.sha256(raw).hexdigest()
            original_filename = Path(str(data.get("original_filename", "upload.bin"))).name
            content_text = extract_text(raw, original_filename)
            stored_path = new_document_upload_relpath(doc["category"], original_filename)
            target = UPLOAD_DIR / stored_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        conn.execute("UPDATE document_versions SET status='历史' WHERE doc_id=? AND status='受控'", (doc_id,))
        cur = conn.execute(
            """INSERT INTO document_versions(doc_id,version,status,stored_path,original_filename,sha256,file_size,change_note,created_by,created_at)
               VALUES(?,?,'受控',?,?,?,?,?,?,?)""",
            (doc_id, version, stored_path, original_filename, sha256, len(raw) if raw else doc["file_size"],
             str(data.get("change_note", "")), str(data.get("created_by", "")), now_text()),
        )
        conn.execute(
            """UPDATE documents SET version=?,version_status='受控',stored_path=?,original_filename=?,sha256=?,
                   file_size=?,content_text=?,updated_at=? WHERE id=?""",
            (version, stored_path, original_filename, sha256, len(raw) if raw else doc["file_size"],
             content_text, now_text(), doc_id),
        )
        audit(conn, "新增文档版本", "document", doc_id, f"{doc['version']} → {version}")
        conn.commit()
        row = conn.execute("SELECT * FROM document_versions WHERE id=?", (cur.lastrowid,)).fetchone()
    payload = as_dict(row)
    payload["message"] = f"新版本 {version} 已入库并设为受控版本"
    return jsonify(payload), 201


@app.get("/api/document-versions")
def list_document_versions():
    doc_id = request.args.get("doc_id", type=int)
    sql = """SELECT v.*, d.name doc_name, d.category, d.security_level, d.version current_version
             FROM document_versions v JOIN documents d ON d.id=v.doc_id WHERE 1=1"""
    params = []
    if doc_id:
        sql += " AND v.doc_id=?"
        params.append(doc_id)
    sql += " ORDER BY d.name, v.id DESC"
    with connect() as conn:
        items = as_list(conn.execute(sql, params).fetchall())
        controlled = conn.execute(
            "SELECT COUNT(*) c FROM document_versions WHERE status='受控'"
        ).fetchone()["c"]
    return jsonify(items=items, total=len(items), controlled=controlled)


@app.post("/api/documents/<int:doc_id>/versions/<int:version_id>/control")
def control_document_version(doc_id, version_id):
    data = body()
    reason = str(data.get("reason") or "手工设为受控版本").strip()
    with connect() as conn:
        version = conn.execute(
            "SELECT * FROM document_versions WHERE id=? AND doc_id=?", (version_id, doc_id)
        ).fetchone()
        if not version:
            return jsonify(error="版本不存在"), 404
        if version["status"] == "作废":
            raise ValueError("已作废版本不能设为受控版本")
        conn.execute("UPDATE document_versions SET status='历史' WHERE doc_id=? AND status='受控'", (doc_id,))
        conn.execute("UPDATE document_versions SET status='受控' WHERE id=?", (version_id,))
        conn.execute(
            """UPDATE documents SET version=?,version_status='受控',stored_path=?,original_filename=?,sha256=?,
                   file_size=?,updated_at=? WHERE id=?""",
            (version["version"], version["stored_path"], version["original_filename"], version["sha256"],
             version["file_size"], now_text(), doc_id),
        )
        audit(conn, "切换受控版本", "document", doc_id, f"→ {version['version']}；理由：{reason}")
        conn.commit()
    return jsonify(message=f"受控版本已切换为 {version['version']}，原版本转为历史版本")


@app.post("/api/documents/<int:doc_id>/reference")
def reference_document(doc_id):
    data = body()
    bid_id = int(data.get("bid_id") or 0)
    section_code = str(data.get("section_code") or "").strip()
    user = str(data.get("user") or "经营计划部 · 张").strip()
    with connect() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            return jsonify(error="文档不存在"), 404
        if doc["deleted_at"]:
            raise ValueError("回收站中的文档不能引用，请先恢复")
        section = None
        if bid_id:
            bid = conn.execute("SELECT * FROM bids WHERE id=?", (bid_id,)).fetchone()
            if not bid:
                return jsonify(error="标书不存在"), 404
            if section_code:
                section = conn.execute(
                    "SELECT * FROM sections WHERE bid_id=? AND code=?", (bid_id, section_code)
                ).fetchone()
        conn.execute(
            "INSERT INTO document_refs(doc_id,bid_id,section_id,section_code,user,created_at) VALUES(?,?,?,?,?,?)",
            (doc_id, bid_id or None, section["id"] if section else None, section_code, user, now_text()),
        )
        conn.execute(
            """UPDATE documents SET reference_count=reference_count+1, access_count=access_count+1,
                   last_access_at=?, status=CASE WHEN status='待评估' THEN '正常' ELSE status END WHERE id=?""",
            (now_text(), doc_id),
        )
        if bid_id:
            conn.execute(
                "INSERT INTO materials(bid_id,name,similarity,source,updated_at) VALUES(?,?,?,?,?)",
                (bid_id, doc["name"], 80, f"文档库引用 · {doc['department']}", now_text()),
            )
        audit(conn, "引用文档", "document", doc_id, f"标书 {bid_id or '未指定'} · {section_code or '未指定章节'}")
        conn.commit()
    return jsonify(message="已记录引用并加入当前标书素材推荐", bid_id=bid_id, section_code=section_code)


@app.post("/api/documents/<int:doc_id>/security-requests")
def create_security_request(doc_id):
    data = body()
    kind = str(data.get("kind") or "降密")
    if kind not in ("降密", "外发"):
        raise ValueError("申请类型只能为降密或外发")
    reason = str(data.get("reason") or "").strip()
    if not reason:
        raise ValueError("请填写申请理由")
    with connect() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            return jsonify(error="文档不存在"), 404
        if doc["deleted_at"]:
            raise ValueError("回收站中的文档不能提交密级申请")
        from_level = doc["security_level"]
        to_level = str(data.get("to_level") or from_level)
        if kind == "降密":
            if to_level not in SECURITY_LEVELS:
                raise ValueError("目标密级无效")
            if SECURITY_RANK[to_level] >= SECURITY_RANK[from_level]:
                raise ValueError("降密目标级别必须低于当前密级")
        if conn.execute(
            "SELECT 1 FROM security_requests WHERE doc_id=? AND kind=? AND status='待审批'", (doc_id, kind)
        ).fetchone():
            raise ValueError("该文档已有同类申请在审批中")
        cur = conn.execute(
            """INSERT INTO security_requests(doc_id,kind,from_level,to_level,reason,applicant,status,created_at)
               VALUES(?,?,?,?,?,?,'待审批',?)""",
            (doc_id, kind, from_level, to_level, reason, str(data.get("applicant") or "经营计划部 · 张"), now_text()),
        )
        audit(conn, f"提交{kind}申请", "document", doc_id, f"{from_level}→{to_level}；{reason}")
        conn.commit()
    return jsonify(id=cur.lastrowid, message=f"{kind}申请已提交，待安全保密管理员审批"), 201


@app.get("/api/security-requests")
def list_security_requests():
    status = request.args.get("status", "")
    sql = """SELECT r.*, d.name doc_name, d.security_level, d.category, d.department
             FROM security_requests r JOIN documents d ON d.id=r.doc_id WHERE 1=1"""
    params = []
    if status:
        sql += " AND r.status=?"
        params.append(status)
    sql += " ORDER BY CASE r.status WHEN '待审批' THEN 0 ELSE 1 END, r.id DESC"
    with connect() as conn:
        items = as_list(conn.execute(sql, params).fetchall())
        pending = conn.execute("SELECT COUNT(*) c FROM security_requests WHERE status='待审批'").fetchone()["c"]
    return jsonify(items=items, total=len(items), pending=pending)


@app.post("/api/security-requests/<int:req_id>/decision")
def decide_security_request(req_id):
    data = body()
    approve = bool(data.get("approve"))
    approver = str(data.get("approver") or "安全保密管理员").strip()
    note = str(data.get("note") or "").strip()
    with connect() as conn:
        req = conn.execute("SELECT * FROM security_requests WHERE id=?", (req_id,)).fetchone()
        if not req:
            return jsonify(error="申请不存在"), 404
        if req["status"] != "待审批":
            raise ValueError("该申请已处理，不能重复审批")
        conn.execute(
            "UPDATE security_requests SET status=?, approver=?, decided_at=? WHERE id=?",
            ("已批准" if approve else "已驳回", approver, now_text(), req_id),
        )
        if approve and req["kind"] == "降密":
            conn.execute("UPDATE documents SET security_level=? WHERE id=?", (req["to_level"], req["doc_id"]))
        audit(conn, "审批密级申请", "document", req["doc_id"],
              f"{req['kind']}：{'批准' if approve else '驳回'}；{note}")
        conn.commit()
    return jsonify(message="审批完成：" + ("已批准" if approve else "已驳回"))


@app.post("/api/documents/<int:doc_id>/review")
def review_document(doc_id):
    data = body()
    decision = str(data.get("decision") or "")
    if decision not in ("续用", "归档", "作废"):
        raise ValueError("处置结论只能为续用、归档或作废")
    note = str(data.get("note") or "").strip()
    with connect() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            return jsonify(error="文档不存在"), 404
        if doc["deleted_at"]:
            raise ValueError("回收站中的文档不能处置，请先恢复")
        if decision == "续用":
            conn.execute("UPDATE documents SET status='正常',last_access_at=? WHERE id=?", (now_text(), doc_id))
        elif decision == "归档":
            conn.execute("UPDATE documents SET status='已归档' WHERE id=?", (doc_id,))
        else:
            conn.execute("UPDATE documents SET status='已作废',version_status='作废' WHERE id=?", (doc_id,))
            conn.execute("UPDATE document_versions SET status='作废' WHERE doc_id=?", (doc_id,))
        audit(conn, f"僵尸文档处置：{decision}", "document", doc_id, note or "—")
        conn.commit()
    return jsonify(message=f"已按“{decision}”处置，处置记录写入审计日志")


@app.get("/api/certificates")
def list_certificates():
    q = request.args.get("q", "").strip()
    category = request.args.get("category", "")
    state = request.args.get("state", "")
    trash = request.args.get("trash", "") in ("1", "true")
    sql = "SELECT * FROM certificates WHERE 1=1"
    params = []
    sql += " AND deleted_at IS NOT NULL" if trash else " AND deleted_at IS NULL"
    if q:
        sql += " AND (name LIKE ? OR certificate_no LIKE ? OR issuer LIKE ? OR owner LIKE ? OR scope LIKE ?)"
        params.extend([f"%{q}%"] * 5)
    if category:
        sql += " AND category=?"
        params.append(category)
    sql += " ORDER BY expiry_date,id"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = [cert_view(r) for r in rows]
    if state:
        if state == "临期":
            items = [i for i in items if i["days_left"] is not None and 0 <= i["days_left"] <= 90]
        elif state == "已过期":
            items = [i for i in items if i["days_left"] is not None and i["days_left"] < 0]
        elif state == "有效":
            items = [i for i in items if i["days_left"] is not None and i["days_left"] > 90]
        elif state == "已作废":
            items = [i for i in items if i.get("status") == "已作废"]
    return jsonify(items=items, total=len(items))


@app.get("/api/certificates/meta")
def certificates_meta():
    with connect() as conn:
        rows = conn.execute("SELECT * FROM certificates").fetchall()
    items = [cert_view(r) for r in rows]
    live = [i for i in items if not i["is_deleted"] and i.get("status") != "已作废"]
    return jsonify(stats={
        "total": len(live),
        "valid": sum(1 for i in live if i["days_left"] is not None and i["days_left"] > 90),
        "expiring": sum(1 for i in live if i["days_left"] is not None and 0 <= i["days_left"] <= 90),
        "expired": sum(1 for i in live if i["days_left"] is not None and i["days_left"] < 0),
        "voided": sum(1 for i in items if i.get("status") == "已作废" and not i["is_deleted"]),
        "with_attachment": sum(1 for i in live if i["has_attachment"]),
        "trash": sum(1 for i in items if i["is_deleted"]),
    }, categories=sorted({i["category"] for i in live if i["category"]}))


@app.get("/api/certificates/<int:cert_id>")
def certificate_detail(cert_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM certificates WHERE id=?", (cert_id,)).fetchone()
        if not row:
            return jsonify(error="资质不存在"), 404
        logs = as_list(conn.execute(
            "SELECT * FROM audit_logs WHERE entity_type='certificate' AND entity_id=? ORDER BY id DESC LIMIT 20",
            (cert_id,),
        ).fetchall())
    item = cert_view(row)
    item["audit_logs"] = logs
    return jsonify(item)


def _store_upload(data, max_mb=8):
    """保存 base64 附件到上传目录，返回 (stored_path, original_filename, sha256, size)。"""
    content = data.get("content_base64")
    if not content:
        return None, None, None, None
    raw = base64.b64decode(content, validate=True)
    if len(raw) > max_mb * 1024 * 1024:
        raise ValueError(f"附件不能超过 {max_mb}MB")
    original = Path(str(data.get("original_filename", "upload.bin"))).name
    suffix = Path(original).suffix[:12]
    target = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    target.write_bytes(raw)
    return target.name, original, hashlib.sha256(raw).hexdigest(), len(raw)


CERT_EDITABLE = ("name", "certificate_no", "issuer", "expiry_date", "owner", "category", "scope", "remark", "status")


def _safe_unlink(stored_path):
    """尽力删除上传目录中的附件文件；Windows 上句柄未释放时重试，失败不阻断主流程。"""
    if not stored_path:
        return False
    path = (UPLOAD_DIR / stored_path).resolve()
    if UPLOAD_DIR.resolve() not in path.parents or not path.exists():
        return False
    for _ in range(3):
        try:
            path.unlink()
            return True
        except PermissionError:
            time.sleep(0.2)
    return False


@app.post("/api/certificates")
def create_certificate():
    data = body()
    required = ["name", "certificate_no", "issuer", "expiry_date", "owner"]
    if any(not str(data.get(k, "")).strip() for k in required):
        raise ValueError("证书名称、编号、发证机关、到期日和责任人均不能为空")
    if not parse_iso_day(data["expiry_date"]):
        raise ValueError("到期日格式应为 YYYY-MM-DD")
    stored_path, original, sha, size = _store_upload(data)
    now = now_text()
    with connect() as conn:
        dup = conn.execute(
            "SELECT id,name FROM certificates WHERE certificate_no=? AND deleted_at IS NULL",
            (str(data["certificate_no"]).strip(),),
        ).fetchone()
        if dup:
            return jsonify(error=f"证书编号已存在（{dup['name']}），同一证书请勿重复登记"), 409
        cur = conn.execute(
            """INSERT INTO certificates(name,certificate_no,issuer,expiry_date,owner,category,scope,remark,
               original_filename,stored_path,sha256,file_size,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?, '正常', ?, ?)""",
            (
                str(data["name"]).strip(), str(data["certificate_no"]).strip(), str(data["issuer"]).strip(),
                str(data["expiry_date"]).strip(), str(data["owner"]).strip(),
                str(data.get("category") or "").strip(), str(data.get("scope") or "").strip(),
                str(data.get("remark") or "").strip(), original, stored_path, sha, size, now, now,
            ),
        )
        audit(conn, "新增资质", "certificate", cur.lastrowid,
              f"{data['name']} · 到期 {data['expiry_date']}" + (" · 已上传扫描件" if stored_path else " · 未上传扫描件"))
        conn.commit()
    return jsonify(id=cur.lastrowid, message="资质已入库" + ("，扫描件已保存" if stored_path else "")), 201


@app.patch("/api/certificates/<int:cert_id>")
def update_certificate(cert_id):
    data = body()
    with connect() as conn:
        cert = conn.execute("SELECT * FROM certificates WHERE id=?", (cert_id,)).fetchone()
        if not cert:
            return jsonify(error="资质不存在"), 404
        if cert["deleted_at"]:
            raise ValueError("回收站中的资质不能修改，请先恢复")
        if cert["status"] == "已作废" and any(
            k in data and k != "status" and str(data[k]).strip() != str(cert[k] or "")
            for k in CERT_EDITABLE
        ):
            raise ValueError("已作废资质为受控记录，不能修改；如需继续使用请先将状态恢复为“正常”")
        changes = []
        for field in CERT_EDITABLE:
            if field in data and str(data[field]).strip() != str(cert[field] or ""):
                if field == "expiry_date" and not parse_iso_day(str(data[field]).strip()):
                    raise ValueError("到期日格式应为 YYYY-MM-DD")
                changes.append(f"{field}：{cert[field] or '—'} → {str(data[field]).strip()}")
        new_file = None
        if data.get("content_base64"):
            stored_path, original, sha, size = _store_upload(data)
            new_file = (stored_path, original, sha, size)
        if not changes and not new_file:
            raise ValueError("没有需要修改的内容")
        if changes:
            fields = ", ".join(f"{f}=?" for f in (ch.split("：", 1)[0] for ch in changes))
            params = [str(data[f]).strip() for f in (ch.split("：", 1)[0] for ch in changes)]
            conn.execute(f"UPDATE certificates SET {fields} WHERE id=?", (*params, cert_id))
        old_path = cert["stored_path"]
        if new_file:
            conn.execute(
                """UPDATE certificates SET original_filename=?,stored_path=?,sha256=?,file_size=?
                   WHERE id=?""",
                (new_file[1], new_file[0], new_file[2], new_file[3], cert_id),
            )
            if old_path:
                _safe_unlink(old_path)
        conn.execute("UPDATE certificates SET updated_at=? WHERE id=?", (now_text(), cert_id))
        audit(conn, "修改资质", "certificate", cert_id, "；".join(changes) or "更换扫描件")
        conn.commit()
    return jsonify(message="资质信息已更新，变更记录写入审计日志")


@app.delete("/api/certificates/<int:cert_id>")
def delete_certificate(cert_id):
    data = request.get_json(silent=True) or {}
    confirm = str(data.get("confirm", "")).strip()
    reason = str(data.get("reason", "")).strip()
    operator = str(data.get("operator", "")).strip() or "当前用户"
    with connect() as conn:
        cert = conn.execute("SELECT * FROM certificates WHERE id=?", (cert_id,)).fetchone()
        if not cert:
            return jsonify(error="资质不存在"), 404
        if confirm == "彻底删除":
            if not cert["deleted_at"]:
                raise ValueError("彻底删除仅限回收站中的资质，请先移入回收站")
            removed = _safe_unlink(cert["stored_path"])
            conn.execute("DELETE FROM certificates WHERE id=?", (cert_id,))
            audit(conn, "彻底删除资质", "certificate", cert_id,
                  f"{cert['name']}（{cert['certificate_no']}）· " + ("扫描件已清除" if removed else "扫描件文件暂被占用，留待定期清理")
                  + "，审计记录永久保留")
            conn.commit()
            return jsonify(message=f"资质“{cert['name']}”已彻底删除，审计记录永久保留")
        if cert["deleted_at"]:
            raise ValueError("该资质已在回收站中")
        if not reason:
            raise ValueError("移入回收站必须填写删除原因（换证合并、误登记、信息作废等）")
        conn.execute(
            "UPDATE certificates SET deleted_at=?,deleted_by=?,delete_reason=? WHERE id=?",
            (now_text(), operator, reason, cert_id),
        )
        audit(conn, "资质移入回收站", "certificate", cert_id, f"{reason} · 操作人 {operator}")
        conn.commit()
    return jsonify(message=f"资质“{cert['name']}”已移入回收站，可随时恢复")


@app.post("/api/certificates/<int:cert_id>/restore")
def restore_certificate(cert_id):
    with connect() as conn:
        cert = conn.execute("SELECT * FROM certificates WHERE id=?", (cert_id,)).fetchone()
        if not cert:
            return jsonify(error="资质不存在"), 404
        if not cert["deleted_at"]:
            raise ValueError("该资质不在回收站中")
        conn.execute("UPDATE certificates SET deleted_at=NULL,deleted_by=NULL,delete_reason='' WHERE id=?", (cert_id,))
        audit(conn, "资质从回收站恢复", "certificate", cert_id, cert["name"])
        conn.commit()
    return jsonify(message=f"资质“{cert['name']}”已恢复到台账")


def _cert_preview_target(conn, cert_id):
    cert = conn.execute("SELECT * FROM certificates WHERE id=?", (cert_id,)).fetchone()
    if not cert:
        return None, (jsonify(error="资质不存在"), 404)
    if cert["deleted_at"]:
        return None, (jsonify(error="回收站中的资质请先恢复后再查看"), 409)
    if not cert["stored_path"]:
        return None, (jsonify(error="该资质未上传扫描件，仅登记元数据", code="no_file"), 404)
    return cert, None


@app.get("/api/certificates/<int:cert_id>/preview-meta")
def certificate_preview_meta(cert_id):
    with connect() as conn:
        cert, error = _cert_preview_target(conn, cert_id)
        if error:
            return error
        path = (UPLOAD_DIR / cert["stored_path"]).resolve()
        if UPLOAD_DIR.resolve() not in path.parents or not path.exists():
            return jsonify(error="扫描件文件缺失，可能已被清理，请联系责任部门"), 404
        filename = cert["original_filename"] or ""
        kind = preview_kind(filename)
        payload = {
            "kind": kind, "filename": filename,
            "size": cert["file_size"] or path.stat().st_size,
            "mime_type": mimetypes.guess_type(filename)[0] or "application/octet-stream",
            "security_level": "内部",
            "doc_name": f"{cert['name']}（扫描件）",
            "cert_name": cert["name"], "certificate_no": cert["certificate_no"],
            "issuer": cert["issuer"], "expiry_date": cert["expiry_date"],
            "updated_at": cert["updated_at"] or cert["created_at"],
        }
        if kind == "text":
            raw = path.read_bytes()[:400000]
            for encoding in ("utf-8", "gb18030"):
                try:
                    payload["text"] = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            payload.setdefault("text", raw.decode("utf-8", errors="replace"))
        audit(conn, "查看资质扫描件", "certificate", cert_id, f"{cert['name']} · {filename}")
        conn.commit()
    return jsonify(payload)


@app.get("/api/certificates/<int:cert_id>/raw")
def certificate_raw(cert_id):
    with connect() as conn:
        cert, error = _cert_preview_target(conn, cert_id)
        if error:
            return error
        path = (UPLOAD_DIR / cert["stored_path"]).resolve()
        if UPLOAD_DIR.resolve() not in path.parents or not path.exists():
            return jsonify(error="扫描件文件缺失"), 404
    return send_file(path, as_attachment=False,
                     download_name=cert["original_filename"] or f"certificate-{cert_id}.png",
                     mimetype=mimetypes.guess_type(cert["original_filename"] or "")[0] or "application/octet-stream")


@app.get("/api/bids")
def list_bids():
    status = request.args.get("status", "")
    reactor = request.args.get("reactor", "")
    sql = "SELECT * FROM bids WHERE 1=1"
    params = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if reactor:
        sql += " AND reactor_type=?"
        params.append(reactor)
    sql += " ORDER BY due_date,id"
    with connect() as conn:
        items = as_list(conn.execute(sql, params).fetchall())
    return jsonify(items=items, total=len(items))


@app.post("/api/bids")
def create_bid():
    data = body()
    required = ["name", "owner", "reactor_type", "due_date"]
    if any(not str(data.get(k, "")).strip() for k in required):
        raise ValueError("项目名称、业主、堆型和投标截止日不能为空")
    if not parse_iso_day(data["due_date"]):
        raise ValueError("投标截止日格式应为 YYYY-MM-DD")
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO bids(name,owner,reactor_type,due_date,status,progress,page_count,deviation_total,deviation_responded,created_at,updated_at)
               VALUES(?,?,?,?,?,0,0,0,0,?,?)""",
            (data["name"].strip(), data["owner"].strip(), data["reactor_type"].strip(), data["due_date"],
             data.get("status", "资料收集"), now_text(), now_text()),
        )
        create_default_sections(conn, cur.lastrowid)
        audit(conn, "新建标书", "bid", cur.lastrowid, data["name"])
        conn.commit()
    app.logger.info("新建标书成功：id=%s，名称=%s", cur.lastrowid, data["name"].strip())
    return jsonify(id=cur.lastrowid, message="标书已创建，默认章节和评分矩阵已生成"), 201


@app.get("/api/bids/<int:bid_id>")
def bid_detail(bid_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM bids WHERE id=?", (bid_id,)).fetchone()
    return jsonify(as_dict(row)) if row else (jsonify(error="标书不存在"), 404)


@app.put("/api/bids/<int:bid_id>")
def update_bid(bid_id):
    data = body()
    allowed = {"status", "progress", "page_count", "signature_complete", "deviation_total", "deviation_responded", "due_date"}
    fields, params = [], []
    for key in allowed:
        if key in data:
            fields.append(f"{key}=?")
            params.append(data[key])
    if not fields:
        raise ValueError("没有可更新字段")
    fields.append("updated_at=?")
    params.extend([now_text(), bid_id])
    with connect() as conn:
        cur = conn.execute(f"UPDATE bids SET {','.join(fields)} WHERE id=?", params)
        if cur.rowcount == 0:
            return jsonify(error="标书不存在"), 404
        audit(conn, "更新标书", "bid", bid_id, json.dumps(data, ensure_ascii=False))
        conn.commit()
    return jsonify(message="标书信息已更新")


@app.delete("/api/bids/<int:bid_id>")
def delete_bid(bid_id):
    """删除标书：需输入项目名称确认；章节/评分/招标文件/解析结果等从属数据随外键级联删除。"""
    data = body()
    confirm = str(data.get("confirm") or "").strip()
    user = str(data.get("user") or "投标经理").strip()
    with connect() as conn:
        row = conn.execute("SELECT * FROM bids WHERE id=?", (bid_id,)).fetchone()
        if not row:
            return jsonify(error="标书不存在"), 404
        if confirm != row["name"].strip() and confirm != "删除":
            raise ValueError("请输入标书名称（或“删除”）确认删除")
        name = row["name"]
        counts = {
            "sections": conn.execute("SELECT COUNT(*) c FROM sections WHERE bid_id=?", (bid_id,)).fetchone()["c"],
            "tender_files": conn.execute("SELECT COUNT(*) c FROM tender_files WHERE bid_id=?", (bid_id,)).fetchone()["c"],
        }
        uploads = [r["stored_path"] for r in conn.execute(
            "SELECT stored_path FROM tender_files WHERE bid_id=? AND stored_path IS NOT NULL", (bid_id,)).fetchall()]
        audit(conn, "删除标书", "bid", bid_id,
              f"{name}（同时删除章节 {counts['sections']} 节、招标文件 {counts['tender_files']} 份）；操作人 {user}")
        conn.execute("DELETE FROM bids WHERE id=?", (bid_id,))
        conn.commit()
    for stored in uploads:
        try:
            path = (UPLOAD_DIR / stored).resolve()
            if UPLOAD_DIR.resolve() in path.parents and path.exists():
                path.unlink()
        except OSError as exc:
            app.logger.warning("删除标书附件失败（%s）：%s", stored, exc)
    app.logger.info("已删除标书：id=%s，名称=%s", bid_id, name)
    return jsonify(message=f"标书「{name}」已删除", id=bid_id,
                   sections=counts["sections"], tender_files=counts["tender_files"])


@app.get("/api/bids/<int:bid_id>/workspace")
def workspace(bid_id):
    with connect() as conn:
        bid = conn.execute("SELECT * FROM bids WHERE id=?", (bid_id,)).fetchone()
        if not bid:
            return jsonify(error="标书不存在"), 404
        sections = as_list(conn.execute("SELECT * FROM sections WHERE bid_id=? ORDER BY sort_order", (bid_id,)).fetchall())
        scoring = as_list(conn.execute("SELECT * FROM scoring WHERE bid_id=? ORDER BY sort_order", (bid_id,)).fetchall())
        materials = as_list(conn.execute("SELECT * FROM materials WHERE bid_id=? ORDER BY similarity DESC,id DESC", (bid_id,)).fetchall())
        # 技术规格 → ：按章节汇总"本节要回应的新要求"，供编制页对照
        tech = conn.execute(
            """SELECT ts.id, ts.file_id, ts.req_count, ts.must_count, tf.filename
               FROM tech_specs ts JOIN tender_files tf ON tf.id=ts.file_id
               WHERE ts.bid_id=? AND tf.deleted_at IS NULL ORDER BY ts.id DESC LIMIT 1""",
            (bid_id,)).fetchone()
        tech_spec = None
        if tech:
            by_section, unmapped = {}, 0
            for o in conn.execute("SELECT * FROM tech_outline WHERE spec_id=? ORDER BY seq", (tech["id"],)).fetchall():
                reqs = []
                try:
                    reqs = json.loads(o["requirements"] or "[]")
                except (TypeError, ValueError):
                    reqs = []
                if not o["section_id"]:
                    unmapped += 1
                    continue
                by_section.setdefault(o["section_id"], []).append({
                    "id": o["id"], "code": o["code"], "title": o["title"], "brief": o["brief"],
                    "req_count": o["req_count"], "must_count": o["must_count"],
                    "page_from": o["page_from"], "page_to": o["page_to"], "requirements": reqs,
                })
            # 由"插入本节/转子目录"生成的子章节，要求只在本节 content 骨架里、不在 tech_outline 映射中，
            # 这里用与编写引擎同一套装配逻辑补齐，保证"右侧要求清单"与"正文实际回应"口径一致。
            codes = [s["code"] for s in sections]
            for s in sections:
                if by_section.get(s["id"]):
                    continue
                if any(c.startswith(s["code"] + ".") for c in codes):
                    continue        # 父章节不重复列子节要求
                reqs = composer.section_requirements(conn, s)
                if not reqs:
                    continue
                pages = [r["page"] for r in reqs if r.get("page")]
                by_section[s["id"]] = [{
                    "id": None, "code": s["code"], "title": f"{s['title']}（原文要求）",
                    "brief": "本节要求来自技术规格条目生成的目录节点骨架",
                    "req_count": len(reqs), "must_count": sum(1 for r in reqs if r["must"]),
                    "page_from": min(pages) if pages else None, "page_to": max(pages) if pages else None,
                    "requirements": [{"title": r["title"], "content": r["content"], "page": r["page"],
                                      "must": r["must"], "category": r["category"]} for r in reqs],
                }]
            for s in sections:
                items = by_section.get(s["id"], [])
                s["tech_req_count"] = sum(i["req_count"] for i in items)
                s["tech_must_count"] = sum(i["must_count"] for i in items)
            tech_spec = {"id": tech["id"], "file_id": tech["file_id"], "filename": tech["filename"],
                         "req_count": tech["req_count"], "must_count": tech["must_count"],
                         "unmapped": unmapped, "by_section": [dict(section_id=k, items=v) for k, v in by_section.items()]}
    section_map = {s["code"]: s for s in sections}
    for item in scoring:
        section = section_map.get(item["section_code"])
        item["status"] = section["status"] if section else "未开始"
    return jsonify(bid=as_dict(bid), sections=sections, scoring=scoring, materials=materials,
                   tech_spec=tech_spec)


@app.put("/api/sections/<int:section_id>")
def update_section(section_id):
    data = body()
    status = str(data.get("status", "编制中"))
    if status not in ("未开始", "编制中", "已完成"):
        raise ValueError("章节状态无效")
    with connect() as conn:
        section = conn.execute("SELECT * FROM sections WHERE id=?", (section_id,)).fetchone()
        if not section:
            return jsonify(error="章节不存在"), 404
        fields, params = ["status=?", "ai_generated=0", "updated_at=?"], [status, now_text()]
        if "content_ai" in data:   # 正文（AI 编写 / 人工改写）
            fields.insert(0, "content_ai=?")
            params.insert(0, str(data.get("content_ai") or ""))
        if "content" in data:      # 原文技术要求（一般不随编辑变化）
            fields.insert(0, "content=?")
            params.insert(0, str(data.get("content") or ""))
        conn.execute(f"UPDATE sections SET {', '.join(fields)} WHERE id=?", (*params, section_id))
        progress = calculate_progress(conn, section["bid_id"])
        conn.execute("UPDATE bids SET progress=?,updated_at=? WHERE id=?", (progress, now_text(), section["bid_id"]))
        audit(conn, "保存章节", "section", section_id, f"{section['code']} · {status}")
        conn.commit()
    return jsonify(message="章节已保存", progress=progress)


@app.post("/api/sections/<int:section_id>/generate")
def generate_section(section_id):
    with connect() as conn:
        section = conn.execute("SELECT * FROM sections WHERE id=?", (section_id,)).fetchone()
        if not section:
            return jsonify(error="章节不存在"), 404
        bid = conn.execute("SELECT * FROM bids WHERE id=?", (section["bid_id"],)).fetchone()
        generated = generate_section_text(section["code"], bid)
        first_line = generated.splitlines()[0]
        content = section["content"].strip()
        if first_line not in content:
            content = (content + "\n\n" + generated).strip()
        conn.execute(
            "UPDATE sections SET content=?,status='编制中',ai_generated=1,updated_at=? WHERE id=?",
            (content, now_text(), section_id),
        )
        progress = calculate_progress(conn, section["bid_id"])
        conn.execute("UPDATE bids SET progress=?,updated_at=? WHERE id=?", (progress, now_text(), section["bid_id"]))
        audit(conn, "AI 辅助生成", "section", section_id, "本地规则模板生成，需人工终审")
        conn.commit()
    return jsonify(content=content, generated=generated, progress=progress, notice="AI 辅助内容已插入，必须人工终审")


@app.post("/api/sections/<int:section_id>/compose")
def compose_section(section_id):
    """依据目录树 + 技术规格要求调用大模型编写正文，写入 content_ai（content 保留原文要求）。"""
    data = body()
    instructions = str(data.get("instructions") or "").strip()[:500]
    with connect() as conn:
        section = conn.execute("SELECT * FROM sections WHERE id=?", (section_id,)).fetchone()
        if not section:
            return jsonify(error="章节不存在"), 404
        try:
            result = composer.compose(conn, section, instructions)
        except llm_client.LLMError as exc:
            return jsonify(error=str(exc)), 502
        text = (result.get("text") or "").strip()
        if not text:
            raise ValueError("生成内容为空，请重试或检查模型配置")
        conn.execute(
            """UPDATE sections SET content_ai=?, summary_ai=?, ai_model=?, ai_written_at=?, ai_generated=1,
               status='编制中', updated_at=? WHERE id=?""",
            (text, (result.get("summary") or "").strip(), result["model"], now_text(), now_text(), section_id))
        progress = calculate_progress(conn, section["bid_id"])
        conn.execute("UPDATE bids SET progress=?,updated_at=? WHERE id=?", (progress, now_text(), section["bid_id"]))
        action = "大模型编写正文" if result["provider"] == "llm" else "结构化生成正文"
        audit(conn, action, "section", section_id,
              f"{section['code']} {section['title']}：{result['model']}，{len(text)} 字，"
              f"回应要求 {result['requirements']} 条（★{result['must']}）")
        conn.commit()
    return jsonify(content=text, provider=result["provider"], model=result["model"],
                   fallback=result["provider"] != "llm", usage=result.get("usage") or {},
                   elapsed=result.get("elapsed"), notice=result["notice"], progress=progress,
                   requirements=result["requirements"], must=result["must"])


LLM_PROFILE_KEYS = ("name", "base_url", "model", "api_key", "temperature",
                    "max_tokens", "timeout", "disable_thinking")


def _llm_payload(data):
    return {key: data[key] for key in LLM_PROFILE_KEYS if key in data}


def _llm_config_view():
    """统一返回当前生效配置 + 全部配置清单（密钥仅掩码）。"""
    cfg = llm_client.load_config()
    return {"configured": llm_client.is_configured(cfg), "base_url": cfg["base_url"],
            "model": cfg["model"], "api_key": llm_client.masked_key(cfg),
            "temperature": cfg.get("temperature"), "active_id": llm_client.get_active_profile_id(),
            "profiles": llm_client.list_profiles(), "env_override": llm_client.has_env_override()}


@app.get("/api/llm/config")
def get_llm_config():
    return jsonify(**_llm_config_view())


@app.put("/api/llm/config")
def set_llm_config():
    """兼容旧接口：无 id 时写入当前默认配置；有 id 时更新指定配置。"""
    data = body()
    cfg = llm_client.save_config(base_url=data.get("base_url"), model=data.get("model"),
                                 api_key=data.get("api_key") or None,
                                 temperature=data.get("temperature"),
                                 name=data.get("name"), profile_id=data.get("id") or None)
    with connect() as conn:
        audit(conn, "配置大模型", "settings", None,
              f"{cfg['base_url']} · {cfg['model']}（密钥{llm_client.masked_key(cfg) or '未配置'}）")
        conn.commit()
    return jsonify(message="模型配置已保存", **_llm_config_view())


@app.post("/api/llm/profiles")
def create_llm_profile():
    data = body()
    profile = llm_client.upsert_profile(_llm_payload(data))
    with connect() as conn:
        audit(conn, "新增大模型配置", "settings", None,
              f"{profile['name']} · {profile['base_url']} · {profile['model']}"
              f"（密钥{llm_client.masked_key(profile) or '未配置'}）")
        conn.commit()
    return jsonify(message="模型配置已新增", id=profile["id"], **_llm_config_view()), 201


@app.put("/api/llm/profiles/<profile_id>")
def update_llm_profile(profile_id):
    profile = llm_client.upsert_profile(_llm_payload(body()), profile_id=profile_id)
    with connect() as conn:
        audit(conn, "修改大模型配置", "settings", None,
              f"{profile['name']} · {profile['base_url']} · {profile['model']}")
        conn.commit()
    return jsonify(message="模型配置已更新", id=profile["id"], **_llm_config_view())


@app.post("/api/llm/profiles/<profile_id>/activate")
def activate_llm_profile(profile_id):
    cfg = llm_client.set_active_profile(profile_id)
    with connect() as conn:
        audit(conn, "切换默认大模型", "settings", None, f"{cfg['base_url']} · {cfg['model']}")
        conn.commit()
    return jsonify(message="已设为默认模型", **_llm_config_view())


@app.delete("/api/llm/profiles/<profile_id>")
def delete_llm_profile(profile_id):
    llm_client.delete_profile(profile_id)
    with connect() as conn:
        audit(conn, "删除大模型配置", "settings", None, f"配置 ID {profile_id}")
        conn.commit()
    return jsonify(message="模型配置已删除", **_llm_config_view())


@app.post("/api/llm/test")
def test_llm():
    """测试默认配置；带 id 测已存配置，带表单字段则测草稿（均不改动当前生效配置）。"""
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("请求体必须为 JSON 对象")
    payload = _llm_payload(data)
    profile_id = data.get("id") or None
    cfg = None
    if profile_id:
        base = llm_client.profile_config(profile_id)
        cfg = llm_client.overlay_config(base, payload) if payload else base
    elif payload:
        cfg = llm_client.config_from_values(payload)
    try:
        result = llm_client.probe(cfg=cfg) if cfg is not None else llm_client.probe()
    except (llm_client.LLMError, ValueError) as exc:
        return jsonify(ok=False, error=str(exc)), 400
    # probe() 返回值已含 ok=True，此处不能重复传 ok（jsonify 关键字冲突）
    return jsonify(**result)


@app.post("/api/bids/<int:bid_id>/compose-agent")
def compose_agent_start(bid_id):
    """技术标编写 Agent：规划→逐节生成（退避重试+自检重写）→落库→对齐审核。

    后台线程执行（同标书同一时刻只允许一个任务），接口立即返回 job_id，
    前端轮询 GET /compose-agent/<job_id> 展示进度，不再挂长请求。
    """
    import threading
    data = body()
    group = str(data.get("group_name") or "技术标")
    overwrite = bool(data.get("overwrite"))
    instructions = str(data.get("instructions") or "").strip()[:500]
    section_ids = data.get("section_ids") or None
    if section_ids is not None:
        try:
            section_ids = [int(x) for x in section_ids]
        except (TypeError, ValueError):
            return jsonify(error="section_ids 格式无效"), 400
    with connect() as conn:
        bid = conn.execute("SELECT * FROM bids WHERE id=?", (bid_id,)).fetchone()
        if not bid:
            return jsonify(error="标书不存在"), 404
        if not llm_client.is_configured():
            return jsonify(error="未配置大模型密钥；请先在「模型配置」中完成配置后再启动编写 Agent"), 400
        running = conn.execute(
            """SELECT id,done_n,total FROM compose_jobs
               WHERE bid_id=? AND status IN ('running','canceling','pausing')""",
            (bid_id,)).fetchone()
        if running:
            return jsonify(error=f"已有编写任务在执行（#{running['id']}，{running['done_n']}/{running['total']}），"
                                 "请等待完成或取消后再发起"), 409
        planned = tech_bid_agent.plan(conn, bid_id, group, overwrite, section_ids)
        if not planned["sections"]:
            return jsonify(error="没有待编写的章节（全部已写或无技术要求）；如需重写请勾选「覆盖已有正文」"), 400
        cur = conn.execute(
            """INSERT INTO compose_jobs(bid_id,group_name,status,total,skipped_n,instructions,
                                        model,started_at,overwrite,section_ids)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (bid_id, group, "running", len(planned["sections"]), len(planned["skipped"]),
             instructions, llm_client.load_config().get("model") or "", now_text(),
             int(overwrite), json.dumps(section_ids) if section_ids is not None else ""))
        job_id = cur.lastrowid
        conn.commit()
    thread = threading.Thread(target=tech_bid_agent.run_job,
                              args=(bid_id, job_id, group, overwrite, instructions, section_ids),
                              daemon=True, name=f"compose-agent-{job_id}")
    thread.start()
    cfg = llm_client.load_config()
    no_think = llm_client.thinking_disabled(cfg)
    n = len(planned["sections"])
    minutes = max(1, round(planned["estimate_seconds"] / 60))
    warn = (f"；待写 {n} 节属大批量任务，建议先用「仅编写当前选中章节」验证效果" if n > 30 else "")
    return jsonify(job_id=job_id, total=n, skipped=planned["skipped"],
                   estimate_seconds=planned["estimate_seconds"],
                   model=cfg["model"], thinking_off=no_think,
                   message=f"Agent 已启动：待写 {n} 节，预计 {minutes} 分钟"
                           + (f"（{cfg['model']} 已自动关闭深度思考，避免非流式长文超时）" if no_think else "")
                           + warn + "；可离开页面，回来继续查看"), 201


@app.get("/api/bids/<int:bid_id>/compose-agent/<int:job_id>")
def compose_agent_status(bid_id, job_id):
    with connect() as conn:
        view = tech_bid_agent.job_view(conn, bid_id, job_id)
    if not view:
        return jsonify(error="任务不存在"), 404
    return jsonify(view)


@app.get("/api/bids/<int:bid_id>/compose-agent")
def compose_agent_latest(bid_id):
    with connect() as conn:
        row = conn.execute("SELECT id FROM compose_jobs WHERE bid_id=? ORDER BY id DESC LIMIT 1",
                           (bid_id,)).fetchone()
        view = tech_bid_agent.job_view(conn, bid_id, row["id"]) if row else None
    return jsonify(job=view)


@app.post("/api/bids/<int:bid_id>/compose-agent/<int:job_id>/pause")
def compose_agent_pause(bid_id, job_id):
    """请求暂停：把状态置 pausing，后台线程在当前章节写完后落 paused 并退出。

    与 cancel 的区别：paused 任务保留完整任务单，可从剩余章节「继续编写」。
    """
    with connect() as conn:
        row = conn.execute("SELECT status FROM compose_jobs WHERE id=? AND bid_id=?",
                           (job_id, bid_id)).fetchone()
        if not row:
            return jsonify(error="任务不存在"), 404
        if row["status"] != "running":
            return jsonify(error="任务不在运行中"), 400
        conn.execute("UPDATE compose_jobs SET status='pausing' WHERE id=?", (job_id,))
        conn.commit()
    return jsonify(message="已请求暂停：当前章节写完后暂停，已完成部分保留，可点「继续编写」续跑")


@app.post("/api/bids/<int:bid_id>/compose-agent/<int:job_id>/resume")
def compose_agent_resume(bid_id, job_id):
    """从 paused / interrupted 续跑：复用同一任务单，只写尚未处理的章节。"""
    import threading
    with connect() as conn:
        row = conn.execute("SELECT status,group_name FROM compose_jobs WHERE id=? AND bid_id=?",
                           (job_id, bid_id)).fetchone()
        if not row:
            return jsonify(error="任务不存在"), 404
        if row["status"] not in ("paused", "interrupted"):
            return jsonify(error="只有已暂停或已中断的任务可以续跑"), 400
        running = conn.execute(
            """SELECT id FROM compose_jobs WHERE bid_id=? AND id<>?
               AND status IN ('running','canceling','pausing')""", (bid_id, job_id)).fetchone()
        if running:
            return jsonify(error=f"已有编写任务在执行（#{running['id']}），请先暂停或结束它"), 409
        conn.execute(
            """UPDATE compose_jobs SET status='running', finished_at=NULL, error=NULL,
               current_code=NULL, current_title=NULL WHERE id=?""", (job_id,))
        conn.commit()
    thread = threading.Thread(target=tech_bid_agent.run_job,
                              kwargs=dict(bid_id=bid_id, job_id=job_id, resume=True),
                              daemon=True, name=f"compose-agent-resume-{job_id}")
    thread.start()
    return jsonify(job_id=job_id, message="已继续编写：只写尚未处理的章节，已写正文不会重写")


@app.post("/api/bids/<int:bid_id>/compose-agent/<int:job_id>/cancel")
def compose_agent_cancel(bid_id, job_id):
    with connect() as conn:
        row = conn.execute("SELECT status FROM compose_jobs WHERE id=? AND bid_id=?",
                           (job_id, bid_id)).fetchone()
        if not row:
            return jsonify(error="任务不存在"), 404
        if row["status"] in ("paused", "interrupted"):
            # 线程已退出，直接定格任务单，避免残留任务一直挂在列表里
            conn.execute("UPDATE compose_jobs SET status='canceled', finished_at=?, error=? WHERE id=?",
                         (now_text(), "用户结束任务（已完成部分保留）", job_id))
            conn.commit()
            return jsonify(message="任务已结束（已完成部分保留）")
        if row["status"] != "running":
            return jsonify(error="任务不在运行中"), 400
        conn.execute("UPDATE compose_jobs SET status='canceling' WHERE id=?", (job_id,))
        conn.commit()
    return jsonify(message="已请求结束：当前章节写完后停止，已完成部分保留")


@app.post("/api/bids/<int:bid_id>/compose-all")
def compose_all_sections(bid_id):
    """按目录顺序批量编写技术标：只挑"有技术要求且尚未编写"的章节，逐节调模型并落库。

    overwrite=False（默认）时跳过已有正文的章节，避免覆盖人工修改；
    单节失败不中断，返回逐节结果，由人工复核。
    """
    data = body()
    overwrite = bool(data.get("overwrite"))
    group = str(data.get("group_name") or "技术标")
    with connect() as conn:
        bid = conn.execute("SELECT * FROM bids WHERE id=?", (bid_id,)).fetchone()
        if not bid:
            return jsonify(error="标书不存在"), 404
        if not llm_client.is_configured():
            return jsonify(error="未配置大模型密钥，批量编写会退化为本地模板；请先在「模型配置」中完成配置"), 400
        sections = conn.execute(
            "SELECT * FROM sections WHERE bid_id=? AND group_name=? ORDER BY sort_order",
            (bid_id, group)).fetchall()
        done, skipped, failed = [], [], []
        for section in sections:
            reqs = composer.section_requirements(conn, section)
            if not reqs:                       # 无技术要求的章节（纯目录节点）不写，避免空话
                skipped.append({"code": section["code"], "title": section["title"], "reason": "本节无技术要求"})
                continue
            if (section["content_ai"] or "").strip() and not overwrite:
                skipped.append({"code": section["code"], "title": section["title"], "reason": "已有正文（未覆盖）"})
                continue
            try:
                result = composer.compose(conn, section)
                text = (result.get("text") or "").strip()
                if not text:
                    raise ValueError("生成内容为空")
            except (llm_client.LLMError, ValueError) as exc:
                failed.append({"code": section["code"], "title": section["title"], "error": str(exc)[:160]})
                continue
            conn.execute(
                """UPDATE sections SET content_ai=?, summary_ai=?, ai_model=?, ai_written_at=?, ai_generated=1,
                   status='编制中', updated_at=? WHERE id=?""",
                (text, (result.get("summary") or "").strip(), result["model"], now_text(), now_text(), section["id"]))
            conn.commit()                      # 逐节提交，中途失败不影响已写内容
            done.append({"code": section["code"], "title": section["title"], "chars": len(text),
                         "requirements": result["requirements"], "must": result["must"],
                         "model": result["model"]})
        progress = calculate_progress(conn, bid_id)
        conn.execute("UPDATE bids SET progress=?,updated_at=? WHERE id=?", (progress, now_text(), bid_id))
        audit(conn, "批量编写技术标", "bid", bid_id,
              f"生成 {len(done)} 节，跳过 {len(skipped)} 节，失败 {len(failed)} 节")
        conn.commit()
    return jsonify(message=f"批量编写完成：生成 {len(done)} 节，跳过 {len(skipped)} 节，失败 {len(failed)} 节",
                   done=done, skipped=skipped, failed=failed, progress=progress)


@app.get("/api/bids/<int:bid_id>/tech-audit")
def bid_tech_audit(bid_id):
    """技术目录树 ↔ 原技术规格书对齐审核：逐节列出未响应/指标缺失/未映射等审核项。"""
    spec_id = request.args.get("spec_id", type=int)
    with connect() as conn:
        report = tech_audit.audit(conn, bid_id, spec_id)
    if report is None:
        return jsonify(error="标书不存在"), 404
    return jsonify(report)


@app.post("/api/bids/<int:bid_id>/tech-audit/mark")
def mark_tech_audit(bid_id):
    """审核项签认：confirmed 确认无误 / ignored 忽略 / open 重开，留痕到审计日志。"""
    data = body()
    item_key = str(data.get("item_key") or "").strip()[:120]
    if not item_key:
        raise ValueError("缺少审核项标识")
    verdict = str(data.get("verdict") or "confirmed")
    note = str(data.get("note") or "").strip()
    user = str(data.get("user") or "投标经理 · 演示")
    with connect() as conn:
        bid = conn.execute("SELECT name FROM bids WHERE id=?", (bid_id,)).fetchone()
        if not bid:
            return jsonify(error="标书不存在"), 404
        message = tech_audit.mark(conn, bid_id, item_key, verdict, note, user)
        action = {"confirmed": "确认审核项", "ignored": "忽略审核项", "open": "重开审核项"}.get(verdict, "确认审核项")
        audit(conn, action, "bid", bid_id, f"{item_key}{('：' + note) if note else ''}")
        conn.commit()
        report = tech_audit.audit(conn, bid_id)
    return jsonify(message=message, report=report)


@app.post("/api/bids/<int:bid_id>/check")
def check_bid(bid_id):
    with connect() as conn:
        result = run_compliance(conn, bid_id)
    return jsonify(result) if result else (jsonify(error="标书不存在"), 404)


@app.get("/api/bids/<int:bid_id>/check")
def latest_check(bid_id):
    with connect() as conn:
        row = conn.execute("SELECT result_json FROM compliance_runs WHERE bid_id=? ORDER BY id DESC LIMIT 1", (bid_id,)).fetchone()
    if not row:
        return jsonify(score=None, checks=[], summary="尚未执行合规校验")
    return jsonify(json.loads(row["result_json"]))


@app.post("/api/drawio/render")
def render_drawio():
    """把 drawio 配置渲染成 base64 PNG，供编制页/全书预览前端内嵌成图。

    请求体：drawio 块的原始 JSON 配置本身（不是 Markdown 文本）。
    """
    from drawio_bridge import render_drawio_png_base64

    data = request.get_json(silent=True)
    uri = render_drawio_png_base64(json.dumps(data, ensure_ascii=False)) if isinstance(data, dict) else None
    if uri is None:
        return jsonify(error="架构图配置无效或渲染失败"), 400
    return jsonify(image=uri)


def _export_docx(bid_id, bid, sections):
    """整本 Markdown → Word：调用用户提供的 WordChapter.DocumentConverter。

    Mermaid 代码块由 mmdc 渲染成 PNG 后嵌入 docx；drawio 配置块由 drawio_bridge
    预先渲染成 PNG 并替换为图片引用；转换完成再 PrettifyWord
    （表格边框/表头底纹/图片居中限幅）。中间文件全部落在临时目录，不污染项目根。
    """
    try:
        import pypandoc
        import docx
        from WordChapter import DocumentConverter
        from drawio_bridge import expand_drawio_blocks
    except ImportError as exc:
        return jsonify(error=f"Word 转换依赖未安装（{exc}）"), 503
    try:
        pypandoc.get_pandoc_version()
    except Exception as exc:  # noqa: BLE001 - pypandoc 找不到 Pandoc 时异常类型不固定
        return jsonify(error=f"未检测到可用的 Pandoc：{exc}"), 503
    if not DOCX_REFERENCE_DOC.exists():
        return jsonify(error=f"Word 参考模板缺失：{DOCX_REFERENCE_DOC}"), 503

    md = render_bid_markdown(bid, sections)
    target = BASE_DIR / "data" / f"bid_{bid_id}_preview.docx"
    with tempfile.TemporaryDirectory(prefix="xnic_docx_") as tmp:
        md = expand_drawio_blocks(md, tmp)
        src = Path(tmp) / "book.md"
        src.write_text(md, encoding="utf-8")
        out = Path(tmp) / "book.docx"
        origin = os.getcwd()
        # convert_markdown_with_mermaid 内部固定写/读相对路径 temp.md；
        # 进程级 cwd 必须串行，避免并发导出互相覆盖（项目其余路径均走 BASE_DIR 绝对路径）。
        with _DOCX_LOCK:
            try:
                os.chdir(tmp)
                ok = DocumentConverter().convert_markdown_with_mermaid(str(src), str(out))
            finally:
                os.chdir(origin)
        if not ok or not out.exists():
            return jsonify(error="Word 转换失败（Pandoc / Mermaid 渲染异常），请查看服务日志"), 500
        document = docx.Document(str(out))
        DocumentConverter().PrettifyWord(document)
        document.save(str(target))
    return send_file(target, as_attachment=True,
                     download_name=f"标书预审版_{bid_id}.docx", mimetype=DOCX_MIME)


@app.get("/api/bids/<int:bid_id>/export")
def export_bid(bid_id):
    """预审版导出：默认 Markdown（.md，便于后期转 Word）。

    `?format=docx` 由 WordChapter 转 Word（Mermaid 自动成图）；
    `?format=html` 保留旧 HTML 版；`?format=md&inline=1` 返回 JSON 供「全书预览」渲染。
    """
    with connect() as conn:
        bid = conn.execute("SELECT * FROM bids WHERE id=?", (bid_id,)).fetchone()
        if not bid:
            return jsonify(error="标书不存在"), 404
        sections = conn.execute("SELECT * FROM sections WHERE bid_id=? ORDER BY sort_order",
                                (bid_id,)).fetchall()
    fmt = str(request.args.get("format") or "md").lower()
    if fmt == "md":
        md = render_bid_markdown(bid, sections)
        if request.args.get("inline"):
            return jsonify(markdown=md, name=bid["name"], format="md",
                           sections=len(sections))
        target = BASE_DIR / "data" / f"bid_{bid_id}_preview.md"
        target.write_text(md, encoding="utf-8")
        return send_file(target, as_attachment=True,
                         download_name=f"标书预审版_{bid_id}.md",
                         mimetype="text/markdown; charset=utf-8")
    if fmt == "docx":
        return _export_docx(bid_id, bid, sections)
    content = [f"<h1>{html.escape(bid['name'])}</h1>",
               f"<p>业主：{html.escape(bid['owner'])}　堆型：{html.escape(bid['reactor_type'])}　版本：预审版</p><hr>"]
    for section in sections:
        content.append(f"<h2>{html.escape(section['code'])} {html.escape(section['title'])}</h2>")
        body = section["content_ai"] or ""
        if not body:
            raw = section["content"] or ""
            body = "【本节正文待编制】" if "【本节编写要点】" in raw or "【技术规格要求" in raw else raw or "【本节待编制】"
        # 代码块统一拆分：mermaid 交页面 JS 渲染；drawio 由 drawio_bridge 渲染成
        # base64 PNG 内嵌，HTML 自包含且无需落盘静态图片。
        from drawio_bridge import render_drawio_png_base64
        parts = re.split(r"(?ms)^```(mermaid|drawio)[ \t]*\r?\n(.*?)^```[ \t]*$", body)
        for idx in range(0, len(parts) - 2, 3):
            text_part = parts[idx]
            kind, block = parts[idx + 1], parts[idx + 2]
            if text_part.strip():
                content.extend(f"<p>{html.escape(p)}</p>" for p in text_part.split("\n\n") if p.strip())
            if kind == "mermaid":
                content.append(f'<div class="mermaid">{html.escape(block.strip())}</div>')
            else:
                uri = render_drawio_png_base64(block)
                if uri:
                    content.append(f'<img class="drawio" src="{uri}" alt="系统架构图">')
                else:
                    content.append(f'<pre class="drawio-src">{html.escape(block.strip())}</pre>')
        tail = parts[-1] if parts else ""
        if tail.strip():
            content.extend(f"<p>{html.escape(p)}</p>" for p in tail.split("\n\n") if p.strip())
    content.append("<hr><p><b>内部预审提示：</b>本文档含 AI 辅助内容，必须经授权人员人工终审后方可对外。</p>")
    # 与编制页共用同一份本地 JS 解析器；离线打开时脚本加载失败则原样显示流程图源码
    engine = (f'<script src="{request.host_url.rstrip("/")}/static/vendor/mermaid.min.js"></script>'
              '<script>window.addEventListener("load",function(){if(!window.mermaid)return;'
              'try{mermaid.initialize({startOnLoad:false,securityLevel:"strict",theme:"default",'
              'flowchart:{htmlLabels:false}});'
              'mermaid.init(undefined,document.querySelectorAll(".mermaid"));}catch(e){}});</script>')
    page = ("<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>标书预审版</title>"
            "<style>body{max-width:850px;margin:40px auto;font:16px/1.9 'Microsoft YaHei',sans-serif;color:#222}"
            "h1,h2{color:#0B2B57}p{white-space:pre-wrap}hr{margin:30px 0}"
            ".mermaid{margin:14px 0;padding:10px;border:1px solid #dbe3ef;border-radius:6px;"
            "background:#fcfdff;text-align:left;font:12px/1.7 Consolas,monospace;white-space:pre-wrap}"
            ".mermaid svg{display:block;margin:0 auto;font-family:'Microsoft YaHei',sans-serif}"
            ".drawio{display:block;max-width:100%;height:auto;margin:16px auto;border:1px solid #dbe3ef;"
            "border-radius:6px;background:#fff}"
            ".drawio-src{margin:14px 0;padding:10px;border:1px solid #dbe3ef;border-radius:6px;"
            "background:#fcfdff;text-align:left;font:12px/1.7 Consolas,monospace;white-space:pre-wrap}"
            "</style>"
            + engine + "</head><body>" + "".join(content) + "</body></html>")
    target = BASE_DIR / "data" / f"bid_{bid_id}_preview.html"
    target.write_text(page, encoding="utf-8")
    return send_file(target, as_attachment=True, download_name=f"标书预审版_{bid_id}.html")


# ==================== 二期（1）招标解析 ====================

def tender_file_view(conn, row):
    item = as_dict(row)
    report = item.pop("parse_report", None)
    if report:
        try:
            item["parse_report"] = json.loads(report)
        except (TypeError, ValueError):
            item["parse_report"] = None
    else:
        item["parse_report"] = None
    counts = {"total": 0, "pending": 0, "rejected": 0}
    for ext in conn.execute("SELECT status FROM tender_extracts WHERE file_id=?", (item["id"],)).fetchall():
        counts["total"] += 1
        if ext["status"] == "待确认":
            counts["pending"] += 1
        elif ext["status"] == "已驳回":
            counts["rejected"] += 1
    item.update(counts)
    item["deleted"] = bool(item.get("deleted_at"))
    # 可编辑判定：已作废不可改；已锁定基准版为受控基线，不可改（变更走澄清差异版）；
    # 差异版仅可改名称/页数/备注（标称总分继承基准版）
    item["editable"] = (not item["deleted"]) and item["status"] != "已锁定"
    item["editable_fields"] = ([] if not item["editable"] else
                               (["filename", "remark"] if item["version_label"] == "差异版"
                                else ["filename", "page_count", "declared_total", "uploaded_by", "remark"]))
    item["deletable"] = (not item["deleted"]) and item["status"] != "已锁定"
    item["restorable"] = item["deleted"]
    item["score_sum"] = round(conn.execute(
        "SELECT COALESCE(SUM(score_value),0) c FROM tender_extracts WHERE file_id=? AND category='评分办法' AND status!='已驳回'",
        (item["id"],),
    ).fetchone()["c"], 2)
    ts = conn.execute("SELECT id,req_count,must_count FROM tech_specs WHERE file_id=?", (item["id"],)).fetchone()
    item["tech_parsed"] = bool(ts)
    item["tech_req_count"] = ts["req_count"] if ts else 0
    item["tech_must_count"] = ts["must_count"] if ts else 0
    return item


@app.get("/api/bids/<int:bid_id>/tender-files")
def list_tender_files(bid_id):
    include_deleted = str(request.args.get("include_deleted") or "") in ("1", "true", "yes")
    with connect() as conn:
        if not conn.execute("SELECT id FROM bids WHERE id=?", (bid_id,)).fetchone():
            return jsonify(error="标书不存在"), 404
        sql = "SELECT * FROM tender_files WHERE bid_id=?"
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        rows = conn.execute(sql + " ORDER BY id", (bid_id,)).fetchall()
        items = [tender_file_view(conn, r) for r in rows]
        deleted_total = conn.execute(
            "SELECT COUNT(*) c FROM tender_files WHERE bid_id=? AND deleted_at IS NOT NULL", (bid_id,)
        ).fetchone()["c"]
        spec_row = conn.execute(
            """SELECT s.file_id, s.req_count, s.must_count, s.outline_count, s.applied_at,
                      f.filename, f.deleted_at
               FROM tech_specs s LEFT JOIN tender_files f ON f.id = s.file_id
               WHERE s.bid_id=? ORDER BY s.id DESC LIMIT 1""",
            (bid_id,),
        ).fetchone()
        spec = {"exists": False, "file_id": None, "req_count": 0, "outline_count": 0,
                "applied_at": None, "filename": None}
        if spec_row and not spec_row["deleted_at"]:
            spec.update(exists=True, file_id=spec_row["file_id"],
                        req_count=spec_row["req_count"], must_count=spec_row["must_count"],
                        outline_count=spec_row["outline_count"], applied_at=spec_row["applied_at"],
                        filename=spec_row["filename"])
    return jsonify(items=items, total=len(items), deleted_total=deleted_total, spec=spec)


@app.get("/api/tender-files/<int:file_id>")
def tender_file_detail(file_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM tender_files WHERE id=?", (file_id,)).fetchone()
        if not row:
            return jsonify(error="招标文件不存在"), 404
        extracts = as_list(conn.execute(
            "SELECT * FROM tender_extracts WHERE file_id=? ORDER BY id", (file_id,)
        ).fetchall())
    item = tender_file_view(conn, row)
    score_ok = None
    if item["declared_total"] is not None:
        score_ok = abs(item["score_sum"] - float(item["declared_total"])) < 0.01
    diff = None
    if item["version_label"] == "差异版":
        changes = [e for e in extracts if e.get("change_kind")]
        diff = {
            "modified": sum(1 for e in changes if e["change_kind"] == "修改"),
            "added": sum(1 for e in changes if e["change_kind"] == "新增"),
            "removed": sum(1 for e in changes if e["change_kind"] == "删除"),
            "items": [e["id"] for e in changes],
        }
    return jsonify(**item, extracts=extracts, score_ok=score_ok, score_threshold=AUTO_CONFIRM_CONFIDENCE, diff=diff)


_SOURCE_CACHE = {}          # { (path, mtime): (pages, page_count) }
SOURCE_BLOCK_LIMIT = 6000   # 单份原文最多返回的文本块数（防止超大文件拖死浏览器）
SOURCE_CHAR_LIMIT = 400000  # 单份原文字符上限


def _source_pages(blocks, page_count):
    """把文本块按页分组，供前端渲染「原文视图」。"""
    grouped, char_total, truncated = {}, 0, False
    for idx, b in enumerate(blocks):
        if idx >= SOURCE_BLOCK_LIMIT or char_total >= SOURCE_CHAR_LIMIT:
            truncated = True
            break
        text = b.get("text") or ""
        char_total += len(text)
        page = b.get("page") or 1
        grouped.setdefault(page, []).append({"k": idx, "kind": b.get("kind", "para"), "text": text})
    pages = [{"page": p, "blocks": grouped[p]} for p in sorted(grouped)]
    return pages, page_count, truncated


@app.get("/api/tender-files/<int:file_id>/source")
def tender_file_source(file_id):
    """招标文件原文浏览：PDF/图片走浏览器内置查看（可定位页码），Word/文本走结构化分页视图。
    返回 pages 时块序号与解析结果同源（同一抽取管线），便于点击条款定位原文。"""
    with connect() as conn:
        row = conn.execute("SELECT * FROM tender_files WHERE id=?", (file_id,)).fetchone()
        if not row:
            return jsonify(error="招标文件不存在"), 404
    base = {"id": file_id, "filename": row["filename"], "page_count": row["page_count"] or None,
            "version_label": row["version_label"], "raw_url": f"/api/tender-files/{file_id}/raw"}
    if not row["stored_path"]:
        return jsonify(**base, kind="missing", pages=[],
                       note="该记录登记时未上传原始文件（演示档案），无原文可对照；重新登记并上传附件后即可定位原文。")
    path = UPLOAD_DIR / row["stored_path"]
    if not path.exists():
        return jsonify(**base, kind="missing", pages=[],
                       note="原始附件已不在存储目录中（可能被迁移或清理），仅保留解析结果与执行轨迹。")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return jsonify(**base, kind="pdf", pages=[],
                       note="PDF 使用浏览器内置阅读器打开，按页码定位；若浏览器禁用内嵌 PDF，请点击“下载原件”。")
    if suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        return jsonify(**base, kind="image", pages=[], note="扫描件按图片查看，页码定位以解析结果为准。")
    if suffix not in (".doc", ".docx", ".txt", ".md", ".csv", ".json", ".html", ".htm", ".xml"):
        return jsonify(**base, kind="unsupported", pages=[],
                       note=f"暂不支持在浏览器内渲染 {suffix or '该格式'} 原文，请下载原件对照；抽取结果中的页码可供人工检索。")
    cache_key = (str(path), path.stat().st_mtime)
    cached = _SOURCE_CACHE.get(cache_key)
    if cached:
        pages, page_count, truncated, page_source = cached
    else:
        blocks, page_count, kind, page_source = extract_file_blocks(path)
        if kind not in ("docx", "doc", "text"):
            return jsonify(**base, kind="unsupported", pages=[],
                           note="原文解析失败，请下载原件对照。")
        pages, page_count, truncated = _source_pages(blocks, page_count)
        if len(_SOURCE_CACHE) >= 6:
            _SOURCE_CACHE.clear()
        _SOURCE_CACHE[cache_key] = (pages, page_count, truncated, page_source)
    if page_source == "typeset":
        note = "页码取自 Word 排版引擎真实分页，与纸质文件逐页一致；与抽取结果同源。"
    else:
        note = ("Word 原文不含分页信息，且本机未检测到 Office 排版引擎，页码按目录锚点与版面高度推算"
                "（章级准确、页级可能有偏差）；与抽取结果同源。")
    if truncated:
        note += f"（原文较长，已截断至前 {SOURCE_BLOCK_LIMIT} 个文本块，完整内容请下载原件）"
    if page_count:
        base["page_count"] = page_count
    return jsonify(**base, kind="docx" if suffix in (".doc", ".docx") else "text", pages=pages,
                   page_source=page_source, note=note)


@app.get("/api/tender-files/<int:file_id>/layout.pdf")
def tender_file_layout_pdf(file_id):
    """Word 排版原样（docx 经本机 Office 引擎转 PDF），按真实分页逐页查看。"""
    with connect() as conn:
        row = conn.execute("SELECT * FROM tender_files WHERE id=?", (file_id,)).fetchone()
        if not row:
            return jsonify(error="招标文件不存在"), 404
    if not row["stored_path"]:
        return jsonify(error="该记录未上传原始文件（演示档案）"), 404
    path = UPLOAD_DIR / row["stored_path"]
    if not path.exists():
        return jsonify(error="原始附件已不在存储目录中"), 404
    if path.suffix.lower() != ".docx":
        return jsonify(error="仅 Word 文件需要排版稿，PDF 请直接查看原件"), 400
    pdf_path, note = docx_to_pdf(path)
    if pdf_path is None:
        return jsonify(error=f"无法生成排版稿：{note}"), 503
    return send_file(pdf_path, as_attachment=False, mimetype="application/pdf",
                     download_name=f"{Path(row['filename']).stem}_排版稿.pdf")


@app.get("/api/tender-files/<int:file_id>/raw")
def tender_file_raw(file_id):
    """原始招标文件直链（inline 打开 / 下载）。"""
    with connect() as conn:
        row = conn.execute("SELECT * FROM tender_files WHERE id=?", (file_id,)).fetchone()
        if not row:
            return jsonify(error="招标文件不存在"), 404
    if not row["stored_path"]:
        return jsonify(error="该记录未上传原始文件（演示档案）"), 404
    path = UPLOAD_DIR / row["stored_path"]
    if not path.exists():
        return jsonify(error="原始附件已不在存储目录中"), 404
    mime = mimetypes.guess_type(row["filename"])[0] or "application/octet-stream"
    if path.suffix.lower() == ".docx":
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return send_file(path, as_attachment=False, download_name=row["filename"], mimetype=mime)


def _tender_save_upload(data):
    """保存附件至 UPLOAD_DIR，返回 (stored_path, original_filename)；无附件返回 (None, None)。"""
    if not data.get("content_base64"):
        return None, None
    raw = base64.b64decode(data["content_base64"], validate=True)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("招标文件不能超过 8MB")
    original = Path(str(data.get("original_filename", "upload.bin"))).name
    target = UPLOAD_DIR / f"{uuid.uuid4().hex}{Path(original).suffix[:12]}"
    target.write_bytes(raw)
    return target.name, original


def _run_agent_and_store(conn, file_id, bid_id, declared_total):
    """对已保存附件执行解析 Agent 管线，清空旧条目并写入新抽取结果与执行轨迹。
    返回 (extract_count, pending, report)。"""
    frow = conn.execute("SELECT * FROM tender_files WHERE id=?", (file_id,)).fetchone()
    path = UPLOAD_DIR / frow["stored_path"]
    if not path.exists():
        raise ValueError("附件文件缺失，无法解析")
    term_hints = conn.execute("SELECT term, synonyms FROM term_dict").fetchall()
    result = run_tender_agent(path, declared_total=declared_total, term_hints=term_hints)
    now = now_text()
    conn.execute("DELETE FROM tender_extracts WHERE file_id=?", (file_id,))
    for ext in result["extracts"]:
        if ext["category"] not in TENDER_EXTRACT_CATEGORIES:
            continue
        conn.execute(
            """INSERT INTO tender_extracts(file_id,bid_id,category,title,content,page,para,score_value,
               confidence,status,signals,confirmed_by,confirmed_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (file_id, bid_id, ext["category"], ext["title"], ext["content"], ext["page"], ext["para"],
             ext["score_value"], ext["confidence"], ext["status"],
             json.dumps(ext.get("signals"), ensure_ascii=False) if ext.get("signals") else None,
             "解析Agent" if ext["status"] == "已确认" else None,
             now if ext["status"] == "已确认" else None, now),
        )
    report = result["report"]
    report["page_source"] = result.get("page_source", "none")  # typeset / estimate / exact
    conn.execute(
        "UPDATE tender_files SET parse_report=? WHERE id=?",
        (json.dumps(report, ensure_ascii=False), file_id),
    )
    if result["page_count"]:
        conn.execute("UPDATE tender_files SET page_count=? WHERE id=?", (result["page_count"], file_id))
    pending = conn.execute(
        "SELECT COUNT(*) c FROM tender_extracts WHERE file_id=? AND status='待确认'", (file_id,)
    ).fetchone()["c"]
    return len(result["extracts"]), pending, result


TENDER_FILE_KINDS = ("招标文件", "招标公示", "招标人须知", "评分办法", "合同条款", "技术规格书", "澄清答疑")


@app.post("/api/bids/<int:bid_id>/tender-files")
def create_tender_file(bid_id):
    """登记招标文件并解析：有附件走解析 Agent 管线（PDF/Word/文本真实抽取），
    无附件按评分矩阵生成演示解析档案。kind 区分制式文件类型（招标公示/招标人须知/
    评分办法/合同条款）与技术规格书。"""
    data = body()
    filename = str(data.get("filename") or "").strip()
    if not filename:
        raise ValueError("请填写招标文件名称")
    kind = str(data.get("kind") or "招标文件").strip()
    if kind not in TENDER_FILE_KINDS:
        kind = "招标文件"
    page_count = int(data.get("page_count") or 0)
    if page_count < 0 or page_count > 500:
        raise ValueError("单份招标文件不超过 500 页，请核对页数")
    try:
        declared_total = int(data.get("declared_total")) if data.get("declared_total") not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError("标称总分必须为整数")
    stored_path, original = _tender_save_upload(data)
    now = now_text()
    with connect() as conn:
        bid = conn.execute("SELECT * FROM bids WHERE id=?", (bid_id,)).fetchone()
        if not bid:
            return jsonify(error="标书不存在"), 404
        cur = conn.execute(
            """INSERT INTO tender_files(bid_id,kind,filename,page_count,declared_total,status,version_label,
               stored_path,uploaded_by,uploaded_at) VALUES(?,?,?,?,?, '已解析','基准版',?,?,?)""",
            (bid_id, kind, filename, page_count, declared_total,
             stored_path, str(data.get("uploaded_by") or "投标经理 · 演示"), now),
        )
        file_id = cur.lastrowid
        if stored_path:
            count, pending, result = _run_agent_and_store(conn, file_id, bid_id, declared_total)
            source = f"解析Agent管线（{result['source']}）"
            report = result["report"]
            summary = report.get("summary", {})
            if report.get("fallback"):
                source = f"解析Agent管线：{report['fallback']}"
        else:
            extracts = build_demo_extracts(conn, bid)
            source = "演示档案（未上传附件）"
            for ext in extracts:
                if ext["category"] not in TENDER_EXTRACT_CATEGORIES:
                    continue
                conn.execute(
                    """INSERT INTO tender_extracts(file_id,bid_id,category,title,content,page,para,score_value,
                       confidence,status,confirmed_by,confirmed_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (file_id, bid_id, ext["category"], ext["title"], ext["content"], ext["page"], ext["para"],
                     ext["score_value"], ext["confidence"], ext["status"],
                     "解析引擎" if ext["status"] == "已确认" else None,
                     now if ext["status"] == "已确认" else None, now),
                )
            count, pending = len(extracts), sum(1 for e in extracts if e["status"] == "待确认")
            summary = {}
        score_sum = round(conn.execute(
            "SELECT COALESCE(SUM(score_value),0) c FROM tender_extracts WHERE file_id=? AND category='评分办法'",
            (file_id,),
        ).fetchone()["c"], 2)
        audit(conn, "登记招标文件", "tender_file", file_id,
              f"{filename} · {page_count} 页 · 解析 {count} 条（{source}），{pending} 条待人工确认")
        conn.commit()
    detail = ""
    if stored_path and summary:
        detail = f"，文本块 {summary.get('blocks', 0)} · 章节 {summary.get('sections', 0)} · 用时 {summary.get('total_ms', 0)}ms"
    return jsonify(
        id=file_id, kind=kind, message=f"解析完成：Agent 管线抽取 {count} 条，其中 {pending} 条置信度不足需人工确认{detail}",
        extracts=count, pending=pending, score_sum=score_sum, declared_total=declared_total,
    ), 201


@app.patch("/api/tender-files/<int:file_id>")
def update_tender_file(file_id):
    """修改招标文件登记信息（名称/页数/标称总分/登记人/备注）。
    规则：已锁定基准版为受控基线不可改；差异版标称总分继承基准版不可改；已作废记录不可改。"""
    data = body()
    user = str(data.get("user") or "投标经理").strip()
    with connect() as conn:
        row = conn.execute("SELECT * FROM tender_files WHERE id=?", (file_id,)).fetchone()
        if not row:
            return jsonify(error="招标文件不存在"), 404
        if row["deleted_at"]:
            return jsonify(error="该记录已作废，请先恢复后再修改"), 409
        if row["status"] == "已锁定":
            return jsonify(
                error="已锁定的基准版属于受控解析基线，登记信息不可修改；如内容有变更，请通过“登记澄清答疑”生成差异版",
                code="locked",
            ), 409
        fields, params, changes = [], [], []

        if "filename" in data:
            filename = str(data["filename"] or "").strip()
            if not filename:
                raise ValueError("招标文件名称不能为空")
            if filename != row["filename"]:
                fields.append("filename=?")
                params.append(filename)
                changes.append(f"名称「{row['filename']}」→「{filename}」")
        if "page_count" in data:
            try:
                page_count = int(data["page_count"] or 0)
            except (TypeError, ValueError):
                raise ValueError("总页数必须为整数")
            if page_count < 0 or page_count > 500:
                raise ValueError("单份招标文件不超过 500 页，请核对页数")
            if page_count != row["page_count"]:
                fields.append("page_count=?")
                params.append(page_count)
                changes.append(f"页数 {row['page_count']} → {page_count}")
        if "declared_total" in data:
            if row["version_label"] == "差异版":
                raise ValueError("差异版的标称总分继承基准版，不可单独修改")
            raw_total = data["declared_total"]
            declared = None
            if raw_total not in (None, ""):
                try:
                    declared = int(raw_total)
                except (TypeError, ValueError):
                    raise ValueError("标称总分必须为整数")
                if declared <= 0 or declared > 1000:
                    raise ValueError("标称总分应为 1~1000 的整数")
            if declared != row["declared_total"]:
                fields.append("declared_total=?")
                params.append(declared)
                old_total = row["declared_total"] if row["declared_total"] is not None else "空"
                changes.append(f"标称总分 {old_total} → {declared if declared is not None else '空'}")
        if "uploaded_by" in data:
            uploaded_by = str(data["uploaded_by"] or "").strip() or row["uploaded_by"]
            if row["version_label"] != "差异版" and uploaded_by != row["uploaded_by"]:
                fields.append("uploaded_by=?")
                params.append(uploaded_by)
                changes.append(f"登记人 {row['uploaded_by']} → {uploaded_by}")
        if "remark" in data:
            remark = str(data["remark"] or "").strip()
            if remark != (row["remark"] or ""):
                fields.append("remark=?")
                params.append(remark)
                changes.append("备注更新")

        if not fields:
            return jsonify(message="没有变更", id=file_id)
        params.append(file_id)
        conn.execute(f"UPDATE tender_files SET {','.join(fields)} WHERE id=?", params)
        audit(conn, "修改招标文件登记信息", "tender_file", file_id, f"{row['filename']}：{'；'.join(changes)}；操作人 {user}")
        conn.commit()
        fresh = conn.execute("SELECT * FROM tender_files WHERE id=?", (file_id,)).fetchone()
        view = tender_file_view(conn, fresh)
    warning = ""
    score_ok = None if view["declared_total"] is None else abs(view["score_sum"] - float(view["declared_total"])) < 0.01
    if view["declared_total"] is not None and not score_ok:
        warning = (f"评分表分值合计 {_fmt_score(view['score_sum'])} 与标称总分 {_fmt_score(view['declared_total'])} "
                   f"不一致，请核对是否漏抽或误改分值")
    return jsonify(message="已保存；" + "；".join(changes) + (f"；⚠ {warning}" if warning else ""),
                   id=file_id, score_sum=view["score_sum"], score_ok=score_ok, warning=warning)


@app.delete("/api/tender-files/<int:file_id>")
def delete_tender_file(file_id):
    """作废招标文件登记记录（软删除，不物理删除附件与抽取条目，保留审计追溯）。
    已锁定基准版不可作废；存在未作废差异版的基准版须先作废差异版。"""
    data = body()
    reason = str(data.get("reason") or "").strip()
    user = str(data.get("user") or "投标经理").strip()
    if len(reason) < 2:
        raise ValueError("请填写作废原因（至少 2 个字）")
    with connect() as conn:
        row = conn.execute("SELECT * FROM tender_files WHERE id=?", (file_id,)).fetchone()
        if not row:
            return jsonify(error="招标文件不存在"), 404
        if row["deleted_at"]:
            return jsonify(error="该记录已作废，无需重复操作"), 409
        if row["status"] == "已锁定":
            return jsonify(
                error="已锁定的基准版是投标响应与偏离表的比对基线，不允许作废；如登记有误请联系管理员按受控记录处置",
                code="locked",
            ), 409
        children = conn.execute(
            "SELECT COUNT(*) c FROM tender_files WHERE parent_id=? AND deleted_at IS NULL", (file_id,)
        ).fetchone()["c"]
        if children:
            return jsonify(error=f"该基准版下仍有 {children} 份澄清差异版，请先作废差异版后再作废基准版"), 409
        now = now_text()
        conn.execute(
            "UPDATE tender_files SET deleted_at=?, deleted_by=?, delete_reason=?, status='已作废' WHERE id=?",
            (now, user, reason, file_id),
        )
        total = conn.execute("SELECT COUNT(*) c FROM tender_extracts WHERE file_id=?", (file_id,)).fetchone()["c"]
        audit(conn, "作废招标文件登记", "tender_file", file_id,
              f"{row['filename']}（{total} 条抽取结果保留备查）；原因：{reason}；操作人 {user}")
        conn.commit()
    return jsonify(message=f"已作废：{row['filename']}（记录与抽取结果保留，可在“已作废”中恢复）", id=file_id)


@app.post("/api/tender-files/<int:file_id>/restore")
def restore_tender_file(file_id):
    """从作废状态恢复登记记录（附件、抽取条目、执行轨迹原样保留）。"""
    data = body()
    user = str(data.get("user") or "投标经理").strip()
    with connect() as conn:
        row = conn.execute("SELECT * FROM tender_files WHERE id=?", (file_id,)).fetchone()
        if not row:
            return jsonify(error="招标文件不存在"), 404
        if not row["deleted_at"]:
            return jsonify(message="该记录未作废，无需恢复", id=file_id, already=True)
        parent = conn.execute("SELECT * FROM tender_files WHERE id=?", (row["parent_id"],)).fetchone() if row["parent_id"] else None
        if parent and parent["deleted_at"]:
            raise ValueError("所属基准版已作废，请先恢复基准版后再恢复该澄清差异版")
        pending = conn.execute(
            "SELECT COUNT(*) c FROM tender_extracts WHERE file_id=? AND status='待确认'", (file_id,)
        ).fetchone()["c"]
        status = "已锁定" if row["version_label"] == "基准版" and row["locked_at"] and not pending else "已解析"
        conn.execute(
            "UPDATE tender_files SET deleted_at=NULL, deleted_by=NULL, delete_reason=NULL, status=? WHERE id=?",
            (status, file_id),
        )
        audit(conn, "恢复招标文件登记", "tender_file", file_id,
              f"{row['filename']}；作废原因原为「{row['delete_reason'] or ''}」；操作人 {user}")
        conn.commit()
    return jsonify(message=f"已恢复：{row['filename']}", id=file_id)


@app.post("/api/tender-files/<int:file_id>/reparse")
def reparse_tender_file(file_id):
    """重新运行解析 Agent（基准版未锁定且存在附件时可用），旧抽取结果作废重抽。"""
    with connect() as conn:
        row = conn.execute("SELECT * FROM tender_files WHERE id=?", (file_id,)).fetchone()
        if not row:
            return jsonify(error="招标文件不存在"), 404
        if row["status"] == "已锁定":
            return jsonify(error="基准版已锁定，不可重新解析；变更请走澄清差异版"), 409
        if row["deleted_at"]:
            return jsonify(error="该记录已作废，请先恢复后再重新解析"), 409
        if row["version_label"] != "基准版":
            raise ValueError("差异版由澄清登记生成，不支持重新解析")
        if not row["stored_path"]:
            raise ValueError("该记录未上传附件（演示档案），无法重新解析")
        count, pending, result = _run_agent_and_store(conn, file_id, row["bid_id"], row["declared_total"])
        audit(conn, "重新解析招标文件", "tender_file", file_id,
              f"Agent 管线重抽 {count} 条，{pending} 条待人工确认")
        conn.commit()
    summary = result["report"].get("summary", {})
    return jsonify(message=f"重新解析完成：抽取 {count} 条，{pending} 条待人工确认",
                   extracts=count, pending=pending, summary=summary)


# ==================== 二期（1-2）技术规格书独立解析 → 技术方案编写目录 ====================

def _tech_flat(outline):
    """递归展开任意层级（1—4 级），保持目录树先序。"""
    flat = []
    def walk(nodes):
        for node in nodes or []:
            flat.append(node)
            walk(node.get("children") or [])
    walk(outline)
    return flat


def _tech_spec_view(conn, file_id):
    """技术规格解析结果视图：目录项按层级展开，附带映射到的技术标章节。"""
    row = conn.execute("SELECT * FROM tech_specs WHERE file_id=?", (file_id,)).fetchone()
    if not row:
        return {"exists": False}
    tfile = conn.execute("SELECT filename FROM tender_files WHERE id=?", (file_id,)).fetchone()
    outline = []
    for o in conn.execute("SELECT * FROM tech_outline WHERE spec_id=? ORDER BY seq", (row["id"],)).fetchall():
        item = as_dict(o)
        try:
            item["requirements"] = json.loads(item.get("requirements") or "[]")
        except (TypeError, ValueError):
            item["requirements"] = []
        item["categories"] = [c for c in (item.get("categories") or "").split("、") if c]
        sec = conn.execute("SELECT id,code,title FROM sections WHERE id=?", (item.get("section_id"),)).fetchone() \
            if item.get("section_id") else None
        item["section"] = as_dict(sec) if sec else None
        outline.append(item)
    report = None
    if row["report_json"]:
        try:
            report = json.loads(row["report_json"])
        except (TypeError, ValueError):
            report = None
    return {
        "exists": True, "id": row["id"], "file_id": file_id,
        "filename": tfile["filename"] if tfile else "",
        "status": row["status"], "page_source": row["page_source"],
        "region_title": row["region_title"], "page_from": row["page_from"], "page_to": row["page_to"],
        "item_count": row["item_count"], "req_count": row["req_count"], "must_count": row["must_count"],
        "outline_count": row["outline_count"], "applied_at": row["applied_at"],
        "created_at": row["created_at"], "report": report, "outline": outline,
    }


def _store_tech_spec(conn, file_id, bid_id, result):
    """落库：一份招标文件只保留最新一次技术规格解析（重新解析覆盖，审计留痕）。"""
    now = now_text()
    old = conn.execute("SELECT id FROM tech_specs WHERE file_id=?", (file_id,)).fetchone()
    if old:
        conn.execute("DELETE FROM tech_outline WHERE spec_id=?", (old["id"],))
        conn.execute("DELETE FROM tech_specs WHERE id=?", (old["id"],))
    flat = _tech_flat(result["outline"])
    region = result.get("region") or {}
    cur = conn.execute(
        """INSERT INTO tech_specs(file_id,bid_id,status,item_count,req_count,must_count,outline_count,
           page_from,page_to,page_source,region_title,report_json,created_at,updated_at)
           VALUES(?,?,'已解析',?,?,?,?,?,?,?,?,?,?,?)""",
        (file_id, bid_id, len(result["items"]), len(result["items"]),
         sum(1 for i in result["items"] if i["must"]), len(flat),
         region.get("page_from"), region.get("page_to"), result.get("page_source", ""),
         region.get("title", ""), json.dumps(result["report"], ensure_ascii=False), now, now),
    )
    spec_id = cur.lastrowid
    for seq, node in enumerate(flat, start=1):
        conn.execute(
            """INSERT INTO tech_outline(spec_id,seq,code,level,title,brief,categories,req_count,must_count,
               child_req_count,child_must_count,page_from,page_to,requirements,section_id,mapping_kind,mapping_note,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (spec_id, seq, node.get("code", ""), node.get("level", 1), node["title"],
             node.get("brief", ""), "、".join(node.get("categories") or []),
             node.get("req_count", 0), node.get("must_count", 0),
             node.get("child_req_count", 0), node.get("child_must_count", 0),
             node.get("page_from"), node.get("page_to"),
             json.dumps(node.get("requirements") or [], ensure_ascii=False),
             node.get("section_id"), node.get("mapping_kind", "new"),
             node.get("mapping_note", ""), now),
        )
    return spec_id


@app.get("/api/tender-files/<int:file_id>/tech-spec")
def get_tech_spec(file_id):
    with connect() as conn:
        if not conn.execute("SELECT id FROM tender_files WHERE id=?", (file_id,)).fetchone():
            return jsonify(error="招标文件不存在"), 404
        data = _tech_spec_view(conn, file_id)
    return jsonify(data)


@app.post("/api/tender-files/<int:file_id>/tech-spec/parse")
def parse_tech_spec(file_id):
    """对技术规格书做独立解析：抽取规格条目 → 归纳技术方案编写目录 → 标注技术标映射。
    与招标解析共用抽取管线，页码口径一致；解析结果不改动招标条款基线。"""
    with connect() as conn:
        row = conn.execute("SELECT * FROM tender_files WHERE id=?", (file_id,)).fetchone()
        if not row:
            return jsonify(error="招标文件不存在"), 404
        if row["deleted_at"]:
            return jsonify(error="该记录已作废，请先恢复后再解析技术规格书"), 409
        if not row["stored_path"]:
            raise ValueError("该记录未上传附件（演示档案），无法解析技术规格书")
        path = UPLOAD_DIR / row["stored_path"]
        if not path.exists():
            raise ValueError("原始附件已不在存储目录中，请重新登记")
        sections = [dict(s) for s in conn.execute(
            "SELECT id,code,title,group_name FROM sections WHERE bid_id=? ORDER BY sort_order",
            (row["bid_id"],)).fetchall()]
        result = run_tech_spec_agent(path, sections)
        if not result["items"]:
            fallback = (result.get("report") or {}).get("fallback") or "未识别到技术规格条款"
            return jsonify(error=f"未解析出技术规格条款：{fallback}。请确认该文件为技术规格书/供货要求章节，或提供可搜索版本。",
                           code="empty"), 422
        spec_id = _store_tech_spec(conn, file_id, row["bid_id"], result)
        audit(conn, "解析技术规格书", "tech_spec", spec_id,
              f"{row['filename']}：规格条目 {len(result['items'])} 条，编写目录 {len(_tech_flat(result['outline']))} 节")
        conn.commit()
        data = _tech_spec_view(conn, file_id)
    data["message"] = (f"技术规格解析完成：抽取 {data['req_count']} 条规格（★/必须 {data['must_count']} 条），"
                       f"归纳 {data['outline_count']} 节目录，可直接同步到标书编制技术标")
    return jsonify(data)


@app.post("/api/tender-files/<int:file_id>/tech-spec/apply")
def apply_tech_spec(file_id):
    """按解析结果原样同步技术标目录：编码、标题、层级、父子关系保持不变；重复同步幂等。"""
    data = body()
    user = str(data.get("user") or "投标经理").strip()

    def section_content(o):
        reqs = json.loads(o["requirements"] or "[]")
        lines = [f"【本节编写要点】{o['brief']}", "",
                 f"【技术规格要求（共 {o['req_count']} 条，★/必须 {o['must_count']} 条，"
                 f"原文第 {o['page_from'] or '-'}—{o['page_to'] or '-'} 页）】"]
        if o["child_req_count"]:
            lines.append(f"（本章另设子章节，另有 {o['child_req_count']} 条要求分别在对应小节响应，此处不重复列出）")
        for i, r in enumerate(reqs[:60], start=1):
            mark = "★" if r.get("must") else ("▲" if r.get("evidence") else "")
            proof = f"；证明材料：{r['evidence']}" if r.get("evidence") else ""
            gap = "（原文未展开，目录占位）" if r.get("placeholder") else ""
            lines.append(f"{i}. {mark}{r.get('title', '')}（原文序号 {r.get('code', '-')}，第 {r.get('page', '-')} 页）："
                         f"{r.get('content', '')}{proof}{gap}")
        lines += ["", "【响应提示】逐条给出响应值与证据位置（型号、检测报告、计算书），负偏离须显式说明；"
                      "本节内容由技术规格解析生成骨架，须由责任人补充实质性方案后定稿。"]
        return "\n".join(lines)

    with connect() as conn:
        spec = conn.execute("SELECT * FROM tech_specs WHERE file_id=?", (file_id,)).fetchone()
        if not spec:
            raise ValueError("请先解析技术规格书，再同步到标书编制")
        frow = conn.execute("SELECT * FROM tender_files WHERE id=?", (file_id,)).fetchone()
        if frow["deleted_at"]:
            return jsonify(error="招标文件已作废，不能同步到标书编制"), 409
        rows = conn.execute("SELECT * FROM tech_outline WHERE spec_id=? ORDER BY seq", (spec["id"],)).fetchall()
        old_secs = [dict(s) for s in conn.execute(
            "SELECT * FROM sections WHERE bid_id=? AND group_name='技术标'", (spec["bid_id"],)).fetchall()]
        by_code = {s["code"]: s for s in old_secs}
        same_structure = (len(old_secs) == len(rows) and
                          all(str(o["code"] or "").strip() in by_code and
                              by_code[str(o["code"] or "").strip()]["title"] == o["title"] and
                              int(by_code[str(o["code"] or "").strip()]["level"] or 1) == int(o["level"] or 1)
                              for o in rows))

        section_ids = {}
        created = reused = 0
        if same_structure:
            for o in rows:
                code = str(o["code"] or "").strip()
                section = by_code[code]
                parent_code = code.rsplit(".", 1)[0] if "." in code else ""
                parent_id = section_ids.get(parent_code) or by_code.get(parent_code, {}).get("id")
                conn.execute("UPDATE sections SET parent_id=?,level=?,content=?,sort_order=?,ai_generated=1,updated_at=? WHERE id=?",
                             (parent_id, int(o["level"] or 1), section_content(o), 1000 + o["seq"], now_text(), section["id"]))
                section_ids[code] = section["id"]
                conn.execute("UPDATE tech_outline SET section_id=?, mapping_kind='strong', mapping_note=? WHERE id=?",
                             (section["id"], "按解析目录原样同步", o["id"]))
                reused += 1
        else:
            # 结构不一致时整体替换技术标目录，避免默认 2.x 章节或标题近似映射打乱层级。
            old_ids = [s["id"] for s in old_secs]
            conn.execute("UPDATE tech_outline SET section_id=NULL WHERE spec_id=?", (spec["id"],))
            if old_ids:
                marks = ",".join("?" for _ in old_ids)
                conn.execute(f"UPDATE document_refs SET section_id=NULL WHERE section_id IN ({marks})", old_ids)
            conn.execute("DELETE FROM sections WHERE bid_id=? AND group_name='技术标'", (spec["bid_id"],))
            for o in rows:
                code = str(o["code"] or "").strip()
                if not code:
                    continue
                parent_code = code.rsplit(".", 1)[0] if "." in code else ""
                parent_id = section_ids.get(parent_code) if parent_code else None
                cur = conn.execute(
                    """INSERT INTO sections(bid_id,parent_id,level,code,title,group_name,content,status,
                           sort_order,ai_generated,updated_at)
                       VALUES(?,?,?,?,?,'技术标',?,'编制中',?,1,?)""",
                    (spec["bid_id"], parent_id, int(o["level"] or 1), code, o["title"], section_content(o),
                     1000 + o["seq"], now_text()),
                )
                section_ids[code] = cur.lastrowid
                conn.execute("UPDATE tech_outline SET section_id=?, mapping_kind='strong', mapping_note=? WHERE id=?",
                             (cur.lastrowid, "按解析目录原样同步", o["id"]))
                created += 1
        conn.execute("UPDATE tech_specs SET applied_at=?, updated_at=? WHERE id=?",
                     (now_text(), now_text(), spec["id"]))
        audit(conn, "技术方案目录同步技术标", "tech_spec", spec["id"],
              f"{frow['filename']}：原样同步，新建 {created} 节，刷新 {reused} 节；操作人 {user}")
        conn.commit()
    return jsonify(message=f"已同步到标书编制：技术标目录已按解析结构同步（新建 {created} 节，刷新 {reused} 节）",
                   created=created, reused=reused)


@app.post("/api/sections/<int:section_id>/tech-children")
def create_tech_children(section_id):
    """“插入本节”：把技术规格条目转为当前章节下的子目录（三级章节），每条一节并预置草稿（幂等）。"""
    data = body()
    user = str(data.get("user") or "投标经理").strip()
    items = data.get("items") or []
    if not items:
        raise ValueError("请提供要转为子目录的要求条目")
    with connect() as conn:
        parent = conn.execute("SELECT * FROM sections WHERE id=?", (section_id,)).fetchone()
        if not parent:
            return jsonify(error="章节不存在"), 404
        base = parent["code"].strip()
        siblings = conn.execute(
            "SELECT code,title FROM sections WHERE bid_id=? AND code LIKE ?",
            (parent["bid_id"], base + ".%")).fetchall()
        existing_titles = {s["title"].strip() for s in siblings}
        used = {s["code"] for s in conn.execute(
            "SELECT code FROM sections WHERE bid_id=?", (parent["bid_id"],)).fetchall()}
        n = 0
        for s in siblings:
            m = re.match(re.escape(base) + r"\.(\d+)$", s["code"])
            if m:
                n = max(n, int(m.group(1)))
        created = []
        for it in items:
            title = str(it.get("title") or "").strip()[:40]
            if not title or title in existing_titles:
                continue  # 幂等：同名子目录不重复创建
            n += 1
            code = f"{base}.{n}"
            while code in used:
                n += 1
                code = f"{base}.{n}"
            used.add(code)
            existing_titles.add(title)
            must = bool(it.get("must"))
            content_lines = [
                f"【来源】{parent['code']} {parent['title']} · 技术规格条目{'（★必须）' if must else ''}"
                f"（原文第 {it.get('page') or '-'} 页）",
                f"【要求】{str(it.get('content') or '').strip()}",
                "",
                "【响应提示】逐条给出响应值与证据位置（型号、检测报告、计算书），负偏离须显式说明；"
                "本节由技术规格条目生成骨架，须由责任人补充实质性方案后定稿。",
            ]
            cur = conn.execute(
                """INSERT INTO sections(bid_id,parent_id,level,code,title,group_name,content,status,
                       sort_order,ai_generated,updated_at)
                   VALUES(?,?,?,?,?,?,?,'编制中',0,0,?)""",
                (parent["bid_id"], section_id, (parent["level"] or 1) + 1, code, title,
                 parent["group_name"], "\n".join(content_lines), now_text()))
            created.append({"id": cur.lastrowid, "code": code, "title": title})
        if not created:
            return jsonify(message="对应子目录已存在，未重复创建", created=[])
        # 子章节按编号重排 sort_order（2.16.1 落在 2.16 与 2.17 之间）
        all_secs = conn.execute("SELECT id,code FROM sections WHERE bid_id=?", (parent["bid_id"],)).fetchall()

        def code_key(s):
            return tuple(int(x) for x in re.findall(r"\d+", s["code"]) or [0])

        for order, s in enumerate(sorted(all_secs, key=code_key), start=1):
            conn.execute("UPDATE sections SET sort_order=? WHERE id=?", (order, s["id"]))
        audit(conn, "插入本节为子目录", "section", section_id,
              f"{parent['code']} {parent['title']}：新建子目录 {len(created)} 节；操作人 {user}")
        conn.commit()
    return jsonify(message=f"已在「{parent['code']} {parent['title']}」下新建子目录 {len(created)} 节",
                   created=created)


SECTION_GROUP_ORDER = ["商务标", "技术标", "报价标"]


def _tree_from_sections(secs):
    """扁平章节（按 sort_order）→ 嵌套树；parent_id 为结构真值，code 前缀仅作历史数据兜底。"""
    nodes = {s["id"]: {"id": s["id"], "code": s["code"], "group": s["group_name"], "children": []}
             for s in secs}
    by_code = {(s["bid_id"], s["code"]): s["id"] for s in secs}
    roots = []
    for s in secs:
        pid = s.get("parent_id")
        if pid is None or pid not in nodes:
            pcode = ".".join(s["code"].split(".")[:-1])
            pid = by_code.get((s["bid_id"], pcode))
        if pid is not None and pid in nodes and pid != s["id"]:
            nodes[pid]["children"].append(nodes[s["id"]])
        else:
            roots.append(nodes[s["id"]])
    return roots


def _renumber_sections(conn, bid_id, roots):
    """按树重编 code / sort_order，并把 parent_id / level 写回为结构真值。

    两阶段改 code 避免 UNIQUE(bid_id,code) 中间冲突；level = 树深度（组根 = 1）。
    """
    level_groups = [n["group"] for n in roots]
    order = SECTION_GROUP_ORDER + [g for g in level_groups if g and g not in SECTION_GROUP_ORDER]
    counters, seq, plan, seen = {}, 0, [], set()

    def walk(nodes, parent_code, parent_group, parent_id, level):
        nonlocal seq
        for n in nodes:
            if n["id"] in seen:
                raise ValueError("章节树存在重复节点")
            seen.add(n["id"])
            if parent_code is None:  # 组根：编号 = 组序号.序
                grp = (n["group"] or "技术标").strip() or "技术标"
                if grp not in order:
                    order.append(grp)
                gi = order.index(grp) + 1
                counters[str(gi)] = counters.get(str(gi), 0) + 1
                code = f"{gi}.{counters[str(gi)]}"
                pid, lv = None, 1
            else:  # 子章节：编号 = 父编号.序
                counters[parent_code] = counters.get(parent_code, 0) + 1
                code = f"{parent_code}.{counters[parent_code]}"
                grp = parent_group
                pid, lv = parent_id, level
            seq += 1
            plan.append((n["id"], code, grp, seq, pid, lv))
            walk(n["children"], code, grp, n["id"], lv + 1)

    walk(roots, None, None, None, 1)
    total = conn.execute("SELECT COUNT(*) c FROM sections WHERE bid_id=?", (bid_id,)).fetchone()["c"]
    if len(plan) != total:
        raise ValueError(f"章节树不完整：覆盖 {len(plan)}/{total} 节，已取消重排（数据未变动）")
    for sid, *_ in plan:
        conn.execute("UPDATE sections SET code='~'||id WHERE id=?", (sid,))
    for sid, code, grp, sort, pid, lv in plan:
        conn.execute(
            "UPDATE sections SET code=?, group_name=?, sort_order=?, parent_id=?, level=?, updated_at=? WHERE id=?",
            (code, grp, sort, pid, lv, now_text(), sid))
    return plan


@app.post("/api/bids/<int:bid_id>/sections")
def create_section(bid_id):
    """左侧目录树新增章节：after=某节之后（同级）、child=作为某节末尾子节、append=组末追加。"""
    data = body()
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("请输入章节标题")
    anchor = data.get("anchor_id")
    position = str(data.get("position") or "append")
    if position not in ("after", "child", "append"):
        raise ValueError("插入位置无效")
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM bids WHERE id=?", (bid_id,)).fetchone():
            return jsonify(error="标书不存在"), 404
        secs = [dict(r) for r in conn.execute(
            "SELECT * FROM sections WHERE bid_id=? ORDER BY sort_order", (bid_id,)).fetchall()]
        roots = _tree_from_sections(secs)
        anchor_node = None

        def find(nodes):
            nonlocal anchor_node
            for n in nodes:
                if n["id"] == anchor:
                    anchor_node = n
                    return True
                if find(n["children"]):
                    return True
            return False

        if anchor is not None:
            find(roots)
            if not anchor_node:
                return jsonify(error="锚点章节不存在"), 404
        cur = conn.execute(
            """INSERT INTO sections(bid_id,parent_id,level,code,title,group_name,content,status,
                   sort_order,ai_generated,updated_at)
               VALUES(?,NULL,1,'~new',?,'技术标','','未开始',0,0,?)""",
            (bid_id, title[:60], now_text()))
        new_id = cur.lastrowid
        node = {"id": new_id, "code": "~new", "group": "技术标", "children": []}
        if position == "child" and anchor_node:
            anchor_node["children"].append(node)
        elif position == "after" and anchor_node:
            _insert_after(roots, anchor_node, node)
        else:  # append：锚点所在组末尾；无锚点时技术标末尾
            group = anchor_node["group"] if anchor_node else "技术标"
            node["group"] = group
            roots.append(node)
        plan = _renumber_sections(conn, bid_id, roots)
        audit(conn, "新增章节", "section", new_id,
              f"{title}（编号 {[p[1] for p in plan if p[0] == new_id][0]}）")
        conn.commit()
        new_code = next(p[1] for p in plan if p[0] == new_id)
    return jsonify(message=f"已新增章节 {new_code} {title}", id=new_id, code=new_code)


def _insert_after(roots, anchor, node):
    """把 node 插到 anchor 同级之后；anchor 若为组根则 node 挂到组根列表中 anchor 后面。"""
    def walk(lst):
        for i, n in enumerate(lst):
            if n["id"] == anchor["id"]:
                node["group"] = n["group"] if lst is roots else node["group"]
                lst.insert(i + 1, node)
                return True
            if walk(n["children"]):
                return True
        return False
    walk(roots)


@app.patch("/api/sections/<int:section_id>")
def rename_section(section_id):
    """目录树重命名章节标题。"""
    data = body()
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("请输入章节标题")
    with connect() as conn:
        sec = conn.execute("SELECT * FROM sections WHERE id=?", (section_id,)).fetchone()
        if not sec:
            return jsonify(error="章节不存在"), 404
        conn.execute("UPDATE sections SET title=?, updated_at=? WHERE id=?", (title[:60], now_text(), section_id))
        audit(conn, "重命名章节", "section", section_id, f"{sec['code']} {sec['title']} → {title}")
        conn.commit()
    return jsonify(message=f"已重命名为「{title}」")


def _subtree_ids(conn, section_id, bid_id):
    """按 parent_id 递归收集「本节 + 全部后代」；历史行无 parent_id 时用 code 前缀兜底。"""
    rows = conn.execute("SELECT id, code, parent_id FROM sections WHERE bid_id=?", (bid_id,)).fetchall()
    base = next((r["code"] for r in rows if r["id"] == section_id), "")
    prefix = base + "."
    kids = {}
    for r in rows:
        pid = r["parent_id"]
        if pid is None and r["id"] != section_id and r["code"].startswith(prefix):
            pid = section_id
        if pid is not None:
            kids.setdefault(pid, []).append(r["id"])
    out, stack, seen = [], [section_id], set()
    while stack:
        sid = stack.pop()
        if sid in seen:
            continue
        seen.add(sid)
        out.append(sid)
        stack.extend(kids.get(sid, []))
    return out


@app.delete("/api/sections/<int:section_id>")
def delete_section(section_id):
    """删除章节及子树（编制草稿的常规编辑动作）；按 parent_id 递归，写审计留痕。"""
    data = body()
    recursive = bool(data.get("recursive"))
    with connect() as conn:
        sec = conn.execute("SELECT * FROM sections WHERE id=?", (section_id,)).fetchone()
        if not sec:
            return jsonify(error="章节不存在"), 404
        ids = _subtree_ids(conn, section_id, sec["bid_id"])
        children = len(ids) - 1
        if children and not recursive:
            return jsonify(error=f"该章节下还有 {children} 个子章节，删除将一并删除整棵子树",
                           children=children), 409
        marks = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM sections WHERE id IN ({marks})", ids)
        audit(conn, "删除章节", "section", section_id,
              f"{sec['code']} {sec['title']}" + (f"（连同 {children} 个子章节）" if children else ""))
        conn.commit()
    return jsonify(message="章节已删除")


@app.post("/api/bids/<int:bid_id>/sections/reorder")
def reorder_sections(bid_id):
    """目录树拖拽重排：前端传嵌套树（level-1 带组名），后端整树重编章节号并按组落位。"""
    data = body()
    tree = data.get("tree")
    if not isinstance(tree, list) or not tree:
        raise ValueError("请提供完整的章节树")
    user = str(data.get("user") or "投标经理").strip()
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM bids WHERE id=?", (bid_id,)).fetchone():
            return jsonify(error="标书不存在"), 404
        all_ids = {r["id"] for r in conn.execute("SELECT id FROM sections WHERE bid_id=?", (bid_id,)).fetchall()}

        def collect(nodes, depth):
            out, g = [], None
            for n in nodes:
                if not isinstance(n, dict) or "id" not in n:
                    raise ValueError("章节树节点缺少 id")
                sid = int(n["id"])
                if sid not in all_ids:
                    raise ValueError("章节不属于本标书")
                g = str(n.get("group_name") or "").strip() if depth == 1 else ""
                out.append({"id": sid, "group": g, "children": collect(n.get("children") or [], depth + 1)})
            return out

        roots = collect(tree, 1)
        plan = _renumber_sections(conn, bid_id, roots)
        audit(conn, "目录树拖拽重排", "bid", bid_id,
              f"重排 {len(plan)} 节；操作人 {user}")
        conn.commit()
    return jsonify(message=f"目录已重排（共 {len(plan)} 节，章节号已自动重编）", count=len(plan))


@app.patch("/api/tech-outline/<int:outline_id>")
def update_tech_outline(outline_id):
    """人工调整目录项：改标题或指定映射章节（纠正自动映射，避免错挂到别的产品线章节）。"""
    data = body()
    with connect() as conn:
        row = conn.execute("SELECT * FROM tech_outline WHERE id=?", (outline_id,)).fetchone()
        if not row:
            return jsonify(error="目录项不存在"), 404
        spec = conn.execute("SELECT * FROM tech_specs WHERE id=?", (row["spec_id"],)).fetchone()
        fields, params, note = [], [], ""
        if "title" in data:
            title = str(data["title"] or "").strip()
            if not title:
                raise ValueError("目录项标题不能为空")
            fields.append("title=?")
            params.append(title)
            note = f"标题改为「{title}」"
        if "section_id" in data:
            sid = data["section_id"]
            if sid in (None, "", 0):
                fields.extend(["section_id=?", "mapping_kind=?", "mapping_note=?"])
                params.extend([None, "new", "人工取消映射，同步时新建章节"])
                note = "取消章节映射"
            else:
                sec = conn.execute("SELECT * FROM sections WHERE id=?", (int(sid),)).fetchone()
                if not sec or sec["bid_id"] != spec["bid_id"]:
                    raise ValueError("只能映射到本项目标书的章节")
                fields.extend(["section_id=?", "mapping_kind=?", "mapping_note=?"])
                params.extend([sec["id"], "strong", f"人工映射到「{sec['code']} {sec['title']}」"])
                note = f"映射到「{sec['code']} {sec['title']}」"
        if not fields:
            return jsonify(message="没有变更")
        params.append(outline_id)
        conn.execute(f"UPDATE tech_outline SET {','.join(fields)} WHERE id=?", params)
        audit(conn, "调整技术方案目录", "tech_outline", outline_id, f"{row['title']}：{note}")
        conn.commit()
    return jsonify(message="已保存；" + note if note else "已保存", id=outline_id)


@app.patch("/api/tender-extracts/<int:extract_id>")
def update_tender_extract(extract_id):
    """人工确认/修正/驳回抽取条目；基准版锁定后禁止改动（变更须走澄清差异版）。"""
    data = body()
    user = str(data.get("user") or "投标经理").strip()
    with connect() as conn:
        row = conn.execute("SELECT * FROM tender_extracts WHERE id=?", (extract_id,)).fetchone()
        if not row:
            return jsonify(error="抽取条目不存在"), 404
        frow = conn.execute("SELECT * FROM tender_files WHERE id=?", (row["file_id"],)).fetchone()
        if frow["status"] == "已锁定":
            raise ValueError("该文件已锁定为基准版，条目不可再修改；变更请通过“登记澄清答疑”生成差异版")
        status = str(data.get("status") or row["status"])
        if status not in ("待确认", "已确认", "已驳回"):
            raise ValueError("状态只能为 待确认 / 已确认 / 已驳回")
        fields, params, changes = [], [], []
        for key in ("title", "content", "para"):
            if key in data and str(data[key]).strip() != str(row[key] or ""):
                fields.append(f"{key}=?")
                params.append(str(data[key]).strip())
                changes.append(f"{key} 修正")
        if "page" in data and data["page"] is not None:
            try:
                page = int(data["page"])
            except (TypeError, ValueError):
                raise ValueError("页码必须为整数")
            if page != row["page"]:
                fields.append("page=?")
                params.append(page)
                changes.append("页码修正")
        if "score_value" in data and data["score_value"] is not None:
            try:
                value = round(float(data["score_value"]), 2)  # 支持 0.5 等小数分值
            except (TypeError, ValueError):
                raise ValueError("分值必须为数字（支持 0.5 等小数）")
            if not 0 < value <= 100:
                raise ValueError("分值须在 (0, 100] 区间")
            if value != row["score_value"]:
                fields.append("score_value=?")
                params.append(value)
                changes.append(f"分值 {row['score_value']} → {value}")
        if status != row["status"]:
            if status == "已确认" and row["status"] != "已确认":
                fields.extend(["status=?", "confirmed_by=?", "confirmed_at=?"])
                params.extend(["已确认", user, now_text()])
                changes.append(f"人工确认（置信度 {row['confidence']}）")
            else:
                fields.append("status=?")
                params.append(status)
                changes.append(f"状态 → {status}")
        if not fields:
            return jsonify(message="没有变更", id=extract_id)
        params.append(extract_id)
        conn.execute(f"UPDATE tender_extracts SET {','.join(fields)} WHERE id=?", params)
        audit(conn, "确认抽取条目", "tender_extract", extract_id,
              f"{row['category']} · {row['title']}：{'；'.join(changes) or status}；操作人 {user}")
        conn.commit()
        score_sum = conn.execute(
            "SELECT COALESCE(SUM(score_value),0) c FROM tender_extracts WHERE file_id=? AND category='评分办法' AND status!='已驳回'",
            (row["file_id"],),
        ).fetchone()["c"]
        declared = conn.execute("SELECT declared_total FROM tender_files WHERE id=?", (row["file_id"],)).fetchone()["declared_total"]
    warning = ""
    if declared is not None and abs(float(score_sum) - float(declared)) >= 0.01:
        warning = (f"评分表分值合计 {_fmt_score(score_sum)} 与标称总分 {_fmt_score(declared)} "
                   f"不一致，请核对是否漏抽或误改分值")
    return jsonify(message="已保存" + (f"；⚠ {warning}" if warning else ""), score_sum=score_sum, warning=warning)


@app.post("/api/tender-files/<int:file_id>/lock")
def lock_tender_file(file_id):
    """投标经理确认全部条目后锁定基准版；存在待确认条目时拒绝锁定。"""
    data = body()
    manager = str(data.get("manager") or "投标经理").strip()
    with connect() as conn:
        row = conn.execute("SELECT * FROM tender_files WHERE id=?", (file_id,)).fetchone()
        if not row:
            return jsonify(error="招标文件不存在"), 404
        if row["version_label"] != "基准版":
            raise ValueError("澄清差异版不参与锁定，仅基准版可锁定")
        if row["deleted_at"]:
            return jsonify(error="该记录已作废，请先恢复后再锁定基准版"), 409
        if row["status"] == "已锁定":
            return jsonify(message="该文件已是锁定的基准版", id=file_id, already=True)
        pending = conn.execute(
            "SELECT COUNT(*) c FROM tender_extracts WHERE file_id=? AND status='待确认'", (file_id,)
        ).fetchone()["c"]
        if pending:
            return jsonify(
                error=f"仍有 {pending} 条抽取结果未确认（含★/废标条款），全部确认后方可锁定基准版",
                code="pending", pending=pending,
            ), 409
        conn.execute(
            "UPDATE tender_files SET status='已锁定', locked_at=? WHERE id=?", (now_text(), file_id)
        )
        total = conn.execute("SELECT COUNT(*) c FROM tender_extracts WHERE file_id=?", (file_id,)).fetchone()["c"]
        audit(conn, "锁定基准版", "tender_file", file_id,
              f"{row['filename']} · {total} 条解析结果经 {manager} 确认后锁定为基准版")
        conn.commit()
    return jsonify(message="已锁定为基准版；后续澄清将生成差异版并高亮变更项", id=file_id)


@app.post("/api/tender-files/<int:file_id>/clarify")
def create_clarify_file(file_id):
    """登记澄清答疑 → 生成差异版文件，高亮变更项（修改/新增/删除）。"""
    data = body()
    filename = str(data.get("filename") or "").strip()
    if not filename:
        raise ValueError("请填写澄清答疑文件名称")
    items = data.get("items") or []
    now = now_text()
    with connect() as conn:
        parent = conn.execute("SELECT * FROM tender_files WHERE id=?", (file_id,)).fetchone()
        if not parent:
            return jsonify(error="基准版招标文件不存在"), 404
        if parent["version_label"] != "基准版":
            raise ValueError("差异版基于基准版生成，不能在差异版上再挂澄清")
        if parent["deleted_at"]:
            return jsonify(error="基准版已作废，请先恢复后再登记澄清答疑"), 409
        cur = conn.execute(
            """INSERT INTO tender_files(bid_id,kind,filename,page_count,declared_total,status,version_label,parent_id,
               uploaded_by,uploaded_at) VALUES(?, '澄清答疑', ?, ?, ?, '已解析','差异版', ?, ?, ?)""",
            (parent["bid_id"], filename, int(data.get("page_count") or 0), parent["declared_total"],
             parent["id"], str(data.get("uploaded_by") or "投标经理 · 演示"), now),
        )
        new_id = cur.lastrowid
        created = 0
        for item in items:
            category = str(item.get("category") or "").strip()
            if category not in TENDER_EXTRACT_CATEGORIES:
                raise ValueError(f"变更条目类别无效：{category or '空'}")
            change_kind = str(item.get("change_kind") or "修改")
            if change_kind not in ("修改", "新增", "删除"):
                raise ValueError("变更类型只能为 修改 / 新增 / 删除")
            confidence = int(item.get("confidence") or 70)
            conn.execute(
                """INSERT INTO tender_extracts(file_id,bid_id,category,title,content,page,para,score_value,
                   confidence,status,change_kind,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id, parent["bid_id"], category, str(item.get("title") or "").strip(),
                 str(item.get("content") or "").strip(), item.get("page"), str(item.get("para") or ""),
                 item.get("score_value"), confidence,
                 "已确认" if confidence >= AUTO_CONFIRM_CONFIDENCE else "待确认", change_kind, now),
            )
            created += 1
        audit(conn, "登记澄清答疑", "tender_file", new_id,
              f"{filename}（基准版 {parent['filename']} 的差异版），高亮 {created} 条变更项")
        conn.commit()
    return jsonify(
        id=new_id, changes=created,
        message=f"差异版已生成并高亮 {created} 条变更项，请对照基准版逐条确认后纳入校验基准",
    ), 201


@app.get("/api/terms")
def list_terms():
    with connect() as conn:
        items = as_list(conn.execute("SELECT * FROM term_dict ORDER BY id").fetchall())
    return jsonify(items=items, total=len(items))


@app.post("/api/terms")
def add_term():
    data = body()
    term = str(data.get("term") or "").strip()
    synonyms = str(data.get("synonyms") or "").strip()
    if not term or not synonyms:
        raise ValueError("术语与同义词不能为空")
    with connect() as conn:
        if conn.execute("SELECT id FROM term_dict WHERE term=?", (term,)).fetchone():
            return jsonify(error=f"术语“{term}”已存在"), 409
        conn.execute(
            "INSERT INTO term_dict(term,synonyms,note,created_at) VALUES(?,?,?,?)",
            (term, synonyms, str(data.get("note") or ""), now_text()),
        )
        audit(conn, "新增术语", "term", None, f"{term} = {synonyms}")
        conn.commit()
    return jsonify(message="术语已入库，后续解析将按同义词表扩充召回"), 201


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="中核西仪文档与标书智能工作台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8009)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    init_db()
    print(f"XNIC 文档与标书系统：http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
