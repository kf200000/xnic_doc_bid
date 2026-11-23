import base64
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
TEST_TEMP = tempfile.TemporaryDirectory()
os.environ["XNIC_DB_PATH"] = str(Path(TEST_TEMP.name) / "test.db")
os.environ["XNIC_UPLOAD_DIR"] = str(Path(TEST_TEMP.name) / "uploads")
sys.path.insert(0, str(PROJECT_DIR))

from app import app
from database import init_db, connect


class XnicApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = app.test_client()
        app.config.update(TESTING=True)

    def test_01_health_and_index(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()["framework"], "Flask")
        index = self.client.get("/")
        self.assertEqual(index.status_code, 200)
        self.assertIn("XNIC 文档与标书智能工作台", index.get_data(as_text=True))

    def test_02_dashboard_and_filters(self):
        data = self.client.get("/api/dashboard").get_json()
        self.assertEqual(data["stats"]["documents"], 13)
        self.assertEqual(data["stats"]["active_bids"], 4)
        docs = self.client.get("/api/documents?q=华龙一号&category=技术方案").get_json()
        self.assertGreaterEqual(docs["total"], 1)
        bids = self.client.get("/api/bids?status=编制中&reactor=华龙一号").get_json()
        self.assertEqual(bids["total"], 1)

    def test_03_document_upload_download_and_reference(self):
        payload = {
            "name": "接口测试技术方案", "category": "技术方案", "security_level": "内部",
            "department": "测试部", "owner": "测试员", "original_filename": "test.txt",
            "content_base64": base64.b64encode("测试附件".encode("utf-8")).decode("ascii"),
        }
        response = self.client.post("/api/documents", json=payload)
        self.assertEqual(response.status_code, 201)
        doc_id = response.get_json()["id"]
        self.assertTrue(response.get_json()["stored_path"].startswith("documents/技术方案/"))
        download = self.client.get(f"/api/documents/{doc_id}/download")
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data.decode("utf-8"), "测试附件")
        reference = self.client.post(f"/api/documents/{doc_id}/reference", json={"bid_id": 1})
        self.assertEqual(reference.status_code, 200)
        workspace = self.client.get("/api/bids/1/workspace").get_json()
        self.assertTrue(any(m["name"] == payload["name"] for m in workspace["materials"]))

    def test_04_create_bid_and_edit_section(self):
        payload = {"name": "接口测试新建标书", "owner": "测试业主", "reactor_type": "华龙一号", "due_date": "2027-01-20"}
        response = self.client.post("/api/bids", json=payload)
        self.assertEqual(response.status_code, 201)
        bid_id = response.get_json()["id"]
        workspace = self.client.get(f"/api/bids/{bid_id}/workspace").get_json()
        self.assertEqual(len(workspace["sections"]), 9)
        self.assertEqual(sum(s["score"] for s in workspace["scoring"]), 100)
        section_id = workspace["sections"][0]["id"]
        saved = self.client.put(f"/api/sections/{section_id}", json={"content": "测试正文", "status": "已完成"})
        self.assertEqual(saved.status_code, 200)
        again = self.client.get(f"/api/bids/{bid_id}/workspace").get_json()
        self.assertEqual(again["sections"][0]["content"], "测试正文")

    def test_05_ai_generate_and_compliance(self):
        workspace = self.client.get("/api/bids/1/workspace").get_json()
        section = next(s for s in workspace["sections"] if s["code"] == "2.5")
        generated = self.client.post(f"/api/sections/{section['id']}/generate", json={})
        self.assertEqual(generated.status_code, 200)
        self.assertIn("服务响应机制", generated.get_json()["content"])
        result = self.client.post("/api/bids/1/check", json={}).get_json()
        self.assertEqual(result["score"], 86)
        self.assertEqual(len(result["checks"]), 7)
        blind = next(c for c in result["checks"] if c["rule"] == "暗标要求检查")
        self.assertEqual(blind["status"], "warn")
        self.assertIn("2.2", blind["description"])

    def test_06_certificate_and_bid_update(self):
        cert = self.client.post("/api/certificates", json={
            "name": "测试资质", "certificate_no": "TEST-001", "issuer": "测试机关",
            "expiry_date": "2028-12-31", "owner": "测试部",
        })
        self.assertEqual(cert.status_code, 201)
        updated = self.client.put("/api/bids/1", json={"deviation_responded": 86, "signature_complete": 1, "page_count": 386})
        self.assertEqual(updated.status_code, 200)
        result = self.client.post("/api/bids/1/check", json={}).get_json()
        self.assertGreaterEqual(result["score"], 90)

    def test_07_export_preview(self):
        response = self.client.get("/api/bids/1/export")
        self.assertEqual(response.status_code, 200)
        self.assertIn("人工终审", response.get_data(as_text=True))


    def test_08_document_meta_and_fulltext(self):
        meta = self.client.get("/api/documents/meta").get_json()
        self.assertGreaterEqual(meta["stats"]["total"], 13)
        self.assertEqual(meta["stats"]["zombies"], 3)
        self.assertGreaterEqual(meta["stats"]["controlled"], 13)
        self.assertIn("辐射监测（KRT）", meta["options"]["product_lines"])
        found = self.client.get("/api/documents?q=KRT&reactor=华龙一号").get_json()
        self.assertEqual(found["total"], 2)
        self.assertIn("elapsed_ms", found)
        self.assertTrue(all("华龙一号" in item["reactors"] for item in found["items"]))

    def test_09_document_version_control(self):
        payload = {"name": "版本控制测试文档", "category": "技术方案", "product_line": "辐射监测（KRT）",
                   "reactors": ["华龙一号"], "security_level": "内部", "department": "测试部", "owner": "测试员",
                   "original_filename": "v1.txt", "content_base64": base64.b64encode("第一版".encode()).decode()}
        created = self.client.post("/api/documents", json=payload)
        self.assertEqual(created.status_code, 201)
        doc_id = created.get_json()["id"]
        version = self.client.post(f"/api/documents/{doc_id}/versions",
                                   json={"version": "V1.1", "change_note": "补充抗震参数"})
        self.assertEqual(version.status_code, 201)
        self.assertEqual(version.get_json()["status"], "受控")
        detail = self.client.get(f"/api/documents/{doc_id}").get_json()
        self.assertEqual(detail["version"], "V1.1")
        history = next(v for v in detail["versions"] if v["version"] == "V1.0")
        switched = self.client.post(f"/api/documents/{doc_id}/versions/{history['id']}/control",
                                    json={"reason": "回退验证"})
        self.assertEqual(switched.status_code, 200)
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}").get_json()["version"], "V1.0")

    def test_10_duplicate_upload_blocked(self):
        payload = {"name": "重复附件测试", "category": "标准规范", "security_level": "内部",
                   "original_filename": "same.txt", "content_base64": base64.b64encode("同一份内容".encode()).decode()}
        self.assertEqual(self.client.post("/api/documents", json=payload).status_code, 201)
        again = self.client.post("/api/documents", json=payload)
        self.assertEqual(again.status_code, 409)
        self.assertIn("SHA-256", again.get_json()["error"])
        forced = self.client.post("/api/documents", json={**payload, "name": "重复附件测试（强制）", "force": True})
        self.assertEqual(forced.status_code, 201)

    def test_11_security_level_flow(self):
        payload = {"name": "商密外发测试文档", "category": "技术方案", "security_level": "商密",
                   "original_filename": "secret.txt", "content_base64": base64.b64encode("商密内容".encode()).decode()}
        doc_id = self.client.post("/api/documents", json=payload).get_json()["id"]
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}/download").status_code, 403)
        blocked = self.client.get(f"/api/documents/{doc_id}/preview-meta")
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.get_json()["code"], "security_blocked")
        request = self.client.post(f"/api/documents/{doc_id}/security-requests",
                                   json={"kind": "外发", "reason": "向业主提交证明材料"})
        self.assertEqual(request.status_code, 201)
        req_id = request.get_json()["id"]
        self.assertEqual(self.client.post(f"/api/security-requests/{req_id}/decision",
                                          json={"approve": True}).status_code, 200)
        approved = self.client.get(f"/api/documents/{doc_id}/download?req={req_id}")
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.data.decode("utf-8"), "商密内容")
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}/preview-meta").status_code, 200)
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}/raw").status_code, 200)
        downgrade = self.client.post(f"/api/documents/{doc_id}/security-requests",
                                     json={"kind": "降密", "to_level": "内部", "reason": "项目结束"})
        self.assertEqual(downgrade.status_code, 201)
        self.client.post(f"/api/security-requests/{downgrade.get_json()['id']}/decision", json={"approve": True})
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}").get_json()["security_level"], "内部")
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}/download").status_code, 200)

    def test_12_zombie_review(self):
        zombies = self.client.get("/api/documents/zombies").get_json()
        self.assertGreaterEqual(zombies["total"], 1)
        target = zombies["items"][0]
        reviewed = self.client.post(f"/api/documents/{target['id']}/review",
                                    json={"decision": "续用", "note": "仍在使用"})
        self.assertEqual(reviewed.status_code, 200)
        after = self.client.get("/api/documents/zombies").get_json()
        self.assertEqual(after["total"], zombies["total"] - 1)
        self.assertEqual(self.client.get(f"/api/documents/{target['id']}").get_json()["status"], "正常")

    def test_13_ref_stats(self):
        stats = self.client.get("/api/documents/ref-stats").get_json()
        self.assertGreaterEqual(stats["total_refs"], 8)
        self.assertGreaterEqual(len(stats["top"]), 1)
        self.assertTrue(stats["by_bid"])

    def _upload(self, name, level="内部", text="测试正文，含 KRT 抗震参数"):
        payload = {
            "name": name, "category": "技术方案", "security_level": level,
            "department": "核仪器事业部", "owner": "测试员", "version": "V1.0",
            "original_filename": "sample.txt",
            "content_base64": base64.b64encode((text + "\n文档标识：" + name).encode("utf-8")).decode("ascii"),
        }
        res = self.client.post("/api/documents", json=payload)
        self.assertEqual(res.status_code, 201, res.get_data(as_text=True))
        return res.get_json()["id"]

    def test_14_document_edit(self):
        doc_id = self._upload("可编辑文档")
        res = self.client.patch(f"/api/documents/{doc_id}", json={
            "name": "可编辑文档（已更名）", "department": "经营计划部",
            "reactors": ["华龙一号", "VVER"], "change_note": "责任人调整",
        })
        self.assertEqual(res.status_code, 200, res.get_data(as_text=True))
        body = res.get_json()
        self.assertEqual(body["name"], "可编辑文档（已更名）")
        self.assertEqual(body["reactors"], ["华龙一号", "VVER"])
        self.assertEqual(len(body["changed"]), 3)

        # 改分类后附件迁到新分类目录
        moved = self.client.patch(f"/api/documents/{doc_id}", json={"category": "标准规范"})
        self.assertEqual(moved.status_code, 200, moved.get_data(as_text=True))
        self.assertTrue(moved.get_json()["stored_path"].startswith("documents/标准规范/"))
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}/download").status_code, 200)

        # 空变更不应报错
        again = self.client.patch(f"/api/documents/{doc_id}", json={"name": "可编辑文档（已更名）"})
        self.assertEqual(again.get_json()["changed"], [])

        # 密级只能上调
        down = self.client.patch(f"/api/documents/{doc_id}", json={"security_level": "公开"})
        self.assertEqual(down.status_code, 400)
        self.assertIn("降密", down.get_json()["error"])
        up = self.client.patch(f"/api/documents/{doc_id}", json={"security_level": "商密"})
        self.assertEqual(up.status_code, 200)
        self.assertEqual(up.get_json()["security_level"], "商密")

        # 审计留痕
        logs = self.client.get(f"/api/documents/{doc_id}").get_json()["logs"]
        self.assertTrue(any(log["action"] == "修改文档" for log in logs))

        # 名称必填
        bad = self.client.patch(f"/api/documents/{doc_id}", json={"name": "  "})
        self.assertEqual(bad.status_code, 400)

    def test_15_document_delete_restore_purge(self):
        doc_id = self._upload("待删除文档")
        before = self.client.get("/api/documents").get_json()["total"]
        deleted = self.client.delete(f"/api/documents/{doc_id}", json={"reason": "重复上传"})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get("/api/documents").get_json()["total"], before - 1)
        trash = self.client.get("/api/documents?deleted=1").get_json()
        self.assertIn(doc_id, [item["id"] for item in trash["items"]])
        # 回收站内不可下载、不可修改
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}/download").status_code, 409)
        self.assertEqual(self.client.patch(f"/api/documents/{doc_id}", json={"name": "x"}).status_code, 400)

        restored = self.client.post(f"/api/documents/{doc_id}/restore", json={})
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(self.client.get("/api/documents").get_json()["total"], before)

        # 彻底删除：需二次确认
        self.client.delete(f"/api/documents/{doc_id}", json={"reason": "误上传"})
        need_confirm = self.client.delete(f"/api/documents/{doc_id}/purge", json={})
        self.assertEqual(need_confirm.status_code, 400)
        purged = self.client.delete(f"/api/documents/{doc_id}/purge", json={"confirm": "彻底删除"})
        self.assertEqual(purged.status_code, 200)
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}").status_code, 404)

        # 未进回收站的文档不能彻底删除
        live_id = self._upload("在库文档不可彻底删除")
        self.assertEqual(self.client.delete(f"/api/documents/{live_id}/purge",
                                            json={"confirm": "彻底删除"}).status_code, 400)

    def test_16_document_preview(self):
        doc_id = self._upload("预览测试文档")
        meta = self.client.get(f"/api/documents/{doc_id}/preview-meta")
        self.assertEqual(meta.status_code, 200)
        body = meta.get_json()
        self.assertEqual(body["kind"], "text")
        self.assertIn("KRT", body["text"])
        self.assertEqual(body["version"], "V1.0")
        raw = self.client.get(f"/api/documents/{doc_id}/raw")
        self.assertEqual(raw.status_code, 200)
        self.assertIn("KRT", raw.get_data(as_text=True))

        # 历史版本可单独查看
        version = self.client.post(f"/api/documents/{doc_id}/versions",
                                   json={"version": "V1.1", "change_note": "补充参数"})
        self.assertEqual(version.status_code, 201)
        hist = self.client.get(f"/api/documents/{doc_id}").get_json()["versions"]
        old = [v for v in hist if v["version"] == "V1.0"][0]
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}/preview-meta?version_id={old['id']}").status_code, 200)

        # 历史版本可删除，受控版本不可
        controlled = [v for v in hist if v["status"] == "受控"][0]
        self.assertEqual(self.client.delete(
            f"/api/documents/{doc_id}/versions/{controlled['id']}", json={"reason": "x"}).status_code, 400)
        self.assertEqual(self.client.delete(
            f"/api/documents/{doc_id}/versions/{old['id']}", json={"reason": "误上传"}).status_code, 200)

        # 商密文档无外发审批不能预览
        sec_id = self._upload("商密预览文档", level="商密")
        blocked = self.client.get(f"/api/documents/{sec_id}/preview-meta")
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.get_json()["code"], "security_blocked")

    def test_17_certificate_crud_attachment_preview(self):
        png = base64.b64encode(b"\x89PNG-demo-cert-image").decode("ascii")
        payload = {
            "name": "测试用质量体系证书", "certificate_no": "TEST-ISO-001", "issuer": "测试认证中心",
            "expiry_date": "2029-06-30", "owner": "质量管理部", "category": "其他资质",
            "scope": "核仪器设计开发与服务", "original_filename": "cert.png", "content_base64": png,
        }
        created = self.client.post("/api/certificates", json=payload)
        self.assertEqual(created.status_code, 201)
        cert_id = created.get_json()["id"]
        # 编号重复拦截
        dup = self.client.post("/api/certificates", json=payload)
        self.assertEqual(dup.status_code, 409)
        # 详情与扫描件预览
        detail = self.client.get(f"/api/certificates/{cert_id}").get_json()
        self.assertTrue(detail["has_attachment"])
        self.assertEqual(detail["display_state"], "有效")
        meta = self.client.get(f"/api/certificates/{cert_id}/preview-meta")
        self.assertEqual(meta.status_code, 200)
        self.assertEqual(meta.get_json()["kind"], "image")
        raw = self.client.get(f"/api/certificates/{cert_id}/raw")
        self.assertEqual(raw.status_code, 200)
        # 修改留痕 + 日期校验
        patched = self.client.patch(f"/api/certificates/{cert_id}", json={"owner": "质量部（变更）"})
        self.assertEqual(patched.status_code, 200)
        bad = self.client.patch(f"/api/certificates/{cert_id}", json={"expiry_date": "2029/13/01"})
        self.assertEqual(bad.status_code, 400)
        logs = self.client.get(f"/api/certificates/{cert_id}").get_json()["audit_logs"]
        self.assertTrue(any("owner" in (log.get("detail") or "") for log in logs))
        # 删除需要原因，软删 → 回收站 → 恢复
        no_reason = self.client.delete(f"/api/certificates/{cert_id}", json={})
        self.assertEqual(no_reason.status_code, 400)
        soft = self.client.delete(f"/api/certificates/{cert_id}", json={"reason": "误登记演示", "operator": "测试"})
        self.assertEqual(soft.status_code, 200)
        self.assertEqual(self.client.get("/api/certificates?trash=1").get_json()["total"], 1)
        patched_in_trash = self.client.patch(f"/api/certificates/{cert_id}", json={"owner": "x"})
        self.assertEqual(patched_in_trash.status_code, 400)
        restored = self.client.post(f"/api/certificates/{cert_id}/restore", json={})
        self.assertEqual(restored.status_code, 200)
        # 软删 → 彻底删除（记录保留、文件清除）
        self.client.delete(f"/api/certificates/{cert_id}", json={"reason": "演示", "operator": "测试"})
        purge = self.client.delete(f"/api/certificates/{cert_id}", json={"confirm": "彻底删除"})
        self.assertEqual(purge.status_code, 200)
        self.assertEqual(self.client.get("/api/certificates?trash=1").get_json()["total"], 0)
        # 种子资质：预览拦截（未挂扫描件）/ 列表分类统计
        no_file = self.client.get("/api/certificates/5/preview-meta")
        self.assertEqual(no_file.status_code, 404)
        self.assertEqual(no_file.get_json()["code"], "no_file")
        meta_all = self.client.get("/api/certificates/meta").get_json()
        self.assertGreaterEqual(meta_all["stats"]["total"], 8)
        self.assertTrue(meta_all["categories"])

    def test_18_tender_parse(self):
        # 种子数据：基准版 + 澄清差异版
        files = self.client.get("/api/bids/1/tender-files").get_json()
        self.assertEqual(files["total"], 2)
        base = next(f for f in files["items"] if f["version_label"] == "基准版")
        clarify = next(f for f in files["items"] if f["version_label"] == "差异版")
        self.assertEqual(base["declared_total"], 100)
        self.assertEqual(base["score_sum"], 100)
        self.assertGreater(base["pending"], 0)
        detail = self.client.get(f"/api/tender-files/{base['id']}").get_json()
        self.assertTrue(detail["score_ok"])
        self.assertEqual(len(detail["extracts"]), 25)
        cats = {e["category"] for e in detail["extracts"]}
        for c in ("资格条件", "评分办法", "★条款", "废标条款", "技术参数", "交付期", "保证金", "份数装订", "澄清截止"):
            self.assertIn(c, cats)
        # 差异版高亮变更项
        cdetail = self.client.get(f"/api/tender-files/{clarify['id']}").get_json()
        self.assertEqual(cdetail["diff"]["modified"], 2)
        self.assertEqual(cdetail["diff"]["added"], 1)
        # 存在待确认条目时禁止锁定
        locked = self.client.post(f"/api/tender-files/{base['id']}/lock", json={"manager": "测试经理"})
        self.assertEqual(locked.status_code, 409)
        # 逐条人工确认
        for e in [e for e in detail["extracts"] if e["status"] == "待确认"]:
            ok = self.client.patch(f"/api/tender-extracts/{e['id']}", json={"status": "已确认", "user": "测试经理"})
            self.assertEqual(ok.status_code, 200)
        # 修改评分分值 → 分值合计报警
        score_item = next(e for e in detail["extracts"] if e["category"] == "评分办法" and e["score_value"] == 25)
        patched = self.client.patch(f"/api/tender-extracts/{score_item['id']}",
                                    json={"score_value": 30, "status": "已确认", "user": "测试经理"})
        self.assertEqual(patched.status_code, 200)
        self.assertTrue(patched.get_json()["warning"])
        self.client.patch(f"/api/tender-extracts/{score_item['id']}", json={"score_value": 25, "status": "已确认"})
        # 全部确认后锁定基准版
        locked2 = self.client.post(f"/api/tender-files/{base['id']}/lock", json={"manager": "测试经理"})
        self.assertEqual(locked2.status_code, 200)
        # 锁定后禁止修改条目（变更须走澄清差异版）
        after = self.client.patch(f"/api/tender-extracts/{score_item['id']}", json={"status": "已确认"})
        self.assertEqual(after.status_code, 400)
        # 登记澄清 → 生成差异版
        clar = self.client.post(f"/api/tender-files/{base['id']}/clarify", json={
            "filename": "澄清答疑（测试）.pdf", "page_count": 2,
            "items": [
                {"category": "★条款", "change_kind": "修改", "title": "交付周期（澄清修改）",
                 "content": "★交付期限由 18 个月调整为 20 个月。", "page": 5, "confidence": 70},
                {"category": "废标条款", "change_kind": "新增", "title": "电子标书命名",
                 "content": "命名不合规否决其投标。", "confidence": 85},
            ]})
        self.assertEqual(clar.status_code, 201)
        self.assertEqual(clar.get_json()["changes"], 2)
        bad = self.client.post(f"/api/tender-files/{base['id']}/clarify", json={
            "filename": "bad.pdf", "items": [{"category": "无效类别", "title": "x", "content": "y"}]})
        self.assertEqual(bad.status_code, 400)
        # 差异版上不能再挂澄清
        chained = self.client.post(f"/api/tender-files/{clar.get_json()['id']}/clarify", json={"filename": "x.pdf"})
        self.assertEqual(chained.status_code, 400)
        # txt 文本招标文件：真实规则解析（分类 / 页码定位 / 置信度 / 分值）
        bid = self.client.post("/api/bids", json={"name": "解析测试标书", "owner": "测试业主",
                                                  "reactor_type": "VVER", "due_date": "2027-03-01"}).get_json()
        text = "\n".join([
            "第一章 投标人须知",
            "投标人须具备民用核安全设备制造许可证方可参与投标。",
            "★安全级设备须按 SL-2 地震工况进行抗震鉴定。",
            "对★条款存在负偏离的，否决其投标。",
            "投标报价评分：以最低价为基准得满分 25 分。",
            "投标保证金金额为 80 万元，开标前到账。",
            "投标文件正本 1 份副本 4 份，逐页编码。",
            "澄清问题须于 2026-12-01 前书面提出。",
        ])
        reg = self.client.post(f"/api/bids/{bid['id']}/tender-files", json={
            "filename": "解析测试招标文件.txt", "page_count": 8, "declared_total": 25,
            "original_filename": "t.txt", "content_base64": base64.b64encode(text.encode("utf-8")).decode("ascii"),
        })
        self.assertEqual(reg.status_code, 201)
        self.assertGreaterEqual(reg.get_json()["extracts"], 6)
        detail2 = self.client.get(f"/api/tender-files/{reg.get_json()['id']}").get_json()
        cats2 = {e["category"] for e in detail2["extracts"]}
        self.assertIn("★条款", cats2)
        self.assertIn("废标条款", cats2)
        self.assertIn("保证金", cats2)
        self.assertIn("份数装订", cats2)
        self.assertTrue(detail2["score_ok"])
        star = next(e for e in detail2["extracts"] if e["category"] == "★条款")
        self.assertGreaterEqual(star["confidence"], 95)
        self.assertIn("页", star["para"])
        # 无附件登记：演示档案分值合计 100 ≠ 标称 90 → 报警
        reg2 = self.client.post(f"/api/bids/{bid['id']}/tender-files",
                                json={"filename": "演示档案.pdf", "page_count": 300, "declared_total": 90})
        self.assertEqual(reg2.status_code, 201)
        detail3 = self.client.get(f"/api/tender-files/{reg2.get_json()['id']}").get_json()
        self.assertFalse(detail3["score_ok"])
        # 页数超限拦截（单份 ≤500 页）
        bad_pages = self.client.post(f"/api/bids/{bid['id']}/tender-files",
                                     json={"filename": "x.pdf", "page_count": 600})
        self.assertEqual(bad_pages.status_code, 400)
        # 行业术语库
        terms = self.client.get("/api/terms").get_json()
        self.assertGreaterEqual(terms["total"], 7)
        added = self.client.post("/api/terms", json={"term": "RPN", "synonyms": "反应性保护系统"})
        self.assertEqual(added.status_code, 201)
        dup = self.client.post("/api/terms", json={"term": "RPN", "synonyms": "重复"})
        self.assertEqual(dup.status_code, 409)

    def test_19_tender_agent_pipeline(self):
        """解析 Agent v2：PDF/Word 真实抽取、执行轨迹、重新解析、锁定拦截。"""
        import io
        import fitz
        import docx as docx_lib
        bid = self.client.post("/api/bids", json={"name": "Agent解析测试标书", "owner": "测试业主",
                                                  "reactor_type": "华龙一号", "due_date": "2027-05-01"}).get_json()
        # —— 真实 PDF（fitz 构造：章节结构 + 评分行 + ★/废标条款）——
        doc = fitz.open()
        page = doc.new_page()
        lines = [
            "第一章 投标人须知",
            "6.1 投标人须具备有效的民用核安全设备制造许可证，许可范围涵盖辐射监测系统（KRT）。",
            "6.2 近五年至少2项同类成套供货业绩，须附合同与验收证明。",
            "8.1 投标保证金：人民币捌拾万元整（80万元），须在开标前到账。",
            "9.1 澄清截止时间：2026年11月20日17时前提交。",
            "15.2 逾期送达或未按要求密封的投标文件，否决其投标。",
            "15.4 对★条款存在负偏离的，否决其投标。",
            "17.1 投标文件正本1份、副本4份、电子版2份，逐页编码装订。",
            "第二章 评标办法",
            "（10分）技术方案完整性：含系统架构、设备清单、接口设计。",
            "（25分）类似业绩：每提供一项有效业绩得5分，最高25分。",
            "（30分）技术参数响应：对技术规格书逐条响应，无负偏离。",
            "（5分）交付期：满足18个月交付得满分。",
            "第三章 技术规格书",
            "★安全级监测通道须按IE级要求设计鉴定。",
            "4.5.1 系统通讯须提供完整Modbus TCP协议文本及寄存器定义表。",
        ]
        y = 72
        for ln in lines:
            page.insert_text((72, y), ln, fontname="china-s", fontsize=11)
            y += 18
        pdf_bytes = doc.tobytes()
        doc.close()
        reg = self.client.post(f"/api/bids/{bid['id']}/tender-files", json={
            "filename": "Agent测试招标文件.pdf", "page_count": 0, "declared_total": 70,
            "original_filename": "t.pdf", "content_base64": base64.b64encode(pdf_bytes).decode("ascii"),
        })
        self.assertEqual(reg.status_code, 201)
        fid = reg.get_json()["id"]
        self.assertEqual(reg.get_json()["extracts"], 13)
        detail = self.client.get(f"/api/tender-files/{fid}").get_json()
        self.assertTrue(detail["score_ok"])  # 10+25+30+5 = 70
        self.assertEqual(detail["page_count"], 1)  # 真实页数回填
        rp = detail["parse_report"]
        self.assertEqual(rp["agent"], "招标解析 Agent v2")
        self.assertEqual(len(rp["steps"]), 5)
        self.assertIn("PDF", rp["steps"][0]["detail"])
        self.assertEqual(rp["summary"]["sections"], 3)
        cats = {e["category"] for e in detail["extracts"]}
        for c in ("资格条件", "评分办法", "★条款", "废标条款", "保证金", "份数装订", "澄清截止", "技术参数"):
            self.assertIn(c, cats)
        # 判定依据（signals）随条目留痕
        star = next(e for e in detail["extracts"] if e["category"] == "★条款")
        self.assertTrue(star["signals"])
        # 评分表行分值抽取正确（防止叙述句"得5分"抢分值）
        def sv_of(prefix):
            return next((e["score_value"] for e in detail["extracts"]
                         if e["category"] == "评分办法" and e["title"].startswith(prefix)), None)
        self.assertEqual(sv_of("技术方案完整性"), 10)
        self.assertEqual(sv_of("技术参数响应"), 30)
        # —— 重新解析（未锁定）——
        rp2 = self.client.post(f"/api/tender-files/{fid}/reparse", json={})
        self.assertEqual(rp2.status_code, 200)
        self.assertEqual(rp2.get_json()["extracts"], 13)
        # —— 锁定后禁止重新解析 ——
        for e in self.client.get(f"/api/tender-files/{fid}").get_json()["extracts"]:
            if e["status"] == "待确认":
                self.client.patch(f"/api/tender-extracts/{e['id']}", json={"status": "已确认", "user": "测试"})
        locked = self.client.post(f"/api/tender-files/{fid}/lock", json={"manager": "测试经理"})
        self.assertEqual(locked.status_code, 200)
        rp3 = self.client.post(f"/api/tender-files/{fid}/reparse", json={})
        self.assertEqual(rp3.status_code, 409)
        # —— 真实 Word（python-docx 构造：表格评分行）——
        d = docx_lib.Document()
        d.add_paragraph("第一章 投标人须知")
        d.add_paragraph("6.1 投标人须具备有效的民用核安全设备制造许可证。")
        d.add_paragraph("15.4 对★条款存在负偏离的，否决其投标。")
        t = d.add_table(rows=2, cols=3)
        t.rows[0].cells[0].text = "评分项"
        t.rows[0].cells[1].text = "分值"
        t.rows[1].cells[0].text = "技术方案完整性"
        t.rows[1].cells[1].text = "10"
        d.add_paragraph("第二章 评标办法")
        d.add_paragraph("（30分）技术参数响应：对技术规格书逐条响应。")
        buf = io.BytesIO()
        d.save(buf)
        reg2 = self.client.post(f"/api/bids/{bid['id']}/tender-files", json={
            "filename": "Agent测试招标文件.docx", "declared_total": 40,
            "original_filename": "t.docx", "content_base64": base64.b64encode(buf.getvalue()).decode("ascii"),
        })
        self.assertEqual(reg2.status_code, 201)
        detail2 = self.client.get(f"/api/tender-files/{reg2.get_json()['id']}").get_json()
        self.assertEqual(reg2.get_json()["extracts"], 4)
        self.assertTrue(detail2["score_ok"])  # 表格 10 + 段落 30 = 40
        row = next(e for e in detail2["extracts"] if "评标办法表" in (e["para"] or ""))
        self.assertEqual(row["score_value"], 10)
        self.assertIn("表格分值列", row["signals"])
        # —— 不支持的格式：明确告警而非伪造结果 ——
        reg3 = self.client.post(f"/api/bids/{bid['id']}/tender-files", json={
            "filename": "压缩包招标文件.zip", "original_filename": "t.zip",
            "content_base64": base64.b64encode(b"PK\x03\x04fakezip").decode("ascii"),
        })
        self.assertEqual(reg3.status_code, 201)
        detail3 = self.client.get(f"/api/tender-files/{reg3.get_json()['id']}").get_json()
        self.assertEqual(reg3.get_json()["extracts"], 0)
        self.assertIn("格式不可解析", detail3["parse_report"]["fallback"])

    def test_20_tender_file_edit_and_void(self):
        """招标文件登记信息可修改（留痕）、可作废（软删除+恢复）；锁定与作废态受控。"""
        bid = self.client.post("/api/bids", json={
            "name": "招标文件增删改用例", "owner": "测试业主", "reactor_type": "华龙一号",
            "due_date": "2026-12-31"}).get_json()
        bid_id = bid["id"]
        reg = self.client.post(f"/api/bids/{bid_id}/tender-files", json={
            "filename": "待更正招标文件（误登记）.pdf", "page_count": 300, "declared_total": 100})
        self.assertEqual(reg.status_code, 201)
        fid = reg.get_json()["id"]
        detail = self.client.get(f"/api/tender-files/{fid}").get_json()
        self.assertTrue(detail["editable"])
        self.assertTrue(detail["deletable"])
        self.assertFalse(detail["deleted"])

        # —— 修改登记信息：名称 / 页数 / 标称总分 / 备注 ——
        patched = self.client.patch(f"/api/tender-files/{fid}", json={
            "filename": "更正后招标文件.pdf", "page_count": 386, "declared_total": 120,
            "remark": "页数以纸质原件为准", "user": "投标经理 · 测试"})
        self.assertEqual(patched.status_code, 200)
        d2 = self.client.get(f"/api/tender-files/{fid}").get_json()
        self.assertEqual(d2["filename"], "更正后招标文件.pdf")
        self.assertEqual(d2["page_count"], 386)
        self.assertEqual(d2["declared_total"], 120)
        self.assertEqual(d2["remark"], "页数以纸质原件为准")
        self.assertFalse(d2["score_ok"])            # 标称 120 ≠ 抽取合计 100
        self.assertTrue(patched.get_json()["warning"])  # 改标称总分立即触发分值校验提示

        # —— 入参校验 ——
        self.assertEqual(self.client.patch(f"/api/tender-files/{fid}", json={"filename": "  "}).status_code, 400)
        self.assertEqual(self.client.patch(f"/api/tender-files/{fid}", json={"page_count": 600}).status_code, 400)
        self.assertEqual(self.client.patch(f"/api/tender-files/{fid}", json={"declared_total": "abc"}).status_code, 400)

        # —— 作废：原因必填，软删除不影响明细查看 ——
        self.assertEqual(self.client.delete(f"/api/tender-files/{fid}", json={}).status_code, 400)
        deleted = self.client.delete(f"/api/tender-files/{fid}", json={"reason": "重复登记", "user": "投标经理 · 测试"})
        self.assertEqual(deleted.status_code, 200)
        listed = self.client.get(f"/api/bids/{bid_id}/tender-files").get_json()
        self.assertEqual(listed["total"], 0)
        self.assertEqual(listed["deleted_total"], 1)
        all_items = self.client.get(f"/api/bids/{bid_id}/tender-files?include_deleted=1").get_json()
        self.assertEqual(all_items["total"], 1)
        self.assertTrue(all_items["items"][0]["deleted"])
        self.assertFalse(all_items["items"][0]["editable"])
        void_detail = self.client.get(f"/api/tender-files/{fid}").get_json()
        self.assertEqual(void_detail["delete_reason"], "重复登记")
        self.assertGreater(void_detail["total"], 0)   # 抽取结果保留备查
        # 作废后不可改、不可重复作废、不可重新解析
        self.assertEqual(self.client.patch(f"/api/tender-files/{fid}", json={"filename": "x.pdf"}).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/tender-files/{fid}", json={"reason": "再次作废"}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/tender-files/{fid}/reparse", json={}).status_code, 409)

        # —— 恢复 ——
        self.assertEqual(self.client.post(f"/api/tender-files/{fid}/restore", json={}).status_code, 200)
        restored = self.client.get(f"/api/bids/{bid_id}/tender-files").get_json()
        self.assertEqual(restored["total"], 1)
        self.assertEqual(restored["items"][0]["declared_total"], 120)

        # —— 已锁定基准版：登记信息不可改、不可作废 ——
        detail = self.client.get(f"/api/tender-files/{fid}").get_json()
        for ext in detail["extracts"]:
            if ext["status"] == "待确认":
                self.client.patch(f"/api/tender-extracts/{ext['id']}", json={"status": "已确认"})
        locked = self.client.post(f"/api/tender-files/{fid}/lock", json={"manager": "投标经理"})
        self.assertEqual(locked.status_code, 200)
        locked_detail = self.client.get(f"/api/tender-files/{fid}").get_json()
        self.assertFalse(locked_detail["editable"])
        self.assertFalse(locked_detail["deletable"])
        self.assertEqual(self.client.patch(f"/api/tender-files/{fid}", json={"filename": "锁定后改名.pdf"}).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/tender-files/{fid}", json={"reason": "锁定后作废"}).status_code, 409)

        # —— 基准版存在未作废差异版时，基准版不可先作废 ——
        reg2 = self.client.post(f"/api/bids/{bid_id}/tender-files", json={
            "filename": "第二份基准版.pdf", "page_count": 200, "declared_total": 100})
        base2 = reg2.get_json()["id"]
        clarify = self.client.post(f"/api/tender-files/{base2}/clarify", json={
            "filename": "第二份基准版 澄清第1号.pdf",
            "items": [{"category": "交付期", "title": "交付期调整", "content": "由 18 个月调整为 20 个月",
                       "page": 12, "change_kind": "修改"}]})
        self.assertEqual(clarify.status_code, 201)
        blocked = self.client.delete(f"/api/tender-files/{base2}", json={"reason": "想直接删基准版"})
        self.assertEqual(blocked.status_code, 409)
        self.assertIn("澄清差异版", blocked.get_json()["error"])
        # 先作废差异版，再作废基准版则放行
        cid = clarify.get_json()["id"]
        self.assertEqual(self.client.delete(f"/api/tender-files/{cid}", json={"reason": "澄清作废"}).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/tender-files/{base2}", json={"reason": "误登记"}).status_code, 200)

    def test_21_tender_source_view(self):
        """原文浏览：docx 走结构化分页视图（页码与抽取同源），演示档案明确提示无原件，raw 直链可用。"""
        buf = io.BytesIO()
        import docx as docx_lib
        d = docx_lib.Document()
        d.add_paragraph("第一章 招标公告")
        d.add_paragraph("1.1 项目名称：原文浏览测试项目。")
        d.add_paragraph("2.6　交货期：合同签订后之日起12个月内完成全部调试工作。")
        d.save(buf)
        reg = self.client.post("/api/bids/1/tender-files", json={
            "filename": "原文浏览测试.docx", "declared_total": None,
            "original_filename": "src.docx", "content_base64": base64.b64encode(buf.getvalue()).decode("ascii")})
        self.assertEqual(reg.status_code, 201)
        fid = reg.get_json()["id"]
        src = self.client.get(f"/api/tender-files/{fid}/source").get_json()
        self.assertEqual(src["kind"], "docx")
        self.assertGreater(len(src["pages"]), 0)
        self.assertTrue(src["raw_url"].endswith("/raw"))
        texts = [b["text"] for p in src["pages"] for b in p["blocks"]]
        self.assertTrue(any(t.startswith("2.6") for t in texts))
        raw = self.client.get(f"/api/tender-files/{fid}/raw")
        self.assertEqual(raw.status_code, 200)
        self.assertIn("wordprocessingml", raw.headers.get("Content-Type", ""))
        # 演示档案（无附件）：明确告知无原件而非伪造
        demo = self.client.post("/api/bids/1/tender-files", json={"filename": "演示档案无附件.pdf"}).get_json()
        src2 = self.client.get(f"/api/tender-files/{demo['id']}/source").get_json()
        self.assertEqual(src2["kind"], "missing")
        self.assertIn("未上传原始文件", src2["note"])
        self.assertEqual(self.client.get(f"/api/tender-files/{demo['id']}/raw").status_code, 404)

    def test_22_docx_real_pagination(self):
        """Word 页码：有排版引擎时取真实分页，无则降级估算——两种都必须自洽且可解释。"""
        from tender_agent import extract_docx_blocks, docx_to_pdf, PDF_CACHE_DIR
        import docx as docx_lib
        from docx.enum.text import WD_BREAK

        d = docx_lib.Document()
        d.add_paragraph("第一章 招标公告")           # 目录锚点同名行
        for i in range(40):
            d.add_paragraph(f"第一部分内容第 {i + 1} 行，用于占位排版行数与版面估算。")
        p = d.add_paragraph()
        p.add_run().add_break(WD_BREAK.PAGE)          # 显式分页符
        d.add_paragraph("第二章 投标人须知")
        d.add_paragraph("分隔符之后的段落，必须落在新的一页上。")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "paging.docx"
            d.save(src)
            blocks, page_count, page_source = extract_docx_blocks(src)
            self.assertIn(page_source, ("typeset", "estimate"))
            self.assertGreaterEqual(page_count, 2)
            before = next(b for b in blocks if b["text"].startswith("第一部分内容第 1 行"))
            after = next(b for b in blocks if b["text"].startswith("分隔符之后"))
            self.assertGreater(after["page"], before["page"], "分页符之后的段落页码必须递增")

            # 有排版引擎时，页码取自真实排版（缓存文件应生成）
            has_engine = page_source == "typeset"
            if has_engine:
                self.assertTrue(any(PDF_CACHE_DIR.glob("paging*.pdf")), "排版缓存未生成")

        # 真实招标文件回归：红华文件的"七、强化契约精神…"在纸质第 99 页
        real = Path(r"C:\doc\四川红华\【非密】四川红华-2602-辅助系统事故自动诊断处理平台招标文件.docx")
        if real.exists():
            blocks2, pages2, src2 = extract_docx_blocks(real)
            hit = next((b for b in blocks2 if "强化契约精神" in b["text"]), None)
            self.assertIsNotNone(hit, "真实文件中未找到目标段落")
            self.assertEqual(pages2, 142)
            if src2 == "typeset":
                self.assertEqual(hit["page"], 99, "真实排版页码应与纸质文件一致（第 99 页）")
            else:
                # 无排版引擎时仅要求章级锚定：落在合同章（44）与供货章（100）之间
                self.assertTrue(44 <= hit["page"] <= 100)

    def test_23_layout_pdf_endpoint(self):
        """排版稿接口：Word 文件返回 PDF（有引擎时），PDF 文件明确拒绝。"""
        from tender_agent import docx_to_pdf
        import docx as docx_lib
        d = docx_lib.Document()
        d.add_paragraph("第一章 测试")
        d.add_paragraph("排版稿接口验证段落。")
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "layout.docx"
            d.save(src)
            if docx_to_pdf(src)[0] is None:
                self.skipTest("本机无 Office/WPS 排版引擎")
        buf = io.BytesIO()
        d.save(buf)
        reg = self.client.post("/api/bids/1/tender-files", json={
            "filename": "排版稿接口验证.docx", "page_count": 0, "declared_total": 100,
            "original_filename": "layout.docx",
            "content_base64": base64.b64encode(buf.getvalue()).decode(),
        })
        self.assertEqual(reg.status_code, 201)
        fid = reg.get_json()["id"]
        src = self.client.get(f"/api/tender-files/{fid}/source").get_json()
        self.assertEqual(src["page_source"], "typeset")
        pdf = self.client.get(f"/api/tender-files/{fid}/layout.pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.headers["Content-Type"], "application/pdf")
        self.assertGreater(len(pdf.data), 1024)
        pdf.close()

    def test_24_fractional_scores(self):
        """小数分值（0.5 分等）：解析引擎识别、人工修正保存、分值校验容差比较。"""
        from tender_agent import _score_value, _validate
        # 解析引擎：小数分值识别 + 时间防误报不回归
        self.assertEqual(_score_value("若提供绿色认证，本项目得0.5分"), 0.5)
        self.assertEqual(_score_value("（1.5分）三体系认证"), 1.5)
        self.assertIsNone(_score_value("09时30分"))
        # 校验器：0.5 尾差不算不一致
        checks = _validate([{"category": "评分办法", "score_value": 99.5, "content": "x" * 30}], 100)
        total_check = next(c for c in checks if c["name"] == "评分分值合计")
        self.assertFalse(total_check["ok"])
        checks_ok = _validate([{"category": "评分办法", "score_value": 60.5, "content": "x" * 30},
                               {"category": "评分办法", "score_value": 39.5, "content": "y" * 30}], 100)
        self.assertTrue(next(c for c in checks_ok if c["name"] == "评分分值合计")["ok"])

        # 接口：登记 → 修正分值为 0.5 → 合计与校验联动
        import docx as docx_lib
        buf = io.BytesIO()
        d = docx_lib.Document()
        d.add_paragraph("第一章 评标办法")
        d.add_paragraph("（40分）技术方案。")
        d.add_paragraph("15.4 对★条款存在负偏离的，否决其投标。")
        d.save(buf)
        reg = self.client.post("/api/bids/1/tender-files", json={
            "filename": "小数分值验证.docx", "page_count": 10, "declared_total": 100,
            "original_filename": "frac.docx",
            "content_base64": base64.b64encode(buf.getvalue()).decode(),
        })
        self.assertEqual(reg.status_code, 201)
        fid = reg.get_json()["id"]
        detail = self.client.get(f"/api/tender-files/{fid}").get_json()
        score_item = next(e for e in detail["extracts"] if e["category"] == "评分办法")
        ext_id = score_item["id"]
        # 整数 40 → 0.5（引擎抽取为整数，人工修正为小数）
        self.assertEqual(score_item["score_value"], 40)
        patch = self.client.patch(f"/api/tender-extracts/{ext_id}", json={
            "score_value": 0.5, "status": "已确认", "user": "投标经理 · 演示"})
        self.assertEqual(patch.status_code, 200)
        body = patch.get_json()
        self.assertEqual(body["score_sum"], 0.5)  # 小数分值被如实保存（旧逻辑会取整为 0 或 1）
        self.assertIn("0.5", body["warning"])     # 合计 0.5 ≠ 100 的报警仍触发
        # 非法分值拒绝
        bad = self.client.patch(f"/api/tender-extracts/{ext_id}", json={"score_value": 0, "user": "x"})
        self.assertEqual(bad.status_code, 400)
        bad2 = self.client.patch(f"/api/tender-extracts/{ext_id}", json={"score_value": "abc", "user": "x"})
        self.assertEqual(bad2.status_code, 400)


    def test_25_tech_spec_outline_and_mapping(self):
        """技术规格书独立解析：抽规格条目 → 归纳方案目录 → 标注技术标映射（弱映射不硬套）。"""
        import docx as docx_lib
        buf = io.BytesIO()
        d = docx_lib.Document()
        d.add_paragraph("第五章 供货要求")
        d.add_paragraph("技术要求")
        d.add_paragraph("1.概述")
        d.add_paragraph("1.1 项目名称：辅助系统事故自动诊断处理平台采购。")
        d.add_paragraph("1.2 供货范围：水辅助系统仿真平台建模、故障诊断系统与视频监控系统软硬件。")
        d.add_paragraph("2.基本工作条件")
        d.add_paragraph("2.1 运行模式：7×24 小时连续运行，视频监控与故障诊断系统共用一台服务器。")
        d.add_paragraph("3.主要技术条件")
        d.add_paragraph("3.1 系统总体组成")
        d.add_paragraph("3.2 仿真平台系统（独立建设）")
        d.add_paragraph("1）功能要求")
        d.add_paragraph("支持事故模拟、操作演练、故障注入、自动评分与培训管理功能，具备标准组件库。")
        d.add_paragraph("1:1 机理建模，稳态参数偏差≤10%，设备模型误差≤15%，支持多任务并发。")
        d.add_paragraph("2）硬件物资要求")
        d.add_paragraph("仿真服务器：国产 CPU、内存 512GB 及以上，数量 2 台。")
        d.add_paragraph("3.3 故障诊断系统（与视频监控系统共用服务器）")
        d.add_paragraph("1）功能要求")
        d.add_paragraph("支持实时采集、状态显示、趋势分析与分级报警，提供设备台账、报警管理与运维知识库功能。")
        d.add_paragraph("故障类型识别准确率≥95%，重要参数异常报警响应速度≤2s。")
        d.add_paragraph("★所有硬件设备必须为全新原厂正品，提供合格证与检测报告。")
        d.add_paragraph("4.标准规范")
        d.add_paragraph("4.1 国家标准：遵循 GB/T 28181 与 GB 50174 现行标准。")
        d.add_paragraph("5.实施与售后")
        d.add_paragraph("5.1 总工期：合同生效后 6 个月内完成安装调试与验收。")
        d.add_paragraph("5.2 质保期：验收合格后 24 个月，7×24 小时响应。")
        table = d.add_table(rows=3, cols=4)
        for i, row in enumerate([["序号", "物料名称", "单位", "数量"],
                                 ["1", "核心交换机", "台", "1"],
                                 ["2", "磁吸式温振传感器", "台", "250"]]):
            for j, val in enumerate(row):
                table.cell(i, j).text = val
        d.save(buf)

        reg = self.client.post("/api/bids/1/tender-files", json={
            "filename": "辅助系统技术规格书.docx", "page_count": 8, "declared_total": 100,
            "original_filename": "tech.docx",
            "content_base64": base64.b64encode(buf.getvalue()).decode(),
        })
        self.assertEqual(reg.status_code, 201)
        fid = reg.get_json()["id"]

        parsed = self.client.post(f"/api/tender-files/{fid}/tech-spec/parse", json={})
        self.assertEqual(parsed.status_code, 200, parsed.get_data(as_text=True))
        data = parsed.get_json()
        self.assertTrue(data["exists"])
        self.assertGreaterEqual(data["req_count"], 8)
        self.assertGreaterEqual(data["must_count"], 1)
        titles = [o["title"] for o in data["outline"]]
        cats = {c for o in data["outline"] for c in (o.get("categories") or [])}
        self.assertIn("性能指标", cats)
        self.assertIn("设备选型与供货清单", titles)
        self.assertIn("标准规范符合性", titles)
        # 分系统章应识别出仿真平台系统与故障诊断系统两个子系统（性能要求归入各自子系统）
        sub = next(o for o in data["outline"] if o["title"] == "分系统设计方案")
        sub_titles = " ".join(c["title"] for c in data["outline"] if c["level"] == 2)
        self.assertIn("仿真平台系统", sub_titles)
        self.assertIn("故障诊断系统", sub_titles)
        self.assertGreaterEqual(sub["req_count"] + sub["child_req_count"], 3)
        # 映射：实施/售后/质量挂到现有章节（强），分系统因专业域不符（KRT）不硬套
        by_title = {o["title"]: o for o in data["outline"]}
        self.assertEqual(by_title["实施进度与组织保障"]["mapping_kind"], "strong")
        self.assertEqual(by_title["实施进度与组织保障"]["section"]["code"], "2.4")
        self.assertEqual(by_title["培训与售后运维保障"]["mapping_kind"], "strong")
        self.assertEqual(by_title["分系统设计方案"]["mapping_kind"], "weak")
        self.assertIsNone(by_title["分系统设计方案"]["section"])
        # 条目必须带原文页码与判定依据，可回看（性能要求归入子系统章节）
        perf = next(o for o in data["outline"] if o["level"] == 2 and "故障诊断系统" in o["title"])
        self.assertTrue(perf["requirements"])
        self.assertTrue(all(r["page"] for r in perf["requirements"]))
        self.assertTrue(any(r["signals"] for r in perf["requirements"]))
        self.assertIsNotNone(perf["page_from"])

        # —— 同步到标书编制：新建章节 + 预置"新要求"清单 ——
        before = self.client.get("/api/bids/1/workspace").get_json()
        applied = self.client.post(f"/api/tender-files/{fid}/tech-spec/apply", json={"user": "投标经理"})
        self.assertEqual(applied.status_code, 200, applied.get_data(as_text=True))
        body = applied.get_json()
        self.assertGreater(body["created"], 0)
        after = self.client.get("/api/bids/1/workspace").get_json()
        self.assertEqual(len(after["sections"]), len(before["sections"]) + body["created"])
        # 技术标章节编号连续（2.x），且新章节带技术规格要求清单
        tech_codes = [s["code"] for s in after["sections"] if s["group_name"] == "技术标"]
        self.assertEqual(len(tech_codes), len(set(tech_codes)))
        new_sec = next(s for s in after["sections"] if "设备选型" in s["title"])
        self.assertIn("技术规格要求", new_sec["content"])
        self.assertIn("【本节编写要点】", new_sec["content"])
        # 工作区回传按章节聚合的技术规格，供编制页"技术规格"页签使用
        self.assertIsNotNone(after["tech_spec"])
        self.assertEqual(after["tech_spec"]["req_count"], data["req_count"])
        mapped_ids = {x["section_id"] for x in after["tech_spec"]["by_section"]}
        self.assertIn(new_sec["id"], mapped_ids)
        mapped_sec = next(s for s in after["sections"] if s["id"] in mapped_ids)
        self.assertGreater(mapped_sec["tech_req_count"], 0)

    def test_26_tech_spec_idempotent_and_manual_mapping(self):
        """再次同步不重复建章；人工改映射后工作区随之更新；跨标书映射被拒绝。"""
        files = self.client.get("/api/bids/1/tender-files").get_json()["items"]
        fid = next(f["id"] for f in files if f["filename"] == "辅助系统技术规格书.docx")
        spec = self.client.get(f"/api/tender-files/{fid}/tech-spec").get_json()
        again = self.client.post(f"/api/tender-files/{fid}/tech-spec/apply", json={}).get_json()
        self.assertEqual(again["created"], 0)
        self.assertEqual(again["reused"], spec["outline_count"])

        target = next(o for o in spec["outline"] if o["level"] == 2)
        ws = self.client.get("/api/bids/1/workspace").get_json()
        sec = next(s for s in ws["sections"] if s["group_name"] == "技术标" and s["code"] == "2.3")
        patched = self.client.patch(f"/api/tech-outline/{target['id']}",
                                    json={"section_id": sec["id"], "title": target["title"]})
        self.assertEqual(patched.status_code, 200)
        refreshed = self.client.get(f"/api/tender-files/{fid}/tech-spec").get_json()
        item = next(o for o in refreshed["outline"] if o["id"] == target["id"])
        self.assertEqual(item["mapping_kind"], "strong")
        self.assertEqual(item["section"]["id"], sec["id"])
        # 取消映射 → 回到"待新建"
        cleared = self.client.patch(f"/api/tech-outline/{target['id']}", json={"section_id": None})
        self.assertEqual(cleared.status_code, 200)
        cleared_item = next(o for o in self.client.get(f"/api/tender-files/{fid}/tech-spec").get_json()["outline"]
                            if o["id"] == target["id"])
        self.assertEqual(cleared_item["mapping_kind"], "new")
        # 映射到别的标书章节应被拒绝
        other = self.client.get("/api/bids/2/workspace").get_json()["sections"][0]["id"]
        bad = self.client.patch(f"/api/tech-outline/{target['id']}", json={"section_id": other})
        self.assertEqual(bad.status_code, 400)
        # 未解析的文件 / 不存在的文件
        demo = self.client.post("/api/bids/1/tender-files", json={"filename": "无附件技术规格.pdf"}).get_json()
        self.assertEqual(self.client.post(f"/api/tender-files/{demo['id']}/tech-spec/parse", json={}).status_code, 400)
        self.assertFalse(self.client.get(f"/api/tender-files/{demo['id']}/tech-spec").get_json()["exists"])
        self.assertEqual(self.client.get("/api/tender-files/99999/tech-spec").status_code, 404)


    def test_27_tech_children_sub_sections(self):
        """“插入本节”→ 在当前章节下新建三级子目录：编号 2.x.y、幂等、sort_order 落位正确。"""
        ws = self.client.get("/api/bids/1/workspace").get_json()
        sec = next(s for s in ws["sections"] if s["group_name"] == "技术标" and s["code"] == "2.3")
        items = [{"title": "数据分析", "content": "支撑三大子系统的图版、手册、程序、测试报告。",
                  "page": 8, "must": False},
                 {"title": "到货验收", "content": "核对三大子系统的图版、手册、程序并三方见证。",
                  "page": 11, "must": True}]
        r = self.client.post(f"/api/sections/{sec['id']}/tech-children", json={"items": items}).get_json()
        self.assertEqual(len(r["created"]), 2)
        self.assertEqual([c["code"] for c in r["created"]], ["2.3.1", "2.3.2"])
        # 幂等：同名条目不重复创建
        again = self.client.post(f"/api/sections/{sec['id']}/tech-children", json={"items": items}).get_json()
        self.assertEqual(again["created"], [])
        # 子目录出现在章节列表，编号与排序正确（2.3.1 落在 2.3 与 2.4 之间）
        ws2 = self.client.get("/api/bids/1/workspace").get_json()
        codes = [s["code"] for s in ws2["sections"]]
        self.assertIn("2.3.1", codes)
        i23, i231, i24 = codes.index("2.3"), codes.index("2.3.1"), codes.index("2.4")
        self.assertTrue(i23 < i231 < i24)
        child = next(s for s in ws2["sections"] if s["code"] == "2.3.2")
        self.assertIn("【要求】核对三大子系统", child["content"])
        self.assertIn("原文第 11 页", child["content"])
        self.assertEqual(child["group_name"], "技术标")
        # 空条目 / 不存在的章节
        self.assertEqual(self.client.post(f"/api/sections/{sec['id']}/tech-children",
                                          json={"items": []}).status_code, 400)
        self.assertEqual(self.client.post("/api/sections/99999/tech-children",
                                          json={"items": items}).status_code, 404)


    def _sec_tree(self, sections, mutate=None):
        nodes = {s["code"]: dict(s, children=[]) for s in sections}
        roots = []
        for s in sections:
            parent = ".".join(s["code"].split(".")[:-1])
            if parent in nodes:
                nodes[parent]["children"].append(nodes[s["code"]])
            else:
                roots.append(nodes[s["code"]])
        if mutate:
            mutate(roots, nodes)

        def conv(lst, root):
            return [{"id": n["id"], **({"group_name": n["group_name"]} if root else {}),
                     "children": conv(n["children"], False)} for n in lst]
        return conv(roots, True)

    def test_28_section_tree_crud_and_drag(self):
        """左侧目录树：新增（同级/子级/组末）、重命名、删除（子树保护）、拖拽重排重编号。"""
        ws = self.client.get("/api/bids/1/workspace").get_json()
        by_code = {s["code"]: s for s in ws["sections"]}
        # 1) 新增：2.2 之后同级插入 → 新节 2.3，原 2.3/2.4/2.5 顺延
        r = self.client.post("/api/bids/1/sections",
                             json={"title": "测试插入章", "anchor_id": by_code["2.2"]["id"],
                                   "position": "after"}).get_json()
        self.assertEqual(r["code"], "2.3")
        ws = self.client.get("/api/bids/1/workspace").get_json()
        titles = {s["code"]: s["title"] for s in ws["sections"]}
        self.assertEqual(titles["2.4"], "质量保证与核安全文化")
        self.assertEqual(titles["2.6"], "售后服务与备件保障")
        self.assertFalse(any("~" in c for c in titles))
        # 2) 新增子节：挂在 2.2 下 → 2.2.1
        r2 = self.client.post("/api/bids/1/sections",
                              json={"title": "测试子节", "anchor_id": by_code["2.2"]["id"],
                                    "position": "child"}).get_json()
        self.assertEqual(r2["code"], "2.2.1")
        # 3) 重命名
        patched = self.client.patch(f"/api/sections/{r2['id']}", json={"title": "改名后的子节"})
        self.assertEqual(patched.status_code, 200)
        ws = self.client.get("/api/bids/1/workspace").get_json()
        self.assertEqual(next(s for s in ws["sections"] if s["id"] == r2["id"])["title"], "改名后的子节")
        # 4) 删除：叶子直接删；带子节先 409 再递归删
        self.assertEqual(self.client.delete(f"/api/sections/{r['id']}", json={}).status_code, 200)
        blocked = self.client.delete(f"/api/sections/{by_code['2.2']['id']}", json={})
        self.assertEqual(blocked.status_code, 409)
        self.assertGreater(blocked.get_json()["children"], 0)
        ok = self.client.delete(f"/api/sections/{by_code['2.2']['id']}", json={"recursive": True})
        self.assertEqual(ok.status_code, 200)
        ws = self.client.get("/api/bids/1/workspace").get_json()
        self.assertNotIn("2.2", {s["code"] for s in ws["sections"]})
        # 5) 拖拽：把 1.1 投标函移到技术标组末（跨组）→ 编号 2.x、组名跟随
        ws = self.client.get("/api/bids/1/workspace").get_json()

        def move(roots, nodes):
            n = nodes["1.1"]
            roots.remove(n)
            n["group_name"] = "技术标"
            roots.append(n)

        tree = self._sec_tree(ws["sections"], move)
        rr = self.client.post("/api/bids/1/sections/reorder", json={"tree": tree}).get_json()
        self.assertIn("重排", rr["message"])
        ws = self.client.get("/api/bids/1/workspace").get_json()
        moved = next(s for s in ws["sections"] if s["title"] == "投标函")
        self.assertTrue(moved["code"].startswith("2."))
        self.assertEqual(moved["group_name"], "技术标")
        # 6) 拖回原位：树不完整 → 拒绝且数据不变
        tree2 = self._sec_tree(ws["sections"])
        tree2.pop()
        bad = self.client.post("/api/bids/1/sections/reorder", json={"tree": tree2})
        self.assertEqual(bad.status_code, 400)
        ws2 = self.client.get("/api/bids/1/workspace").get_json()
        self.assertEqual(len(ws2["sections"]), len(ws["sections"]))


    def test_29_llm_compose_and_content_fields(self):
        """正文编写：content 原文保留、content_ai 存正文；未配置模型降级为结构化模板；配置后走大模型。"""
        import llm_client
        from unittest.mock import patch

        cfg_path = Path(TEST_TEMP.name) / "llm-none.json"
        os.environ["XNIC_LLM_CONFIG"] = str(cfg_path)
        os.environ.pop("XNIC_LLM_API_KEY", None)
        self.assertFalse(llm_client.is_configured())

        # 前置：确保本标书已完成技术规格解析与同步（本节要求清单是编写上下文来源）
        files = self.client.get("/api/bids/1/tender-files").get_json()["items"]
        fid = next((f["id"] for f in files if f["filename"] == "辅助系统技术规格书.docx"), None)
        if not fid:
            import docx as docx_lib
            buf = io.BytesIO()
            d = docx_lib.Document()
            for text in ["第五章 供货要求", "技术要求", "1.概述",
                         "1.1 项目名称：辅助系统事故自动诊断处理平台采购。",
                         "3.主要技术条件", "3.2 仿真平台系统（独立建设）", "1）功能要求",
                         "支持事故模拟、操作演练、故障注入、自动评分与培训管理功能。",
                         "故障类型识别准确率≥95%，重要参数异常报警响应速度≤2s。",
                         "★所有硬件设备必须为全新原厂正品，提供合格证与检测报告。"]:
                d.add_paragraph(text)
            d.save(buf)
            reg = self.client.post("/api/bids/1/tender-files", json={
                "filename": "辅助系统技术规格书.docx", "page_count": 8, "declared_total": 100,
                "original_filename": "tech.docx",
                "content_base64": base64.b64encode(buf.getvalue()).decode()})
            fid = reg.get_json()["id"]
        self.client.post(f"/api/tender-files/{fid}/tech-spec/parse", json={})
        self.client.post(f"/api/tender-files/{fid}/tech-spec/apply", json={})
        ws = self.client.get("/api/bids/1/workspace").get_json()
        sec = next(s for s in ws["sections"] if s.get("tech_req_count") and s["group_name"] == "技术标")
        before = dict(sec)
        # 1) 未配置大模型 → 本地结构化生成，仍写入 content_ai，原文字段保持不变
        r = self.client.post(f"/api/sections/{sec['id']}/compose", json={}).get_json()
        self.assertEqual(r["provider"], "template")
        self.assertTrue(r["fallback"])
        self.assertGreater(r["requirements"], 0)
        self.assertGreater(len(r["content"]), 200)
        after = next(s for s in self.client.get("/api/bids/1/workspace").get_json()["sections"]
                     if s["id"] == sec["id"])
        self.assertEqual(after["content"], before["content"])          # 原文技术要求不动
        self.assertEqual(after["content_ai"], r["content"])            # 正文落到新字段
        self.assertEqual(after["ai_model"], "本地结构化模板")
        self.assertIn("【待补充", after["content_ai"])                 # 缺事实用占位，不编造
        self.assertEqual(after["status"], "编制中")
        # 2) 配置大模型（密钥仅落本机文件、接口回显掩码）
        cfg = self.client.put("/api/llm/config", json={
            "base_url": "https://api.example.com/v1", "model": "demo-model",
            "api_key": "sk-test-1234567890"}).get_json()
        self.assertTrue(cfg["configured"])
        got = self.client.get("/api/llm/config").get_json()
        self.assertEqual(got["model"], "demo-model")
        self.assertNotIn("1234567890", got["api_key"])
        self.assertTrue(cfg_path.exists())
        # 3) 走大模型：提示词包含本节要求，正文仅写 content_ai
        with patch("llm_client.chat", return_value={
                "text": "1 概述\n大模型生成的正文内容。", "model": "demo-model",
                "provider": "llm", "usage": {"total_tokens": 123}, "elapsed": 1.2}) as fake:
            r2 = self.client.post(f"/api/sections/{sec['id']}/compose", json={}).get_json()
        self.assertEqual(r2["provider"], "llm")
        self.assertFalse(r2["fallback"])
        self.assertEqual(r2["usage"]["total_tokens"], 123)
        fake.assert_called_once()
        prompt = fake.call_args[0][0][-1]["content"]
        self.assertIn("本节须逐条回应的技术要求", prompt)
        self.assertIn("禁止编造", prompt)
        after2 = next(s for s in self.client.get("/api/bids/1/workspace").get_json()["sections"]
                      if s["id"] == sec["id"])
        self.assertEqual(after2["content_ai"], "1 概述\n大模型生成的正文内容。")
        self.assertEqual(after2["ai_model"], "demo-model")
        self.assertEqual(after2["content"], before["content"])
        # 4) 人工改写正文：只更新 content_ai，不动 content
        self.client.put(f"/api/sections/{sec['id']}",
                        json={"content_ai": "人工改写后的正文", "status": "编制中"})
        after3 = next(s for s in self.client.get("/api/bids/1/workspace").get_json()["sections"]
                      if s["id"] == sec["id"])
        self.assertEqual(after3["content_ai"], "人工改写后的正文")
        self.assertEqual(after3["content"], before["content"])
        # 5) 连接测试接口成功路径（回归：jsonify ok 关键字冲突曾致 500）
        with patch("llm_client.probe", return_value={
                "ok": True, "model": "deepseek-flash", "reply": "连接正常", "elapsed": 1.0}):
            r4 = self.client.post("/api/llm/test", json={})
        self.assertEqual(r4.status_code, 200)
        self.assertTrue(r4.get_json()["ok"])
        self.assertEqual(r4.get_json()["model"], "deepseek-flash")
        # 6) 连接测试失败路径：LLMError → 400 且带可读错误
        with patch("llm_client.probe", side_effect=llm_client.LLMError("大模型调用失败（401 密钥无效或已过期）")):
            r5 = self.client.post("/api/llm/test", json={})
        self.assertEqual(r5.status_code, 400)
        self.assertIn("401", r5.get_json()["error"])
        os.environ.pop("XNIC_LLM_CONFIG", None)
        cfg_path.unlink(missing_ok=True)


    def test_30_requirements_grounding_and_batch_compose(self):
        """编写是否结合原文要求：骨架要求纳入上下文（不再误判 0 条）、父章节汇总子节、批量编写不覆盖已有正文。"""
        import composer
        import llm_client
        from database import connect
        from unittest.mock import patch

        cfg_path = Path(TEST_TEMP.name) / "llm-test30.json"
        cfg_path.write_text('{"base_url":"https://api.deepseek.com","model":"deepseek-test","api_key":"sk-test"}',
                            encoding="utf-8")
        os.environ["XNIC_LLM_CONFIG"] = str(cfg_path)
        try:
            # 前置：造两节带原文要求骨架的子章节（模拟"插入本节/转子目录"）
            sec = next(s for s in self.client.get("/api/bids/1/workspace").get_json()["sections"]
                       if s["code"] == "2.3")
            self.client.post(f"/api/sections/{sec['id']}/tech-children", json={"items": [
                {"title": "验收资料", "content": "提交图版、手册、程序、测试报告及三方签署记录。",
                 "page": 8, "must": False}]})
            ws = self.client.get("/api/bids/1/workspace").get_json()
            child = next(s for s in ws["sections"] if s["code"].startswith("2.3."))
            with connect() as conn:
                row = conn.execute("SELECT * FROM sections WHERE id=?", (child["id"],)).fetchone()
                reqs = composer.section_requirements(conn, row)
                self.assertEqual(len(reqs), 1, "骨架里的原文要求必须被纳入编写上下文")
                self.assertEqual(reqs[0]["page"], 8)
                parent = conn.execute("SELECT * FROM sections WHERE id=?", (sec["id"],)).fetchone()
                parent_reqs = composer.section_requirements(conn, parent)
                self.assertGreaterEqual(len(parent_reqs), 1, "父章节应汇总子章节原文要求")

            # 批量编写：已有正文不覆盖，仅写"有要求且为空"的章节
            with patch("llm_client.chat", return_value={
                    "text": "1 概述\n依据原文要求编写的正文。", "model": "deepseek-test",
                    "provider": "llm", "usage": {}, "elapsed": 0.5}):
                r1 = self.client.post("/api/bids/1/compose-all", json={"group_name": "技术标"}).get_json()
                r2 = self.client.post("/api/bids/1/compose-all", json={"group_name": "技术标"}).get_json()
            self.assertTrue(r1["done"], "应至少编写一节以上的技术章节")
            self.assertTrue(all(d["requirements"] > 0 for d in r1["done"]),
                            "只对确有技术要求的章节编写，避免空话")
            codes1 = [d["code"] for d in r1["done"]]
            self.assertEqual([d for d in r2["done"] if d["code"] in codes1], [],
                             "默认不覆盖已有正文")
            ws2 = self.client.get("/api/bids/1/workspace").get_json()
            written = next(s for s in ws2["sections"] if s["code"] == codes1[0])
            self.assertEqual(written["content_ai"], "1 概述\n依据原文要求编写的正文。")
            self.assertEqual(written["ai_model"], "deepseek-test")
        finally:
            os.environ.pop("XNIC_LLM_CONFIG", None)
            cfg_path.unlink(missing_ok=True)


    def test_31_tech_audit_alignment(self):
        """技术目录树对齐审核：未映射 / 映射失效 / 空章节 / ★未响应 / 指标缺失 逐项定位，且可签认留痕。"""
        import json
        from datetime import datetime
        from database import connect

        def row_for(rep, section_id):
            return next((r for r in rep["by_section"] if r["section_id"] == section_id), None)

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        bid = self.client.post("/api/bids", json={
            "name": "对齐审核测试项目", "owner": "测试业主", "reactor_type": "核设施",
            "due_date": "2027-06-30"}).get_json()["id"]
        ws = self.client.get(f"/api/bids/{bid}/workspace").get_json()
        sec_power = next(s for s in ws["sections"] if s["code"] == "2.2")
        sec_qa = next(s for s in ws["sections"] if s["code"] == "2.3")
        sec_empty = next(s for s in ws["sections"] if s["code"] == "2.5")

        power_req = [{"code": "", "title": "供电要求",
                      "content": "支持 AC220V 与 DC24V 双路供电，备用电源切换时间≤0.5s。",
                      "page": 5, "must": False, "category": "环境供电"}]
        must_req = [{"code": "", "title": "★所有软件必须为正版授权",
                     "content": "★所有软件必须为正版授权，提供授权文件、安装介质与使用手册。",
                     "page": 6, "must": True, "category": "质量保证"}]
        empty_req = [{"code": "", "title": "包装要求",
                      "content": "包装应采取防潮、防锈、防震措施，满足运输与保管需要。",
                      "page": 9, "must": False, "category": "试验验收"}]
        with connect() as conn:
            cur = conn.execute("INSERT INTO tender_files(bid_id,filename,page_count,uploaded_at) VALUES(?,?,?,?)",
                               (bid, "对齐测试技术规格书.docx", 20, now))
            fid = cur.lastrowid
            cur = conn.execute(
                """INSERT INTO tech_specs(file_id,bid_id,status,item_count,req_count,must_count,outline_count,
                     page_from,page_to,page_source,region_title,report_json,applied_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fid, bid, "已解析", 4, 4, 1, 4, 3, 12, "native", "第五章 供货要求",
                 json.dumps({"agent": "技术规格解析", "steps": []}, ensure_ascii=False), now, now, now))
            spec_id = cur.lastrowid
            rows = [
                ("1", 1, "环境适应性与供电设计", sec_power["id"], power_req, 0, 5, 5),
                ("2", 1, "质量保证与正品承诺", sec_qa["id"], must_req, 1, 6, 6),
                ("3", 1, "包装与运输", sec_empty["id"], empty_req, 0, 9, 9),
                ("4", 1, "试验检验与验收方案", None, empty_req, 0, 11, 12),
            ]
            for seq, (code, level, title, section_id, reqs, must_count, pfrom, pto) in enumerate(rows, 1):
                conn.execute(
                    """INSERT INTO tech_outline(spec_id,seq,code,level,title,brief,categories,req_count,must_count,
                         child_req_count,child_must_count,page_from,page_to,requirements,section_id,mapping_kind,
                         mapping_note,created_at) VALUES(?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?,?,?)""",
                    (spec_id, seq, code, level, title, "测试编写要点", "功能设计", len(reqs), must_count,
                     pfrom, pto, json.dumps(reqs, ensure_ascii=False), section_id,
                     "strong" if section_id else "new", "", now))
            conn.commit()
            # 供电节：标题命中且双路电压写进正文，但 0.5s 切换时间漏了 → 指标缺失
            conn.execute("UPDATE sections SET content_ai=? WHERE id=?",
                         ("2.2 环境适应性与供电设计\n\n2.1 供电要求\n\n"
                          "要求：支持 AC220V 与 DC24V 双路供电，备用电源切换。\n"
                          "响应：满足。双路电源自动切换，切换时间小于 1 秒。", sec_power["id"]))
            # 质量节：正文泛泛而谈，★正版授权未响应
            conn.execute("UPDATE sections SET content_ai=? WHERE id=?",
                         ("2.3 质量保证\n我公司重视质量管理工作，建立了完善的质量保证体系，"
                          "并通过内部评审持续改进。", sec_qa["id"]))
            conn.commit()

        rep = self.client.get(f"/api/bids/{bid}/tech-audit").get_json()
        self.assertEqual(rep["spec"]["id"], spec_id)
        self.assertEqual(rep["summary"]["sections_total"], len(
            [s for s in ws["sections"] if s["group_name"] == "技术标"]))
        kinds = {f["kind"] for f in rep["findings"]}
        self.assertIn("unmapped", kinds)          # 目录项未落到章节
        self.assertIn("must_miss", kinds)         # ★必须条款未响应
        self.assertIn("num_miss", kinds)          # 0.5s 未落入正文
        self.assertGreaterEqual(rep["summary"]["open"]["high"], 2)
        self.assertEqual(rep["summary"]["must_total"], 1)
        self.assertEqual(rep["summary"]["must_covered"], 0)
        self.assertTrue(0 <= rep["summary"]["score"] <= 100)
        self.assertTrue(rep["summary"]["verdict"])
        self.assertLess(rep["summary"]["score"], 85, "★未响应 + 空章节 + 未映射应显著拉低对齐分")
        # 覆盖率：供电要求标题命中 → 已响应；包装要求正文为空 → 只报"未成文"一条
        sec_row = row_for(rep, sec_power["id"])
        self.assertGreaterEqual(sec_row["req_covered"], 1)
        empty_row = row_for(rep, sec_empty["id"])
        self.assertEqual(empty_row["total"], 1, "空章节不应再叠加条目/指标类提示")
        empty_find = next(f for f in rep["findings"] if f["kind"] == "empty")
        self.assertEqual(empty_find["section_id"], sec_empty["id"])
        self.assertEqual(empty_find["level"], "high")
        # 未映射目录项应点明丢失的要求条数
        unmapped = next(f for f in rep["findings"] if f["kind"] == "unmapped")
        self.assertIn("未对应技术标章节", unmapped["message"])
        self.assertIn("0.5s", next(f for f in rep["findings"] if f["kind"] == "num_miss")["message"])

        # 签认：★条款确认后不再计入待处理，重开可恢复
        must_key = next(f["key"] for f in rep["findings"] if f["kind"] == "must_miss")
        high_before = rep["summary"]["open"]["high"]
        ok = self.client.post(f"/api/bids/{bid}/tech-audit/mark",
                              json={"item_key": must_key, "verdict": "confirmed", "note": "已核对授权清单"}).get_json()
        self.assertEqual(ok["report"]["summary"]["open"]["high"], high_before - 1)
        self.assertEqual(ok["report"]["summary"]["resolved"], 1)
        marked = next(f for f in ok["report"]["findings"] if f["key"] == must_key)
        self.assertEqual(marked["state"], "confirmed")
        self.assertEqual(marked["note"], "已核对授权清单")
        reopened = self.client.post(f"/api/bids/{bid}/tech-audit/mark",
                                    json={"item_key": must_key, "verdict": "open"}).get_json()
        self.assertEqual(reopened["report"]["summary"]["open"]["high"], high_before)
        self.assertEqual(reopened["report"]["summary"]["resolved"], 0)

    def test_32_audit_coverage_rules(self):
        """对齐判定规则：同义表述不误报、指标抽取不吞小数、缺指标才判未响应。"""
        import tech_audit as ta

        metrics = [m["raw"] for m in ta._metrics(
            "共 250 台传感器、40 块硬盘，10TB 容量，切换≤0.5s，抖动 10ms，防护 IP67，遵循 GB/T 28181，第 12 页，2026 年")]
        for raw in ("250台", "40块", "10TB", "0.5s", "10ms", "IP67", "GB/T28181"):
            self.assertIn(raw, metrics)
        self.assertNotIn("12页", metrics)

        body = "系统工作温度覆盖-40℃～+75℃；外壳防护等级不低于IP67，具备防尘、防水、防腐蚀能力。"
        state, _ = ta._coverage("环境条件", "设备运行环境温度范围-40℃~+75℃，防护等级≥IP67，具备防尘、防水、防腐蚀能力。",
                                ta._compact(body), ta._norm(body))
        self.assertEqual(state, "ok", "同义表述（覆盖/范围）不应误报未响应")

        paraphrase = "2.3 软件正版授权\n全部软件均为正版授权，提供授权文件、安装介质与使用手册。"
        state, _ = ta._coverage("所有软件必须为正版授权",
                                "所有软件必须为正版授权，无盗版、破解情况，供方需提供授权文件、安装介质、使用手册。",
                                ta._compact(paraphrase), ta._norm(paraphrase))
        self.assertIn(state, ("ok", "weak"), "必须/均为 这类同义替换应视为已响应")

        vague = "我方提供高性能服务器，满足系统运行需要。"
        state, _ = ta._coverage("硬件配置", "仿真服务器：国产 CPU、内存 512GB 及以上，数量 2 台。",
                                ta._compact(vague), ta._norm(vague))
        self.assertEqual(state, "miss", "未给指标值的空泛承诺应判未响应")

        listing = "配置 16 台服务器与 40 块 8TB 硬盘。"
        state, _ = ta._coverage("存储设备", "存储设备：8TB 硬盘 40 块。",
                                ta._compact(listing), ta._norm(listing))
        self.assertEqual(state, "ok", "指标齐备时按指标判响应，不依赖句式")

    def test_33_tender_flow_kind_and_spec_summary(self):
        """统一标书编写流程：登记时区分制式文件类型（kind 白名单），
        列表接口返回技术规格书解析汇总（spec），供流程步骤条判定进度。"""
        bid = self.client.post("/api/bids", json={"name": "流程测试标书", "owner": "测试业主",
                                                  "reactor_type": "VVER", "due_date": "2027-03-01"}).get_json()
        # 制式文件类型登记：kind=评分办法
        reg = self.client.post(f"/api/bids/{bid['id']}/tender-files", json={
            "filename": "评标办法（流程测试）.txt", "page_count": 6, "kind": "评分办法",
            "original_filename": "t.txt",
            "content_base64": base64.b64encode("第1章 评分办法\n投标报价评分：以最低价为基准得满分 25 分。\n投标人须具备民用核安全设备制造许可证。\n".encode("utf-8")).decode("ascii"),
        })
        self.assertEqual(reg.status_code, 201)
        self.assertEqual(reg.get_json()["kind"], "评分办法")
        detail = self.client.get(f"/api/tender-files/{reg.get_json()['id']}").get_json()
        self.assertEqual(detail["kind"], "评分办法")

        # 非法 kind 兜底为 招标文件
        reg_bad = self.client.post(f"/api/bids/{bid['id']}/tender-files", json={
            "filename": "兜底测试.txt", "page_count": 3, "kind": "任意类型"})
        self.assertEqual(reg_bad.get_json()["kind"], "招标文件")

        # 未解析技术规格书 → spec.exists=False
        lst = self.client.get(f"/api/bids/{bid['id']}/tender-files").get_json()
        self.assertIn("spec", lst)
        self.assertFalse(lst["spec"]["exists"])

        # 技术规格书登记（kind=技术规格书）后独立解析 → spec.exists=True 且汇总带条目/目录数
        spec_reg = self.client.post(f"/api/bids/{bid['id']}/tender-files", json={
            "filename": "技术规格书（流程测试）.txt", "page_count": 5, "kind": "技术规格书",
            "original_filename": "s.txt",
            "content_base64": base64.b64encode(
                "第1章 供货要求及技术规格\n1.1 摄像机\n（1）网络摄像机应支持 H.265 编码，防护等级不低于 IP67。\n（2）★摄像机数量不少于 26 台。\n1.2 服务器\n（1）存储容量不小于 10TB。\n".encode("utf-8")).decode("ascii"),
        })
        fid = spec_reg.get_json()["id"]
        self.assertEqual(spec_reg.get_json()["kind"], "技术规格书")
        parsed = self.client.post(f"/api/tender-files/{fid}/tech-spec/parse", json={})
        self.assertEqual(parsed.status_code, 200)
        pj = parsed.get_json()
        self.assertGreaterEqual(pj["req_count"], 2)
        self.assertGreaterEqual(pj["outline_count"], 1)

        lst2 = self.client.get(f"/api/bids/{bid['id']}/tender-files").get_json()
        self.assertTrue(lst2["spec"]["exists"])
        self.assertEqual(lst2["spec"]["file_id"], fid)
        self.assertEqual(lst2["spec"]["req_count"], pj["req_count"])
        self.assertEqual(lst2["spec"]["outline_count"], pj["outline_count"])

    def test_34_compose_agent_job_flow(self):
        """技术标编写 Agent：任务化执行（规划→生成→自检重写→落库→对齐审核→任务单汇总）；
        首稿过短触发带反馈重写、摘要剥离回写、基线卡落地、运行锁 409。"""
        import re
        import time as _time
        from unittest.mock import patch

        import tech_bid_agent  # noqa: F401 - 确保 Agent 模块可导入
        from database import connect

        cfg_path = Path(TEST_TEMP.name) / "llm-test34.json"
        cfg_path.write_text('{"base_url":"https://api.deepseek.com","model":"agent-test","api_key":"sk-test"}',
                            encoding="utf-8")
        os.environ["XNIC_LLM_CONFIG"] = str(cfg_path)
        try:
            calls = {"n": 0}
            filler = ("响应：满足且优于要求。实现方式：按技术规格书条款执行，出厂试验逐项验证，"
                      "提交测试记录与合格证明文件，全过程纳入 HAF003 质保体系管控，见证点分级管理，记录可追溯。")

            def fake_chat(messages):
                calls["n"] += 1
                if calls["n"] == 1:          # 首稿过短 → 触发自检闸门失败 + 带反馈重写一次
                    return {"text": "1 概述\n太短。", "model": "agent-test", "provider": "llm",
                            "usage": {"prompt_tokens": 50, "completion_tokens": 8}, "elapsed": 0.1}
                titles = [m.group(1).strip() for m in
                          (re.match(r"^\d+\.\s*(?:★)?([^（:：]+)", ln) for ln in messages[1]["content"].split("\n"))
                          if m]
                body = ["1 概述", "本节依据招标技术规格书与全书基线卡编写。", "2 技术要求响应"]
                body += [f"{t}：{filler}" for t in titles]
                while sum(len(x) for x in body) < 1600:
                    body.append(filler)
                body += ["3 保障措施", filler, "4 本章小结", filler,
                         "【本章要点摘要】本章逐条响应全部技术要求，关键数值以规格指标库为准，"
                         "接口与承诺与前后章节保持一致。"]
                return {"text": "\n".join(body), "model": "agent-test", "provider": "llm",
                        "usage": {"prompt_tokens": 80, "completion_tokens": 600}, "elapsed": 0.2}

            with patch("llm_client.chat", side_effect=fake_chat):
                bid = self.client.post("/api/bids", json={"name": "Agent 流程标书", "owner": "测试",
                                                          "reactor_type": "VVER", "due_date": "2027-01-01"}).get_json()
                with connect() as conn:   # 造两节带原文要求骨架的章节（有要求且未编写 → 进入 Agent 规划）
                    conn.execute(
                        "UPDATE sections SET content=? WHERE bid_id=? AND code='2.1'",
                        ("【来源】2.1 存储方案 · 技术规格条目（原文第 10 页）\n【要求】存储设备。配置 8TB 硬盘 40 块。\n",
                         bid["id"]))
                    conn.execute(
                        "UPDATE sections SET content=? WHERE bid_id=? AND code='2.2'",
                        ("【来源】2.2 接口方案 · 技术规格条目（原文第 11 页）\n【要求】接口协议。提供 Modbus TCP 点表。\n",
                         bid["id"]))
                    conn.commit()
                start = self.client.post(f"/api/bids/{bid['id']}/compose-agent", json={"group_name": "技术标"})
                self.assertEqual(start.status_code, 201, start.get_data(as_text=True))
                sj = start.get_json()
                self.assertGreaterEqual(sj["total"], 1)
                job_id = sj["job_id"]
                deadline = _time.time() + 30
                job = None
                while _time.time() < deadline:
                    job = self.client.get(f"/api/bids/{bid['id']}/compose-agent/{job_id}").get_json()
                    if job["status"] not in ("running", "canceling"):
                        break
                    _time.sleep(0.2)
                self.assertEqual(job["status"], "done", f"Agent 应完成：{job.get('error')}")
                self.assertGreaterEqual(job["done_n"], 1)
                self.assertGreaterEqual(job["rewrites"], 1, "首稿过短应触发一次自检重写")
                self.assertIsNotNone(job["audit_score"], "收尾应自动对齐审核并写回分数")
                first = next(i for i in job["items"] if i["status"] == "written")
                self.assertEqual(first, job["items"][0], "首个章节即经历重写的那节")
                self.assertTrue(first["rewritten"])
                self.assertTrue(first["summary"], "正文同次产出本章摘要")
                with connect() as conn:
                    row = conn.execute("SELECT content_ai, summary_ai FROM sections WHERE id=?",
                                       (first["section_id"],)).fetchone()
                    self.assertNotIn("【本章要点摘要】", row["content_ai"], "摘要块必须从正文剥离")
                    self.assertTrue((row["summary_ai"] or "").strip())
                    base = conn.execute("SELECT baseline_card FROM bids WHERE id=?",
                                        (bid["id"],)).fetchone()
                    self.assertTrue((base["baseline_card"] or "").strip(), "锚点章摘要应写入全书基线卡")
                    log = conn.execute(
                        "SELECT detail FROM audit_logs WHERE action='技术标编写Agent' ORDER BY id DESC").fetchone()
                    self.assertIn("对齐分", log["detail"])

            # 运行锁：伪造 running 任务 → 409；已完成任务不可取消
            bid2 = self.client.post("/api/bids", json={"name": "锁测试标书", "owner": "测试",
                                                       "reactor_type": "VVER", "due_date": "2027-01-01"}).get_json()
            with connect() as conn:
                conn.execute(
                    """INSERT INTO compose_jobs(bid_id,group_name,status,total,started_at)
                       VALUES(?,?,?,?,?)""", (bid2["id"], "技术标", "running", 1, "2026-11-13 10:00:00"))
                conn.commit()
            locked = self.client.post(f"/api/bids/{bid2['id']}/compose-agent", json={})
            self.assertEqual(locked.status_code, 409)
            with connect() as conn:
                conn.execute("UPDATE compose_jobs SET status='done', finished_at=? WHERE bid_id=?",
                             ("2026-11-13 10:01:00", bid2["id"]))
                conn.commit()
            cancel_done = self.client.post(f"/api/bids/{bid2['id']}/compose-agent/1/cancel", json={})
            self.assertIn(cancel_done.status_code, (200, 400, 404))
        finally:
            os.environ.pop("XNIC_LLM_CONFIG", None)
            cfg_path.unlink(missing_ok=True)

    def test_35_consistency_audit_and_selfcheck(self):
        """横向一致性审核：数值口径冲突（同条款不同数量）/ 交叉引用失效；
        Agent 自检闸门：篇幅不足检出、达标正文通过。"""
        import tech_audit
        import tech_bid_agent
        from database import connect, now_text

        bid = self.client.post("/api/bids", json={"name": "一致性测试标书", "owner": "测试",
                                                  "reactor_type": "VVER", "due_date": "2027-02-01"}).get_json()
        with connect() as conn:
            conn.executemany(
                """INSERT INTO sections(bid_id,code,title,group_name,content,content_ai,status,sort_order,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                [(bid["id"], "2.6", "存储方案", "技术标",
                  "【来源】2.6 存储方案 · 技术规格条目（原文第 10 页）\n【要求】存储设备。配置 8TB 硬盘 40 块。\n",
                  "存储设备：配置 8TB 硬盘 40 块，出厂测试合格并提交检测报告。", "编制中", 10, now_text()),
                 (bid["id"], "2.7", "售后方案", "技术标", "",
                  "存储设备方面，本项目配置 8TB 硬盘共 32 块，满足存储要求。详见 9.9 节。", "编制中", 11, now_text())])
            conn.commit()
            rep = tech_audit.audit(conn, bid["id"])
        kinds = {f["kind"] for f in rep["findings"]}
        self.assertIn("num_conflict", kinds, "同条款不同数量应报数值口径冲突")
        self.assertIn("xref", kinds, "引用不存在的 9.9 节应报交叉引用失效")
        self.assertGreaterEqual(rep["summary"]["consistency"]["num_conflict"], 1)
        conflict = next(f for f in rep["findings"] if f["kind"] == "num_conflict")
        self.assertIn("32块", conflict["message"])
        self.assertIn("40块", conflict["message"])

        # 自检闸门（本地计算）：短稿判不合格并给出问题清单；达标正文通过
        ctx = {"requirements": [{"title": "存储设备", "must": True}], "parent_body": ""}
        ok, issues = tech_bid_agent.check(ctx, "1 概述\n太短。")
        self.assertFalse(ok)
        self.assertTrue(any("正文过短" in i for i in issues))
        good = "存储设备响应：" + "满足要求并给出验证方法，出厂试验逐项验证。" * 40
        ok2, issues2 = tech_bid_agent.check(ctx, good)
        self.assertTrue(ok2, str(issues2))

    def test_36_llm_thinking_and_timeout_degrade(self):
        """慢模型适配：Qwen3 自动关闭深度思考；超时重试改为分级降级而非原样重发；
        鉴权类错误不重试；父章节只有正文无摘要时子节仍能拿到父章范围。"""
        import json as _json
        from unittest.mock import patch

        import composer
        import llm_client
        import tech_bid_agent

        ds_cfg = {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                  "model": "qwen3.8-max-0902", "api_key": "sk-x", "temperature": 0.35,
                  "max_tokens": 4000, "timeout": 180}
        self.assertTrue(llm_client.thinking_disabled(ds_cfg), "百炼 Qwen3 应自动关闭思考")
        self.assertFalse(llm_client.thinking_disabled({**ds_cfg, "model": "qwen-plus"}))
        self.assertFalse(llm_client.thinking_disabled(
            {"base_url": "https://api.deepseek.com", "model": "deepseek-flash"}))
        self.assertFalse(llm_client.thinking_disabled({**ds_cfg, "disable_thinking": False}),
                         "显式保持思考时不得注入参数")

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return _json.dumps({"choices": [{"message": {"content": "正常"},
                                                "finish_reason": "stop"}]}).encode("utf-8")

        with patch("llm_client.load_config", return_value=ds_cfg), \
             patch("urllib.request.urlopen", return_value=_Resp()) as opened:
            llm_client.chat([{"role": "user", "content": "ping"}])
            sent = _json.loads(opened.call_args[0][0].data)
            self.assertFalse(sent.get("enable_thinking"), "请求体顶层须注入 enable_thinking=false")

        user = "\n".join([
            "【项目信息】", "项目名称：X",
            "【规格指标库】", "- 存储要求：300TB",
            "【统一术语】", "KRT（辐射监测系统）",
            "【章节族谱 · 上级章节（必须遵循其范围与口径）】", "【父章节】2.1 总体方案 已成文", "范围边界：只写本节",
            "【前后章节衔接】", "【前一章】2.1.2 摘要：……", "【后续章节预告】2.1.4 供货范围",
            "【本次编写章节】2.1.3 建设目标", "【本节须逐条回应的技术要求】共 1 条", "1. 智能化升级：详见原文",
            "【写作要求】", "1) 篇幅约 900 字",
        ])
        msgs = [{"role": "system", "content": "角色卡"}, {"role": "user", "content": user}]
        compact = tech_bid_agent.compact_messages(msgs)
        body = compact[1]["content"]
        for kept in ("【项目信息】", "项目名称：X", "【本次编写章节】",
                     "【本节须逐条回应的技术要求】", "1. 智能化升级", "【写作要求】"):
            self.assertIn(kept, body)
        for dropped in ("【规格指标库】", "300TB", "【统一术语】", "KRT（辐射监测系统）",
                        "【章节族谱", "【父章节】", "【前后章节衔接】", "【前一章】", "【后续章节预告】"):
            self.assertNotIn(dropped, body, f"降级时应摘除参考层：{dropped}")

        calls = []
        stats = {"llm_calls": 0, "retries": 0, "degraded": 0}

        def flaky(messages, **kw):
            calls.append((messages, kw))
            if len(calls) == 1:
                raise llm_client.LLMError("大模型响应超时")     # retryable=True
            return {"text": "ok"}

        with patch("llm_client.chat", side_effect=flaky), \
             patch("tech_bid_agent.time.sleep", lambda *_: None):
            self.assertEqual(tech_bid_agent._chat_retry(msgs, stats)["text"], "ok")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], {}, "首次调用保持完整五层上下文")
        self.assertNotIn("【规格指标库】", calls[1][0][1]["content"], "重试须自动压缩")
        self.assertEqual(calls[1][1], {"max_tokens": 2600, "timeout": 240})
        self.assertEqual(stats["degraded"], 1)

        no_retry = {"llm_calls": 0, "retries": 0, "degraded": 0}
        n = []

        def unauthorized(messages, **kw):
            n.append(1)
            raise llm_client.LLMError("大模型调用失败（401 密钥无效或已过期）", retryable=False)

        with patch("llm_client.chat", side_effect=unauthorized):
            with self.assertRaises(llm_client.LLMError):
                tech_bid_agent._chat_retry(msgs, no_retry)
        self.assertEqual(len(n), 1, "鉴权错误不应重试三次")
        self.assertEqual(no_retry["degraded"], 0)

        parent = {"id": 1, "code": "2.1", "title": "系统总体方案", "content": "",
                  "content_ai": "1 概述\n本章给出系统总体架构与三大子系统的划分原则，明确接口边界与数据流。"
                                "方案遵循 HAF003 质保体系，关键指标以规格指标库为唯一事实源。",
                  "summary_ai": ""}
        child = {"id": 2, "code": "2.1.3", "title": "建设目标"}
        lines = " ".join(composer._ancestor_lines([parent, child], child))
        self.assertIn("已成文", lines, "父章节已有正文时不得再判'尚未成文'")
        self.assertIn("正文首段", lines)
        self.assertNotIn("尚未成文", lines)

    def test_37_section_length_policy(self):
        """章节默认 800 字（封顶 1200）、允许留白；自检不再因篇幅不足强制重写。"""
        import composer
        import tech_bid_agent

        self.assertEqual(composer.section_target(0), 800)
        self.assertEqual(composer.section_target(1), 800)
        self.assertEqual(composer.section_target(5), 960)
        self.assertEqual(composer.section_target(20), 1200, "封顶 1200，不再膨胀到 2400")

        ctx = {"section": {"code": "2.1.3", "title": "建设目标", "group_name": "技术标"},
               "bid": {"name": "X", "owner": "Y", "reactor_type": "Z", "due_date": ""},
               "path": [], "children": [], "raw_count": 1, "must_count": 0,
               "requirements": [{"title": "智能化升级", "content": "详见原文", "count": 1,
                                 "page": 101, "must": False, "category": "设备选型"}],
               "scoring": [], "baseline_card": "", "metric_library": [], "term_library": [],
               "ancestor_lines": [], "neighbor_lines": []}
        prompt = composer.build_messages(ctx)[1]["content"]
        self.assertIn("800 字", prompt)
        self.assertIn("允许章节留白", prompt)
        self.assertIn("不要为凑字堆套话", prompt)

        # 旧策略下 300 字左右会被判"篇幅不足"并触发重写，现在应直接通过
        short = "1 概述\n" + "智能化升级响应：满足要求，按技术规格书条款实施并逐项验证。" * 12
        ok, issues = tech_bid_agent.check({"requirements": ctx["requirements"], "parent_body": ""}, short)
        self.assertTrue(ok, str(issues))
        # 明显敷衍的极短稿仍要拦住
        ok2, issues2 = tech_bid_agent.check({"requirements": ctx["requirements"], "parent_body": ""},
                                            "1 概述\n太短。")
        self.assertFalse(ok2)
        self.assertTrue(any("正文过短" in i for i in issues2))

    def test_38_pause_and_resume_compose_job(self):
        """进度条改造配套：暂停 pausing→paused 保留任务单；继续复用同一 job 只写剩余章节。"""
        import re
        import threading
        import time as _time
        from unittest.mock import patch
        from database import connect

        cfg_path = Path(TEST_TEMP.name) / "llm-test38.json"
        cfg_path.write_text('{"base_url":"https://api.deepseek.com","model":"pause-test","api_key":"sk-test"}',
                            encoding="utf-8")
        os.environ["XNIC_LLM_CONFIG"] = str(cfg_path)
        entered_second = threading.Event()   # 第 2 节开始生成
        release_second = threading.Event()   # 主线程发完暂停请求后放行
        calls = {"n": 0}
        filler = ("响应：满足且优于要求。实现方式：按技术规格书条款执行，出厂试验逐项验证，"
                  "提交测试记录与合格证明文件，全过程纳入 HAF003 质保体系管控，见证点分级管理，记录可追溯。")
        try:
            def fake_chat(messages):
                calls["n"] += 1
                if calls["n"] == 2:          # 卡在第 2 节，留出稳定的暂停窗口
                    entered_second.set()
                    release_second.wait(10)
                titles = [m.group(1).strip() for m in
                          (re.match(r"^\d+\.\s*(?:★)?([^（:：]+)", ln)
                           for ln in messages[1]["content"].split("\n")) if m]
                body = ["1 概述", "本节依据招标技术规格书与全书基线卡编写。", "2 技术要求响应"]
                body += [f"{t}：{filler}" for t in titles]
                while sum(len(x) for x in body) < 600:
                    body.append(filler)
                body += ["3 保障措施", filler, "4 本章小结", filler,
                         "【本章要点摘要】本章逐条响应全部技术要求，关键数值以规格指标库为准，"
                         "与前后章节保持一致。"]
                return {"text": "\n".join(body), "model": "pause-test", "provider": "llm",
                        "usage": {"prompt_tokens": 60, "completion_tokens": 300}, "elapsed": 0.1}

            with patch("llm_client.chat", side_effect=fake_chat):
                bid = self.client.post("/api/bids", json={"name": "暂停续跑标书", "owner": "测试",
                                                          "reactor_type": "VVER", "due_date": "2027-03-01"}).get_json()
                with connect() as conn:      # 三节带原文要求骨架，均未编写 → 全部进入规划
                    for code in ("2.1", "2.2", "2.3"):
                        conn.execute("UPDATE sections SET content=? WHERE bid_id=? AND code=?",
                                     (f"【来源】{code} · 技术规格条目（原文第 10 页）\n"
                                      f"【要求】{code} 指标。按技术规格书条款执行。\n", bid["id"], code))
                    conn.commit()
                start = self.client.post(f"/api/bids/{bid['id']}/compose-agent", json={"group_name": "技术标"})
                self.assertEqual(start.status_code, 201, start.get_data(as_text=True))
                sj = start.get_json()
                self.assertEqual(sj["total"], 3)
                job_id = sj["job_id"]

                self.assertTrue(entered_second.wait(15), "第 2 节应开始生成")
                paused_req = self.client.post(f"/api/bids/{bid['id']}/compose-agent/{job_id}/pause", json={})
                self.assertEqual(paused_req.status_code, 200, paused_req.get_data(as_text=True))
                lock = self.client.post(f"/api/bids/{bid['id']}/compose-agent", json={})
                self.assertEqual(lock.status_code, 409, "pausing 仍应占用运行锁")
                release_second.set()         # 第 2 节写完 → 线程在下一节前落 paused

                deadline = _time.time() + 20
                job = None
                while _time.time() < deadline:
                    job = self.client.get(f"/api/bids/{bid['id']}/compose-agent/{job_id}").get_json()
                    if job["status"] == "paused":
                        break
                    _time.sleep(0.1)
                self.assertEqual(job["status"], "paused", f"应停在 paused：{job.get('error')}")
                self.assertEqual(job["done_n"], 2, "暂停前已完成的两节应保留")
                self.assertEqual(len(job["items"]), 2)
                with connect() as conn:
                    row = conn.execute("SELECT overwrite, section_ids FROM compose_jobs WHERE id=?",
                                       (job_id,)).fetchone()
                self.assertEqual(row["overwrite"], 0, "续跑参数应持久化")

                resumed = self.client.post(f"/api/bids/{bid['id']}/compose-agent/{job_id}/resume", json={})
                self.assertEqual(resumed.status_code, 200, resumed.get_data(as_text=True))
                self.assertEqual(resumed.get_json()["job_id"], job_id, "续跑应复用同一任务单")
                deadline = _time.time() + 25
                job = None
                while _time.time() < deadline:
                    job = self.client.get(f"/api/bids/{bid['id']}/compose-agent/{job_id}").get_json()
                    if job["status"] in ("done", "failed"):
                        break
                    _time.sleep(0.1)
                self.assertEqual(job["status"], "done", f"续跑应完成：{job.get('error')}")
                self.assertEqual(job["done_n"], 3)
                self.assertEqual(len(job["items"]), 3, "续跑不得重复追加已写章节")
                self.assertEqual(len({i["section_id"] for i in job["items"]}), 3)
                self.assertGreaterEqual(job["llm_calls"], 3, "统计应累计暂停前后两段")
                with connect() as conn:
                    rows = conn.execute(
                        "SELECT code,length(content_ai) n FROM sections WHERE bid_id=? AND code IN ('2.1','2.2','2.3')",
                        (bid["id"],)).fetchall()
                self.assertEqual(len(rows), 3)
                self.assertTrue(all(r["n"] > 100 for r in rows))

            # 暂停态「结束任务」：线程已退出，直接定格而非置 canceling
            with connect() as conn:
                cur = conn.execute(
                    """INSERT INTO compose_jobs(bid_id,group_name,status,total,started_at)
                       VALUES(?,?,?,?,?)""", (bid["id"], "技术标", "paused", 1, "2026-11-13 10:00:00"))
                pid = cur.lastrowid
                conn.commit()
            ended = self.client.post(f"/api/bids/{bid['id']}/compose-agent/{pid}/cancel", json={})
            self.assertEqual(ended.status_code, 200, ended.get_data(as_text=True))
            with connect() as conn:
                st = conn.execute("SELECT status FROM compose_jobs WHERE id=?", (pid,)).fetchone()
            self.assertEqual(st["status"], "canceled")
        finally:
            release_second.set()   # 任何异常下都放行后台线程，避免测试挂死
            os.environ.pop("XNIC_LLM_CONFIG", None)
            cfg_path.unlink(missing_ok=True)

    def test_39_markdown_unified_output_and_export(self):
        """正文统一 Markdown：提示词 / 本地模板 / 历史稿归一化 / .md 导出与全书预览。"""
        import composer
        import llm_client
        import services
        import tech_bid_agent
        from database import connect
        from unittest.mock import patch

        ctx = {"section": {"code": "2.1.3", "title": "建设目标", "group_name": "技术标"},
               "bid": {"name": "X", "owner": "Y", "reactor_type": "Z", "due_date": ""},
               "path": [], "children": [], "raw_count": 1, "must_count": 0,
               "requirements": [{"title": "智能化升级", "content": "详见原文", "count": 1,
                                 "page": 101, "must": False, "category": "设备选型"}],
               "scoring": [], "baseline_card": "", "metric_library": [], "term_library": [],
               "ancestor_lines": [], "neighbor_lines": []}
        system, prompt = (m["content"] for m in composer.build_messages(ctx))
        self.assertIn("Markdown", system)
        self.assertIn("编号由 Word 按标题层级自动生成", system)
        self.assertIn("【标题禁令】", prompt)
        self.assertIn("由程序/Word 自动生成", prompt)
        self.assertIn("###", prompt)
        self.assertNotIn("不输出任何 Markdown 标题标记", prompt, "旧规约必须移除")

        local = composer.local_compose(ctx)
        self.assertIn("### 概述", local, "本地降级稿也须是 MD 且标题不带编号")
        self.assertNotIn("### 1 概述", local)
        self.assertIn("- **响应**", local)
        self.assertIn("【待补充", local)

        dirty = ("### 2.1.3 建设目标\n\n正文开头。\n\n"
                 "### 2.1.3.1 实施安排\n\n内容。")
        clean = composer.clean_generated_body(dirty, ctx["section"])
        self.assertTrue(clean.startswith("正文开头"), "不得复述本节大标题")
        self.assertIn("### 实施安排", clean, "章内小标题须去掉手写编号")
        self.assertNotIn("2.1.3", clean)

        with patch("llm_client.load_config", return_value={"api_key": "sk-x"}), \
             patch("llm_client.is_configured", return_value=True), \
             patch("llm_client.chat", return_value={"text": dirty, "model": "test-model",
                                                    "provider": "llm", "usage": {}, "elapsed": 0.1}):
            ai = composer.compose(None, ctx["section"], ctx=ctx)
        self.assertEqual(ai["provider"], "llm")
        self.assertTrue(ai["text"].startswith("正文开头"), "AI 编写结果不得复述本节大标题")
        self.assertIn("### 实施安排", ai["text"])

        legacy = "1 概述\n本节依据招标技术规格书编写。\n2 技术要求响应\n响应：满足要求。"
        norm = services.normalize_markdown(legacy)
        self.assertIn("### 概述", norm)
        self.assertIn("### 技术要求响应", norm)
        self.assertNotIn("本节依据招标技术规格书编写。\n响应", norm, "历史稿须按行分段")
        self.assertTrue(services.normalize_markdown("## 旧标题\n\n- 要点一").startswith("### 旧标题"),
                        "模型误输出的 #/## 须降级，避免与章节大标题冲突")

        ok, issues = tech_bid_agent.check(
            {"requirements": ctx["requirements"], "parent_body": ""},
            "### 1 概述\n\n本节逐条响应技术要求。\n\n### 2 技术要求响应\n\n"
            "#### 2.1 智能化升级\n\n- **响应**：满足，实现方式按技术规格书执行。\n"
            + "相关验证记录与质保措施按 HAF003 体系执行并留痕可追溯。" * 6)
        self.assertTrue(ok, f"自检闸门须兼容 MD 正文：{issues}")

        bid = self.client.post("/api/bids", json={"name": "MD 导出测试标书", "owner": "测试",
                                                  "reactor_type": "华龙一号", "due_date": "2027-05-01"}).get_json()
        with connect() as conn:
            conn.execute("UPDATE sections SET content_ai=? WHERE bid_id=? AND code='2.1'",
                         ("### 1 概述\n\n本节响应智能化升级要求。\n\n"
                          "| 项 | 值 |\n| --- | --- |\n| 诊断精度 | 95% |\n\n"
                          "【本章要点摘要】本章关键数值 95%，与前后章节一致。", bid["id"]))
            conn.commit()
        r = self.client.get(f"/api/bids/{bid['id']}/export")
        self.assertEqual(r.status_code, 200)
        self.assertIn(".md", r.headers.get("Content-Disposition", ""))
        self.assertIn("text/markdown", r.headers.get("Content-Type", ""))
        body = r.get_data(as_text=True)
        self.assertTrue(body.startswith("# MD 导出测试标书"), body[:80])
        self.assertIn("## 系统总体方案", body)
        self.assertNotIn("## 2.1 系统总体方案", body, "章节编号由 Word 模板生成，正文合并时不得手写")
        self.assertIn("### 概述", body, "历史编号标题导出时须去掉编号，由 Word 自动生成")
        self.assertNotIn("### 1 概述", body)
        self.assertIn("| 项 | 值 |", body)
        self.assertIn("【待补充：本节正文尚未编制】", body, "未写章节须留可核查占位")
        self.assertNotIn("【本章要点摘要】", body, "摘要块不进导出正文")

        inline = self.client.get(f"/api/bids/{bid['id']}/export?format=md&inline=1")
        self.assertEqual(inline.status_code, 200)
        self.assertEqual(inline.get_json()["format"], "md")
        self.assertIn("## 2.1", inline.get_json()["markdown"])
        html = self.client.get(f"/api/bids/{bid['id']}/export?format=html")
        self.assertEqual(html.status_code, 200)
        self.assertIn(".html", html.headers.get("Content-Disposition", ""), "旧 HTML 导出保留")

    def test_40_mermaid_html_renderer(self):
        """HTML 侧接入 Mermaid JS 解析器：编制页 / HTML 预览版渲染 ```mermaid，正文与 .md 保持原样。"""
        from pathlib import Path
        from database import connect

        root = Path(__file__).resolve().parents[1] / "static"
        vendor = root / "vendor" / "mermaid.min.js"
        self.assertTrue(vendor.exists(), "本地 Mermaid 解析器须随项目交付（离线可用）")
        self.assertGreater(vendor.stat().st_size, 500_000)
        self.assertIn("/static/vendor/mermaid.min.js", (root / "index.html").read_text(encoding="utf-8"))

        bid = self.client.post("/api/bids", json={"name": "Mermaid 渲染测试", "owner": "测试",
                                                  "reactor_type": "华龙一号", "due_date": "2027-05-01"}).get_json()
        diagram = ("### 1 概述\n\n本节描述系统数据流向。\n\n"
                   "```mermaid\ngraph TD\n    A[温振传感器] --> B(POE交换机)\n"
                   "    B --> C[(8TB硬盘40块)]\n```\n\n上图说明数据经网络写入存储。")
        with connect() as conn:
            conn.execute("UPDATE sections SET content_ai=? WHERE bid_id=? AND code='2.1'",
                         (diagram, bid["id"]))
            conn.commit()

        md = self.client.get(f"/api/bids/{bid['id']}/export?format=md&inline=1").get_json()["markdown"]
        self.assertIn("```mermaid", md, "正文/.md 数据源不改动")
        self.assertIn("graph TD", md)

        r = self.client.get(f"/api/bids/{bid['id']}/export?format=html")
        self.assertEqual(r.status_code, 200)
        page = r.get_data(as_text=True)
        self.assertIn('<div class="mermaid">', page)
        self.assertIn("/static/vendor/mermaid.min.js", page, "HTML 版须引入本地 JS 解析器")
        self.assertIn("mermaid.init(undefined,document.querySelectorAll", page)
        self.assertIn("--&gt;", page, "图源码须 HTML 转义，避免注入")
        self.assertIn("<p>本节描述系统数据流向。</p>", page, "图前后的正文段落不能丢")
        self.assertNotIn("```mermaid", page, "渲染后的 HTML 不应残留代码块标记")

    def test_41_docx_export_with_mermaid(self):
        """?format=docx 走 WordChapter 主接口：Mermaid 成图、表格/标题落位，md/html 不受影响。"""
        import io
        import shutil
        from pathlib import Path
        from unittest import mock
        from docx import Document
        from database import BASE_DIR, connect
        import app as app_module

        self.assertTrue(app_module.DOCX_REFERENCE_DOC.exists(), "Word 参考模板须就绪")
        bid = self.client.post("/api/bids", json={"name": "Word 导出测试", "owner": "测试",
                                                  "reactor_type": "华龙一号", "due_date": "2027-05-01"}).get_json()
        body = ("### 1 概述\n\n本节说明数据流向。\n\n"
                "| 项 | 值 |\n| --- | --- |\n| 精度 | 95% |\n\n"
                "```mermaid\ngraph TD\n    A[传感器] --> B(交换机)\n```\n\n图后说明。")
        with connect() as conn:
            conn.execute("UPDATE sections SET content_ai=? WHERE bid_id=? AND code='2.1'",
                         (body, bid["id"]))
            conn.commit()

        target = BASE_DIR / "data" / f"bid_{bid['id']}_preview.docx"
        try:
            r = self.client.get(f"/api/bids/{bid['id']}/export?format=docx")
            self.assertEqual(r.status_code, 200, repr(r.data[:120]))
            self.assertIn(".docx", r.headers.get("Content-Disposition", ""))
            d = Document(io.BytesIO(r.data))
            self.assertTrue(any("2.1" in p.text for p in d.paragraphs), "章节标题须落位")
            self.assertGreaterEqual(len(d.tables), 1, "Markdown 表格须转为 Word 表格")
            if shutil.which("mmdc"):
                self.assertGreaterEqual(len(d.inline_shapes), 1, "Mermaid 须成图嵌入 docx")
            md = self.client.get(f"/api/bids/{bid['id']}/export?format=md&inline=1").get_json()["markdown"]
            self.assertIn("```mermaid", md, "正文与 .md 数据源不改动")
            self.assertEqual(self.client.get(f"/api/bids/{bid['id']}/export?format=html").status_code, 200)
        finally:
            try:
                target.unlink(missing_ok=True)
            except OSError:      # 沙箱把 unlink 转 trash 可能失败，不影响用例判定
                pass

        with mock.patch.object(app_module, "DOCX_REFERENCE_DOC", Path("C:/__not_exist__.docx")):
            bad = self.client.get(f"/api/bids/{bid['id']}/export?format=docx")
            self.assertEqual(bad.status_code, 503, "依赖缺失须明确报错，不静默产出坏文件")
            self.assertIn("模板", bad.get_json()["error"])


    def test_42_sections_parent_level(self):
        """目录树结构真值：parent_id / level 迁移回填、新增、拖拽、删除、祖链与审核子树。"""
        import composer
        import tech_audit
        from database import connect, migrate_sections_tree

        # 1) schema：两列 + 自引用外键（级联） + 索引
        with connect() as conn:
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(sections)")}
            self.assertIn("parent_id", cols)
            self.assertIn("level", cols)
            fks = list(conn.execute("PRAGMA foreign_key_list(sections)"))
            self.assertTrue(any(r["table"] == "sections" and r["from"] == "parent_id"
                                and r["on_delete"] == "CASCADE" for r in fks))
            indexes = {r["name"] for r in conn.execute("PRAGMA index_list(sections)")}
            self.assertIn("idx_sections_parent", indexes)

        # 2) 新建标书：9 个组根 parent_id=NULL / level=1
        bid = self.client.post("/api/bids", json={"name": "目录树结构测试标书", "owner": "测试",
                                                  "reactor_type": "VVER", "due_date": "2027-03-01"}).get_json()
        ws = self.client.get(f"/api/bids/{bid['id']}/workspace").get_json()
        self.assertEqual(len(ws["sections"]), 9)
        self.assertTrue(all(s["parent_id"] is None and s["level"] == 1 for s in ws["sections"]))
        by_code = {s["code"]: s for s in ws["sections"]}

        # 3) 新增子节 / 孙节：parent_id 指向锚点，level 逐级 +1
        child = self.client.post(f"/api/bids/{bid['id']}/sections",
                                 json={"title": "结构测试子节", "anchor_id": by_code["2.1"]["id"],
                                       "position": "child"}).get_json()
        self.assertEqual(child["code"], "2.1.1")
        ws = self.client.get(f"/api/bids/{bid['id']}/workspace").get_json()
        sec = next(s for s in ws["sections"] if s["id"] == child["id"])
        self.assertEqual(sec["parent_id"], by_code["2.1"]["id"])
        self.assertEqual(sec["level"], 2)
        grand = self.client.post(f"/api/bids/{bid['id']}/sections",
                                 json={"title": "结构测试孙节", "anchor_id": child["id"],
                                       "position": "child"}).get_json()
        ws = self.client.get(f"/api/bids/{bid['id']}/workspace").get_json()
        gsec = next(s for s in ws["sections"] if s["id"] == grand["id"])
        self.assertEqual(gsec["code"], "2.1.1.1")
        self.assertEqual(gsec["parent_id"], child["id"])
        self.assertEqual(gsec["level"], 3)

        # 4) 拖拽换父：parent_id / level 跟随新父，code 同步重编
        ws = self.client.get(f"/api/bids/{bid['id']}/workspace").get_json()

        def reparent(roots, nodes):
            src = nodes[child["code"]]["children"]
            n = next(x for x in src if x["id"] == grand["id"])
            src.remove(n)
            nodes["2.2"]["children"].append(n)

        tree = self._sec_tree(ws["sections"], reparent)
        rr = self.client.post(f"/api/bids/{bid['id']}/sections/reorder", json={"tree": tree})
        self.assertEqual(rr.status_code, 200, repr(rr.data[:120]))
        ws = self.client.get(f"/api/bids/{bid['id']}/workspace").get_json()
        moved = next(s for s in ws["sections"] if s["id"] == grand["id"])
        self.assertEqual(moved["parent_id"], by_code["2.2"]["id"])
        self.assertEqual(moved["level"], 2)
        self.assertTrue(moved["code"].startswith("2.2."))

        # 5) 删除父：409 保护 → 递归删整棵子树，不留悬空 parent_id
        blocked = self.client.delete(f"/api/sections/{by_code['2.1']['id']}", json={})
        self.assertEqual(blocked.status_code, 409)
        ok = self.client.delete(f"/api/sections/{by_code['2.1']['id']}", json={"recursive": True})
        self.assertEqual(ok.status_code, 200)
        ws = self.client.get(f"/api/bids/{bid['id']}/workspace").get_json()
        ids = {s["id"] for s in ws["sections"]}
        self.assertNotIn(by_code["2.1"]["id"], ids)
        self.assertNotIn(child["id"], ids)
        self.assertIn(grand["id"], ids, "已换父的孙节不应被误删")

        # 6) 数据库层 FK CASCADE：不经应用层直接删父，子行自动消失
        pair = self.client.post(f"/api/bids/{bid['id']}/sections",
                                json={"title": "级联测试父节", "position": "append"}).get_json()
        kid = self.client.post(f"/api/bids/{bid['id']}/sections",
                               json={"title": "级联测试子节", "anchor_id": pair["id"],
                                     "position": "child"}).get_json()
        with connect() as conn:
            conn.execute("DELETE FROM sections WHERE id=?", (pair["id"],))
            left = conn.execute("SELECT COUNT(*) c FROM sections WHERE id=?", (kid["id"],)).fetchone()["c"]
        self.assertEqual(left, 0, "自引用外键须级联删除子章节")

        # 7) 迁移自愈：parent_id 被清空 / level 被改错时按 code 前缀回填并重算
        with connect() as conn:
            conn.execute("UPDATE sections SET parent_id=NULL, level=99 WHERE id=?", (grand["id"],))
            migrate_sections_tree(conn)
            fixed = conn.execute("SELECT parent_id,level FROM sections WHERE id=?", (grand["id"],)).fetchone()
        self.assertIsNotNone(fixed["parent_id"])
        self.assertEqual(fixed["level"], 2)

        # 8) 祖链：直接父缺失时断链，不再把祖父误当父
        gp = {"id": 1, "parent_id": None, "code": "2.1", "title": "祖父", "content": "",
              "content_ai": "", "summary_ai": "G"}
        p = {"id": 2, "parent_id": 1, "code": "2.1.1", "title": "父", "content": "",
             "content_ai": "", "summary_ai": "P"}
        ch = {"id": 3, "parent_id": 2, "code": "2.1.1.1", "title": "子", "content": ""}
        self.assertIn("父", " ".join(composer._ancestor_lines([gp, p, ch], ch)))
        self.assertEqual(composer._ancestor_lines([gp, ch], ch), [],
                         "直接父不存在时不得把祖父当父章节")

        # 9) 审核子树：code 不规则（非前缀）时仍靠 parent_id 找到全部后代
        a = {"id": 10, "parent_id": None, "code": "2.1", "title": "A"}
        b = {"id": 11, "parent_id": 10, "code": "9.9", "title": "B"}
        c = {"id": 12, "parent_id": 11, "code": "9.9.9", "title": "C"}
        sub = tech_audit._subtree({x["code"]: x for x in (a, b, c)}, a)
        self.assertEqual({x["id"] for x in sub}, {10, 11, 12})

    def test_43_llm_multi_profile(self):
        """多模型配置：旧格式兼容、新增/切换/更新保留密钥/删除回落、按配置单独测试不影响默认。"""
        import json as _json
        import llm_client
        from unittest.mock import patch

        cfg_path = Path(TEST_TEMP.name) / "llm-multi.json"
        cfg_path.unlink(missing_ok=True)
        os.environ["XNIC_LLM_CONFIG"] = str(cfg_path)
        for key in ("XNIC_LLM_BASE_URL", "XNIC_LLM_API_KEY", "XNIC_LLM_MODEL",
                    "XNIC_LLM_DISABLE_THINKING"):
            os.environ.pop(key, None)
        try:
            cfg_path.write_text(
                '{"base_url":"https://api.deepseek.com","model":"legacy-model",'
                '"api_key":"sk-legacy-123456"}', encoding="utf-8")
            got = self.client.get("/api/llm/config").get_json()
            self.assertEqual(got["model"], "legacy-model")
            self.assertEqual(len(got["profiles"]), 1)
            self.assertNotIn("123456", got["api_key"])
            legacy_id = got["profiles"][0]["id"]

            with patch("llm_client.probe", return_value={
                    "ok": True, "model": "kimi-k2", "reply": "连接正常", "elapsed": 0.2}) as fake:
                created = self.client.post("/api/llm/profiles", json={
                    "name": "Kimi 备用", "base_url": "https://api.moonshot.cn/v1",
                    "model": "kimi-k2", "api_key": "sk-kimi-abcdefgh"}).get_json()
                tested = self.client.post("/api/llm/test", json={"id": created["id"]}).get_json()
            self.assertTrue(tested["ok"])
            self.assertEqual(fake.call_args.kwargs["cfg"]["model"], "kimi-k2")
            self.assertEqual(self.client.get("/api/llm/config").get_json()["model"], "legacy-model")

            act = self.client.post(f"/api/llm/profiles/{created['id']}/activate", json={}).get_json()
            self.assertEqual(act["model"], "kimi-k2")
            self.assertTrue(next(p for p in act["profiles"] if p["id"] == created["id"])["active"])

            self.client.put(f"/api/llm/profiles/{created['id']}", json={
                "name": "Kimi 主力", "base_url": "https://api.moonshot.cn/v1",
                "model": "kimi-k2", "api_key": ""})
            stored = _json.loads(cfg_path.read_text(encoding="utf-8"))
            saved = next(x for x in stored["models"] if x["id"] == created["id"])
            self.assertEqual(saved["api_key"], "sk-kimi-abcdefgh", "密钥留空不得清空")
            self.assertEqual(saved["name"], "Kimi 主力")
            self.assertEqual(llm_client.get_active_profile_id(), created["id"])

            dele = self.client.delete(f"/api/llm/profiles/{created['id']}", json={}).get_json()
            self.assertEqual(len(dele["profiles"]), 1)
            self.assertEqual(dele["active_id"], legacy_id, "删除默认配置后须回落到剩余配置")
            self.assertEqual(dele["model"], "legacy-model")

            old = self.client.put("/api/llm/config", json={
                "base_url": "https://api.deepseek.com", "model": "legacy-v2",
                "api_key": "sk-legacy-abcdef"}).get_json()
            self.assertEqual(old["model"], "legacy-v2", "旧版单配置写入接口须继续可用")
            self.assertTrue(old["configured"])
        finally:
            os.environ.pop("XNIC_LLM_CONFIG", None)
            cfg_path.unlink(missing_ok=True)

    def test_44_create_bid_default_sections(self):
        """新建标书：9 个默认章节编码唯一，成功信息写入 logs/app.log。"""
        import app as app_module
        from database import connect

        r = self.client.post("/api/bids", json={
            "name": "新建标书约束回归", "owner": "测试", "reactor_type": "华龙一号",
            "due_date": "2027-12-31", "status": "编制中"})
        self.assertEqual(r.status_code, 201, repr(r.get_json()))
        bid_id = r.get_json()["id"]
        with connect() as conn:
            rows = conn.execute(
                "SELECT code,title,group_name FROM sections WHERE bid_id=? ORDER BY sort_order",
                (bid_id,)).fetchall()
        self.assertEqual(len(rows), 9)
        codes = [x["code"] for x in rows]
        self.assertEqual(len(set(codes)), 9, "章节编码须在单本标书内唯一")
        self.assertIn("2.4", codes)
        self.assertIn("3.1", codes)
        self.assertTrue(app_module.LOG_FILE.exists(), "日志文件须随服务启动创建")

    def test_45_tech_spec_table_rows_and_modules(self):
        """三列技术参数表：长功能条款不得被丢弃，表格内嵌章节须恢复为目录模块。"""
        import tempfile
        import docx
        import tech_spec_agent
        from unittest.mock import patch

        tmp = Path(tempfile.mkdtemp(prefix="spec-table-"))
        src = tmp / "spec.docx"
        d = docx.Document()
        d.add_paragraph("1-1 标的名称：测试平台")
        table = d.add_table(rows=1, cols=3)
        for cell, value in zip(table.rows[0].cells, ("参数性质", "序号", "技术参数与性能指标")):
            cell.text = value
        rows = [
            ("★", "1", "1.基本要求\n平台需基于B/S架构设计，支持本地化部署。"),
            ("▲", "2", "支持CT影像自动标注。证明材料：软件截图及承诺函"),
            ("", "3", "支持儿童骨龄辅助评估，输出骨龄报告。"),
            ("", "4", "提取特征值≥2264种。"),
            ("", "5", "所投乳腺MR产品需具备医疗器械注册证。"),
            ("", "6", "支持乳腺MR动态增强扫描与病灶标注。"),
        ]
        for flag, serial, body in rows:
            cells = table.add_row().cells
            cells[0].text, cells[1].text, cells[2].text = flag, serial, body
        d.save(src)
        try:
            with patch("tender_agent.docx_to_pdf", return_value=(None, "")):
                result = tech_spec_agent.run_tech_spec_agent(src, sections=[])
            self.assertGreaterEqual(len(result["items"]), 5, "长功能条款不应被表格规则丢弃")
            summary = result["report"]["summary"]
            self.assertGreaterEqual(summary["spec_rows"], 6)
            self.assertEqual(summary["table_rows"], 7, "表头也应计入表格行数")
            titles = " ".join(i["title"] + i["content"] for i in result["items"])
            self.assertIn("骨龄", titles)
            self.assertIn("注册证", titles)
            outline_titles = [n["title"] for n in result["outline"]]
            self.assertTrue(any("骨龄" in t for t in outline_titles), outline_titles)
            self.assertTrue(any("乳腺" in t for t in outline_titles), outline_titles)
            self.assertTrue(any("平台" in t for t in outline_titles), outline_titles)
            evidence = next(i for i in result["items"] if "CT影像自动标注" in i["title"])
            self.assertTrue(evidence["must"])
            self.assertIn("软件截图", evidence.get("evidence", ""))
            self.assertTrue(any(i.get("module") for i in result["items"]))
        finally:
            for f in tmp.glob("*"):
                f.unlink(missing_ok=True)
            tmp.rmdir()

    def test_46_tech_outline_profile_four_levels(self):
        """专用目录模板：生成 1—4 级目录、占位节点与证明材料索引。"""
        import tempfile
        import docx
        import tech_spec_agent
        from unittest.mock import patch

        tmp = Path(tempfile.mkdtemp(prefix="spec-profile-"))
        src = tmp / "profile.docx"
        d = docx.Document()
        d.add_paragraph("技术要求")
        table = d.add_table(rows=1, cols=3)
        for cell, value in zip(table.rows[0].cells, ("参数性质", "序号", "技术参数与性能指标")):
            cell.text = value
        rows = [
            ("★", "1", "放射影像人工智能平台，儿童骨龄辅助评估，CT-FFR，BI-RADS。"),
            ("▲", "3", "支持平台用户通过影像ID从PACS系统拉取数据。证明材料：软件截图"),
            ("", "66", "支持骨龄自动计算。"),
            ("", "141", "支持机架式部署。"),
        ]
        for flag, serial, body in rows:
            cells = table.add_row().cells
            cells[0].text, cells[1].text, cells[2].text = flag, serial, body
        d.save(src)
        try:
            with patch("tender_agent.docx_to_pdf", return_value=(None, "")):
                result = tech_spec_agent.run_tech_spec_agent(src, sections=[])
            summary = result["report"]["summary"]
            self.assertEqual(summary["profile"], "放射影像人工智能平台技术规格目录模板")
            self.assertEqual(summary["max_level"], 4)

            flat = list(tech_spec_agent._all_nodes(result["outline"]))
            codes = [n["code"] for n in flat]
            self.assertIn("3", codes)
            self.assertIn("3.1", codes)
            self.assertIn("3.1.1", codes)
            self.assertIn("3.1.1.1", codes, "区间要求须展开为四级目录项")
            self.assertIn("7.4.1", codes)
            self.assertIn("8.1", codes)

            placeholder = next(n for n in flat if n["code"] == "7.4.1")
            self.assertTrue(placeholder["requirements"][0]["placeholder"])
            self.assertIn("原文未展开", placeholder["requirements"][0]["content"])
            evidence = result["report"]["evidence_index"]
            self.assertIn("141-148", evidence["missing"])
            self.assertIn("3", evidence["screenshot"])
        finally:
            for f in tmp.glob("*"):
                f.unlink(missing_ok=True)
            tmp.rmdir()

    def test_47_delete_bid(self):
        """标书中心删除：需确认名称，级联清理章节且列表不再出现。"""
        from database import connect

        name = "待删除回归标书"
        created = self.client.post("/api/bids", json={
            "name": name, "owner": "测试", "reactor_type": "华龙一号",
            "due_date": "2027-12-31", "status": "编制中"})
        self.assertEqual(created.status_code, 201, repr(created.get_json()))
        bid_id = created.get_json()["id"]
        with connect() as conn:
            before = conn.execute("SELECT COUNT(*) c FROM sections WHERE bid_id=?", (bid_id,)).fetchone()["c"]
        self.assertGreater(before, 0)

        bad = self.client.delete(f"/api/bids/{bid_id}", json={"confirm": "错误名称", "user": "测试"})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.client.get(f"/api/bids/{bid_id}").status_code, 200)

        good = self.client.delete(f"/api/bids/{bid_id}", json={"confirm": name, "user": "测试"})
        self.assertEqual(good.status_code, 200, repr(good.get_json()))
        self.assertEqual(good.get_json()["sections"], before)
        self.assertEqual(self.client.get(f"/api/bids/{bid_id}").status_code, 404)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) c FROM bids WHERE id=?", (bid_id,)).fetchone()["c"], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) c FROM sections WHERE bid_id=?", (bid_id,)).fetchone()["c"], 0)
        self.assertNotIn(bid_id, [x["id"] for x in self.client.get("/api/bids").get_json()["items"]])

    def test_48_tech_spec_apply_preserves_parsed_tree(self):
        """同步技术标：编码/标题/层级/父子关系与解析目录完全一致，重复同步幂等。"""
        import docx
        import tech_spec_agent

        bid = self.client.post("/api/bids", json={
            "name": "原样同步回归标书", "owner": "测试", "reactor_type": "华龙一号",
            "due_date": "2027-12-31", "status": "编制中"}).get_json()
        bid_id = bid["id"]
        buf = io.BytesIO()
        d = docx.Document()
        d.add_paragraph("技术要求")
        table = d.add_table(rows=1, cols=3)
        for cell, value in zip(table.rows[0].cells, ("参数性质", "序号", "技术参数与性能指标")):
            cell.text = value
        for flag, serial, body in (
            ("★", "1", "平台基于B/S架构设计，支持Web登录。"),
            ("▲", "2", "支持数据处理与任务管理。证明材料：软件截图"),
            ("", "3", "支持结果导出与共享。"),
        ):
            cells = table.add_row().cells
            cells[0].text, cells[1].text, cells[2].text = flag, serial, body
        d.save(buf)
        reg = self.client.post(f"/api/bids/{bid_id}/tender-files", json={
            "filename": "原样同步规格.docx", "page_count": 2, "declared_total": 3,
            "original_filename": "spec.docx",
            "content_base64": base64.b64encode(buf.getvalue()).decode()})
        self.assertEqual(reg.status_code, 201)
        fid = reg.get_json()["id"]
        parsed = self.client.post(f"/api/tender-files/{fid}/tech-spec/parse", json={})
        self.assertEqual(parsed.status_code, 200, parsed.get_data(as_text=True))
        expected = list(tech_spec_agent._all_nodes(parsed.get_json()["outline"]))
        self.assertTrue(expected)

        applied = self.client.post(f"/api/tender-files/{fid}/tech-spec/apply", json={"user": "测试"})
        self.assertEqual(applied.status_code, 200, applied.get_data(as_text=True))
        self.assertEqual(applied.get_json()["created"], len(expected))
        ws = self.client.get(f"/api/bids/{bid_id}/workspace").get_json()
        tech = {s["code"]: s for s in ws["sections"] if s["group_name"] == "技术标"}
        self.assertEqual(set(tech), {o["code"] for o in expected})
        for node in expected:
            section = tech[node["code"]]
            self.assertEqual(section["title"], node["title"])
            self.assertEqual(section["level"], node["level"])
            if "." in node["code"]:
                parent_code = node["code"].rsplit(".", 1)[0]
                self.assertEqual(section["parent_id"], tech[parent_code]["id"])
            else:
                self.assertIsNone(section["parent_id"])

        again = self.client.post(f"/api/tender-files/{fid}/tech-spec/apply", json={"user": "测试"}).get_json()
        self.assertEqual(again["created"], 0)
        self.assertEqual(again["reused"], len(expected))

    def test_49_migrate_sections_unique_scope_preserves_ai_columns(self):
        """旧库章节唯一约束迁移不得丢失 content_ai 等 AI 编写字段。"""
        import shutil
        import sqlite3
        import tempfile
        import database

        tmp = Path(tempfile.mkdtemp(prefix="section-migrate-"))
        path = tmp / "old.db"
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript("""
                CREATE TABLE bids(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE sections(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bid_id INTEGER NOT NULL,
                    parent_id INTEGER,
                    level INTEGER NOT NULL DEFAULT 1,
                    code TEXT NOT NULL,
                    title TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    content TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '未开始',
                    sort_order INTEGER NOT NULL,
                    ai_generated INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    content_ai TEXT NOT NULL DEFAULT '',
                    ai_model TEXT NOT NULL DEFAULT '',
                    ai_written_at TEXT NOT NULL DEFAULT '',
                    summary_ai TEXT NOT NULL DEFAULT '',
                    UNIQUE(bid_id, code)
                );
                CREATE INDEX idx_sections_bid ON sections(bid_id, sort_order);
            """)
            conn.execute("INSERT INTO bids(id,name) VALUES(1,'迁移测试')")
            conn.execute("""INSERT INTO sections(bid_id,level,code,title,group_name,content,sort_order,
                            updated_at,content_ai,ai_model)
                            VALUES(1,1,'1.1','投标函','商务标','原文',1,'2026-01-01','已写正文','deepseek')""")
            conn.commit()

            database.migrate_sections_unique_scope(conn)
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(sections)").fetchall()}
            self.assertIn("content_ai", cols)
            self.assertIn("summary_ai", cols)
            saved = conn.execute("SELECT content_ai,ai_model FROM sections WHERE code='1.1'").fetchone()
            self.assertEqual(saved["content_ai"], "已写正文")
            self.assertEqual(saved["ai_model"], "deepseek")
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='sections'").fetchone()["sql"]
            self.assertIn("UNIQUE(bid_id, group_name, code)", sql)
            conn.execute("""INSERT INTO sections(bid_id,level,code,title,group_name,content,sort_order,
                            updated_at,content_ai) VALUES(1,1,'1.1','技术要求','技术标','骨架',2,
                            '2026-01-01','')""")
            conn.commit()
        finally:
            conn.close()
            try:
                shutil.rmtree(tmp)
            except PermissionError:
                pass

    def test_50_export_markdown_respects_section_levels(self):
        """导出目录层级须与标书编制目录树一致：1/2/3/4 级 → ##/###/####/#####。"""
        import re
        from services import render_bid_markdown

        bid = {"name": "层级导出回归", "owner": "测试", "reactor_type": "华龙一号", "due_date": "2027-12-31"}
        sections = [
            {"code": "1", "title": "基本要求", "level": 1, "group_name": "技术标",
             "content": "", "content_ai": "正文"},
            {"code": "1.1", "title": "标的构成", "level": 2, "group_name": "技术标",
             "content": "", "content_ai": "### 三级说明\n小节正文"},
            {"code": "3.1.1", "title": "数据接入", "level": 3, "group_name": "技术标",
             "content": "", "content_ai": "正文"},
            {"code": "3.1.1.1", "title": "PACS拉取", "level": 4, "group_name": "技术标",
             "content": "", "content_ai": "### 补充说明\n更深层级"},
        ]
        md = render_bid_markdown(bid, sections)
        self.assertIn("## 基本要求", md)
        self.assertIn("### 标的构成", md)
        self.assertIn("#### 数据接入", md)
        self.assertIn("##### PACS拉取", md)
        self.assertFalse(any(re.match(r"^#{2,6}\s+\d", line) for line in md.splitlines()),
                         "合并正文不得在标题里手写章节编号")
        self.assertIn("###### 补充说明", md, "章内标题须比所在章节深一级")

    def test_51_section_tree_migration_keeps_groups_separate(self):
        """目录树自愈：不同分组的同前缀编号不得互为父子。"""
        import shutil
        import sqlite3
        import tempfile
        import database

        tmp = Path(tempfile.mkdtemp(prefix="tree-group-"))
        path = tmp / "tree.db"
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript("""
                CREATE TABLE sections(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bid_id INTEGER NOT NULL,
                    parent_id INTEGER,
                    level INTEGER NOT NULL DEFAULT 1,
                    code TEXT NOT NULL,
                    title TEXT NOT NULL,
                    group_name TEXT NOT NULL,
                    sort_order INTEGER NOT NULL
                );
            """)
            conn.executemany(
                "INSERT INTO sections(id,bid_id,parent_id,level,code,title,group_name,sort_order) "
                "VALUES(?,?,?,?,?,?,?,?)",
                [(1, 1, None, 1, "1", "技术根", "技术标", 1),
                 (2, 1, 1, 2, "1.1", "投标函", "商务标", 2),
                 (3, 1, 1, 2, "1.1", "报价汇总表", "报价标", 3),
                 (4, 1, None, 1, "1.1", "技术子节", "技术标", 4)])
            conn.commit()

            database.migrate_sections_tree(conn)
            rows = {r["id"]: r for r in conn.execute("SELECT id,parent_id,level FROM sections")}
            self.assertIsNone(rows[2]["parent_id"])
            self.assertIsNone(rows[3]["parent_id"])
            self.assertEqual(rows[2]["level"], 1)
            self.assertEqual(rows[3]["level"], 1)
            self.assertEqual(rows[4]["parent_id"], 1, "同组同前缀仍应正常挂父")
            self.assertEqual(rows[4]["level"], 2)
        finally:
            conn.close()
            try:
                shutil.rmtree(tmp)
            except PermissionError:
                pass


    def test_52_legacy_document_upload_migration(self):
        """旧扁平附件在 init_db 时自动迁到分类目录，且下载仍可用。"""
        import shutil
        import database

        doc_id = self._upload("旧目录迁移文档")
        with connect() as conn:
            old_rel = conn.execute(
                "SELECT stored_path FROM documents WHERE id=?", (doc_id,)
            ).fetchone()["stored_path"]
            source = database.UPLOAD_DIR / old_rel
            legacy = database.UPLOAD_DIR / source.name
            shutil.move(str(source), str(legacy))
            conn.execute("UPDATE documents SET stored_path=? WHERE id=?", (source.name, doc_id))
            conn.execute(
                "UPDATE document_versions SET stored_path=? WHERE doc_id=? AND stored_path=?",
                (source.name, doc_id, old_rel),
            )
            conn.execute("DELETE FROM settings WHERE key='doc_upload_categories'")
            conn.commit()

        init_db()
        with connect() as conn:
            new_rel = conn.execute(
                "SELECT stored_path FROM documents WHERE id=?", (doc_id,)
            ).fetchone()["stored_path"]
            version_rel = conn.execute(
                "SELECT stored_path FROM document_versions WHERE doc_id=? ORDER BY id LIMIT 1", (doc_id,)
            ).fetchone()["stored_path"]
            self.assertTrue(new_rel.startswith("documents/技术方案/"))
            self.assertEqual(version_rel, new_rel)
            self.assertTrue((database.UPLOAD_DIR / new_rel).exists())
            self.assertFalse(legacy.exists())
        self.assertEqual(self.client.get(f"/api/documents/{doc_id}/download").status_code, 200)


if __name__ == "__main__":
    import gc
    unittest.main(verbosity=2, exit=False)
    gc.collect()  # 回收残留 SQLite 连接，避免 Windows 下临时目录清理报错
    try:
        TEST_TEMP.cleanup()
    except PermissionError:
        pass
