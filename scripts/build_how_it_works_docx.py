"""Build docs/how-it-works.docx -- the end-to-end technical walkthrough.

The .docx is the deliverable, but a binary is a bad place to keep prose: it
cannot be diffed, reviewed or patched. So the text lives here and the document
is generated, which means a correction is a normal code change and the file can
always be rebuilt from scratch:

    .venv/Scripts/python.exe scripts/build_how_it_works_docx.py

Charts are drawn with PIL rather than matplotlib. Three bar charts do not
justify pulling matplotlib (and its numpy/pillow pins) into the environment the
MIDI pipeline runs in; PIL is already a dependency, and drawing at 3x then
downsampling gives clean edges without a plotting library.
"""

import io
from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "how-it-works.docx"

# ── palette ───────────────────────────────────────────────────────────────────
# Deep navy and aqua, after the app's own deep-sea look.
NAVY_HEX, AQUA_HEX = "0B2545", "149EC4"
NAVY = RGBColor(0x0B, 0x25, 0x45)
AQUA = RGBColor(0x14, 0x9E, 0xC4)
AQUA_DARK = RGBColor(0x0E, 0x6F, 0x8C)
AQUA_LIGHT = RGBColor(0x8F, 0xDD, 0xF2)
INK = RGBColor(0x1B, 0x24, 0x30)
MUTED = RGBColor(0x5B, 0x6B, 0x7B)
ACCENT = NAVY
CODE_BG, CODE_FG = "0F1B2D", RGBColor(0xD7, 0xE3, 0xF0)
CALLOUT_BG = "E9F6FB"
ZEBRA = "F3F8FB"
RULE = "C9DCE8"
HEAD_FONT, BODY_FONT = "Segoe UI", "Calibri"
UPDATED = "28 September 2026"
TEXT_WIDTH_IN = 6.6   # 8.5in page less 0.95in margins each side

CHART_BLUE = (11, 37, 69)
CHART_GREY = (176, 190, 200)
CHART_RED = (224, 93, 93)
CHART_AQUA = (20, 158, 196)


# ── PIL chart helpers ─────────────────────────────────────────────────────────

