# -*- coding: utf-8 -*-
"""docx → PDF 转换器（子进程调用本机 Word/WPS 排版引擎）。

用法：python tools/docx2pdf.py <src.docx> <dst.pdf>

为什么独立成子进程：
  ① COM 组件（Word.Application）在 Flask 工作线程里初始化容易和 WSGI 线程模型冲突；
  ② 排版引擎偶发卡死时，子进程可被父进程 timeout 强杀，不拖垮服务；
  ③ 转换失败只让页码降级为版面估算，不影响解析主流程。

退出码：0 成功；2 无本机 Office/WPS；3 打开或导出失败。
"""
import os
import sys


def main():
    if len(sys.argv) < 3:
        print("usage: docx2pdf.py <src.docx> <dst.pdf>", file=sys.stderr)
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
                app = win32.DispatchEx(prog)  # DispatchEx：独立实例，不复用用户正在编辑的窗口
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
        # wdExportFormatPDF = 17；Range 缺省 = 全文
        doc.ExportAsFixedFormat(dst, 17)
        if not os.path.exists(dst) or os.path.getsize(dst) < 1024:
            print("导出结果为空", file=sys.stderr)
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
