# -*- coding: utf-8 -*-
"""生成 Phase 1 的样例招标书 PDF fixture（合成、可复现）。

为何要合成样例：
- 真实招标书有版权/敏感顾虑，不能入库；合成样例含全部"类型陷阱"（★、废标、偏离表、张冠李戴靶子等），
  便于离线跑通 pipeline + 让 pytest 断言稳定。
- 本脚本每次运行生成完全一致的 PDF（无时间戳/随机），可作回归基线。

用法：uv run python scripts/make_tender_fixture.py
输出：fixtures/tender_sample.pdf
"""
from __future__ import annotations

from pathlib import Path

import fitz

OUT = Path(__file__).resolve().parents[1] / "fixtures" / "tender_sample.pdf"

# Windows 自带中文字体候选（按优先级）。找不到时退回内置字体（文本层仍可抽取，
# 只是预览是方块 —— 不影响 pipeline，仅影响肉眼查看）。
FONT_CANDIDATES = [
    Path("C:/Windows/Fonts/msyh.ttc"),   # 微软雅黑
    Path("C:/Windows/Fonts/simsun.ttc"),  # 宋体
    Path("C:/Windows/Fonts/simhei.ttf"),  # 黑体
]


class PagedWriter:
    """把文本行灌进一页页 A4：行满自动开新页。"""

    FONT_SIZE = 11
    LINE_H = 16
    MARGIN = 56
    PAGE_W = 595
    PAGE_H = 842

    def __init__(self, doc: fitz.Document, fontfile: Path | None) -> None:
        self.doc = doc
        self.fontfile = fontfile
        self.fontname = "cjk" if fontfile else "helv"
        self._page: fitz.Page | None = None
        self.y = 0

    def _ensure_page(self) -> fitz.Page:
        if self._page is None or self.y > self.PAGE_H - self.MARGIN - self.LINE_H:
            self._page = self.doc.new_page(width=self.PAGE_W, height=self.PAGE_H)
            if self.fontfile:
                # 嵌入 CJK 字体，保证中文正常显示与复制
                self._page.insert_font(fontname=self.fontname, fontfile=str(self.fontfile))
            self.y = self.MARGIN
        return self._page

    def write(self, text: str) -> None:
        for raw_line in text.splitlines():
            self._write_line(raw_line if raw_line.strip() else " ")

    def _write_line(self, line: str) -> None:
        # 超宽自动折行：估算字符宽度（CJK≈1em，ASCII≈0.55em）
        max_w = self.PAGE_W - self.MARGIN * 2
        while line:
            width = 0.0
            cut = 0
            for i, ch in enumerate(line):
                w = self.FONT_SIZE if ord(ch) > 0x2E7F else self.FONT_SIZE * 0.55
                if width + w > max_w and cut > 0:
                    break
                width += w
                cut = i + 1
            piece, line = line[:cut], line[cut:]
            page = self._ensure_page()
            page.insert_text(
                (self.MARGIN, self.y), piece, fontname=self.fontname, fontsize=self.FONT_SIZE
            )
            self.y += self.LINE_H

    def blank(self, n: int = 1) -> None:
        for _ in range(n):
            page = self._ensure_page()
            self.y += self.LINE_H * 2  # 空行：两行高更醒目
            _ = page


