"""Read report files people send instead of a voice note: Word, Excel, CSV, text, PDF.

Stdlib only. PDF text uses ``pypdf`` when it happens to be installed; otherwise the
result tells the agent to look at the file with vision (scanned reports need that
anyway). Photos are not handled here: Hermes already passes them to vision_analyze.
"""
from __future__ import annotations

import csv
import io
import re
import shutil
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_CHARS = 12000
MAX_BYTES = 25 * 1024 * 1024
TEXT_EXT = {".txt", ".md", ".csv", ".tsv", ".json", ".log"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _clip(text: str) -> tuple[str, bool]:
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\r", "")).strip()
    return (text[:MAX_CHARS], True) if len(text) > MAX_CHARS else (text, False)


def _docx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    lines = []
    for block in root.iter():
        if block.tag == W + "p":
            t = "".join(x.text or "" for x in block.iter(W + "t"))
            if t.strip():
                lines.append(t)
        elif block.tag == W + "tr":
            cells = ["".join(x.text or "" for x in c.iter(W + "t")).strip()
                     for c in block.iter(W + "tc")]
            if any(cells):
                lines.append(" | ".join(cells))
    # table cell paragraphs were also emitted above; drop exact repeats in a row
    out = []
    for ln in lines:
        if not out or out[-1] != ln:
            out.append(ln)
    return "\n".join(out)


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _xlsx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(S + "si"):
                shared.append("".join(t.text or "" for t in si.iter(S + "t")))
        sheet_names = []
        if "xl/workbook.xml" in names:
            sheet_names = [s.get("name") for s in ET.fromstring(z.read("xl/workbook.xml")).iter(S + "sheet")]
        sheets = sorted((n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                        key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
        out = []
        for i, name in enumerate(sheets[:5]):
            out.append(f"## Sheet: {sheet_names[i] if i < len(sheet_names) else i + 1}")
            for row in ET.fromstring(z.read(name)).iter(S + "row"):
                cells = {}
                for c in row.iter(S + "c"):
                    v = c.find(S + "v")
                    if c.get("t") == "inlineStr":
                        val = "".join(t.text or "" for t in c.iter(S + "t"))
                    elif v is None:
                        continue
                    elif c.get("t") == "s":
                        val = shared[int(v.text)] if v.text and int(v.text) < len(shared) else ""
                    else:
                        val = v.text or ""
                    cells[_col_index(c.get("r", "A1"))] = val
                if cells:
                    width = max(cells) + 1
                    out.append(" | ".join(cells.get(i, "") for i in range(min(width, 30))))
        return "\n".join(out)


def _pdf(path: Path) -> str | None:
    try:
        from pypdf import PdfReader  # optional, not a dependency
    except Exception:
        return None
    reader = PdfReader(str(path))
    return "\n\n".join((p.extract_text() or "") for p in reader.pages[:30])


def read_report_file(path: str) -> dict:
    p = Path(str(path)).expanduser()
    if not p.is_file():
        raise FileNotFoundError(f"no file at {path!r}")
    if p.stat().st_size > MAX_BYTES:
        raise ValueError("file is larger than 25 MB")
    ext = p.suffix.lower()
    kind, text, hint = ext.lstrip(".") or "file", None, ""
    if ext in IMAGE_EXT:
        kind = "photo"
        hint = ("This is a photo. Read it with vision_analyze (image_url = this path), "
                "including handwriting, whiteboards and printed tables, then call steward_record_report.")
    elif ext == ".docx":
        text = _docx(p)
    elif ext == ".xlsx":
        text = _xlsx(p)
    elif ext in TEXT_EXT:
        raw = p.read_bytes().decode("utf-8", errors="replace")
        if ext in (".csv", ".tsv"):
            rows = csv.reader(io.StringIO(raw), delimiter="\t" if ext == ".tsv" else ",")
            raw = "\n".join(" | ".join(r) for r in rows)
        text = raw
    elif ext == ".pdf":
        text = _pdf(p)
        if not text or len(text.strip()) < 40:
            text = None
            hint = ("No text layer could be read (scanned PDF, or pypdf not installed). "
                    "Look at it with vision_analyze or the ocr-and-documents skill.")
    elif ext in (".doc", ".xls", ".ppt"):
        hint = "Old Office format. Ask for a .docx/.xlsx/PDF, a photo, or a voice note instead."
    else:
        hint = "Unknown file type. Ask the sender for a PDF, Word, Excel, photo or voice note."
    clipped = False
    if text is not None:
        text, clipped = _clip(text)
        if not text:
            hint = "The file has no readable text. Ask for a photo or a voice note."
            text = None
    return {"file": p.name, "kind": kind, "text": text, "truncated": clipped, "hint": hint}


def keep_copy(path: str, dest_dir: Path) -> str:
    """Keep the original next to the report so the founder can open it later."""
    src = Path(str(path)).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", src.name)[:120] or "report"
    dest = dest_dir / safe
    i = 1
    while dest.exists() and dest.read_bytes() != src.read_bytes():
        dest = dest_dir / f"{dest.stem}-{i}{dest.suffix}"; i += 1
    if not dest.exists():
        shutil.copy2(src, dest)
    return str(dest)
