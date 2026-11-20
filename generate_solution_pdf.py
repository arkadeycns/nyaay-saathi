"""
Render docs/solution_outline.md into docs/solution.pdf (target: 2 pages).
The markdown file is the single source; this script only lays it out.

    python generate_solution_pdf.py
"""
from __future__ import annotations

import pathlib
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = pathlib.Path(__file__).parent
SRC = ROOT / "docs" / "solution_outline.md"
OUT = ROOT / "docs" / "solution.pdf"

ss = getSampleStyleSheet()
BODY = ParagraphStyle("b", parent=ss["BodyText"], fontSize=7.6, leading=9.4, spaceAfter=1.5)
H1 = ParagraphStyle("h1", parent=ss["Title"], fontSize=12.5, leading=15, spaceAfter=3)
H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=9, leading=11, spaceBefore=4, spaceAfter=1.5, textColor=colors.HexColor("#1f3b57"))
SUB = ParagraphStyle("sub", parent=BODY, leftIndent=12)
CELL = ParagraphStyle("c", parent=BODY, fontSize=6.8, leading=8.2)


def inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("₹", "Rs ")  # base-14 PDF fonts have no rupee glyph
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\s][^*]*?)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"`(.+?)`", r"<font face='Courier'>\1</font>", text)
    text = re.sub(r"\[(.+?)\]\((https?://[^)]+)\)", r"<link href='\2' color='#1f5fa8'><u>\1</u></link>", text)
    return text


def build() -> None:
    flow, table = [], []
    lines = SRC.read_text(encoding="utf-8").splitlines() + [""]
    for line in lines:
        if line.startswith("|"):
            if not re.match(r"^\|[\s\-|:]+\|$", line):
                table.append([Paragraph(inline(c.strip()), CELL) for c in line.strip("|").split("|")])
            continue
        if table:
            t = Table(table, repeatRows=1, hAlign="LEFT")
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                                   ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef4")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 1),
                                   ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
            flow += [t, Spacer(1, 3)]
            table = []
        if line.startswith("# "):
            flow.append(Paragraph(inline(line[2:]), H1))
        elif line.startswith("## "):
            flow.append(Paragraph(inline(line[3:]), H2))
        elif line.startswith("  - "):
            flow.append(Paragraph("– " + inline(line[4:]), SUB))
        elif line.startswith("- "):
            flow.append(Paragraph("• " + inline(line[2:]), BODY))
        elif line.strip():
            flow.append(Paragraph(inline(line), BODY))
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=34, rightMargin=34, topMargin=30, bottomMargin=30)
    doc.build(flow)
    from pypdf import PdfReader
    print(f"wrote {OUT} ({len(PdfReader(str(OUT)).pages)} pages)")


if __name__ == "__main__":
    build()
