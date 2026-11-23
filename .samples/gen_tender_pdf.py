# -*- coding: utf-8 -*-
"""生成演示用招标文件 PDF 样张：真实章节结构 + 评分表 + ★/废标条款，
用于演示解析 Agent 的 PDF 版面真实抽取（HTML → 无头 Edge → PDF）。"""
from pathlib import Path

OUT = Path(__file__).resolve().parent

CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: "SimSun", "Microsoft YaHei", serif; color: #1b2430; font-size: 12px; line-height: 1.95; margin: 0; }
.cover { text-align: center; padding-top: 70mm; }
.cover h1 { font-size: 24px; letter-spacing: 3px; margin: 14px 0; }
.cover .no { font-size: 14px; letter-spacing: 2px; margin: 10px 0; }
.cover .meta { font-size: 12px; color: #43505e; margin-top: 30mm; line-height: 2.2; }
h2.chap { font-size: 16px; text-align: center; margin: 26px 0 14px; letter-spacing: 2px; font-family: "SimHei", "Microsoft YaHei", sans-serif; }
h3 { font-size: 13.5px; margin: 14px 0 6px; color: #0b2b57; }
p { margin: 6px 0; text-align: justify; text-indent: 2em; }
p.noindent { text-indent: 0; }
table { width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 11px; }
th, td { border: 1px solid #666; padding: 5px 8px; text-align: center; }
th { background: #eef2f8; }
td.l { text-align: left; }
.star { color: #b42318; font-weight: 700; }
.note { border: 1px dashed #c9a227; padding: 8px 10px; font-size: 10.5px; color: #43505e; margin: 10px 0; }
"""

HTML = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>{CSS}</style></head><body>
<div class="cover">
  <div style="font-size:15px;letter-spacing:6px">招标文件</div>
  <h1>漳州核电 3、4 号机组辐射监测系统（KRT）设备采购</h1>
  <div class="no">招标编号：CNPE-ZZ-2026-KRT-118</div>
  <div class="meta">
    招标人：中国核电工程有限公司<br>
    招标代理机构：中核（上海）供应链管理有限公司<br>
    2026 年 11 月
  </div>
</div>

<h2 class="chap">第一章 招标公告</h2>
<p>中国核电工程有限公司（以下简称“招标人”）采用公开招标方式，采购漳州核电 3、4 号机组辐射监测系统（KRT）成套设备及配套服务，欢迎符合资格条件的投标人参加投标。</p>
<p class="noindent"><b>1.1 项目概况：</b>本工程为华龙一号堆型，采购范围包括核电厂辐射监测系统（KRT）的监测通道设备、中央处理与显示设备、应用软件及专用工具等。</p>
<p class="noindent"><b>1.2 交付地点：</b>福建省漳州市云霄县漳州核电现场。</p>

<h2 class="chap">第二章 投标人须知</h2>
<h3>一、资格要求</h3>
<p class="noindent">6.1 投标人须具备有效的民用核安全设备制造许可证，许可范围涵盖本标的安全级监测设备类别。</p>
<p class="noindent">6.2 投标人近五年（2021 年至今）须具有至少 2 项同类辐射监测系统成套供货业绩，须附合同复印件及验收证明。</p>
<p class="noindent">6.3 投标人须具备有效的 ISO 9001 质量管理体系认证证书。</p>
<h3>二、投标保证金</h3>
<p class="noindent">8.1 投标保证金金额为人民币捌拾万元整（¥800,000），须以基本账户电汇方式于开标前到账。</p>
<h3>三、澄清与截止</h3>
<p class="noindent">9.1 澄清截止时间：投标人若有澄清问题，请于 2026 年 11 月 20 日 17 时前以书面形式提交。</p>
<p class="noindent">9.2 投标截止时间（开标时间）：2026 年 12 月 8 日 09 时 30 分。</p>
<h3>四、投标文件的密封与递交</h3>
<p class="noindent">15.2 逾期送达或者未按要求密封的投标文件，招标人将拒收并否决其投标。</p>
<p class="noindent">15.4 投标文件对招标文件★条款存在负偏离的，评标委员会将否决其投标。</p>
<p class="noindent">17.1 投标文件须装订成册：正本 1 份、副本 4 份、电子版 2 份（U 盘），逐页编码。</p>
<h3>五、评标程序</h3>
<p class="noindent">20.1 评标采用综合评估法，评分因素及分值分布见第三章评标办法。各评分因素分值合计 100 分。</p>

<h2 class="chap">第三章 评标办法</h2>
<p class="noindent">评标委员会按下列评分因素对通过初步评审的投标文件进行量化打分：</p>
<table>
  <tr><th style="width:8%">序号</th><th style="width:44%">评分因素</th><th style="width:10%">分值</th><th style="width:38%">评分标准</th></tr>
  <tr><td>1</td><td class="l">技术方案完整性（系统架构、设备清单、接口设计）</td><td>10</td><td class="l">方案完整、逻辑清晰得 8-10 分；基本完整得 5-7 分</td></tr>
  <tr><td>2</td><td class="l">技术参数响应程度</td><td>30</td><td class="l">对技术规格书逐条响应，无负偏离得满分；每项一般偏离扣 2 分</td></tr>
  <tr><td>3</td><td class="l">类似供货业绩</td><td>25</td><td class="l">每提供 1 项有效业绩得 5 分，最多得 25 分</td></tr>
  <tr><td>4</td><td class="l">项目实施方案与质量保证</td><td>15</td><td class="l">进度计划合理、质保体系健全得 12-15 分</td></tr>
  <tr><td>5</td><td class="l">培训与技术服务</td><td>10</td><td class="l">培训方案与现场服务承诺满足要求得 8-10 分</td></tr>
  <tr><td>6</td><td class="l">投标报价</td><td>10</td><td class="l">以评标基准价计算，每高 1%扣 0.5 分</td></tr>
  <tr><td colspan="2"><b>合计</b></td><td><b>100</b></td><td></td></tr>
</table>
<p class="noindent">（注：评分分值合计 100 分。投标报价得分按插入法计算，保留两位小数。）</p>

<h2 class="chap">第四章 技术规格书（节选）</h2>
<h3>一、总体要求</h3>
<p class="noindent"><span class="star">★</span>4.1 安全级监测通道须按 IE 级要求进行设计鉴定，满足事故工况下的功能要求。</p>
<p class="noindent"><span class="star">★</span>4.2 设备须满足 SL-2 载荷下的抗震要求，并提供抗震鉴定报告。</p>
<h3>二、技术参数</h3>
<p class="noindent">4.5.1 系统通讯采用冗余工业以太网，须提供完整 Modbus TCP 协议文本及寄存器定义表。</p>
<p class="noindent">4.5.2 区域 γ 监测通道量程须覆盖 0.1 μSv/h 至 10 Sv/h，精度优于 ±10%。</p>
<h3>三、交付与验收</h3>
<p class="noindent">6.1 合同生效后 20 个月内完成全部设备交付，交付顺序满足现场安装进度需求。</p>
<p class="noindent">6.2 设备到货后由买方组织开箱检验，卖方须派代表参加并完成现场安装调试指导。</p>
<div class="note">本招标文件所有条款以电子版为准，澄清答疑将统一以书面形式发出，与招标文件具有同等效力。</div>
</body></html>"""


if __name__ == "__main__":
    target = OUT / "zz_krt_tender_excerpt.html"
    target.write_text(HTML, encoding="utf-8")
    print("written", target)
