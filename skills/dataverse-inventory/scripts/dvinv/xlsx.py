"""Gerador mínimo de .xlsx (Office Open XML) só com a biblioteca padrão — evita dependência extra.

Suporta: várias planilhas, cabeçalho em negrito, linha 1 congelada, autofiltro e largura de coluna.
Valores numéricos viram número; o resto, texto (inline string).
"""

import re
import zipfile
from xml.sax.saxutils import escape

_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SHEET_BAD = re.compile(r"[\[\]:*?/\\]")


def _col(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref, v, style):
    st = ' s="1"' if style else ""
    if isinstance(v, bool):
        v = "sim" if v else "não"
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return f'<c r="{ref}"{st}><v>{v}</v></c>'
    if v is None or v == "":
        return ""
    text = escape(_BAD.sub("", str(v)))[:32000]
    return f'<c r="{ref}" t="inlineStr"{st}><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet(rows):
    widths = {}
    for r in rows[:500]:
        for i, v in enumerate(r):
            widths[i] = min(max(widths.get(i, 8), len(str(v if v is not None else "")) + 2), 70)
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>' for i, w in sorted(widths.items()))
    body = []
    for ri, r in enumerate(rows):
        cells = "".join(_cell(f"{_col(ci)}{ri + 1}", v, ri == 0) for ci, v in enumerate(r))
        body.append(f'<row r="{ri + 1}">{cells}</row>')
    last = f"{_col(max(len(rows[0]) - 1, 0))}{len(rows)}" if rows else "A1"
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
            'state="frozen"/></sheetView></sheetViews>'
            f'<cols>{cols}</cols><sheetData>{"".join(body)}</sheetData>'
            + (f'<autoFilter ref="A1:{last}"/>' if rows else "") + "</worksheet>")


def write(path, sheets):
    """sheets: lista de (nome, linhas) — a primeira linha é o cabeçalho."""
    names = []
    for name, _ in sheets:
        n = _SHEET_BAD.sub("-", name)[:31] or "Planilha"
        while n in names:
            n = n[:28] + f"_{len(names)}"
        names.append(n)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(len(sheets))) + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(names))
                   + "</sheets>"
                   + "<definedNames>" + "".join(
                       f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
                       f"'{escape(n)}'!$A$1:${_col(max(len(rows[0]) - 1, 0))}${len(rows)}</definedName>"
                       for i, (n, (_, rows)) in enumerate(zip(names, sheets)) if rows) + "</definedNames>"
                   + "</workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                             for i in range(len(sheets)))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   "</Relationships>")
        z.writestr("xl/styles.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                   '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
                   '<fill><patternFill patternType="solid"><fgColor rgb="FFDDE7EE"/></patternFill></fill></fills>'
                   '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
                   '<cellXfs count="2"><xf xfId="0"/><xf fontId="1" fillId="2" xfId="0" applyFont="1" applyFill="1"/></cellXfs>'
                   '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
                   "</styleSheet>")
        for i, (_, rows) in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet(rows))
