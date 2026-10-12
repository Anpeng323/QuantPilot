"""
PDF 导出：基于 reportlab（轻量，无 weasyprint 系统依赖）。
"""
from __future__ import annotations
import os
import re
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle


# 注册中文字体（reportlab 自带 CID 字体，无需额外文件）
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def md_to_pdf(md_text: str, out_dir: str, filename: str = "export.pdf") -> str:
    """把 markdown 文本转 PDF（基础渲染：标题、列表、段落、表格）。"""
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    if not filename.endswith(".pdf"):
        filename += ".pdf"
    path = os.path.join(out_dir, filename)

    styles = getSampleStyleSheet()
    # 中文段落样式
    cn_para = ParagraphStyle(
        "CN", parent=styles["Normal"],
        fontName="STSong-Light", fontSize=11, leading=16, spaceAfter=6,
    )
    cn_h1 = ParagraphStyle("CNH1", parent=styles["Heading1"],
        fontName="STSong-Light", fontSize=18, leading=22, spaceBefore=10, spaceAfter=8, textColor=colors.HexColor("#0a2540"))
    cn_h2 = ParagraphStyle("CNH2", parent=styles["Heading2"],
        fontName="STSong-Light", fontSize=14, leading=18, spaceBefore=8, spaceAfter=6, textColor=colors.HexColor("#0a2540"))

    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=2*cm, rightMargin=2*cm,
                            topMargin=2*cm, bottomMargin=2*cm)
    story = []

    # 简易 markdown 解析
    in_table = False
    table_rows = []
    for line in md_text.splitlines():
        stripped = line.strip()

        # 表格行
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            table_rows.append(cells)
            in_table = True
            continue
        else:
            # 表格结束 → 渲染
            if in_table and table_rows:
                # 跳过分隔行（只含 --- 的）
                if not all(set(c) <= set("-: ") for c in table_rows[0]):
                    data = [[Paragraph(str(c), cn_para) for c in row] for row in table_rows]
                    tbl = Table(data)
                    tbl.setStyle(TableStyle([
                        ("FONTNAME",   (0,0), (-1,-1), "STSong-Light"),
                        ("FONTSIZE",   (0,0), (-1,-1), 10),
                        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e8eef5")),
                        ("INNERGRID",  (0,0), (-1,-1), 0.4, colors.grey),
                        ("BOX",        (0,0), (-1,-1), 0.4, colors.grey),
                        ("VALIGN",     (0,0), (-1,-1), "TOP"),
                        ("LEFTPADDING",  (0,0), (-1,-1), 5),
                        ("RIGHTPADDING", (0,0), (-1,-1), 5),
                    ]))
                    story.append(tbl)
                    story.append(Spacer(1, 0.4*cm))
                table_rows = []
                in_table = False

        if not stripped:
            story.append(Spacer(1, 0.2*cm))
            continue
        if stripped.startswith("# "):
            story.append(Paragraph(_escape(stripped[2:]), cn_h1))
        elif stripped.startswith("## "):
            story.append(Paragraph(_escape(stripped[3:]), cn_h2))
        elif stripped.startswith("### "):
            story.append(Paragraph(f"<b>{_escape(stripped[4:])}</b>", cn_para))
        elif stripped.startswith("> "):
            story.append(Paragraph(f"<i>{_escape(stripped[2:])}</i>", cn_para))
        elif re.match(r"^[-*] ", stripped):
            story.append(Paragraph(f"• {_escape(stripped[2:])}", cn_para))
        elif re.match(r"^\d+\. ", stripped):
            story.append(Paragraph(stripped, cn_para))
        elif re.match(r"^---+$", stripped):
            story.append(Spacer(1, 0.2*cm))
        else:
            story.append(Paragraph(_escape(stripped), cn_para))

    # 尾部残留表格
    if in_table and table_rows:
        data = [[Paragraph(str(c), cn_para) for c in row] for row in table_rows]
        tbl = Table(data)
        tbl.setStyle(TableStyle([
            ("FONTNAME",   (0,0), (-1,-1), "STSong-Light"),
            ("FONTSIZE",   (0,0), (-1,-1), 10),
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#e8eef5")),
            ("INNERGRID",  (0,0), (-1,-1), 0.4, colors.grey),
            ("BOX",        (0,0), (-1,-1), 0.4, colors.grey),
        ]))
        story.append(tbl)

    doc.build(story)
    return path


def _escape(text: str) -> str:
    """escape reportlab Paragraph 特殊字符"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def ai_analysis_to_pdf(conn, analysis_id: str, out_dir: str) -> str:
    """ai_analysis → MD → PDF"""
    from exports.markdown import ai_analysis_to_md
    md = ai_analysis_to_md(conn, analysis_id)
    return md_to_pdf(md, out_dir, f"analysis_{analysis_id}.pdf")
