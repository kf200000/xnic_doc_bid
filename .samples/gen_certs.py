# -*- coding: utf-8 -*-
"""生成 3 张演示用资质证照样张 PNG（非真实证照，仅供系统演示预览）。"""

CERTS = [
    {
        "file": "cert_nnsa_design.html",
        "title": "民用核安全设备设计许可证",
        "no": "国核安证字 S-2xxx",
        "issuer": "国家核安全局",
        "date": "发证日期：2022-07-01",
        "expiry": "有效期至：2027-06-30",
        "holder": "西安中核核仪器股份有限公司（演示）",
        "scope": "许可范围：核安全级电离辐射监测仪表（RMS/KRT，1E 级）设计",
        "seal": "国家核安全局\n（演示样张）",
        "accent": "#8c1f1f",
    },
    {
        "file": "cert_radiation.html",
        "title": "辐射安全许可证",
        "no": "陕环辐证[0xxx]号",
        "issuer": "陕西省生态环境厅",
        "date": "发证日期：2021-12-31",
        "expiry": "有效期至：2026-12-31",
        "holder": "西安中核核仪器股份有限公司（演示）",
        "scope": "许可种类和范围：销售、使用Ⅱ类、Ⅲ类放射源；乙级非密封放射性物质工作场所",
        "seal": "陕西省生态环境厅\n（演示样张）",
        "accent": "#1f5c8c",
    },
    {
        "file": "cert_fire_3c.html",
        "title": "消防产品认证证书",
        "no": "CCCF-2xxx（3C 认证）",
        "issuer": "消防产品合格评定中心",
        "date": "初次发证：2021-12-31",
        "expiry": "有效期至：2026-12-31",
        "holder": "西安中核核仪器股份有限公司（演示）",
        "scope": "认证产品：点型感烟火灾探测器、手动火灾报警按钮（核电厂用）",
        "seal": "消防产品合格评定中心\n（演示样张）",
        "accent": "#8c5a1f",
    },
]

TEMPLATE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{width:1200px;height:850px;background:#fff;font-family:"Microsoft YaHei","PingFang SC",sans-serif;position:relative;overflow:hidden}}
.frame{{border:18px solid {accent};position:absolute;inset:0}}
.frame::after{{content:"";position:absolute;inset:14px;border:2px solid {accent};border-radius:4px}}
.inner{{position:absolute;inset:44px;display:flex;flex-direction:column;align-items:center;padding:34px 60px 0;text-align:center}}
.gov{{font-size:26px;font-weight:700;color:{accent};letter-spacing:14px;margin-bottom:4px}}
.hint{{font-size:13px;color:#999;letter-spacing:2px}}
.title{{font-size:44px;font-weight:800;color:{accent};letter-spacing:6px;margin:34px 0 26px}}
.no{{font-size:20px;color:#333;letter-spacing:2px;margin-bottom:30px}}
table{{border-collapse:collapse;margin:0 auto}}
td{{font-size:17px;color:#333;padding:8px 18px;letter-spacing:1px;text-align:left}}
td.k{{color:#666;white-space:nowrap}}
.holder{{font-size:24px;font-weight:700;color:#111;margin:26px 0 8px;letter-spacing:2px}}
.scope{{font-size:15px;color:#555;max-width:820px;line-height:1.8}}
.seal{{position:absolute;right:120px;bottom:120px;width:170px;height:170px;border:5px solid rgba(178,32,32,.75);border-radius:50%;
  display:flex;align-items:center;justify-content:center;color:rgba(178,32,32,.85);font-size:16px;font-weight:700;line-height:1.5;
  transform:rotate(-12deg);text-align:center;letter-spacing:1px}}
.valid{{position:absolute;left:120px;bottom:120px;font-size:16px;color:#333;line-height:2}}
.valid b{{font-size:19px;color:{accent}}}
.wm{{position:absolute;inset:0;pointer-events:none;overflow:hidden}}
.wm span{{position:absolute;transform:rotate(-24deg);color:rgba(60,60,60,.10);font-size:26px;font-weight:700;white-space:nowrap;letter-spacing:6px}}
.foot{{position:absolute;bottom:44px;width:100%;text-align:center;font-size:12px;color:#aaa;letter-spacing:3px}}
</style></head><body>
<div class="frame"></div>
<div class="inner">
  <div class="gov">{issuer}</div>
  <div class="hint">（本证书为系统演示样张，非真实证照）</div>
  <div class="title">{title}</div>
  <div class="no">证书编号：{no}</div>
  <div class="holder">{holder}</div>
  <div class="scope">{scope}</div>
</div>
<div class="valid">{date}<br>{expiry}</div>
<div class="seal">{seal}</div>
<div class="wm">__WMS__</div>
<div class="foot">XNIC 文档与标书系统 · 演示数据</div>
</body></html>"""


def main():
    import pathlib
    out = pathlib.Path(__file__).resolve().parent
    tops = [12, 40, 68, 90]
    lefts = [-6, 22, 50, 78]
    wm = "".join(
        f'<span style="top:{t}%;left:{l}%">演示样张 · 非真实证照</span>'
        for t in tops for l in lefts
    )
    for cert in CERTS:
        html = TEMPLATE.format(wm=None, **cert).replace("__WMS__", wm)
        (out / cert["file"]).write_text(html, encoding="utf-8")
        print("written", cert["file"])


if __name__ == "__main__":
    main()