def _font(size, bold=False):
    """A real UI font if Windows has one, else PIL's bitmap default."""
    for name in (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
        path = Path("C:/Windows/Fonts") / name
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _bar_chart(title, groups, series, ymax, ylabel, note=None, width=1000, height=450):
    """Grouped bar chart -> PNG bytes.

    groups: ["0%", "30%", ...]           one cluster per entry
    series: [(label, colour, [v, ...])]  one bar per group, per series
    """
    s = 3                                       # supersample, downsampled at the end
    W, H = width * s, height * s
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    f_title = _font(21 * s, bold=True)
    f_axis = _font(15 * s)
    f_val = _font(14 * s, bold=True)
    f_leg = _font(15 * s)

    # Three bands above the plot -- title, then a legend/axis-label row, then the
    # bars' own value labels -- because stacking them collides: at ymax a value
    # label sits exactly where a single-row legend would be.
    left, right = 90 * s, 30 * s
    top, bottom = 112 * s, (64 if note else 52) * s
    plot_w, plot_h = W - left - right, H - top - bottom
    legend_y = 60 * s

    d.text((left - 60 * s, 18 * s), title, font=f_title, fill=(26, 26, 26))

    # gridlines + y axis
    for i in range(6):
        v = ymax * i / 5
        y = top + plot_h - plot_h * (v / ymax)
        d.line([(left, y), (left + plot_w, y)], fill=(228, 230, 233), width=s)
        label = f"{v:.2f}".rstrip("0").rstrip(".") if ymax <= 2 else f"{v:.0f}"
        d.text((left - 14 * s - d.textlength(label, font=f_axis), y - 9 * s),
               label, font=f_axis, fill=(110, 110, 110))
    d.line([(left, top), (left, top + plot_h)], fill=(140, 140, 140), width=s)
    d.line([(left, top + plot_h), (left + plot_w, top + plot_h)], fill=(140, 140, 140), width=s)

    d.text((left - 60 * s, legend_y), ylabel, font=f_axis, fill=(110, 110, 110))

    n_groups, n_series = len(groups), len(series)
    slot = plot_w / n_groups
    bar_w = slot * 0.74 / n_series
    for gi, group in enumerate(groups):
        gx = left + slot * gi + slot * 0.13
        for si, (_, colour, values) in enumerate(series):
            v = values[gi]
            h = plot_h * (v / ymax)
            x0 = gx + bar_w * si
            y0 = top + plot_h - h
            d.rectangle([x0, y0, x0 + bar_w * 0.92, top + plot_h], fill=colour)
            txt = f"{v:.2f}" if ymax <= 2 else (f"{v:.0f}" if v == int(v) else f"{v:.1f}")
            d.text((x0 + (bar_w * 0.92 - d.textlength(txt, font=f_val)) / 2, y0 - 25 * s),
                   txt, font=f_val, fill=colour)
        label_w = d.textlength(group, font=f_axis)
        d.text((left + slot * gi + (slot - label_w) / 2, top + plot_h + 10 * s),
               group, font=f_axis, fill=(70, 70, 70))

    # legend, right-aligned on its own row so it can never reach the title
    lx = left + plot_w
    for label, colour, _ in reversed(series):
        tw = d.textlength(label, font=f_leg)
        lx -= tw + 14 * s
        d.text((lx, legend_y), label, font=f_leg, fill=(70, 70, 70))
        lx -= 21 * s        # swatch width (13) + the gap before the label (8)
        d.rectangle([lx, legend_y + 4 * s, lx + 13 * s, legend_y + 17 * s], fill=colour)
        lx -= 16 * s        # gap to the next legend entry

    if note:
        d.text((left, H - 26 * s), note, font=f_axis, fill=(120, 120, 120))

    img = img.resize((width, height), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── docx helpers ──────────────────────────────────────────────────────────────

# Word validates the ORDER of property elements, and reports a file with them
# out of order as corrupt. Each entry lists the tags that must come after the
# key, so a new element can be slotted in front of the first one present.
_AFTER = {
    "w:pBdr": ["w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap",
               "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN",
               "w:bidi", "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind",
               "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap", "w:jc",
               "w:textDirection", "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl",
               "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange"],
    "w:spacing": ["w:w", "w:kern", "w:position", "w:sz", "w:szCs", "w:highlight", "w:u",
                  "w:effect", "w:bdr", "w:shd", "w:fitText", "w:vertAlign", "w:rtl", "w:cs",
                  "w:em", "w:lang", "w:eastAsianLayout", "w:specVanish", "w:oMath"],
    "w:tblBorders": ["w:shd", "w:tblLayout", "w:tblCellMar", "w:tblLook", "w:tblCaption",
                     "w:tblDescription"],
    "w:tblCellMar": ["w:tblLook", "w:tblCaption", "w:tblDescription"],
    "w:tcBorders": ["w:shd", "w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText",
                    "w:vAlign", "w:hideMark"],
    "w:shd": ["w:noWrap", "w:tcMar", "w:textDirection", "w:tcFitText", "w:vAlign", "w:hideMark"],
}


def _insert(parent, el):
    """Add `el` to a property element in schema order, replacing any existing one."""
    tag = el.tag
    for old in parent.findall(tag):
        parent.remove(old)
    prefix_tag = "w:" + tag.split("}")[1]
    for succ in _AFTER.get(prefix_tag, []):
        found = parent.find(qn(succ))
        if found is not None:
            found.addprevious(el)
            return
    parent.append(el)


def _shade(element, hex_fill):
    """Cell shading; `element` is a tcPr."""
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hex_fill)
    _insert(element, shd)


def _border_el(tag, edges):
    """<w:tag> holding one child per edge: {"left": (size_eighths_pt, "RRGGBB")}.
    A size of 0 means no line on that edge."""
    box = OxmlElement(tag)
    for edge, (size, colour) in edges.items():
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single" if size else "nil")
        if size:
            el.set(qn("w:sz"), str(size))
            el.set(qn("w:space"), "0")
            el.set(qn("w:color"), colour)
        box.append(el)
    return box


def _para_border(owner, edges, space=6):
    """Borders on a paragraph (CT_P) or paragraph style (CT_Style) -- e.g. the
    aqua bar down the left of every H2."""
    box = _border_el("w:pBdr", edges)
    for el in box:
        el.set(qn("w:space"), str(space))
    _insert(owner.get_or_add_pPr(), box)


def _cell_borders(cell, edges):
    _insert(cell._tc.get_or_add_tcPr(), _border_el("w:tcBorders", edges))


def _table_borders(table, edges):
    _insert(table._tbl.tblPr, _border_el("w:tblBorders", edges))


def _cell_margins(table, top=70, bottom=70, left=110, right=110):
    """Cell padding in twentieths of a point -- Word's default is cramped."""
    mar = OxmlElement("w:tblCellMar")
    for edge, val in (("top", top), ("bottom", bottom), ("left", left), ("right", right)):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:w"), str(val))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    _insert(table._tbl.tblPr, mar)


def _letter_spacing(run, twentieths):
    sp = OxmlElement("w:spacing")
    sp.set(qn("w:val"), str(twentieths))
    _insert(run._r.get_or_add_rPr(), sp)


def _field(paragraph, instruction, placeholder=""):
    """A Word field (PAGE, TOC ...). Word fills it in; the placeholder shows
    until it does."""
    def fld(kind):
        el = OxmlElement("w:fldChar")
        el.set(qn("w:fldCharType"), kind)
        return el
    run = paragraph.add_run()
    run._r.append(fld("begin"))
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {instruction} "
    run._r.append(instr)
    run._r.append(fld("separate"))
    shown = paragraph.add_run(placeholder)
    end = paragraph.add_run()
    end._r.append(fld("end"))
    return shown


def _header_footer(section):
    """Running header (not on the cover) and a centred page number."""
    section.different_first_page_header_footer = True
    hp = section.header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.LEFT
    run = hp.add_run("HOW IMAGESOUND WORKS")
    run.font.size = Pt(7.5)
    run.font.bold = True
    run.font.color.rgb = AQUA
    _letter_spacing(run, 40)
    _para_border(hp._p, {"bottom": (4, RULE)}, space=4)

    fp = section.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    shown = _field(fp, "PAGE", "1")
    for r in fp.runs:
        r.font.size = Pt(8.5)
        r.font.color.rgb = MUTED
    shown.font.size = Pt(8.5)


class Doc:
    def __init__(self):
        self.d = Document()
        self.figures = 0
        style = self.d.styles["Normal"]
        style.font.name = BODY_FONT
        style.font.size = Pt(10.5)
        style.font.color.rgb = INK
        style.paragraph_format.space_after = Pt(7)
        style.paragraph_format.line_spacing = 1.18

        # H1 opens a chapter: new page, big aqua number, hairline underneath.
        h1 = self.d.styles["Heading 1"]
        h1.font.name, h1.font.size, h1.font.bold = HEAD_FONT, Pt(24), False
        h1.font.color.rgb = NAVY
        h1.paragraph_format.page_break_before = True
        h1.paragraph_format.space_before = Pt(0)
        h1.paragraph_format.space_after = Pt(16)
        h1.paragraph_format.keep_with_next = True
        _para_border(h1.element, {"bottom": (8, AQUA_HEX)}, space=8)

        # H2: navy, with an aqua bar down its left edge.
        h2 = self.d.styles["Heading 2"]
        h2.font.name, h2.font.size, h2.font.bold = HEAD_FONT, Pt(14), True
        h2.font.color.rgb = NAVY
        h2.paragraph_format.space_before = Pt(18)
        h2.paragraph_format.space_after = Pt(6)
        h2.paragraph_format.keep_with_next = True
        _para_border(h2.element, {"left": (24, AQUA_HEX)}, space=8)

        # H3: small caps in aqua -- a label more than a heading.
        h3 = self.d.styles["Heading 3"]
        h3.font.name, h3.font.size, h3.font.bold = HEAD_FONT, Pt(11), True
        h3.font.small_caps = True
        h3.font.color.rgb = AQUA_DARK
        h3.paragraph_format.space_before = Pt(12)
        h3.paragraph_format.space_after = Pt(4)
        h3.paragraph_format.keep_with_next = True

        # The TOC's own entry styles, so the contents page matches.
        for name, size, bold, colour, indent in (("TOC 1", 11, True, NAVY, 0),
                                                 ("TOC 2", 10, False, INK, 0.3)):
            try:
                s = self.d.styles[name]
            except KeyError:
                from docx.enum.style import WD_STYLE_TYPE
                s = self.d.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
                s.base_style = self.d.styles["Normal"]
            s.font.size, s.font.bold, s.font.color.rgb = Pt(size), bold, colour
            s.paragraph_format.left_indent = Inches(indent)
            s.paragraph_format.space_before = Pt(8 if bold else 0)
            s.paragraph_format.space_after = Pt(2)

        sec = self.d.sections[0]
        sec.left_margin = sec.right_margin = Inches(0.95)
        sec.top_margin = Inches(0.85)
        sec.bottom_margin = Inches(0.8)
        _header_footer(sec)

    # -- blocks --
    def h1(self, text):
        """"3. Stage 1 — image to words" -> a chapter opener: "03" in aqua,
        then the title. The number stays in the heading text so the table of
        contents carries it too."""
        num, _, title = text.partition(". ")
        para = self.d.add_paragraph(style="Heading 1")
        if num.isdigit():
            n = para.add_run(f"{int(num):02d}")
            n.font.color.rgb = AQUA
            n.font.bold = True
            para.add_run("   ")
            para.add_run(title)
        else:
            para.add_run(text)

    def h2(self, text):
        self.d.add_heading(text, level=2)

    def h3(self, text):
        self.d.add_heading(text, level=3)

    def p(self, *runs, space_after=None):
        """p("plain ", ("bold", "b"), ("code", "c")) -- b bold, i italic, c code."""
        para = self.d.add_paragraph()
        if space_after is not None:
            para.paragraph_format.space_after = Pt(space_after)
        for item in runs:
            text, kind = item if isinstance(item, tuple) else (item, "")
            run = para.add_run(text)
            run.bold = "b" in kind
            run.italic = "i" in kind
            if "b" in kind:
                run.font.color.rgb = NAVY
            if "c" in kind:
                run.font.name = "Consolas"
                run.font.size = Pt(9.5)
                run.font.color.rgb = AQUA_DARK
        return para

    def bullet(self, *runs, level=0):
        para = self.d.add_paragraph(style="List Bullet")
        para.paragraph_format.left_indent = Inches(0.3 + 0.25 * level)
        para.paragraph_format.space_after = Pt(4)
        for item in runs:
            text, kind = item if isinstance(item, tuple) else (item, "")
            run = para.add_run(text)
            run.bold = "b" in kind
            run.italic = "i" in kind
            if "b" in kind:
                run.font.color.rgb = NAVY
            if "c" in kind:
                run.font.name = "Consolas"
                run.font.size = Pt(9.5)
        return para

    def _box(self, fill, left_bar=None, pad=(110, 180)):
        """One-cell table used as a panel. Returns the cell."""
        table = self.d.add_table(rows=1, cols=1)
        table.alignment = WD_TABLE_ALIGNMENT.LEFT
        _table_borders(table, {e: (0, "") for e in ("top", "left", "bottom", "right", "insideH", "insideV")})
        _cell_margins(table, top=pad[0], bottom=pad[0], left=pad[1], right=pad[1])
        table.autofit = False
        cell = table.cell(0, 0)
        cell.width = Inches(TEXT_WIDTH_IN)
        _shade(cell._tc.get_or_add_tcPr(), fill)
        if left_bar:
            _cell_borders(cell, {"left": (36, left_bar)})
        return cell

    def code(self, text, caption=None):
        """Dark panel, light monospace -- reads as code at a glance."""
        cell = self._box(CODE_BG)
        cell.paragraphs[0]._p.getparent().remove(cell.paragraphs[0]._p)
        for line in text.strip("\n").split("\n"):
            para = cell.add_paragraph()
            para.paragraph_format.space_after = Pt(0)
            para.paragraph_format.line_spacing = 1.0
            run = para.add_run(line or " ")
            run.font.name = "Consolas"
            run.font.size = Pt(8.5)
            run.font.color.rgb = CODE_FG
        if caption:
            self.caption(caption)
        self._gap()

    def callout(self, title, body):
        """Aqua bar, pale aqua fill, letter-spaced caps title."""
        cell = self._box(CALLOUT_BG, left_bar=AQUA_HEX)
        para = cell.paragraphs[0]
        para.paragraph_format.space_after = Pt(3)
        run = para.add_run(title.upper())
        run.bold = True
        run.font.size = Pt(8.5)
        run.font.color.rgb = AQUA_DARK
        _letter_spacing(run, 20)
        body_p = cell.add_paragraph()
        body_p.paragraph_format.space_after = Pt(0)
        r = body_p.add_run(body)
        r.font.size = Pt(10)
        self._gap()

    def table(self, headers, rows, widths=None, caption=None, mono_cols=(), gap=True):
        t = self.d.add_table(rows=1, cols=len(headers))
        t.alignment = WD_TABLE_ALIGNMENT.LEFT
        # Hairlines between rows only: a full grid is what makes Word tables
        # look like spreadsheets.
        _table_borders(t, {"top": (0, ""), "left": (0, ""), "right": (0, ""), "insideV": (0, ""),
                           "bottom": (8, NAVY_HEX), "insideH": (4, RULE)})
        _cell_margins(t)
        hdr = t.rows[0]
        # Repeat the header row when a table runs onto the next page.
        hdr._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        for i, text in enumerate(headers):
            cell = hdr.cells[i]
            _shade(cell._tc.get_or_add_tcPr(), NAVY_HEX)
            para = cell.paragraphs[0]
            para.paragraph_format.space_after = Pt(0)
            run = para.add_run(text.upper())
            run.bold = True
            run.font.size = Pt(8)
            run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            _letter_spacing(run, 12)
        for r, row in enumerate(rows):
            cells = t.add_row().cells
            for i, text in enumerate(row):
                cell = cells[i]
                if r % 2 == 1:
                    _shade(cell._tc.get_or_add_tcPr(), ZEBRA)
                para = cell.paragraphs[0]
                para.paragraph_format.space_after = Pt(0)
                para.paragraph_format.line_spacing = 1.08
                # **bold** anywhere in the cell, not only wrapping the whole of
                # it: splitting on the marker makes every odd segment a bold run,
                # so "scores 0.73 -- **worse** than noise" works as written.
                for seg, is_bold in ((s, n % 2 == 1) for n, s in enumerate(text.split("**"))):
                    if not seg:
                        continue
                    run = para.add_run(seg)
                    run.bold = is_bold
                    run.font.size = Pt(9.5)
                    if is_bold:
                        run.font.color.rgb = NAVY
                    if i == 0 and not mono_cols:
                        run.font.color.rgb = NAVY
                    if i in mono_cols:
                        run.font.name = "Consolas"
                        run.font.size = Pt(8.5)
                        run.font.color.rgb = AQUA_DARK
        if widths:
            for row in t.rows:
                for i, w in enumerate(widths):
                    row.cells[i].width = Inches(w)
        if caption:
            self.caption(caption)
        if gap:
            self._gap()
        return t

    def image(self, png_bytes, width_in=6.5, caption=None):
        self.d.add_picture(io.BytesIO(png_bytes), width=Inches(width_in))
        self.d.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        self.d.paragraphs[-1].paragraph_format.space_after = Pt(3)
        self.figures += 1
        self.caption(f"Fig. {self.figures}" + (f" — {caption}" if caption else ""))

    def caption(self, text):
        para = self.d.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        para.paragraph_format.space_before = Pt(3)
        para.paragraph_format.space_after = Pt(10)
        run = para.add_run(text)
        run.italic = True
        run.font.size = Pt(8.5)
        run.font.color.rgb = MUTED

    def _gap(self):
        self.d.add_paragraph().paragraph_format.space_after = Pt(2)

    def page_break(self):
        # Chapters (Heading 1) start on a new page by style now; an explicit
        # break before one would leave a blank page.
        pass

    # -- front matter --
    def cover(self, eyebrow, title, subtitle, meta):
        cell = self._box(NAVY_HEX, pad=(500, 240))
        first = cell.paragraphs[0]
        run = first.add_run(eyebrow)
        run.bold = True
        run.font.size = Pt(9)
        run.font.color.rgb = AQUA_LIGHT
        _letter_spacing(run, 60)
        first.paragraph_format.space_after = Pt(10)
        t = cell.add_paragraph()
        t.paragraph_format.space_after = Pt(6)
        run = t.add_run(title)
        run.font.name = HEAD_FONT
        run.font.size = Pt(36)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        s = cell.add_paragraph()
        s.paragraph_format.space_after = Pt(18)
        run = s.add_run(subtitle)
        run.font.size = Pt(12.5)
        run.font.color.rgb = AQUA_LIGHT
        m = cell.add_paragraph()
        m.paragraph_format.space_after = Pt(0)
        run = m.add_run(meta)
        run.font.size = Pt(8.5)
        run.font.color.rgb = RGBColor(0xB8, 0xC9, 0xD9)
        self._gap()

    def stage_strip(self, stages):
        """The four representations as a row of chips joined by arrows."""
        cols = len(stages) * 2 - 1
        t = self.d.add_table(rows=1, cols=cols)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        _table_borders(t, {e: (0, "") for e in ("top", "left", "bottom", "right", "insideH", "insideV")})
        _cell_margins(t, top=90, bottom=90, left=60, right=60)
        for i, cell in enumerate(t.rows[0].cells):
            para = cell.paragraphs[0]
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            para.paragraph_format.space_after = Pt(0)
            if i % 2:
                cell.width = Inches(0.3)
                run = para.add_run("→")
                run.font.size = Pt(14)
                run.font.color.rgb = AQUA
                continue
            label, sub = stages[i // 2]
            cell.width = Inches(1.35)
            _shade(cell._tc.get_or_add_tcPr(), CALLOUT_BG)
            _cell_borders(cell, {"top": (18, AQUA_HEX)})
            run = para.add_run(label)
            run.bold = True
            run.font.size = Pt(10)
            run.font.color.rgb = NAVY
            sp = cell.add_paragraph()
            sp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            sp.paragraph_format.space_after = Pt(0)
            r = sp.add_run(sub)
            r.font.size = Pt(8)
            r.font.color.rgb = MUTED
        self._gap()

    def label(self, text):
        """A small letter-spaced caps label above a block ("AT A GLANCE")."""
        para = self.d.add_paragraph()
        para.paragraph_format.space_before = Pt(10)
        para.paragraph_format.space_after = Pt(4)
        run = para.add_run(text.upper())
        run.bold = True
        run.font.size = Pt(8.5)
        run.font.color.rgb = AQUA_DARK
        _letter_spacing(run, 40)

    def contents(self):
        """A real Word table of contents on its own page. Word computes the
        page numbers; build() asks Word to do that before saving if it can."""
        para = self.d.add_paragraph()
        para.paragraph_format.page_break_before = True
        run = para.add_run("Contents")
        run.font.name = HEAD_FONT
        run.font.size = Pt(24)
        run.font.color.rgb = NAVY
        para.paragraph_format.space_after = Pt(14)
        _para_border(para._p, {"bottom": (8, AQUA_HEX)}, space=8)
        toc = self.d.add_paragraph()
        shown = _field(toc, 'TOC \\o "1-2" \\h \\z \\u',
                       "Open in Word and press F9 to build the table of contents.")
        shown.italic = True
        shown.font.color.rgb = MUTED

    def save(self, path):
        self.d.save(str(path))


def _refresh_toc(path: Path) -> bool:
    """Have Word compute the table of contents (page numbers need a layout
    engine; python-docx has none). Uses Word through COM when it is installed.
    Returns False when it could not, and the document then asks Word to update
    fields on open instead."""
    import subprocess
    script = (
        "$ErrorActionPreference='Stop';"
        "$w=New-Object -ComObject Word.Application;$w.Visible=$false;"
        f"$d=$w.Documents.Open('{path}');"
        "foreach($t in $d.TablesOfContents){$t.Update()};"
        "$d.Save();$d.Close();$w.Quit()"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       check=True, capture_output=True, timeout=180)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _update_fields_on_open(path: Path) -> None:
    doc = Document(str(path))
    settings = doc.settings.element
    el = OxmlElement("w:updateFields")
    el.set(qn("w:val"), "true")
    settings.append(el)
    doc.save(str(path))


# ── the document ──────────────────────────────────────────────────────────────

def build():
    doc = Doc()
    d = doc.d

    # ---- cover ----
    doc.cover(
        "HOW IT WORKS",
        "How ImageSound Works",
        "Image → prompt → audio → MIDI → Strudel, and the mathematics of each stage",
        f"Updated {UPDATED} · generated from scripts/build_how_it_works_docx.py",
    )
    doc.p("An end-to-end walkthrough of what happens between dropping an image on the home "
          "page and reading back a Strudel pattern.")
    doc.callout(
        "Before you read on",
        "This is the why does it work document. The README is the how do I run it document and "
        "is not repeated here. Where a number below looks oddly specific — 20 dB, 0.32, 0.03 — "
        "it was measured rather than chosen, and the benchmark that produced it is named so you "
        "can re-run it.")
    doc.label("The path at a glance")
    doc.stage_strip([("Prompt", "words"), ("Audio", "samples"), ("MIDI", "notes"),
                     ("Strudel", "code")])
    doc.label("What changed in this update")
    doc.p("Accounts and public access (§2.6–2.7), lyric writing (§3.3), vocals (§4.4), the "
          "remix fix (§4.5) and running on an 8 GB GPU (§4.6).")
    doc.label("Chapters")
    doc.table(
        ["#", "Chapter", "What it covers"],
        [["01", "The whole path", "Four representations, and why each is lossier than the last"],
         ["02", "The web layer", "Processes, the job queue, storage, accounts, public access"],
         ["03", "Image to words", "Vision-model chain, fallbacks, instruction design, lyrics"],
         ["04", "Words to audio", "Flow matching, CFG, samplers, vocals, remixing, memory"],
         ["05", "Audio to MIDI", "Separation, transcription, drum classification, reduction"],
         ["06", "MIDI to Strudel", "Tempo, phase, key, chords, mini-notation compaction"],
         ["07", "Measured vs assumed", "The grid-fit evidence, known limits, benchmarks"],
         ["08", "Appendix", "Endpoints and file map"]],
        widths=[0.5, 1.8, 4.2],
        gap=False,
    )
    doc.contents()

    # ══ 1 ══════════════════════════════════════════════════════════════════════
    doc.h1("1. The whole path in one picture")
    doc.code("""
 IMAGE --+
 TEXT  --+--> music prompt --> audio (WAV) --> MIDI --> lead sheet --> Strudel code
 SONG  --+       (words)        (samples)      (notes)   (4 parts)       (text)
""")
    doc.p("Four representations, each strictly lossier and more symbolic than the last. That "
          "direction is the whole design: every stage answers a smaller question than the one "
          "before it.")
    doc.table(
        ["Stage", "Representation", "Produced by", "The question it answers"],
        [["1", "Music prompt", "Vision LM — Gemini, or local Ollama",
          "What kind of music does this image feel like?"],
         ["2", "Audio (WAV)", "ACE-Step 1.5 — latent flow-matching DiT",
          "What does that music sound like?"],
         ["3", "MIDI", "Demucs + Basic Pitch + onset detection",
          "What notes are in this recording?"],
         ["3b", "Lead sheet", "Skyline + overtone stripping",
          "What is this song?"],
         ["4", "Strudel", "Quantization + mini-notation compaction",
          "How would someone play this on a grid?"]],
        widths=[0.5, 1.1, 2.3, 2.6],
    )
    doc.p("Nothing downstream is required. A song is finished at stage 2 — MIDI and Strudel are "
          "opt-in conversions of a song you already have.")

    # ══ 2 ══════════════════════════════════════════════════════════════════════
    doc.h1("2. The web layer")
    doc.h2("2.1 Processes")
    doc.p("run.py starts two processes. A third is created and destroyed per song.")
    doc.table(
        ["Process", "What it is", "Lifetime"],
        [["Frontend", "React + Vite + TypeScript (src/) — Home, Player, Library, Login",
          "Long-running"],
         ["Backend", "FastAPI + SQLite (backend/) — auth gate → routers → job queue",
          "Long-running"],
         ["Model worker", "The chosen model in its own virtualenv, with its own torch and its "
          "own VRAM", "**One per generation**; exits when the song is done, freeing all VRAM"]],
        widths=[1.1, 3.3, 2.1],
    )
    doc.code("""
  frontend  <-->  backend  --spawns-->  model worker
   (HTTP)         (queue)                (one song, then exits)
""")
    doc.p("The worker lives in a separate process for three reasons that are hard to get any "
          "other way:")
    doc.bullet(("Dependency isolation. ", "b"),
               "ACE-Step wants torch 2.7, Demucs wants 2.1. They never share a Python.")
    doc.bullet(("Cancellation. ", "b"),
               "Killing a PID works; an in-process model cannot be interrupted mid-tensor.")
    doc.bullet(("VRAM. ", "b"),
               "Memory is returned by the OS rather than by an allocator you have to trust.")
    doc.p("It speaks a JSON-lines protocol on stdout — status, progress, result, error — read "
          "line by line in pipeline/runner.py. A watchdog thread polls once a second for a "
          "cancel flag or a timeout and kills the process tree on either.")

    doc.h2("2.2 The request flow")
    doc.table(
        ["Step", "Call", "What happens"],
        [["1", "POST /upload", "Image stored, row created → file_id"],
         ["2", "POST /describe", "Image → prompt, via the vision-model chain"],
         ["2b", "POST /lyrics", "Vocal songs only: lyrics + a tempo, via the same chain (§3.3)"],
         ["3", "POST /generate", "202 Accepted, job queued"],
         ["4", "GET /status/{id}", "Polled every 2 s: queued → loading_model → processing (+ %) → done"],
         ["4b", "GET /jobs/active", "On page load and on return to the tab: \"is anything of mine still running?\" (§2.9)"],
         ["5", "GET /audio/{id}", "The finished WAV"],
         ["6", "POST /save/{id}", "Keep it; otherwise it expires"],
         ["7", "POST /midi/convert/{id}", "202 Accepted, a second job → a new MIDI id (or the running one, if this song is already converting); polled on /status with a % and a stage name"],
         ["8", "GET /midi/preview/{id}", "Sonified WAV; ?track= solos one part"],
         ["9", "GET /midi/strudel/{id}", "Pattern code; ?mode= and ?view="]],
        widths=[0.5, 1.9, 4.1], mono_cols=(1,),
    )
    doc.p("Generation is asynchronous because it takes tens of seconds to minutes. MIDI "
          "conversion is asynchronous because Demucs separation alone runs 30–120 s on CPU. "
          "Strudel conversion is synchronous — it needs only pretty_midi and finishes in "
          "milliseconds, which is also why it is derived on demand rather than stored, so "
          "entries converted before the feature existed still work.")

    doc.h2("2.3 The queue")
    doc.p("One queue.Queue and one daemon worker thread. One job at a time, deliberately: the "
          "GPU fits one model.")
    doc.callout(
        "A subtlety worth knowing",
        "is_active() is tracked separately from qsize(), because the in-flight job has already "
        "been dequeued — queue depth alone reads 0 while a song is being generated. /generate "
        "rejects a second concurrent request on that flag (409) rather than silently queueing "
        "it, which previously let two requests targeting the same image overwrite each other.")
    doc.p("With a shared public account, \"someone else is generating\" is now literally another "
          "person, so the 409 says so. The admin's 409 also carries the running job's id, and "
          "the UI offers ", ("stop their generation & start mine", "i"), ": it cancels that job, "
          "then retries its own request up to 10 times a second apart while the worker releases "
          "the GPU. Admin priority means being able to take the GPU back, not jumping a queue — "
          "there is no queue to jump.")
    doc.p("The queue lives in memory, so a restart loses it. Rows still marked queued or "
          "processing at startup are therefore marked ", ("failed", "b"), " by init_db — nothing "
          "will ever pick them up, and left alone they would read as \"still running\" forever "
          "and keep the UI's buttons locked (§2.9).")
    doc.p("Cancellation is checked at three points: before dequeue, once a second during "
          "generation via the runner's watchdog, and immediately after the model returns — the "
          "last one covers a cancel landing in the gap between the final poll and the worker "
          "finishing.")

    doc.h2("2.4 Storage and lifetime")
    doc.p("SQLite holds one files row per entry: id, owner, input type, prompt, model, duration, "
          "job status, the storage key, and expires_at. Unsaved entries are swept by a "
          "scheduler; POST /save clears the expiry. MIDI entries carry source_file_id back to "
          "the audio they came from.")
    doc.p("On disk, one MIDI conversion produces up to five files:")
    doc.table(
        ["File", "Contents"],
        [["{id}.mid", "Full transcription, one track per stem"],
         ["{id}_preview.wav", "Sonified, all parts"],
         ["{id}_lead.mid", "The reduction: melody / chords / bass / drums"],
         ["{id}_lead_preview.wav", "Sonified reduction"],
         ["{id}_preview_{track}.wav", "One part alone, rendered on first request, then cached"]],
        widths=[2.3, 4.2], mono_cols=(0,),
    )
    doc.callout(
        "Storage discipline",
        "Weights, Hugging Face and torch caches, stem scratch and worker temp are all pinned "
        "inside the project directory by environment variables set at import time. Nothing is "
        "written to the system drive.")

    doc.h2("2.5 Frontend Design & Layout")
    doc.p("The UI is built with a deep-sea ", ("Frutiger Aero / Aqua", "b"), " aesthetic, relying "
          "heavily on glossy gradients, inner shadows, and glassmorphic panels defined in ",
          ("index.css", "c"), ". This moves away from flat brutalism into a richer, "
          "fluid visual language.")
    doc.p("The animated background waves are built without SVGs to avoid aspect-ratio distortion "
          "and Edge browser battery-saver penalties. Instead, they use a pure CSS technique: "
          "massive 500vw rectangles with a 46% border radius, rotated continuously to create "
          "gentle, infinitely scaling slopes. The CSS also explicitly bypasses ",
          ("prefers-reduced-motion", "c"), " constraints to ensure the core visualizer runs "
          "even under strict browser efficiency modes.")
    doc.p("The studio (", ("Player.tsx", "c"), ") is laid out as ", ("Input | Output", "b"),
          ", read top to bottom in the order you use it:")
    doc.table(
        ["Column", "Contents, in order"],
        [["Input (left, 3/5 wide)", "Source → Description (image description, prompt editor or "
          "remix style) → Settings (length · model · vocals) → Lyrics → Generate"],
         ["Output (right, 2/5 wide)", "Player → Effects (folded until there is a song). Sticky on "
          "laptops, capped to the screen height with its own thin scrollbar"]],
        widths=[1.8, 4.7],
    )
    doc.p("Phones stack the two columns. Two bars at the bottom of the screen keep the main "
          "action reachable without scrolling back:")
    doc.bullet(("Generate bar. ", "b"), "Before there is a song, whenever the real Generate "
               "button is off screen (a long lyrics box pushes it down): pinned full-width on "
               "phones, a floating card on laptops. Shows progress while generating.")
    doc.bullet(("Mini-player. ", "b"), "Phones only, once there is a song and the player has "
               "scrolled away: play/pause, a progress line, the time and a jump back to the player.")
    doc.p("Both are driven by IntersectionObserver, and the playhead is written to the DOM "
          "through refs rather than React state, so playback does not re-render the page. The "
          "old Classic mode (raw sliders) was removed; everything goes through the one layout. "
          "Tap targets on phones are at least 36–40 px tall, and the smallest text is 11 px.")

    doc.h2("2.6 Accounts and roles")
    doc.p("Two roles, stored on the users row and loaded into request.state.user by the auth "
          "gate on every request (backend/app/access.py reads it from there):")
    doc.table(
        ["", "admin", "user (the shared public account)"],
        [["Library", "Every song", "Only songs made on this account"],
         ["Song length", "Up to the model's maximum", "Capped at 60 s (USER_MAX_DURATION_SECONDS)"],
         ["Vocal tone (test knob)", "Yes", "Hidden, and refused (403) if asked for directly"],
         ["Busy GPU", "Can stop the running job and take over", "Told to wait"],
         ["Low-RAM warning", "\"Close some apps\"", "\"Server under heavy load\""]],
        widths=[1.3, 2.3, 2.9],
    )
    doc.p("Ownership is enforced in SQL, not in the UI: every route that touches a song appends "
          "owner_filter()'s clause to its WHERE. A song another account owns answers ",
          ("404, never 403", "b"), " — a 403 would confirm the id exists. Songs from before "
          "accounts existed were assigned to the admin, and the oldest account was promoted to "
          "admin, by a one-time migration in database.py.")
    doc.p("Manifest options can be marked admin_only. GET /models strips them from other "
          "accounts' view and POST /generate refuses a non-default value for them, so hiding "
          "the control is a convenience and the server is the rule.")

    doc.h2("2.7 Public access")
    doc.p("The app runs on a laptop. Making it reachable without paying for hosting or moving "
          "the GPU work anywhere takes three free pieces:")
    doc.code("""
  visitor --> GitHub Pages status page --reads--> Gist status.json
                    |                                   ^
                    | redirect once /api/health answers | written by run.py
                    v                                   |
            https://<random>.trycloudflare.com --> laptop :4000 (Vite) --> :8000 (FastAPI)
""")
    doc.table(
        ["Piece", "What it does"],
        [["Cloudflare quick tunnel",
          "cloudflared (tools/cloudflared.exe) exposes the Vite server on a random "
          "trycloudflare.com URL. Free and account-less, but the URL changes every run."],
         ["Gist",
          "run.py PATCHes status.json with {online, url} when the tunnel comes up and "
          "{online: false} on every exit path. The token (GIST_TOKEN, gist scope only) lives "
          "in .env."],
         ["Status page",
          "A separate public repo (ImageSound-Status). Reads the gist, accepts only a "
          "*.trycloudflare.com URL, probes its /api/health, and redirects to /login only once "
          "the server actually answers."]],
        widths=[1.6, 4.9],
    )
    doc.p("python run.py opens the tunnel by default (it was too easy to forget a flag); "
          "--local keeps everything on this machine. The launcher refuses to go public, and "
          "falls back to local-only, when either check fails:")
    doc.bullet(("The session secret ", "b"), "is unset, the hardcoded default, or shorter than "
               "32 characters — anyone knowing it could forge a login.")
    doc.bullet(("The seeded admin password ", "b"), "still works.")
    doc.p("It also refuses to start at all if ports 8000 or 4000 are taken, printing the "
          "process holding them. An orphaned backend from an earlier run once kept 8000 on "
          "0.0.0.0 while the new one also started: requests split between two servers, and two "
          "models could load at once — one of the causes of the laptop freezing (§4.6).")
    doc.p("Behind the tunnel every request arrives from 127.0.0.1, so rate limiting keys on "
          "Cloudflare's CF-Connecting-IP header — but only when the peer really is loopback, so "
          "a direct caller cannot pick its own key. Session cookies are HTTPS-only in tunnel "
          "mode. /health is the one endpoint the status page may call cross-origin.")


    doc.h2("2.8 From the laptop to a phone: one request, end to end")
    doc.p("Everything runs on the one laptop — there is no cloud server doing the work. The "
          "backend is ", ("FastAPI", "b"), " (Python, served by uvicorn on :8000) with ",
          ("SQLite", "b"), " for the song records and plain folders for the files; the frontend is ",
          ("React + Vite", "b"), " on :4000. A phone or another computer reaches it like this:")
    doc.code("""
  phone browser
      |  https://<random>.trycloudflare.com/api/generate
      v
  Cloudflare edge  (HTTPS ends here; public internet)
      |  outbound tunnel the laptop opened -- no port forwarding, no open router ports
      v
  cloudflared on the laptop  --> 127.0.0.1:4000  Vite
                                      |  /api/* forwarded, /api prefix stripped
                                      v
                                 127.0.0.1:8000  FastAPI
                                      |  auth gate -> router -> SQLite row -> queue
                                      v
                                 worker thread --> model process on the GPU
""")
    doc.table(
        ["Step", "Where", "What happens"],
        [["1", "Phone", "The page (HTML/JS) comes from Vite through the tunnel. In tunnel mode "
          "run.py sets VITE_API_BASE=/api, so every API call goes to the same https origin — "
          "one URL, one certificate, cookies just work."],
         ["2", "Cloudflare", "Terminates HTTPS and passes the request down the tunnel that "
          "cloudflared opened outwards from the laptop. The laptop never accepts an inbound "
          "connection from the internet."],
         ["3", "Vite :4000", "Serves the frontend; forwards /api/* to the backend on localhost."],
         ["4", "FastAPI :8000", "The auth gate loads the session's account; the router checks "
          "ownership and limits, writes a files row (status queued), puts a Job on the queue "
          "and answers 202 with the job id — in milliseconds."],
         ["5", "Worker", "The single worker thread takes the job, starts the model's own "
          "process on the GPU, and writes status and progress as it goes."],
         ["6", "Phone", "Polls GET /status/{id} every 2 s through the same path, and fetches "
          "/audio/{id} when done."]],
        widths=[0.5, 1.3, 4.7],
    )
    doc.p("The consequences are worth stating plainly:")
    doc.bullet(("The laptop is the server. ", "b"), "Closed lid, sleep, or a stopped run.py "
               "means the site is down; the status page then says offline rather than "
               "sending people to a dead link.")
    doc.bullet(("Every client shares one GPU. ", "b"), "Hence one generation at a time and "
               "the 409 for a second one (§2.3).")
    doc.bullet(("The phone does no work. ", "b"), "It sends a request and polls. Closing the "
               "browser, locking the phone or losing signal does not stop a job — which is "
               "exactly why the page has to find its way back to it (§2.9).")
    doc.bullet(("Local use skips the tunnel. ", "b"), "python run.py --local: the browser talks "
               "to localhost:8000 directly.")

    doc.h2("2.9 Leaving and coming back")
    doc.p("A job outlives the page that started it. Phones make that the normal case: a "
          "backgrounded tab has its network suspended, and after a minute or so it is often "
          "discarded and reloaded from scratch. Two things used to go wrong:")
    doc.bullet(("\"Not generating\" while it was. ", "b"), "Polls fail while the phone suspends "
               "the network; five in a row read as \"server stopped responding\" and the page "
               "gave up. Failed polls while the page is hidden no longer count.")
    doc.bullet(("Buttons live again mid-job. ", "b"), "The job id was kept in sessionStorage, "
               "which a discarded tab can lose, and MIDI conversions were only held in page "
               "memory — so after a reload Generate and Convert were offered again, inviting a "
               "double request.")
    doc.p("The fix makes the server the source of truth. GET /jobs/active returns the caller's "
          "own jobs still queued or running (always their own, even for the admin). The Studio "
          "and Library pages ask on load, on returning to the tab (visibilitychange) and on a "
          "back/forward-cache restore (pageshow), then:")
    doc.table(
        ["Page", "What it does with the answer"],
        [["Studio", "Checks the job it remembers first (localStorage now) — if that finished "
          "meanwhile, the song shows. Otherwise adopts any running generation: progress bar "
          "back, Generate locked. A Generate pressed while this check is in flight wins."],
         ["Library", "Adopts every running MIDI conversion onto its song's card with a live "
          "progress bar, whatever the format picker says."]],
        widths=[1.2, 5.3],
    )
    doc.p("The server also refuses the duplicates on its own: /generate already answered 409 "
          "while anything runs, and /midi/convert on a song that is already converting now "
          "returns the running job instead of queueing a second. Jobs orphaned by a server "
          "restart are failed at startup (§2.3) so they can never lock a button forever. This "
          "works across devices too — start on the laptop, open the phone, and it shows the "
          "job running.")


    # ══ 3 ══════════════════════════════════════════════════════════════════════
    doc.h1("3. Stage 1 — image to words")
    doc.p("A music model takes text. The image has to become a caption first, and that caption "
          "is the single highest-leverage artifact in the whole app: everything downstream is "
          "conditioned on it.")

    doc.h2("3.1 The backend chain")
    doc.p("Two backends, tried in the order DESCRIBER_ORDER names (default gemini,ollama):")
    doc.table(
        ["", "Gemini (gemini-3.1-flash-lite)", "Ollama (gemma3:4b, local)"],
        [["Quality", "Higher", "Lower"],
         ["Cost", "Free-tier quota; 503s when busy", "None"],
         ["Network", "Required", "None"],
         ["Role", "The normal path", "Used only when Gemini actually fails"]],
        widths=[0.9, 2.8, 2.8],
    )
    doc.p("Only ", ("transient", "i"), " failures fall through to the next backend. A malformed "
          "instruction or a corrupt image would fail identically everywhere, so those raise "
          "immediately rather than burning the fallback on a retry that cannot help.")
    doc.p("Gemini retries on 503 and 429 with a doubling backoff, honouring the server's own "
          "retryDelay when it sends one, under a hard 40 s budget for the whole call.")
    doc.callout(
        "Why the budget is a contract, not a guess",
        "The HTTP route waits on this call synchronously, so the two timeouts have to agree. An "
        "earlier version allowed a 65 s sleep behind a 45 s route timeout, which meant the retry "
        "for a per-minute quota could never finish — the user was told \"timeout\" instead of "
        "\"quota\".")
    doc.p("The local model is deliberately ", ("not", "b"), " a reasoning model. qwen3-vl:4b "
          "scores better on paper but reasons before answering and does not reliably stop: with "
          "the full instruction it spent its entire token budget deliberating in 6 of 6 attempts "
          "and produced nothing.")

    doc.h2("3.2 The instruction")
    doc.p("Model-independent framing plus a per-model rules block, selected by the manifest's "
          "prompt_style. ACE-Step wants a comma-separated caption of 8–16 descriptors spanning "
          "genre, mood, 2–4 named instruments, timbre, production style and a tempo feel, with "
          "one clear lead instrument and no contradictions.")
    doc.p("Two rules exist purely to protect later stages:")
    doc.table(
        ["Rule", "Why"],
        [["No numeric BPM, no key names",
          "The model is a poor judge of both, and a stated tempo it then ignores is worse than none"],
         ["No \"a photo of…\"",
          "Describing the image rather than the music is the failure mode that produces unusable captions"]],
        widths=[2.0, 4.5],
    )
    doc.p("Small local models get a ", ("compact", "i"), " variant of every instruction — same "
          "requirements, a fifth of the words, one example instead of three. A 4B model handles "
          "the hosted-model instruction much worse: 1 usable caption in 4 attempts with the long "
          "version, versus a ~4 s answer with the short one.")
    doc.p("A GIF is sampled to three frames (first, middle, last) sent in ", ("one", "b"),
          " call, and the instruction asks the model to read change across frames as energy: "
          "rapid change means fast music, near-identical frames mean treat it as a still.")

    doc.h2("3.3 Lyrics (vocal songs only)")
    doc.p("A vocal song needs a second piece of text: the lyrics. They are written by the ",
          ("same backend chain", "b"), " as the caption — Gemini, with the local model behind "
          "it — from the image (when there is one) and the caption, in pipeline/lyrics.py. It "
          "costs one extra request per vocal song and nothing when vocals are off. It cannot "
          "ride on the caption's request: the image is described the moment it is uploaded, "
          "before anyone has chosen vocals.")
    doc.p("Every rule in the instruction came out of a listening test, not a guess:")
    doc.table(
        ["Rule", "What went wrong without it"],
        [["One language; Japanese written in romaji",
          "\"Rocket jump, let's go now\" inside Japanese lyrics was sung with no timing"],
         ["The hook at most twice per chorus, never on two lines in a row",
          "The same line three times running made the singer rush and drag — it lost its place"],
         ["Lines of 5–8 syllables, each ending in a comma or period",
          "Long unpunctuated lines were cut off; punctuation gives the singer a breath"],
         ["A vocalise (\"aa, aa,\") intro and outro — for J-pop-like genres only",
          "Best-liked part of the J-pop take, but judged generic once every song had it"],
         ["The chorus tagged [Chorus - lead vocal, high belt]",
          "Without it the backing voice, not the lead, took the high note at the climax"],
         ["First line: BPM: <n>, 70–160", "See §4.4 — the tempo must be locked"]],
        widths=[2.6, 3.9],
    )
    doc.h3("Languages")
    doc.p(("Japanese and English are unlocked; Korean and Mandarin are listed but locked ", "b"),
          "(manifest locked_choices and TESTED_LANGUAGES in lyrics.py, both enforced by the "
          "server). A language changes phrasing and how the melody fits the words, not only the "
          "words, so each gets its own line rule and its own listening tests before it is offered.")
    doc.table(
        ["Language", "Line rule", "Why"],
        [["Japanese", "5–8 syllables, ending in a comma or period", "The first tests (§4.4)"],
         ["English", "6–10 syllables of conversational English, rhyming in pairs (AABB/ABAB)",
          "Tested against the Japanese rule on the same image and seeds, in rock and in EDM: the "
          "short lines sounded choppy and \"old school\"; the longer rhyming lines were the clear "
          "pick"]],
        widths=[1.0, 2.6, 2.9],
    )
    doc.h3("Song plans: the shape depends on the genre")
    doc.p("The first vocal plan was verse–chorus for everything, and every song came out sounding "
          "like verse–chorus rock. plan_for() now picks a family from the caption's genre words, "
          "once per song — the same plan writes the lyrics and then cleans them:")
    doc.table(
        ["Family", "Picked when", "Shape"],
        [["EDM", "edm, house, techno, trance, dubstep, future bass, festival, club…",
          "Instrumental intro → verse → 2-line build (the hook) → **instrumental drop** → "
          "(soft breakdown, over 130 s) → build again → drop → instrumental outro"],
         ["Pop", "Everything else", "Verse → 2-line **pre-chorus** → chorus, scaled by length "
          "(from 60 s; under 40 s it is the chorus alone). Over 130 s: a bridge, then an "
          "instrumental break before the last chorus"]],
        widths=[0.8, 2.2, 3.5],
    )
    doc.p("The pre-chorus (tagged \"building tension, quieter\") is what gives the chorus "
          "something to land on. Verse straight into chorus was judged \"not punchy, no delay to "
          "build up\". A fixed-seed A/B on the same Japanese lyrics, with the pre-chorus the only "
          "difference, showed the band pulling back before the chorus and then hitting: about "
          "13 dB (seed 42) and 6 dB (seed 7), where the version without it stayed flat. By ear, "
          "both seeds got to the point faster, with a longer drop and a better build-up.")
    doc.p("Why the EDM plan: in a 90 s progressive-house test the verse–chorus plan sang straight "
          "through the song, while the EDM plan left drops the vocal separator measured as silent "
          "(−45 to −69 dB). But with too many lines for the length the singer spills back into "
          "the drops, so the plan also budgets words: about 10 sung lines in 150 s, which gave a "
          "clean 20 s drop and the best-rated build-up. Drop tags are followed often, not always.")
    doc.p("Intros and outros vary so songs do not all open and close alike. J-pop-like genres "
          "keep the vocalise, but at one end only: \"aa, aa\" opening and closing every song "
          "was noticed as a pattern. Otherwise the intro is instrumental about 70% of the time (a soft "
          "vocalise otherwise) and the outro is an instrumental fade, a hook echo, or vocalise "
          "plus hook, a third each. In about a third of EDM songs the build may end on a short "
          "repeated fragment of the hook — the pre-drop vocal chop of a song like Clarity — "
          "offered, never required, because the same device every time gets boring.")
    doc.h3("Cleaning, because the fallback ignores structure")
    doc.p("Compared on the same images, Gemini followed the structure every time. gemma3:4b "
          "wrote 12–21 lines where 8 were asked for, added English translations in brackets "
          "under each line, and stage directions like \"(Synth arpeggio, building intensity)\". "
          "Whatever is in the lyrics field gets ", ("sung", "b"), ", so every draft — from either "
          "backend — is rebuilt by clean_lyrics():")
    doc.bullet("Sections are matched to the plan in order by name; extra sections and extra lines "
               "are dropped, and the tags are rewritten to the planned ones, so the chorus always "
               "carries the tag the drop depends on.")
    doc.bullet("Bracketed and *starred* text is removed; characters outside the language's script "
               "are removed.")
    doc.bullet("For Japanese, any word that cannot be romaji is dropped, and a line losing most of "
               "its words goes entirely. The test is structural — a word must be a sequence of "
               "morae (optional consonant, optional y, vowel), a syllabic n, or a doubled "
               "consonant — so \"fly\", \"high\" and \"yeah\" fail while kitto and chotto matte "
               "pass. It exists because Gemini itself slipped \"fly high\" into a Japanese chorus "
               "despite the rule against it.")
    doc.p("In the UI, setting Vocals to Lyrics fills the Lyrics box with the basic parts and no "
          "words — Intro (///), Verse, Pre-Chorus, Chorus, Outro (///) — and nothing is drafted "
          "unasked, so a user can write their own. Buttons above the box add a section (Intro, "
          "Verse, Pre-Chorus, Chorus, Build, Drop, Bridge, Outro) or a /// at the cursor. "
          "SURPRISE ME drafts words (REWRITE once there are some). If the sections were edited, "
          "it writes into them: plan_from_structure() turns the box's tags into the plan (verse "
          "and chorus 4 lines, other sections 2, an unmarked intro or outro a vocalise). The "
          "untouched template leaves it to the genre's tested plans. Pressing Generate with "
          "nothing to sing asks first — Auto-generate, or Write my own — and both lead to the "
          "box; the first also presses SURPRISE ME. (The API still drafts server-side if it is "
          "sent a vocal request with no lyrics.)")
    doc.p("Plain tags typed by hand get the tested wording at generation (normalize_tags): "
          "[Chorus] becomes the high-belt chorus tag the drop depends on, [Pre-Chorus] the "
          "building one, [Build] \"Build - rising\". A tag that already says something after "
          "\" - \" is the user's and is left alone.")
    doc.p("A line holding only /// marks the section above it as having no singing: "
          "expand_marks() turns \"[Intro]\\n///\" into \"[Intro - instrumental]\" with no lines "
          "under it, which is how the plans' own instrumental sections are written. A /// "
          "before any tag becomes an instrumental intro. Drafts show /// on their instrumental "
          "sections, so the symbol is visible before anyone has to learn it.")


    # ══ 4 ══════════════════════════════════════════════════════════════════════
    doc.h1("4. Stage 2 — words to audio")
    doc.p("ACE-Step 1.5, run in its own venv by pipeline/workers/ace_step_worker.py. Two variants "
          "ship from one manifest:")
    doc.table(
        ["Variant", "Steps", "Shift", "Guidance", "Character"],
        [["Turbo", "8", "3.0", "off (distilled)", "A full song in tens of seconds"],
         ["SFT", "50", "1.0", "5.5", "Richer detail, slower"]],
        widths=[1.0, 0.7, 0.7, 1.4, 2.7],
    )

    doc.h2("4.1 The model in three parts")
    doc.code("""
  caption --> text encoder (Qwen3-Embedding-0.6B) --> conditioning
                                                          |
  noise ----> DiT (2048-dim transformer) <----------------+
                 iterated N times over t: 1 -> 0
                              |
                              v
               latent --> audio VAE decoder --> 48 kHz stereo audio
""")
    doc.p("The autoencoder is an AutoencoderOobleck with downsampling ratios 2·4·4·6·10 = ",
          ("1920", "b"), ", so at 48 kHz one latent frame covers 40 ms — 25 frames per second of "
          "music, 64 channels wide.")
    doc.callout(
        "Why this is the enabling number",
        "Generating three minutes of audio is a ~4,500-frame sequence problem, not an "
        "~8,600,000-sample one. That compression is what makes long-form generation tractable "
        "at all.")

    doc.h2("4.2 The sampler: flow matching")
    doc.p("The model does not predict noise. It predicts a ", ("velocity", "b"), " — the "
          "direction that moves a point along a path from noise (t = 1) to data (t = 0). "
          "Sampling integrates that field:")
    doc.code("""
dx/dt = −v_θ(x, t, c)

Euler   x ← x − v·Δt

Heun    x̂ ← x − v·Δt                  (predictor)
        v̄ ← ½(v + v_θ(x̂, t−Δt, c))    (corrector, trapezoidal rule)
        x ← x − v̄·Δt
""")
    doc.p("Heun costs a second model evaluation per step but is second-order accurate. Measured "
          "on this app's material the difference is small and shows most on dense electronic "
          "tracks, where Euler is likelier to crackle and Heun likelier to drop level abruptly — "
          "so it is exposed as a user choice rather than decided here.")

    doc.h3("Classifier-free guidance")
    doc.p("Runs the model twice, conditioned and unconditioned, then extrapolates away from the "
          "unconditioned answer:")
    doc.code("v = v_uncond + w·(v_cond − v_uncond)")
    doc.p("with w = guidance_scale (5.5 on SFT). Turbo is distilled to need no guidance, so it "
          "is left off — w ≤ 1 disables the second pass entirely, which is also half the "
          "compute.")

    doc.h3("Timestep shift")
    doc.p("Reshapes where the N steps land on [0, 1]:")
    doc.code("t' = s·t / (1 + (s−1)·t)")
    doc.p("s = 1 leaves the schedule uniform. s > 1 pushes steps toward the noisy end, where the "
          "trajectory bends most. Turbo has only 8 steps to spend, so it uses s = 3.0; SFT has "
          "50 and does not need to ration them.")

    doc.h3("Velocity norm clamping")
    doc.p("velocity_norm_threshold (θ = 2.0) limits how far one step can move relative to where "
          "it already is:")
    doc.code("v ← v · min(1, θ·‖x‖ / ‖v‖)")
    doc.p("A stability guard. A single outsized velocity prediction early in the trajectory is "
          "audible later as a burst of noise, and clamping the ", ("ratio", "i"), " rather than "
          "the absolute magnitude keeps it scale-free.")

    doc.h2("4.3 What the app adds around the model")
    doc.table(
        ["Addition", "Why"],
        [["Structure tags",
          "ACE-Step's lyrics field is temporal, not lyrical: for instrumental music it marks how "
          "the piece develops. Sending a bare [Instrumental] gives the model nothing to lay out "
          "in time — measured on four 180 s generations made that way, the music repeated every "
          "6–64 s and the first and last 15 s were indistinguishable from the middle. A plan is "
          "sent instead, thinned to fit the duration at ~18 s per section, always keeping the "
          "intro and outro."],
         ["Tail trim",
          "Every text-to-music generation ends with roughly 3 s of near-silence whatever the "
          "requested length. The worker asks for duration + 3 and cuts back, with a 0.75 s "
          "cosine fade so the cut cannot click."],
         ["BPM lock",
          "With bpm unset the model estimates its own tempo, and that estimate drifts over a "
          "long generation. Passing a value pins the grid. Optional for instrumentals; vocal "
          "songs always get one (§4.4)."],
         ["Reference audio",
          "cover mode (\"remix\") conditions on an existing song's structure with a strength "
          "knob; style mode passes it as a style reference only. See §4.5."]],
        widths=[1.3, 5.2],
    )

    doc.h2("4.4 Vocals")
    doc.p("The Vocals option has three settings. What the worker receives "
          "differs only in the lyrics field and a few caption words:")
    doc.table(
        ["Setting", "Lyrics field", "Added to the caption"],
        [["off", "Structure tags (§4.3)", "Nothing"],
         ["lyrics", "The drafted or edited lyrics (§3.3), plus vocal_language",
          "vocaloid, synthesized female vocals, auto-tuned robotic voice, powerful lead vocal"],
         ["chops", "Sparse wordless vocalise: oh / aa / hmm, scaled to length",
          "soft background vocal chops, airy, breathy, low in the mix"]],
        widths=[0.8, 2.6, 3.1],
    )
    doc.p("The caption words are added after the user's caption, which is stored unchanged; a "
          "stray \"instrumental\" is removed so the two do not argue. The chops wording is "
          "deliberately quiet: the first syllable-only test was judged too loud and too "
          "machine-like.")
    doc.h3("Why energetic words, and why no voice-style switch")
    doc.p("A fixed-seed comparison changed one thing at a time on the same lyrics. Taking out the "
          "energetic caption words, then the high-belt tag, made the voice ", ("more", "i"),
          " synthetic and removed the build-up into the drop; full-precision weights instead of "
          "int8 made no audible difference. The preferred take had \"vocaloid, robotic voice\" "
          "in its caption and still sounded the most natural — its energy did that, not its "
          "words — so a Natural/Vocaloid toggle could not do what its label promised and was "
          "left out.")
    doc.h3("The tempo is always locked")
    doc.p("The biggest single fix. A vocal song left to choose its own tempo picked about 200 BPM "
          "for fast material and then fell into half time partway through — the song \"didn't "
          "know which part was the chorus\". Measured with the Beat This tracker on the same "
          "lyrics:")
    doc.image(_bar_chart(
        "Tempo in each 10 s of a 60 s vocal song (seed 7)",
        ["0–10 s", "10–20 s", "20–30 s", "30–40 s", "40–50 s", "50–60 s"],
        [("Tempo left free", CHART_RED, [200, 200, 200, 200, 100, 100]),
         ("Locked at 140", CHART_BLUE, [143, 143, 143, 143, 143, 143])],
        ymax=220, ylabel="BPM",
        note="The first app render (random seed) did the same: 200 → 97."),
        width_in=6.4,
        caption="Left free, the tempo halves 40 s in; locked, it holds. Measured with Beat This.")
    doc.table(
        ["Tempo", "Runs", "Fell to half time", "Beat jitter", "Bar-length jitter"],
        [["Free", "3", "**2 of 3**", "6–37%", "7–40%"],
         ["Locked at 140", "2", "0", "2–30% *", "**0.7%**"],
         ["Locked at 150", "2", "0", "4–8%", "18–40%"]],
        widths=[1.4, 0.6, 1.4, 1.3, 1.8],
        caption="* The 30% run eased from 143 to 136 BPM partway through — a drift, not a "
                "collapse. Few runs per row: treat these as direction, not precision.",
    )
    doc.p("So a vocal song never runs free. The lyric writer's first line is its BPM (clamped to "
          "70–160 — 150 already made bars uneven), which the UI puts on the Lock BPM control. "
          "Without one, a small table maps the caption's genre and tempo words to a tempo: "
          "ballad or ambient 80, lo-fi or hip hop 90, funk or city pop 112, house 126, and "
          "rock, punk, J-pop, anime or \"energetic\" 140. A value set by hand always wins.")
    doc.h3("The planner, for sung songs only")
    doc.p("ACE-Step ships an optional 0.6B planner LM (acestep-5Hz-lm-0.6B) that sketches the "
          "whole song as coarse audio codes before the DiT renders it. SFT runs it when Vocals "
          "is on Lyrics, and only then. Its rewrites of the caption and the lyrics language stay "
          "off so they cannot undo the app's own inputs; it only fills in missing metadata "
          "such as key, and a locked BPM passes through unchanged.")
    doc.table(
        ["Seed 42, 60 s, SFT", "Planner off", "Planner on"],
        [["Sung song", "Good", "**Better** melody, tone and phrasing"],
         ["Instrumental", "**Better**", "Messy: ten-odd instruments fighting for the top"],
         ["Time", "~38 s", "~77 s (about 2×)"],
         ["Peak VRAM", "4.5 GB", "4.6–4.7 GB (it parks on the CPU after planning)"]],
        widths=[1.6, 1.6, 3.2],
        caption="Judged by ear. The beat tracker could not settle it: the planned vocal take "
                "moved to half time at 30 s, which here was an arrangement choice, not a collapse.")
    doc.p("So the manifest sets use_lm to \"lyrics\", not true. Turbo, chops and instrumentals "
          "skip the planner, and a missing planner checkpoint means the song is made without it "
          "rather than failing.")
    doc.h3("Voice: words, not voice models")
    doc.p("ACE-Step has no singer presets: the voice is whatever the caption describes, varied "
          "by the seed. So Voice (Auto, Female, Male, Duet) picks the words added to the "
          "caption. Auto goes by genre: male for rock, punk, metal, hip hop, country and blues, "
          "female for J-pop-like genres, either otherwise. Admins "
          "also get Vocal tone (bright, breathy, raspy, soft, falsetto), one extra word for "
          "listening tests. Male was found with a fixed-seed A/B on the same J-pop lyrics:")
    doc.table(
        ["Take", "Wording", "Median pitch (seeds 42 / 7)", "By ear"],
        [["F", "vocaloid, synthesized female vocals, auto-tuned robotic voice…", "460 / 372 Hz",
          "Kept as Female"],
         ["M1", "the same, with \"male\" swapped in", "401 / 415 Hz",
          "Still female, more artifacts"],
         ["M2", "male vocal, deep male lead singer, auto-tuned…", "335 / 367 Hz",
          "Male, some vocal artifacts"],
         ["M3", "M2 + \"baritone\", chorus tag without \"high belt\"", "442 / **278 Hz**",
          "**Most natural**: kept as Male"]],
        widths=[0.5, 2.7, 1.6, 1.7],
        caption="\"Vocaloid\" reads as the Miku voice whatever gender word sits next to it, and "
                "\"high belt\" on the chorus pushes any singer up. Pitch alone cannot prove "
                "gender: a tenor belting a J-pop chorus sits above 400 Hz.")
    doc.p("The chorus tag is swapped at generation time, not when drafting, so lyrics written "
          "before the Voice setting changed still get the right one. A male Vocaloid-style voice "
          "(a Kagamine Len sound) is not reachable with words yet; a reference song in Style "
          "only mode is the way to borrow a specific voice's character.")
    doc.p("Duet was tested the same way. The singers are asked for twice: in the caption, and "
          "by tagging each section with who sings it (verse male, pre-chorus and bridge female, "
          "chorus and builds both).")
    doc.table(
        ["Take", "How it was asked", "By ear"],
        [["D1", "Caption only: \"male and female duet, alternating vocals\"",
          "Female only on one seed (a lower, more mature one); male only in the second half on the other"],
         ["D2", "D1 + who-sings section tags", "Male came through and followed the tags, but "
          "the female swamped the chorus"],
         ["D3", "Section tags + the Female and Male wordings named together "
          "(\"vocaloid female vocal and deep baritone male vocal\")",
          "**Both audible in the chorus on both seeds**, with a distinctive voice effect: shipped"]],
        widths=[0.5, 2.9, 3.1])
    doc.callout(
        "Vocals are still a roll",
        "Each run starts from new noise. With good settings a chorus can still come out messy — "
        "the same settings gave an unlistenable chorus on one seed and a clean one on another. "
        "Regenerating is the fix, and the Vocals help text says so.")

    doc.h2("4.5 Remixing")
    doc.p("cover mode takes the source song's structure and restyles it by the caption. For a "
          "long time SFT remixes came out as a structureless smear with a nasal "
          "\"quack\" on every note, whatever the strength, guidance, shift, offload or "
          "quantization — all of which were tried, along with a tempo theory that turned out "
          "wrong.")
    doc.callout(
        "The cause was one missing argument",
        "ACE-Step's GenerationParams defaults its instruction to the text-to-music sentence, and "
        "only swaps in the cover instruction when it auto-detects a cover. An explicit "
        "task_type=\"cover\" keeps whatever instruction it is given. So the DiT received the "
        "source's structure while being told to write a fresh song. The worker now passes "
        "instruction=TASK_INSTRUCTIONS[\"cover\"], and the quack is gone.")
    doc.p("Remix strength is the fraction of the diffusion steps that follow the source. It is "
          "capped to 0.3–0.7 (default 0.5): 0.3–0.5 for a different genre, 0.5–0.7 to restyle. "
          "Above 0.7 the source's structure dominates and a new style comes out choppy. The "
          "automatic tempo lock for remixes was removed — it was a wrong theory for the quack "
          "and did not help.")

    doc.h2("4.6 Fitting on an 8 GB laptop GPU")
    doc.p("Running publicly showed the laptop freezing hard — once to a black screen — during "
          "generation. The chain of causes, in order of discovery:")
    doc.table(
        ["Cause", "Fix"],
        [["An orphaned backend from an earlier run was still serving, so two models could load "
          "at once", "run.py refuses to start while 8000 or 4000 is taken (§2.7)"],
         ["The DiT stayed resident in VRAM alongside the text encoder and VAE; overflowing 8 GB "
          "made the NVIDIA driver spill into system RAM, stalling the whole machine",
          "offload_dit_to_cpu on both models, plus int8 weight-only quantization; NVIDIA's "
          "\"Prefer No Sysmem Fallback\" set in the driver so an overflow fails fast instead"],
         ["With VRAM fixed, system RAM became the bottleneck: loading weights while other apps "
          "held memory paged Windows to disk",
          "A soft warning, not a lock: below 7 GB free (LOW_RAM_WARN_GB) the terminal prints a "
          "warning and the requester is told, in words fitting their role (§2.6)"]],
        widths=[3.4, 3.1],
    )
    doc.p("Free RAM is read with GlobalMemoryStatusEx on Windows (backend/app/sysmem.py), "
          "/proc/meminfo elsewhere. It never refuses a generation: the person at the laptop is "
          "the only one who can free memory, and a hard lock would just turn a slow run into a "
          "failed one.")


    # ══ 5 ══════════════════════════════════════════════════════════════════════
    doc.h1("5. Stage 3 — audio to MIDI")
    doc.code("""
  WAV --> Demucs htdemucs_6s --> 6 stems --> gate --> per-stem transcription
                                                               |
          drums   --> onset detection + flux classify ---------+
          pitched --> Basic Pitch --> polyphony thinning ------+
                                                               |
                                                               v
                                           multitrack .mid + preview WAV
                                                               |
                                                               v
                                           lead sheet .mid + preview WAV
""")
    doc.callout(
        "Separating first is the design decision that matters",
        "Basic Pitch is instrument-agnostic and polyphonic, but on a full mix it has to explain "
        "everything at once. Measured against MT3 — a transformer trained for exactly this job — "
        "the separate-then-transcribe path was competitive on clean audio (F1 0.80 against 0.83) "
        "and clearly better on the dense generated material this app produces. The extra "
        "30–120 s of CPU separation buys accuracy, not just tidier tracks.")

    doc.h2("5.1 Separation and the leakage gate")
    doc.p("Demucs runs ", ("on CPU only", "b"), ", deliberately: separation must never contend "
          "with a generation for VRAM. The model is loaded, used, then del'd and gc.collect()'d "
          "before Basic Pitch is even imported.")
    doc.p("Input is normalised the way Demucs expects — (x − μ)/σ over the mono mean — and "
          "denormalised after. Stems come back at 44.1 kHz and are resampled to 22.05 kHz mono, "
          "which is Basic Pitch's native rate.")
    doc.p("htdemucs_6s always emits all six stems whether or not the song contains the "
          "instrument. A phonk track with no piano still gets a piano stem: bleed from "
          "everything else, which Basic Pitch then confidently transcribes. On one measured "
          "track that was 604 piano and 940 guitar notes out of 3,996, from stems 26 dB and "
          "11 dB below the loudest.")
    doc.p("Two gates, both on RMS level in dB (20·log₁₀(rms)):")
    doc.table(
        ["Gate", "Test", "Meaning"],
        [["Absolute", "db < −50", "Silent — skip"],
         ["Relative", "db < loudest − 20, and not the loudest pitched stem",
          "Separation leakage — skip"]],
        widths=[0.9, 3.0, 2.6], mono_cols=(1,),
    )
    doc.p("The relative gate is the useful one, because −40 dB is a quiet part in a quiet mix "
          "and pure bleed in a loud one; only the ratio distinguishes them. 20 dB rather than "
          "something tighter because the evidence only supports removing the obvious cases — "
          "measured on three tracks, tightening it traded one metric against the other, and on "
          "one track deleted the entire harmony.")
    doc.p("The \"loudest pitched stem is always kept\" clause exists because the loudest stem can "
          "be the drums, and without it a quiet mix would lose every pitched part to the gate "
          "and transcribe to percussion only.")

    doc.h2("5.2 Pitched transcription")
    doc.p("Basic Pitch's ICASSP-2022 model, held as a process-wide singleton — loaded once, "
          "reused across every stem and every conversion.")
    doc.p("It takes a ", ("harmonic-stacked CQT", "b"), " (the constant-Q transform gives a "
          "frequency axis linear in pitch; stacking harmonics aligns each note's partials on "
          "that one axis) and outputs three posteriorgrams at 86.13 frames/s — hop 256 at "
          "22.05 kHz, so ", ("11.6 ms", "b"), " per frame — over 88 semitones from A0 (27.5 Hz), "
          "at 3 bins per semitone for the contour head.")
    doc.table(
        ["Output head", "What it means", "Threshold used"],
        [["Onset", "A note starts here", "0.5"],
         ["Note", "A note is sounding here", "0.3"],
         ["Contour", "Fine pitch, sub-semitone (bends, vibrato)", "—"]],
        widths=[1.2, 3.6, 1.7],
    )
    doc.p("Notes are decoded by thresholding onsets, extending them while the note head stays "
          "above its threshold, and discarding anything shorter than 127.7 ms. Defaults "
          "throughout — a sweep of the frequency bounds was tried and measured ", ("−0.001", "b"),
          " on note support, which is a negative result worth recording rather than a knob worth "
          "shipping.")

    doc.h2("5.3 Drums: onsets, not pitch")
    doc.p("Percussion does not really have pitch, and Basic Pitch run on a drum stem returns a "
          "handful of arbitrary notes carrying no rhythm. The drum stem gets its own detector.")
    doc.p("Onsets come from librosa's onset strength envelope with backtracking (hop 256). Each "
          "onset is then classified by ", ("which frequency band grew", "b"), ", not by which "
          "band is loud:")
    doc.code("""
S    = |STFT(y)|                       n_fft 1024, hop 256
flux = max(0, S[:, t] − S[:, t−1])     positive spectral flux
band_flux[p] = Σ flux over that band's bins

scale[p]  = 99.5th percentile of band_flux[p]    ← that band's own loudest hits
fires(p)  = max(band_flux[p][frame−1 : frame+3]) / scale[p]  ≥  0.32
""")
    doc.table(
        ["Order", "Band", "General MIDI key"],
        [["1st", "< 150 Hz", "36 — kick"],
         ["2nd", "200 – 2,500 Hz", "38 — snare"],
         ["3rd", "≥ 5,000 Hz", "42 — hi-hat"]],
        widths=[0.8, 2.0, 2.0],
        caption="Tried low to high; the first band that fires wins. Gaps between bands are "
                "deliberate, so a hit must sit clearly inside one.",
    )
    doc.p("Two details carry almost all of the accuracy.")

    doc.h3("Flux, not energy")
    doc.p("The earlier version scored each band's energy at the onset against that band's average "
          "across the whole stem. That fails completely on exactly the music this app generates: "
          "a sustained 808 holds the low band above its own average permanently, so a kick attack "
          "can never beat the baseline.")
    doc.table(
        ["Case", "Kicks found, energy baseline", "Ground truth"],
        [["Sustained bass, pattern A", "0", "31"],
         ["Sustained bass, pattern B", "0", "63"],
         ["Sustained bass, pattern C", "0", "47"]],
        widths=[2.2, 2.4, 1.6],
        caption="It lost every kick in all eight sustained-bass cases — and it is why one phonk "
                "track transcribed to 15 kicks against 357 snares.",
    )
    doc.p("A drone contributes energy but almost no flux; it only grows at its own attacks.")
    doc.image(_bar_chart(
        "Drum label accuracy — 16 cases (4 patterns × dry/808 × 2 tempos)",
        ["Mean label accuracy"],
        [("Energy vs. own average", CHART_GREY, [63.5]),
         ("Positive spectral flux", CHART_BLUE, [95.1])],
        ymax=100, ylabel="% correct",
        note="Re-run with scripts/drum_bench.py", width=1000, height=380),
        width_in=6.4,
        caption="Positive spectral flux versus energy-vs-average, 16 measured cases.")

    doc.h3("Low-to-high priority, not argmax")
    doc.p("A snare has real high-frequency content, so on argmax a track whose hats are quiet has "
          "its snares labelled hats: ", ("0 of 32 correct before, 32 of 32 after", "b"), ".")
    doc.p("The 0.32 threshold sits in the middle of a plateau rather than on a value that "
          "happened to score well:")
    doc.table(
        ["Threshold", "0.25", "0.30", "0.32", "0.40"],
        [["Accuracy", "94.3%", "95.0%", "**95.1%**", "94.7%"]],
        widths=[1.1, 1.1, 1.1, 1.1, 1.1],
    )
    doc.p("Velocity is mapped from onset strength: clip(60 + 67·strength, 40, 127).")

    doc.h2("5.4 Polyphony thinning")
    doc.p("Basic Pitch reports a sustained tone's ", ("overtones as separate simultaneous notes",
          "b"), ". On one measured 180 s track the bass stem peaked at 5 voices and used 29 "
          "distinct pitches for what is played as a single line.")
    doc.table(
        ["Stem", "Voice cap", "Note dropped on overflow"],
        [["Bass", "1", "Highest pitch — overtones sit above the fundamental"],
         ["Vocals", "1", "Highest pitch — a sung line is one note at a time"],
         ["Guitar", "3", "Quietest — preserves the voicing a listener would hear"],
         ["Piano", "4", "Quietest"],
         ["Other", "4", "Quietest"]],
        widths=[1.0, 1.0, 4.5],
    )
    doc.p("A single pass over note starts keeps a live set and drops the least-wanted note when "
          "it overflows. The incoming note counts as a candidate, so a low note arriving under a "
          "stack of overtones still wins its place.")
    doc.callout(
        "A negative result worth recording",
        "Note length is not the problem, which matters because it is the obvious thing to reach "
        "for: 0% of pitched notes in that file were under 80 ms, and none overlapped a repeat of "
        "the same pitch. Basic Pitch's own 127.7 ms minimum already handles that.")

    doc.h2("5.5 Tempo and meter")
    doc.p("Analysed on ", ("real audio", "b"), ", from the drum stem where there is one — once "
          "notes are transcribed and quantized, the timing evidence a beat tracker needs is "
          "already degraded.")
    doc.table(
        ["Analyser", "Gives", "Notes"],
        [["Beat This (preferred)", "Tempo + time signature",
          "Tracks downbeats, so it can report a real meter. Runs in its own .venv-beat because it "
          "needs torch ≥ 2.14, which would upgrade torch underneath Demucs."],
         ["librosa (fallback)", "Tempo only", "Meter assumed 4/4."]],
        widths=[1.5, 1.5, 3.5],
    )
    doc.p("When bar lengths disagree — meter agreement below 0.6 — 4/4 is assumed, because a "
          "confidently wrong 5/4 is worse than a safe default. Both results are written into the "
          "MIDI header, so everything downstream gets them without re-analysing audio.")

    doc.h2("5.6 The lead-sheet reduction")
    doc.p("A 180 s track transcribes to ~3,300 notes across five stems at ~20 notes a second. "
          "Nobody reads that. pipeline/lead_sheet.py reduces it to the four parts a human would "
          "write down: ", ("melody, chords, bass, drums", "b"), ".")
    doc.p("Doing it here rather than in the Strudel emitter is strictly better placed — the stems "
          "are still separate, key and tempo are already estimated, and nothing has been pooled "
          "yet.")
    doc.callout(
        "Parts are chosen by register, not by stem name",
        "A separator's labels say where a sound came from, not what job it does in the "
        "arrangement; the vocal stem of an instrumental track is whatever leaked into it. So the "
        "lowest-median-pitch stem is the bass and the highest one with at least 20 notes is the "
        "lead. With only one pitched stem, both lines come from it — its top is the melody, its "
        "bottom the bass.")

    doc.h3("Overtone stripping — the fix that mattered most")
    doc.p("skyline keeps the highest note sounding. Basic Pitch reports partials as real notes. "
          "A partial is by definition ", ("above", "i"), " its fundamental. So skyline preferred "
          "the artifact every time one appeared.")
    doc.p("Harmonics land at 12·log₂(k) semitones above the fundamental:")
    doc.table(
        ["Partial k", "2", "3", "4", "5", "6"],
        [["Semitones above", "12.00", "19.02", "24.00", "27.86", "31.02"],
         ["Rounded, as used", "**12**", "**19**", "**24**", "**28**", "**31**"]],
        widths=[1.5, 1.0, 1.0, 1.0, 1.0, 1.0],
    )
    doc.p("A note is dropped when some other note — starting within 50 ms, still sounding, and ",
          ("at least as loud", "b"), " — sits exactly a partial interval below it.")
    doc.callout(
        "Why the velocity test is what makes it safe",
        "It keeps a real melody note an octave above quiet accompaniment. A partial is always "
        "weaker than its fundamental, so anything louder than the note below it is not that "
        "note's overtone.")
    doc.image(_bar_chart(
        "Melody extraction: does it find the tune, or the overtones above it?",
        ["0% overtones", "30%", "60%", "90%"],
        [("skyline alone", CHART_RED, [1.00, 0.25, 0.05, 0.00]),
         ("+ overtone stripping", CHART_BLUE, [1.00, 1.00, 1.00, 1.00])],
        ymax=1.0, ylabel="note F1",
        note="Synthetic material with known ground truth — scripts/melody_bench.py"),
        width_in=6.4,
        caption="Octave errors fall from 38–41% to 0%; recall stays 1.00. A clean melody with "
                "genuine octave leaps passes through untouched (98 notes in, 98 out).")
    doc.p("The same filter applied before ", ("chord", "i"), " detection moved note overlap from "
          "55% to 59% but cost a root match, so it is used for the melody only.")

    doc.h3("Skyline")
    doc.p("A sweep over every start and end time, taking the highest note active in each segment "
          "and merging neighbouring segments that agree. A max-heap keyed on −pitch gives the top "
          "note; finished notes expire lazily, since anything expired below the top is covered by "
          "a higher note anyway.")
    doc.callout(
        "The obvious implementation is wrong, and it is worth knowing why",
        "Walking notes in start order and truncating against whatever was appended last fails: a "
        "note appended to cover the tail of a long low note begins later than notes still to be "
        "processed, so \"the last thing appended\" stops being \"the thing currently sounding\" "
        "and the output overlaps itself. That version came out monophonic on only 666 of 2,000 "
        "random inputs. The sweep is monophonic by construction — one pitch per segment, segments "
        "do not overlap: 2,000/2,000, and 2,000/2,000 picking the true highest note.")
    doc.p("The bass line reuses it exactly: mirror pitches (127 − p), run skyline, mirror back. "
          "An exact dual beats a second near-identical walk that could drift out of step with it.")

    doc.h3("Chords")
    doc.p("One block chord per bar, from ", ("every pitched note pooled", "b"), " — a song has "
          "one harmony and each stem only sees part of it. Naming is shared with the Strudel "
          "emitter (§6.5). Consecutive identical bars merge into one held chord, and each is "
          "voiced into MIDI 52–76: the middle of a piano, where block chords sit without "
          "colliding with the bass or the melody.")

    doc.h2("5.7 The preview")
    doc.callout(
        "pretty_midi ignores General MIDI programs entirely",
        "Its synthesizer renders whatever waveform it is handed, so the programs written into the "
        "file only ever reach a DAW. To make the preview's parts tellable apart, each track is "
        "synthesized separately with its own waveshape and the results are mixed.")
    doc.table(
        ["Part", "Waveform", "Character"],
        [["Bass", "Fundamental + 0.3 × 2nd", "Stays underneath"],
         ["Guitar", "Odd harmonics (1, 3, 5)", "Hollow — reads as plucked"],
         ["Vocals / Melody", "Saw, 8 partials", "Bright — cuts through"],
         ["Piano / Other", "4 partials at ~1/n", "Reads as struck"],
         ["Drums", "Shaped noise bursts", "Synthesized directly (see below)"]],
        widths=[1.4, 2.2, 2.9],
    )
    doc.p("A pure sine was the original choice and is the wrong one: with no harmonics, octave "
          "errors and doubled overtones — the exact mistakes worth hearing in a transcription — "
          "are almost impossible to pick out.")
    doc.p("Instrument.synthesize also returns ", ("zeros for any drum track", "b"), ", so every "
          "preview was silently missing its drums. Drums are now synthesized directly as noise "
          "bursts, shaped per key: the kick gets a low-passed burst plus a pitched thump sweeping "
          "97 → 42 Hz, the snare a brighter one, the hat no filtering at all.")
    doc.p("Levelling is two steps, and the order matters:")
    doc.code("""
part ← part / max|part|                         unit peak: removes an arbitrary scale
part ← part · clamp(0.12 / rms(part), 0.3, 4)   then match loudness as heard
""")
    doc.table(
        ["Step", "Why it cannot be skipped"],
        [["Normalise to unit peak first",
          "synthesize sums overlapping notes without normalising, so a dense track comes back at "
          "RMS ~30 and a sparse one near 1. Any absolute target applied to that is meaningless — "
          "it pinned two parts at the clamp and left the drums 60× quieter than everything else."],
         ["Then level by RMS",
          "Peak levelling alone is not balance. Drums are almost entirely transient, so matching "
          "their one loud sample to a sustained part leaves them far quieter to the ear."]],
        widths=[2.0, 4.5],
    )


    # ══ 6 ══════════════════════════════════════════════════════════════════════
    doc.h2("5.8 Progress while converting")
    doc.p("A conversion takes about 30 s for a short song and minutes for a long one, so the "
          "Library shows a progress bar with the current step. convert_to_midi takes an "
          "on_progress(fraction, stage) callback; the worker stores both in memory and "
          "GET /status returns them as progress and stage. Separation reports real progress: "
          "Demucs calls back as it finishes each chunk of the song, and chunk offset over song "
          "length is how far along it is. The other steps are fixed marks.")
    doc.table(
        ["Stage", "Bar", "Measured share (31 s song)"],
        [["Loading the separator", "2–25 %", "~9 s — the Demucs model loads fresh each time"],
         ["Separating instruments", "25–60 %", "~10 s, moves smoothly"],
         ["Finding the tempo", "62 %", "~4.5 s"],
         ["Transcribing <stem>", "72–92 %", "~7 s, one step per stem"],
         ["Writing the MIDI · preview · lead sheet", "92–97 %", "under a second"]],
        widths=[2.4, 1.0, 3.1],
    )
    doc.p("The old client gave up after 3 minutes and called the conversion failed, which long "
          "songs on a busy machine legitimately exceed. There is no client time limit now; the "
          "status of the job decides.")


    doc.h1("6. Stage 4 — MIDI to Strudel")
    doc.p("pipeline/strudel_convert.py reads the .mid and emits a self-contained JavaScript "
          "snippet; pipeline/strudel_emit.py owns the notation itself.")
    doc.callout(
        "The core impedance mismatch",
        "Strudel is cycle-based, not timeline-based: a pattern string is divided evenly across "
        "one cycle. MIDI is absolute seconds. So notes are quantized onto a fixed grid — 16 steps "
        "per bar, i.e. sixteenth notes in 4/4 — and each bar becomes one entry in a <> "
        "alternation, so one cycle plays one bar. Quantizing is lossy by design: triplets and "
        "swing are lost, and notes shorter than a step can collide.")

    doc.h2("6.1 Tempo")
    doc.p("The header written by stage 3 is the best available estimate, but beat trackers "
          "famously halve and double — and this one does:")
    doc.table(
        ["Track", "Header written", "Real tempo", "Error"],
        [["Cool jazz A", "187.5", "~94", "Doubled"],
         ["Cool jazz B", "60.0", "~118", "Halved"]],
        widths=[1.6, 1.6, 1.6, 1.6],
    )
    doc.callout(
        "Why a half-tempo header is not a cosmetic error",
        "Every bar then covers two real bars, so the chord detected for it pools two different "
        "chords. That is precisely the \"the MIDI gets confused on jazz\" symptom.")
    doc.p("So the header is trusted for its ", ("value", "i"), " but not for its ",
          ("octave", "i"), ". Onsets — drums when present — are rendered as an impulse train at "
          "100 Hz, mean-removed, and autocorrelated:")
    doc.code("r[τ] = Σ_t  e[t]·e[t+τ]            τ ∈ [60·fs/180, 60·fs/60]")
    doc.p("A pattern repeats at its beat ", ("and at every multiple and division of it", "b"),
          ", so raw autocorrelation is nearly as happy with half or double the true tempo: "
          "measured on synthetic kit patterns, the unweighted peak landed on the wrong metrical "
          "level in 9 of 13 cases (60→120, 174→87, 160→80). The standard remedy — Ellis 2007, "
          "and what librosa's own estimator uses — is a log-normal prior:")
    doc.code("w(bpm) = exp( −½ · ( log₂(bpm / 120) / 0.9 )² )")
    doc.p("It does not forbid extreme tempos; it requires better evidence for them. Each of "
          "{h/2, h, h·2} is scored by r[τ]·w, and the winner takes it. This only ever moves the "
          "answer by a factor of two, so a correct header stays correct.")
    doc.table(
        ["Track", "Before", "After", "Verdict"],
        [["Cool jazz B", "60.0", "**120**", "Fixed"],
         ["Cool jazz A", "187.5", "**94**", "Fixed"],
         ["Pop punk", "136", "136", "Untouched"],
         ["J-pop", "158", "158", "Untouched"]],
        widths=[1.6, 1.3, 1.3, 1.6],
        caption="Verified 6/6 on synthetic halving and doubling cases before being applied to "
                "real tracks.",
    )

    doc.h3("Sub-lag refinement, and where \"139.6 BPM\" came from")
    doc.p("With no usable header, the prior-weighted peak is used directly and refined between "
          "samples. Only whole lags exist on a 100 Hz grid, and near 140 BPM the reachable values "
          "are 139.53 (lag 43) and 142.86 (lag 42) — ", ("140 itself is unreachable", "b"), ".")
    doc.p("That is where readings like setcpm(139.6/4) came from: not a song at 139.6 BPM, but "
          "the nearest lag to one at 140. A parabola through the peak and its neighbours recovers "
          "the fractional lag:")
    doc.code("δ = ½·(y₋₁ − y₊₁) / (y₋₁ − 2y₀ + y₊₁)          clamped to ±0.5")
    doc.p("Finally the result snaps to a whole number when within 0.75 of one, because neither "
          "source resolves fractions of a BPM and printing one implies a precision that does not "
          "exist. A genuine 137.5 survives.")

    doc.h2("6.2 Phase")
    doc.p("Stage 3 writes the time signature at t = 0 and keeps no downbeat, so without "
          "correction the first grid step is assumed to land on the file's first sample. Measured "
          "on generated tracks that assumption is wrong by up to ", ("half a beat", "b"),
          " — and being wrong shifts ", ("every", "i"), " note by the same amount. The pattern is "
          "then internally correct but sits off the beat.")
    doc.p("32 candidate offsets within one step (~4 ms at 150 BPM) are scored by total distance "
          "to the nearest grid line, and the best wins:")
    doc.code("E(φ) = Σ_notes | ((sᵢ − φ)/Δ + 0.5) mod 1 − 0.5 |")
    doc.p("Fitted on drums when present: they come from onset detection, not pitch tracking, and "
          "are by far the most grid-aligned part of the file (see §7).")

    doc.h2("6.3 Key")
    doc.p("A duration-weighted pitch-class histogram correlated against the Krumhansl–Schmuckler "
          "profiles, all 24 rotations:")
    doc.code("score(root, mode) = Σᵢ h[i]·p[(i − root) mod 12] / ‖p‖")
    doc.callout(
        "Confidence is measured against a different scale, not the runner-up",
        "A key and its relative minor contain exactly the same pitch classes, so confusing them is "
        "harmless here — both would correct notes identically. Only genuine disagreement counts: "
        "confidence = (best − best with different pitch classes) / best. Below 0.03 the scale is "
        "not trusted to correct notes against.")
    doc.p("Mode is then settled on the ", ("sounding third", "b"), ", not on the correlation. The "
          "profiles weight every degree, so on a transcription dominated by roots and fifths — "
          "which a bass-heavy one is — the thirds barely influence the result and major/minor "
          "becomes a coin flip.")
    doc.table(
        ["Measured on a pop-punk track", "Value"],
        [["A major won the correlation by", "0.7%"],
         ["Major third, share of total duration", "1%"],
         ["Minor third, share of total duration", "**4%**"],
         ["Leading tone", "Absent entirely"]],
        widths=[3.4, 1.6],
    )
    doc.p("When confidence is below threshold the snippet says (uncertain) in its header and adds "
          "two comment lines telling the reader to check .scale() first, rather than asserting a "
          "key it does not have.")
    doc.p("In-key correction moves an out-of-scale note to the nearest scale tone, trying ±1 then "
          "±2 semitones. Basic Pitch's overtones and octave errors are what make stacked tracks "
          "sound sour; nudging them keeps rhythm and density intact. A genuine chromatic passing "
          "note is flattened along with them — the accepted cost of cleaning up a noisy "
          "transcription. The count is reported in the header comment.")

    doc.h2("6.4 Notes as scale degrees")
    doc.callout(
        "Strudel's .scale() takes scale steps, not semitones",
        "n(\"7\") on a seven-note scale is one octave up, not a fifth. Emitting semitones — which "
        "an earlier version did — transposes everything wildly: −36 became 36 scale steps down "
        "rather than three octaves.")
    doc.code("""
rel           = pitch − (48 + root_pc)      # degree 0 = the root in octave 3
octave, semis = divmod(rel, 12)
idx           = argminᵢ |scale[i] − semis|
degree        = octave·len(scale) + idx
""")
    doc.p("Chromatic notes snap to the nearest degree, which is the same compromise .scale() "
          "itself makes. Degree 0 is the root in octave 3 because that is where Strudel puts "
          "it: .scale(\"A:major\") with no octave roots the scale at A3 (@strudel/tonal, "
          "oct = 3). Counting from octave 4, as the converter did until 27 September, played "
          "every bass and melody line an octave low. Notes mode no longer uses degrees at all: "
          "it writes exact note names with note(), so neither the key guess nor in-key "
          "correction can move a note there.")

    doc.h2("6.5 Chord naming")
    doc.p("Per bar, duration-weighted pitch-class weights are scored against 16 templates — "
          "triads, sus, 6ths, 7ths, 9ths — at all 12 roots:")
    doc.code("""
score = Σ w over pitch classes the template explains
      − Σ w over pitch classes it does not
""")
    doc.callout(
        "The penalty is what stops the biggest template always winning",
        "Without it, a 9th chord explains any triad plus one passing note. Templates are ordered "
        "simplest-first and compared with a strict >, so a tie goes to the simpler name. A root "
        "that is not actually sounding — below 5% of the bar's weight — is rejected outright, "
        "since those are usually artifacts.")
    doc.table(
        ["Post-pass", "What it does", "Why it is safe"],
        [["Gap filling", "A bar with no confident chord inherits the previous one",
          "A bar with no clear chord is usually a bar where the harmony simply did not change"],
         ["Smoothing", "A one-bar chord between two identical neighbours is replaced by them",
          "Dm Dm F Dm Dm almost certainly had no F in it — and it only ever substitutes a chord "
          "already present adjacent"]],
        widths=[1.1, 2.6, 2.8],
    )

    doc.h2("6.6 Making it look like Strudel")
    doc.p("A quantized bar arrives as sixteen slots whether or not anything is in them, and "
          "written literally that is what it looks like:")
    doc.code("x ~ ~ ~ x ~ ~ ~ x ~ ~ ~ x ~ ~ ~")
    doc.p("Nobody writes Strudel that way. Four ", ("lossless", "b"), " rewrites are applied in "
          "order of how much they say:")
    doc.table(
        ["#", "Rewrite", "Example"],
        [["1", "Halve the grid while every odd slot is empty", "16 slots → x ~ ~ ~ → x"],
         ["2", "Euclidean (k,n) when the onsets are one", "x ~ ~ x ~ ~ x ~ → x(3,8)"],
         ["3", "@n elongation absorbs each rest run", "x ~ ~ ~ → x@4"],
         ["4", "*n when what remains is one token repeated", "x x x x → x*4"]],
        widths=[0.4, 3.3, 2.8], mono_cols=(2,),
    )
    doc.p("Every step preserves the same events at the same times. For .struct() only the onset "
          "matters, so @n is exactly equivalent; for a note pattern it additionally says the note "
          "is held, which is nearer the truth than an instant note plus three silences anyway.")
    doc.p("The Euclidean test uses ", ("Bjorklund's algorithm", "b"), " — start with k [x] groups "
          "and n−k [~] groups, repeatedly appending the remainder groups onto the front ones "
          "until at most one remainder group is left. Only the un-rotated form is emitted: "
          "Strudel takes a third rotation argument, but which way it turns is not pinned down by "
          "the docs, and a rhythm written the wrong way round is worse than one written out in "
          "full — path 3 is always correct.")
    doc.p("Above the bar, <a b c> alternation gives one bar per cycle, with !n for consecutive "
          "repeats. Bars carrying an operator are bracketed before going in, so [x*4]!3 cannot be "
          "misread as x*4!3.")
    doc.callout(
        "Line length is its own kind of unreadable",
        "Even compacted, sixteen bars ran to a single 424-character line. Strudel patterns are "
        "ordinary JS strings, so a long one is emitted as a template literal with one bar per "
        "line — inside <> a newline is just whitespace. Longest line now 91 characters.")

    doc.h2("6.7 The shape of the output")
    doc.p("The snippet is laid out the way Strudel is written by hand — drums, one harmony, a "
          "bass, a melody — ", ("not", "b"), " one part per separated stem. Stems are what a "
          "separator produced; they are not the parts of the music, and emitting six chord tracks "
          "playing the same progression is both unreadable and wrong about what is being played. "
          "Roles are assigned by median pitch: lowest is bass, highest with at least 20 notes is "
          "melody, everything else folds into the pooled harmony.")
    doc.code("""
// a warm lo-fi hip hop track
// ~94 BPM, 4/4, 16 of 41 bars, key F minor
// 37 off-key notes snapped to F minor
// Sounds and effects are plain defaults, not detected from the audio -- change them first.

setcpm(94/4)
let chords = chord("<Fm!4 Ab!2 Cm!2>").dict("ireal")
stack(
  // DRUMS -- 412 hits, from onset detection
  stack(
    s("bd").struct("<[x@4]!3 [x ~ x@3]>"),
    s("hh").struct("x*8")
  ).bank("RolandTR909"),
  // HARMONY -- one progression, from all pitched stems pooled
  chords.voicing().s("gm_epiano1").room(.4).gain(.5),
  // BASS (Bass) -- 188 notes as scale degrees
  n(`<
    [0@2 2 ~]
    [0@4]
  >`).scale("F:minor")
    .s("sawtooth").lpf(600)
)
""", caption="Illustrative: the structure and header-line order match the emitter, but the "
             "numbers are made up rather than copied from a real conversion.")
    doc.table(
        ["Query parameter", "Effect"],
        [["?view=lead",
          "Reads the reduced file instead — usually the better source, since the emitter's job is "
          "to find drums/harmony/bass/melody and the lead sheet has already separated exactly "
          "those"],
         ["?mode=notes",
          "Keeps every transcribed pitch instead of naming the harmony. Much longer, and what you "
          "want when the transcription itself is the thing being checked"]],
        widths=[1.4, 5.1], mono_cols=(0,),
    )
    doc.callout(
        "Derived from the audio, versus invented",
        "Derived: tempo, meter, key, chords, drum rhythms, pitches, arrangement. Invented: the "
        "sounds and the effect chain. A filter cutoff or a reverb size is a musical decision, not "
        "a property of a recording, so those stay to a plain starting point — and the output says "
        "so, because a generated chain that looks considered invites the reader to assume it "
        "means something.")

    doc.h2("6.8 Accuracy, checked through Strudel itself")
    doc.p("Reading the snippet is not the same as hearing it, so the output was scored the way "
          "Strudel plays it: each part run through Strudel's own engine (@strudel/core, mini "
          "and tonal in Node), its notes compared with the MIDI it came from. Onset tolerance "
          "60 ms; chords compared as the pitch classes sounding in each beat. Two songs, the "
          "lead-sheet MIDI of each (a 121 s folk track and a 61 s synth-pop track):")
    doc.table(
        ["", "Before", "After"],
        [["Song covered", "28–34 s (the first 16 bars)", "**The whole song**"],
         ["Bass / melody at the right pitch", "**0%** — all an octave low", "84–93%"],
         ["Chords (chords mode)", "0.43–0.52 overlap", "**0.89–0.96**"],
         ["Chords (notes mode)", "—", "**1.00**"],
         ["Note length vs. the MIDI", "1.3–1.6× (held to the next note)", "1.1–1.2×"]],
        widths=[2.4, 2.2, 1.9],
        caption="The pitched parts now score as well as their timing allows: exact-pitch and "
                "rhythm-only scores are equal. The remaining misses are notes whose transcribed "
                "start falls between sixteenths; no tempo lines them up much better, so a "
                "finer grid would only lengthen the code.")
    doc.p("What was wrong, in order of how much it cost:")
    doc.bullet(("Every pitched note an octave low. ", "b"),
               "Degrees counted from octave 4; Strudel's .scale() counts from octave 3.")
    doc.bullet(("A third of the chords silent. ", "b"),
               "maj7, maj9, sus4, sus2 and dim are not names Strudel's ireal voicings know; they "
               "voiced to nothing. They are now written ^7, ^9, sus, 2 and o, with roots spelled "
               "for the key (F#m7 in A major, not Gbm7).")
    doc.bullet(("Only 16 bars. ", "b"),
               "DEFAULT_BARS was 16; the whole song (up to 96 bars) is written now.")
    doc.bullet(("Chords re-guessed. ", "b"),
               "From a lead sheet, the progression is read straight off its block chords instead "
               "of detected again from melody, bass and chords pooled. For the full MIDI, notes "
               "count in every bar they sound in: filing them by start bar put a chord that "
               "began on a bar line a bar early whenever float rounding gave 5.9999.")
    doc.bullet(("Notes held too long. ", "b"),
               "Rests used to be folded into the note before them; now a note lasts its own "
               "length and the rest is written as a rest.")
    doc.bullet(("A measured 120 BPM thrown away. ", "b"),
               "120 was treated as pretty_midi's \"unset\" default, and one song the beat tracker "
               "put at 120 came out at 77. midi_convert now marks a measured tempo in the file "
               "(a text event), and a marked header is trusted whatever it reads.")
    doc.p("The Library now asks for the lead sheet by default, falling back to the full "
          "transcription for entries made before lead sheets existed.")


    # ══ 7 ══════════════════════════════════════════════════════════════════════
    doc.h1("7. What is measured and what is assumed")
    doc.p("Each part's mean distance to the nearest grid line, normalised against what uniformly "
          "random note times would score. 1.0 means the grid explains nothing; lower is better.")
    doc.image(_bar_chart(
        "How well does each part actually sit on a rhythmic grid?",
        ["Drums, 8ths", "Drums, 16ths", "Drums, 32nds", "Pitched parts"],
        [("Normalised grid error", CHART_BLUE, [0.29, 0.51, 0.73, 0.90])],
        ymax=1.0, ylabel="error / random",
        note="1.0 = indistinguishable from random onsets. Pitched parts span 0.82–0.99."),
        width_in=6.4,
        caption="Normalised grid error by part. Lower is better.")
    doc.callout(
        "The drum track carries essentially all of the rhythm that survives",
        "It comes from onset detection here in the pipeline; the pitched tracks come from Basic "
        "Pitch, whose note starts on this material are close to unquantizable. So a style built "
        "on clear percussive transients converts well, and one carried by sustained or heavily "
        "processed pitched material converts into something rhythmically vague however the grid "
        "is set.")
    doc.p("Two things that sound like they would help and do not, both measured:")
    doc.table(
        ["Idea", "Result", "Why"],
        [["A finer grid", "32nds score 0.73 on drums — **worse** relative to noise",
          "They fit everything, which means they discriminate nothing"],
         ["Dropping low-confidence notes",
          "Moved pitched tracks by 0.01–0.06, drums not at all",
          "Filtering to the loudest or longest half changes density, not timing"]],
        widths=[1.6, 2.6, 2.3],
    )

    doc.h2("7.1 Known limits")
    doc.table(
        ["Limit", "Detail"],
        [["Vocal takes vary run to run",
          "Same settings, different noise: one seed gives a clean chorus, another a messy one. "
          "Regenerate. The lyrics a song was sung with are logged by the server but not yet "
          "saved with the song."],
         ["Long songs roll bad seeds more often",
          "At 150 s, 2 of 3 seeds broke down (tempo jumping 125 → 222 → 273, 37–42% beat "
          "jitter); at 60–90 s it was about 1 in 2 on the worst seed. Every seed rated messy by "
          "ear also measured badly, so an automatic check-and-re-roll could catch breakdowns — "
          "but only as a breakdown detector: a Heun take that measured 22% jitter was rated good."],
         ["Sampler and guidance are not artifact fixes",
          "On the same seed, Heun and guidance 4.0 each produced a different song rather than a "
          "cleaner copy. Heun was rated well; guidance 4.0 gave a more varied melody but more "
          "artifacts near the end. Euler at 5.5 stays the default."],
         ["The local lyrics fallback can mix languages",
          "Whole English lines inside Japanese lyrics from gemma3:4b survive cleaning when "
          "every word happens to fit the romaji pattern. Gemini is the normal path."],
         ["Swing is real but unimplemented",
          "Once the half-tempo bug (§6.1) was fixed, a swung grid does fit cool jazz better: 0.73 "
          "against 0.83 on pitched parts, 0.26 against 0.29 on drums. It is a modest effect on "
          "one of four tracks and grid selection is not per-track, so nothing has been changed "
          "for it."],
         ["Some material is genuinely unquantizable",
          "One J-pop track scores 0.97–0.99 on every grid — statistically indistinguishable from "
          "random onsets — and its tempo estimates disagree irreconcilably (158 from onsets, 108 "
          "from librosa, not a factor of two apart). Dense 8-bit arpeggios have no beat to find. "
          "That is a limit, not a bug."],
         ["Basic Pitch's note starts are not rhythm",
          "Everything in §6 is downstream of that."],
         ["The autocorrelation tempo fallback is weak",
          "5–6 of 13 on synthetic kit patterns. It only matters for foreign MIDI; our own files "
          "carry a header."]],
        widths=[1.9, 4.6],
    )

    doc.h2("7.2 Re-running the measurements")
    doc.table(
        ["Script", "Answers"],
        [["scripts/drum_bench.py", "Is a hit labelled kick / snare / hat correctly?"],
         ["scripts/melody_bench.py", "Does melody extraction find the tune, or the overtones?"],
         ["scripts/amt_eval.py",
          "Does the MIDI agree with the recording? Note support and chroma agreement, against "
          "tritone / time-shift / pitch-shuffle null baselines"],
         ["scripts/amt_sanity.py", "Does the evaluation harness itself behave?"]],
        widths=[1.9, 4.6], mono_cols=(0,),
    )
    doc.callout(
        "Why the null baselines are the point",
        "A metric that cannot tell the real transcription from a tritone-transposed one is not "
        "measuring anything.")


    # ══ 8 ══════════════════════════════════════════════════════════════════════
    doc.h1("8. Appendix: endpoints and file map")
    doc.h2("8.1 Endpoints")
    doc.table(
        ["Method", "Path", "Notes"],
        [["POST", "/auth/login · /auth/logout", "Public"],
         ["GET", "/auth/me", "Public; returns username and role"],
         ["GET", "/health", "Public, and the only cross-origin endpoint (status page)"],
         ["POST", "/upload", "Image or audio in, file_id out"],
         ["POST", "/describe", "Image → prompt"],
         ["POST", "/lyrics", "Lyrics + suggested BPM for a vocal song"],
         ["POST", "/theme", "Random prompt (\"surprise me\") — local model only"],
         ["GET", "/models", "The manifest's public view — never worker paths; admin_only "
          "options and the user duration cap applied per role"],
         ["POST", "/generate", "202; 409 while another generation runs (with active_job_id "
          "for the admin); 403 for an admin-only option (Vocal tone) on a user account"],
         ["GET", "/status/{id}", "Polled every 2 s; progress, and a stage name for MIDI jobs"],
         ["GET", "/jobs/active", "The caller's own queued/running jobs — how a page finds its way back (§2.9)"],
         ["POST", "/cancel/{id}", "Kills the worker within ~1 s"],
         ["GET", "/audio/{id} · /image/{id}", "Media"],
         ["GET", "/download/{id}?format=", "mp3 / wav / flac / m4a / ogg, via PyAV"],
         ["POST", "/save/{id} · /discard/{id}", "Expiry control"],
         ["GET", "/library", "The caller's own songs (admin: all)"],
         ["POST", "/convert", "Audio format conversion"],
         ["POST", "/midi/convert/{id}", "202, new MIDI entry — or the running one if this song is already converting"],
         ["GET", "/midi/tracks/{id}?view=", "Parts list, for the solo buttons"],
         ["GET", "/midi/preview/{id}?track=&view=", "Sonified WAV; track solos one part"],
         ["GET", "/midi/strudel/{id}?mode=&view=", "Pattern code"]],
        widths=[0.7, 2.6, 3.2], mono_cols=(1,),
    )
    doc.callout(
        "Auth is deny by default",
        "Anything not on the public list requires a session, including endpoints added later. "
        "The public list is only /health and the three /auth routes; every song route also "
        "filters by owner (§2.6). The track parameter is validated with isalnum() and a length cap before it can ever reach "
        "the filesystem as a path fragment.")

    doc.h2("8.2 Where things live")
    doc.code("""
pipeline/
  generate_song.py   entry point; owns the image->prompt step, nothing model-specific
  describers.py      vision backends and the order they are tried
  prompting.py       the instructions themselves, per prompt_style
  lyrics.py          lyric writing, cleaning, section plans, vocal captions, tempo pick
  models.py          manifest loader; the trust boundary for user options
  models.json        which models exist, what each can do, what it exposes
  runner.py          spawns a worker, reads its events, cancels and times out
  workers/           one script per model family + the JSON-lines protocol
  midi_convert.py    separation, transcription, assembly, preview
  lead_sheet.py      the melody/chords/bass/drums reduction
  beat_track.py      optional Beat This bridge (its own venv)
  strudel_convert.py MIDI -> snippet: tempo, phase, key, layout
  strudel_emit.py    the notation: chords, degrees, compaction, Euclid

backend/app/
  main.py            middleware order, auth gate, router registration
  access.py          roles, owner filtering, per-role duration cap
  jobs.py            the queue and its single worker thread
  limiter.py         rate limits, keyed on the real client behind the tunnel
  sysmem.py          free-RAM reading for the low-memory warning
  routers/           one file per endpoint group

run.py               starts backend + frontend + tunnel, publishes the status gist
scripts/set_password.py   create accounts / set passwords
status-page/         separate public repo (ImageSound-Status): the redirect page

src/
  api.ts             the typed client — every endpoint above
  pages/             Home, Player, Library, Login
  hooks/             generation polling, model selection, audio effects
""")

    doc.h2("8.3 Adding a model")
    doc.p("Add an entry to pipeline/models.json and a worker script. Nothing in the routes, the "
          "job queue or the UI needs to change — the manifest is what the API and the frontend "
          "read. validate_request() is the trust boundary: only options the manifest declares can "
          "reach a worker, and each is range- or choice-checked first.")
    doc.p("Two manifest flags shape how an option is offered. when_option ({\"vocals\": "
          "[\"lyrics\"]}) makes it apply only while another option has one of those values — the "
          "server drops it otherwise, and the UI hides it. admin_only hides it from other "
          "accounts and makes /generate refuse it (§2.6).")

    doc.p("")
    end = doc.p("Generated by scripts/build_how_it_works_docx.py — edit the script, not the .docx.")
    end.runs[0].italic = True
    end.runs[0].font.size = Pt(8.5)
    end.runs[0].font.color.rgb = MUTED

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT)
    if not _refresh_toc(OUT):
        print("Word not available: the table of contents fills in when the file is opened.")
        _update_fields_on_open(OUT)
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
