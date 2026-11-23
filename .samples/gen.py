# -*- coding: utf-8 -*-
"""生成 DEMO 用样例附件源文件（HTML / TXT），再由无头 Edge 转 PDF 与 PNG。"""
from pathlib import Path

OUT = Path(__file__).resolve().parent

CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: "Microsoft YaHei", "SimSun", sans-serif; color: #1b2430; font-size: 12px; line-height: 1.9; margin: 0; }
.head { border-bottom: 2px solid #0b2b57; padding-bottom: 10px; margin-bottom: 16px; }
.brand { font-size: 11px; color: #7b8794; letter-spacing: 1px; }
h1 { font-size: 19px; color: #0b2b57; margin: 8px 0 6px; }
.meta { display: flex; gap: 18px; font-size: 10.5px; color: #5b6774; flex-wrap: wrap; }
.meta b { color: #0b2b57; font-weight: 600; }
.stamp { float: right; border: 1.5px solid #b42318; color: #b42318; padding: 2px 8px; font-size: 10px; border-radius: 3px; transform: rotate(-6deg); }
h2 { font-size: 13.5px; color: #0b2b57; margin: 18px 0 6px; border-left: 3px solid #c9a227; padding-left: 8px; }
h3 { font-size: 12px; color: #1b2430; margin: 12px 0 4px; }
p { margin: 5px 0; text-align: justify; }
table { width: 100%; border-collapse: collapse; margin: 8px 0; font-size: 10.5px; }
th, td { border: 1px solid #d5dce5; padding: 5px 7px; text-align: left; }
th { background: #eef2f8; color: #0b2b57; font-weight: 600; }
.note { background: #f7f9fc; border-left: 3px solid #c9a227; padding: 8px 10px; font-size: 10.5px; color: #43505e; margin: 10px 0; }
.foot { margin-top: 22px; border-top: 1px solid #d5dce5; padding-top: 8px; font-size: 9.5px; color: #7b8794; }
ol { margin: 6px 0 6px 20px; padding: 0; }
li { margin: 3px 0; }
"""


def page(title, version, date, sections, stamp=""):
    body = "".join(sections)
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><style>{CSS}</style></head>
<body><div class="head">
  <div class="brand">西安中核核仪器股份有限公司 · 技术文件</div>
  <h1>{title}</h1>
  <div class="meta"><span>文件编号 <b>XNIC-KRT-TS-2026-018</b></span><span>版本 <b>{version}</b></span>
  <span>密级 <b>内部</b></span><span>日期 <b>{date}</b></span><span>责任部门 <b>核仪器事业部</b></span></div>
  {stamp}
</div>{body}
<div class="foot">本文件为受控文件，打印后不具受控效力，请以系统内受控版本为准。未经许可不得复制、外传。</div>
</body></html>"""


V32 = [
    "<h2>1 适用范围</h2>",
    "<p>本方案适用于华龙一号（HPR1000）核电机组 KRT 辐射监测系统的设计、制造、出厂验收与现场调试，覆盖反应堆厂房、核辅助厂房、燃料厂房及厂区环境监测通道。方案同时给出与 DCS、KIC 的接口约定及抗震、EMC 的符合性说明。</p>",
    "<h2>2 系统组成</h2>",
    "<table><tr><th style='width:22%'>子系统</th><th style='width:30%'>主要设备</th><th>功能说明</th></tr>"
    "<tr><td>工艺辐射监测</td><td>惰性气体监测道、碘监测道、气溶胶监测道</td><td>一回路泄漏与燃料包壳破损监测，超阈值触发报警并送入 DCS</td></tr>"
    "<tr><td>区域辐射监测</td><td>区域 γ 监测仪、中子剂量率仪</td><td>厂房剂量率连续监测，联动门禁与通风</td></tr>"
    "<tr><td>环境监测</td><td>气象站、环境 γ 剂量率仪、气溶胶采样器</td><td>厂区及周边环境放射性水平监测与取样</td></tr>"
    "<tr><td>中央数据处理</td><td>KRT 机柜、数据采集单元、KIC 通信接口</td><td>集中显示、报警分级、数据存档与远程诊断</td></tr></table>",
    "<h2>3 主要技术指标</h2>",
    "<table><tr><th>指标项</th><th>要求</th><th>本方案</th></tr>"
    "<tr><td>探测效率（惰性气体）</td><td>≥ 25%</td><td>28%（NaI 探测器，<sup>137</sup>Cs 参考源）</td></tr>"
    "<tr><td>能量范围</td><td>60 keV — 3 MeV</td><td>50 keV — 3 MeV</td></tr>"
    "<tr><td>报警响应时间</td><td>≤ 2 s</td><td>≤ 1.2 s</td></tr>"
    "<tr><td>抗震等级</td><td>SL-2（安全级）</td><td>按 SL-2 进行抗震鉴定，报告编号 XNIC-SE-2025-073</td></tr>"
    "<tr><td>平均无故障时间 MTBF</td><td>≥ 8000 h</td><td>≥ 12000 h（基于同型设备运行数据）</td></tr></table>",
    "<div class='note'><b>版本 V3.2 变更说明：</b>依据 2026 年 8 月设计评审意见，将区域 γ 监测仪报警阈值由两级改为三级，补充与 KIC 的 Modbus-TCP 冗余链路描述，并更新抗震鉴定报告编号。</div>",
    "<h2>4 国产化情况</h2>",
    "<p>探测器、前置放大、数据采集单元、机柜及软件均为自主设计制造，国产化率 96.4%。其中高压模块、光电倍增管已完成国产替代验证，验证报告随投标文件一并提交。</p>",
]

V31 = [
    "<h2>1 适用范围</h2>",
    "<p>本方案适用于华龙一号核电机组 KRT 辐射监测系统，覆盖反应堆厂房、核辅助厂房与燃料厂房的主要监测通道。本版本为结构评审前的过渡版本，环境监测子系统仅列出接口要求，未展开设备选型。</p>",
    "<h2>2 系统组成</h2>",
    "<table><tr><th style='width:22%'>子系统</th><th>配置说明</th></tr>"
    "<tr><td>工艺辐射监测</td><td>惰性气体、碘、气溶胶三类监测道，共 12 个通道</td></tr>"
    "<tr><td>区域辐射监测</td><td>区域 γ 监测仪 26 台，中子剂量率仪 6 台</td></tr>"
    "<tr><td>中央数据处理</td><td>KRT 机柜 4 面，数据采集单元 8 套</td></tr></table>",
    "<h2>3 主要技术指标（待评审确认）</h2>",
    "<table><tr><th>指标项</th><th>要求</th><th>本方案</th></tr>"
    "<tr><td>探测效率（惰性气体）</td><td>≥ 25%</td><td>26%</td></tr>"
    "<tr><td>报警响应时间</td><td>≤ 2 s</td><td>≤ 1.8 s</td></tr>"
    "<tr><td>抗震等级</td><td>SL-2</td><td>拟按 SL-2，鉴定工作待启动</td></tr></table>",
    "<div class='note'><b>版本 V3.1 状态：</b>结构评审用稿。环境监测子系统、KIC 通信协议、抗震鉴定报告尚待补充，不作为投标提交版本。</div>",
]

V30 = [
    "<h2>1 编制目的</h2>",
    "<p>本稿为 KRT 辐射监测系统技术方案的初稿，用于内部技术路线讨论，仅覆盖探测器选型与信号处理链路的基本设想。</p>",
    "<h2>2 技术路线设想</h2>",
    "<ol><li>惰性气体监测道采用 NaI(Tl) 探测器 + 数字多道分析，替代原模拟单道方案；</li>"
    "<li>区域监测仪统一采用 GM 计数管方案，量程 0.1 μSv/h — 10 Sv/h；</li>"
    "<li>数据采集单元采用自研板卡，预留 Modbus-TCP 与 IEC 60870-5-104 两种上行协议。</li></ol>",
    "<div class='note'><b>版本 V3.0 说明：</b>初稿，未包含系统组成表、技术指标与合规性分析，仅作讨论用，已归档保留。</div>",
]

TXT = """HAF003《核电厂质量保证安全规定》条款摘录
（用于文档库全文检索与受控引用演示，非正式文本，正式引用请以国家核安全局发布版本为准）

一、文件控制（对应 HAF003 第 5 章）
1. 必须对文件的编制、审核、批准、发放、修订和作废实施控制，确保使用场所只使用最新有效版本。
2. 必须建立受控文件清单，标明文件编号、版本、发放范围与持有人。
3. 作废文件如需留存，必须明确标识，防止误用。

二、质量记录（对应 HAF003 第 6 章）
1. 质量记录必须及时、真实、完整、清晰，并可追溯至所涉及的对象。
2. 质量记录的保存期限必须按法规、合同与业主的要求确定；核安全相关记录通常不少于机组寿期。
3. 记录介质变更（纸质转电子）时必须保证内容完整、可读、可追溯，并保留转换过程的验证记录。

三、不符合项控制
1. 对不符合要求的物项必须形成不符合项报告，明确处置方式（返工、返修、让步接收、报废）。
2. 重大不符合项的处理必须经原设计单位与业主确认。

四、内部应用说明（XNIC）
· 文档库中的“受控版本”对应本章“只使用最新有效版本”要求：新版本入库后旧版本自动转为历史，历史版本仅可查看与追溯，不得作为投标引用依据。
· “僵尸文档”处置不采用物理删除，对应质量记录保存期限要求。
· 删除操作仅移入回收站并保留审计痕迹，彻底删除需满足无引用记录且经二次确认。
"""

DIAGRAM = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8"><style>
body{margin:0;background:#f4f7fb;font-family:"Microsoft YaHei",sans-serif;color:#12263f;width:1200px;height:760px}
.wrap{padding:28px 34px}
h1{font-size:22px;margin:0 0 4px;color:#0b2b57}
.sub{font-size:12px;color:#7b8794;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.card{background:#fff;border:1px solid #d9e2ec;border-radius:8px;padding:12px 14px}
.card b{display:block;font-size:13px;color:#0b2b57;margin-bottom:6px}
.card span{font-size:11.5px;color:#5b6774;line-height:1.7}
.card ul{margin:4px 0 0 16px;padding:0;font-size:11.5px;color:#43505e;line-height:1.8}
.flow{display:flex;align-items:center;gap:10px;margin-top:18px}
.node{flex:1;background:#fff;border:1px solid #c9a227;border-radius:8px;padding:10px;text-align:center;font-size:12px;color:#0b2b57}
.arrow{color:#c9a227;font-size:18px}
.foot{margin-top:18px;font-size:10.5px;color:#7b8794}
</style></head><body><div class="wrap">
<h1>实物保护系统（PPS）系统组成框图</h1>
<div class="sub">XNIC-PPS-DWG-2026-004 · V2.0 · 商密 · 仅限内部查阅</div>
<div class="grid">
<div class="card"><b>周界入侵报警</b><span>双技术探测器、张力围栏、微波对射</span><ul><li>分区 12 个防区</li><li>报警响应 ≤ 1 s</li></ul></div>
<div class="card"><b>视频监控</b><span>高清球机、固定枪机、存储 90 天</span><ul><li>点位 186 个</li><li>与报警联动预置位</li></ul></div>
<div class="card"><b>出入口控制</b><span>双因子读卡器、速通门、车辆道闸</span><ul><li>通道 28 条</li><li>与人事系统同步权限</li></ul></div>
<div class="card"><b>周界照明与音响</b><span>高压钠灯、号角扬声器</span><ul><li>与报警分区联动</li></ul></div>
<div class="card"><b>中央控制室</b><span>集成管理平台、报警复核工作站</span><ul><li>双机热备</li><li>事件留存 5 年</li></ul></div>
<div class="card"><b>供电与防雷</b><span>UPS 2×20kVA、二级防雷</span><ul><li>后备时间 ≥ 60 min</li></ul></div>
</div>
<div class="flow"><div class="node">前端探测 / 采集</div><span class="arrow">→</span><div class="node">区域汇聚交换机</div><span class="arrow">→</span><div class="node">中央集成平台</div><span class="arrow">→</span><div class="node">值班终端 / 上级平台</div></div>
<div class="foot">本图为系统级示意图，设备数量与型号以设备清单（XNIC-PPS-BOM-2026-011）为准。</div>
</div></body></html>"""

(OUT / "krt_v32.html").write_text(page("KRT 辐射监测系统技术方案（华龙一号堆型）", "V3.2", "2026-09-12", V32,
                                       "<span class='stamp'>受控文件</span>"), encoding="utf-8")
(OUT / "krt_v31.html").write_text(page("KRT 辐射监测系统技术方案（华龙一号堆型）", "V3.1", "2026-07-28", V31), encoding="utf-8")
(OUT / "krt_v30.html").write_text(page("KRT 辐射监测系统技术方案（华龙一号堆型）", "V3.0", "2026-06-15", V30), encoding="utf-8")
(OUT / "haf003_excerpt.txt").write_text(TXT, encoding="utf-8")
(OUT / "pps_diagram.html").write_text(DIAGRAM, encoding="utf-8")
print("written")
