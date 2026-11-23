# -*- coding: utf-8 -*-
"""为演示资质挂接证照扫描样张（PNG），幂等：已有扫描件则跳过。
仅作用于本地演示库。样张均为“演示样张 · 非真实证照”水印图，仅供系统预览演示。
"""
import hashlib
import shutil
import sys
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from database import UPLOAD_DIR, connect, init_db, now_text, audit  # noqa: E402

SRC = BASE / ".samples"

PLAN = [
    # (证书名称关键词, 样张文件, 备注)
    ("民用核安全设备设计许可证", "cert_nnsa_design.png", "许可证正本扫描样张"),
    ("辐射安全许可证", "cert_radiation.png", "许可证正本扫描样张"),
    ("消防产品 3C 认证证书", "cert_fire_3c.png", "3C 认证证书扫描样张"),
]


def main():
    init_db()
    changed = 0
    with connect() as conn:
        for keyword, filename, note in PLAN:
            src = SRC / filename
            if not src.exists():
                print(f"跳过 {filename}：样张不存在")
                continue
            cert = conn.execute(
                "SELECT * FROM certificates WHERE name LIKE ? AND deleted_at IS NULL ORDER BY id LIMIT 1",
                (f"%{keyword}%",),
            ).fetchone()
            if not cert:
                print(f"跳过 {filename}：未找到资质 {keyword}")
                continue
            if cert["stored_path"]:
                print(f"跳过 {cert['name']}：已有扫描件 {cert['stored_path']}")
                continue
            raw = src.read_bytes()
            target = UPLOAD_DIR / f"{uuid.uuid4().hex}{src.suffix}"
            shutil.copyfile(src, target)
            conn.execute(
                """UPDATE certificates SET original_filename=?,stored_path=?,sha256=?,file_size=?,updated_at=?
                   WHERE id=?""",
                (filename, target.name, hashlib.sha256(raw).hexdigest(), len(raw), now_text(), cert["id"]),
            )
            audit(conn, "挂接证照扫描样张", "certificate", cert["id"], f"{filename} · {note}")
            print(f"已挂接 {cert['name']} ← {filename} ({len(raw) / 1024:.0f} KB)")
            changed += 1
        conn.commit()
    print(f"完成：挂接 {changed} 张证照样张")


if __name__ == "__main__":
    main()