def _chapters() -> list[str]:
    """章节正文。设计成"一章一个目标栏目"，与 rules.py 的锚点一一对应。"""
    return [
        # 封面
        "\n".join(
            [
                "XX市城市大脑建设指挥部",
                "智慧园区安防系统升级改造项目",
                "招 标 文 件",
                "项目编号：XXCG2026-058",
                "",
                "采购人：XX市智慧城市建设发展中心",
                "招标代理机构：XX招标咨询有限公司",
                "2026年8月",
                "",
                "重要提示：本招标文件中标注 ★ 的条款均为实质性条款，投标人必须实质性响应；",
                "对实质性条款存在负偏离（不满足）的，其投标将被否决（废标）。",
            ]
        ),
        # 第一章 项目概况（不含目标锚点，仅承载 header 启发式信息）
        "\n".join(
            [
                "第一章 项目概况",
                "1.1 项目名称：智慧园区安防系统升级改造项目。",
                "1.2 采购人：XX市智慧城市建设发展中心。",
                "1.3 建设地点：XX市高新技术产业开发区A区。",
                "1.4 预算金额：人民币 580 万元整（含税），投标报价不得超过预算金额。",
                "1.5 投标截止时间：2026年10月31日 09:30（北京时间）；开标时间同投标截止时间。",
                "1.6 资金来源：财政资金，已落实。",
                "1.7 建设内容：对A区既有安防系统进行升级，包含前端感知设备更换、AI分析平台部署、",
                "          存储扩容、管线改造及与既有平台对接等。",
                "1.8 计划工期：120 日历天（自合同签订次日起算），其中须在 60 日历天内完成全部",
                "          前端设备更换并接入平台试运行。",
            ]
        ),
        # 第二章 投标人资格要求（锚点：资格要求）
        "\n".join(
            [
                "第二章 投标人资格要求",
                "2.1 资质要求：投标人须具备电子与智能化工程专业承包壹级资质，提供有效证书复印件。",
                "2.2 业绩要求：近三年（2023年1月1日至投标截止日）须具有不少于3个同类园区安防集成",
                "          类项目业绩，且单个合同金额不低于500万元，须提供合同关键页及验收证明。",
                "2.3 项目团队：拟派项目经理须持有机电工程专业一级建造师注册证书，且为本单位在职",
                "          人员（须提供近6个月社保证明）。",
                "2.4 信用要求：未被\u201c信用中国\u201d列入失信被执行人、重大税收违法案件当事人名单；",
                "          参加本次采购活动前三年内无重大违法记录。",
                "2.5 本项目不接受联合体投标。",
            ]
        ),
        # 第三章 评标办法（锚点：评标办法 → score_points）
        "\n".join(
            [
                "第三章 评标办法",
                "3.1 本项目采用综合评分法，总分100分：技术部分60分、商务部分20分、价格20分。",
                "3.2 评分因素与分值如下：",
                "评分点   评分因素    分值    评分标准",
                "SP-01    技术方案    10    提供总体技术方案（含系统架构图、设计原理说明），",
                "                             方案先进可行、针对性强、体现对需求的理解，得0-10分。",
                "SP-02    项目业绩    15    每提供一个同类园区安防项目业绩（附合同与验收证明）得5分，",
                "                             最高15分。",
                "SP-03    项目团队    10    项目经理具备高级职称或机电一级建造师的得5分；",
                "                             核心实施人员配置合理（须提供简历与社保证明）得5分。",
                "SP-04    实施计划    10    项目实施计划与工期保障措施科学、里程碑清晰得0-10分。",
                "SP-05    售后服务    5     质保期3年为基础，每延长1年得1分（满分3分）；",
                "                             本地化7×24响应、2小时到场承诺得2分。",
                "SP-06    价格分      20    报价得分=评标基准价÷投标报价×20，",
                "                             评标基准价=通过初审的最低有效报价。",
                "3.3 各评分因素得分之和为技术商务分；总分由技术商务分与价格分加总得出。",
            ]
        ),
        # 第四章 技术规格与参数要求（锚点：技术规格 → tech_params；★行即关键参数）
        "\n".join(
            [
                "第四章 技术规格与参数要求",
                "4.1 投标人须逐项响应下表；标注★的技术参数为关键参数，不允许负偏离。",
                "序号  设备/项目       技术参数要求",
                "1    高清网络摄像机    ★ 分辨率≥400万像素；传感器1/1.8英寸CMOS；",
                "                             最低照度≤0.005Lux；内置智能人脸抓拍算法。",
                "2    网络硬盘录像机    接入路数≥32路；存储周期≥30天。",
                "3    AI分析服务器      ★ CPU核心数≥32核；内存≥128GB；",
                "                             支持≥100路实时视频流AI分析，误报率≤2%。",
                "4    存储磁盘阵列      有效存储容量≥120TB；支持RAID6，含热备盘。",
                "5    平台软件          ★ 具备电子地图、报警联动、移动端APP能力；",
                "                             符合GB/T28181协议可对接上级平台；并发用户≥200。",
                "6    管线及配套材料     主干光缆采用24芯单模；含全部熔接、标识与桥架。",
                "7    安装与联调        含原有设备利旧改造、新旧系统并行切换与整体联调。",
                "4.2 表中所有设备须为原厂正品，提供出厂合格证明。",
            ]
        ),
        # 第五章 ★实质性条款（锚点：★ → star_clauses）
        "\n".join(
            [
                "第五章 ★实质性要求（一票否决项）",
                "★5.1 投标技术方案须完整响应第四章全部技术参数要求；任一★关键参数负偏离的，",
                "        投标文件按无效处理。",
                "★5.2 项目交付工期须≤120日历天（自合同签订次日起算），不满足的按废标处理。",
                "★5.3 整体质保期须≥3年，并提供7×24小时响应；重大故障须2小时内到达现场处置。",
                "★5.4 投标人须提供本项目核心设备原厂授权书及原厂售后服务承诺函（加盖原厂公章），",
                "        否则按未实质响应处理。",
                "★5.5 付款方式须接受：合同签订后支付30%，货到验收后支付至95%，质保期满无息付清",
                "        剩余5%。",
            ]
        ),
        # 第六章 废标与否决投标条款（锚点：废标 → waste_bid_terms）
        "\n".join(
            [
                "第六章 废标与否决投标条款",
                "投标文件属下列情形之一的，由评标委员会按否决投标处理：",
                "(一) 未在投标截止时间前递交投标文件，或未按招标文件要求密封、标记的；",
                "(二) 投标文件未实质性响应招标文件的，包括对任一★实质性条款存在负偏离的；",
                "(三) 未按规定提交投标保证金的；",
                "(四) 投标文件未按招标文件要求签字盖章，或关键内容涂改未加盖校对章的；",
                "(五) 存在弄虚作假、围标串标行为的；",
                "(六) 投标报价超过预算金额，或明显低于其他有效报价且不能合理说明的。",
            ]
        ),
        # 第七章 项目实施时间安排（锚点：时间安排 → timeline）
        "\n".join(
            [
                "第七章 项目实施时间安排",
                "中标后关键里程碑节点如下（中标人须据此倒排计划）：",
                "合同签订：2026-11-15",
                "深化设计及设备采购到货：2026-12-15",
                "管线施工与前端设备安装：2027-01-20",
                "平台部署、系统联调与试运行：2027-02-20",
                "初验：2027-02-28",
                "终验（竣工验收）：2027-03-10",
                "注：以上为计划时间，如采购人要求提前，中标人应服从采购人统一安排。",
            ]
        ),
        # 第八章 投标文件组成与偏离表（展示偏离表语义，Phase4 判偏离的靶子）
        "\n".join(
            [
                "第八章 投标文件组成与偏离表格式",
                "8.1 投标文件分商务标、技术标、价格标三部分，缺一不可。",
                "8.2 偏离表须对照招标文件要求逐项填写，格式如下：",
                "序号 | 招标文件要求 | 投标响应 | 偏离类型（正/无/负偏离）",
                "示例：对★实质性条款存在\u201c负偏离\u201d的，投标将被否决；",
                "      正偏离须附可核实的证明材料，否则评标委员会不予认可。",
                "8.3 投标人应提供完整投标报价一览表，含分项报价与总价；总价须大小写一致。",
            ]
        ),
    ]


def main() -> None:
    fontfile = next((f for f in FONT_CANDIDATES if f.exists()), None)
    doc = fitz.open()
    w = PagedWriter(doc, fontfile)
    for page_no, chapter in enumerate(_chapters(), start=1):
        if page_no > 1:
            w.blank()
        w.write(chapter)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT, garbage=4, deflate=True)
    doc.close()
    print(f"[ok] 已生成 {OUT}  (pages={len(_chapters())}, font={'embedded:'+fontfile.name if fontfile else 'builtin-fallback'})")


if __name__ == "__main__":
    main()
