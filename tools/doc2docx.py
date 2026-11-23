# -*- coding: utf-8 -*-
"""doc → docx 转换器（子进程调用本机 Word/WPS，避免 COM 侵入 Flask 线程）。

用法：python tools/doc2docx.py <src.doc> <dst.docx>
退出码：0 成功；2 无 pywin32/Office/WPS；3 打开或另存失败。
"""
import os
import sys

WD_FORMAT_XML_DOCUMENT = 16


def main():
    if len(sys.argv) < 3:
        print("usage: doc2docx.py <src.doc> <dst.docx>", file=sys.stderr)
        return 1
    src, dst = os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2])
    if not os.path.exists(src):
        print(f"源文件不存在: {src}", file=sys.stderr)
        return 3

    try:
        import pythoncom
        import win32com.client as win32
    except ImportError:
        print("pywin32 未安装", file=sys.stderr)
        return 2

    pythoncom.CoInitialize()
    app, doc = None, None
    try:
        last_err = None
        for prog in ("Word.Application", "KWPS.Application", "kwps.Application"):
            try:
                app = win32.DispatchEx(prog)
                break
            except Exception as exc:  # 该 ProgID 未注册
                last_err = exc
        if app is None:
            print(f"无可用 Office/WPS: {last_err}", file=sys.stderr)
            return 2
        try:
            app.Visible = False
        except Exception:
            pass
        for attr, val in (("DisplayAlerts", 0), ("AutomationSecurity", 3), ("ScreenUpdating", False)):
            try:
                setattr(app, attr, val)
            except Exception:
                pass

        doc = app.Documents.Open(src, ReadOnly=True, AddToRecentFiles=False, Visible=False)
        try:
            doc.SaveAs2(dst, WD_FORMAT_XML_DOCUMENT)
        except Exception:
            doc.SaveAs(dst, WD_FORMAT_XML_DOCUMENT)
        if not os.path.exists(dst) or os.path.getsize(dst) < 1024:
            print("转换结果为空", file=sys.stderr)
            return 3
        print(f"OK {dst}")
        return 0
    except Exception as exc:
        print(f"转换失败: {exc}", file=sys.stderr)
        return 3
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:
            pass
        try:
            if app is not None:
                app.Quit()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
