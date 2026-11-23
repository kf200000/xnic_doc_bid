# -*- coding: utf-8 -*-
"""为演示数据挂接真实样例附件（PDF / TXT / PNG），用于验证在线预览与历史版本查看。
幂等：版本已有附件则跳过。仅作用于本地演示库，不影响 init_db 的种子数据。
"""
import hashlib
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from database import UPLOAD_DIR, audit, connect, init_db, new_document_upload_relpath, now_text  # noqa: E402

SRC = BASE / ".samples"

PLAN = [
    # (doc_id, version_id, 源文件, 版本说明)
    (8, 8, "krt_v32.pdf", "补充三级报警阈值与 KIC 冗余链路"),
    (8, 9, "krt_v31.pdf", "结构评审版，环境监测待补充"),
    (8, 10, "krt_v30.pdf", "初稿，技术路线讨论用"),
    (12, 15, "haf003_excerpt.txt", "法规条款摘录，用于全文检索验证"),
    (10, 13, "pps_diagram.png", "系统组成框图（商密）"),
]


def attach(conn, doc_id, version_id, filename, note):
    src = SRC / filename
    if not src.exists():
        print(f"跳过 {filename}：源文件不存在")
        return False
    version = conn.execute("SELECT * FROM document_versions WHERE id=? AND doc_id=?", (version_id, doc_id)).fetchone()
    doc = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not version or not doc:
        print(f"跳过 {filename}：文档或版本不存在")
        return False
    if version["stored_path"]:
        print(f"跳过 {filename}：版本已有附件 {version['stored_path']}")
        return False
    raw = src.read_bytes()
    rel = new_document_upload_relpath(doc["category"], src.name)
    target = UPLOAD_DIR / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target)
    sha = hashlib.sha256(raw).hexdigest()
    mime = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".png": "image/png",
    }.get(src.suffix, "application/octet-stream")
    conn.execute(
        """UPDATE document_versions SET stored_path=?,original_filename=?,sha256=?,file_size=?
           WHERE id=?""",
        (rel, filename, sha, len(raw), version_id),
    )
    if version["status"] == "受控":
        conn.execute(
            """UPDATE documents SET stored_path=?,original_filename=?,sha256=?,file_size=?,mime_type=?,updated_at=?
               WHERE id=?""",
            (rel, filename, sha, len(raw), mime, now_text(), doc_id),
        )
    audit(conn, "挂接样例附件", "document", doc_id, f"{filename} · 版本 {version['version']} · {note}")
    print(f"已挂接 {doc['name'][:24]} / {version['version']} ← {filename} ({len(raw)/1024:.0f} KB)")
    return True


def main():
    init_db()
    changed = 0
    with connect() as conn:
        for doc_id, version_id, filename, note in PLAN:
            if attach(conn, doc_id, version_id, filename, note):
                changed += 1
        conn.commit()
    print(f"完成：挂接 {changed} 个附件")


if __name__ == "__main__":
    main()
