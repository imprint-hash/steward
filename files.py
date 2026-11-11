"""Read report files people send instead of a voice note: Word, Excel, CSV, text, PDF.

Stdlib only. PDF text uses ``pypdf`` when it happens to be installed; otherwise the
result tells the agent to look at the file with vision (scanned reports need that
anyway). Photos are not handled here: Hermes already passes them to vision_analyze.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import shutil
import tempfile
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


# Office files are zip archives: a tiny upload can unpack to gigabytes ("zip bomb").
# Check the declared sizes first, then read through a byte cap and parse as a stream.
ZIP_MAX_MEMBERS = 2000
ZIP_MAX_TOTAL = 50 * 1024 * 1024      # all members, uncompressed
ZIP_MAX_MEMBER = 10 * 1024 * 1024     # each member we actually read
XML_MAX_ELEMENTS = 200_000
MAX_SHEETS = 5
MAX_ROWS = 2000                       # per sheet
MAX_CELLS = 200                       # per row looked at
MAX_COLS = 30                         # per row shown
MAX_SHARED = 20_000                   # shared strings kept (xlsx)
TOO_BIG = ("this file unpacks to more than Steward will read; ask for a smaller file, "
           "a PDF, a photo or a voice note")


class _Capped:
    """File-like wrapper that refuses to hand out more than ``cap`` bytes."""

    def __init__(self, f, cap: int):
        self.f, self.cap, self.n = f, cap, 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = 64 * 1024
        data = self.f.read(min(size, self.cap - self.n + 1))
        self.n += len(data)
        if self.n > self.cap:
            raise ValueError(TOO_BIG)
        return data


def _check_zip(z: zipfile.ZipFile) -> None:
    infos = z.infolist()
    if len(infos) > ZIP_MAX_MEMBERS:
        raise ValueError(TOO_BIG)
    if sum(i.file_size for i in infos) > ZIP_MAX_TOTAL:
        raise ValueError(TOO_BIG)


def _iterparse(z: zipfile.ZipFile, name: str, events=("end",)):
    """Stream (event, element) pairs from one zip member, within the size and element caps."""
    if z.getinfo(name).file_size > ZIP_MAX_MEMBER:
        raise ValueError(TOO_BIG)
    with z.open(name) as raw:
        for count, (ev, el) in enumerate(ET.iterparse(_Capped(raw, ZIP_MAX_MEMBER), events=events)):
            if count >= XML_MAX_ELEMENTS:
                return
            yield ev, el


def _docx(path: Path) -> str:
    lines, size, in_table = [], 0, 0
    with zipfile.ZipFile(path) as z:
        _check_zip(z)
        for ev, el in _iterparse(z, "word/document.xml", events=("start", "end")):
            if el.tag == W + "tbl":
                in_table += 1 if ev == "start" else -1
                continue
            if ev != "end":
                continue
            if el.tag == W + "p" and not in_table:
                t = "".join(x.text or "" for x in el.iter(W + "t"))
                if t.strip():
                    lines.append(t); size += len(t) + 1
                el.clear()
            elif el.tag == W + "tr":
                cells = ["".join(x.text or "" for x in c.iter(W + "t")).strip()
                         for c in el.iter(W + "tc")][:MAX_COLS]
                if any(cells):
                    t = " | ".join(cells)
                    lines.append(t); size += len(t) + 1
                el.clear()
            if size > MAX_CHARS:
                break
    out = []
    for ln in lines:
        if not out or out[-1] != ln:
            out.append(ln)
    return "\n".join(out)


def _col_index(ref: str) -> int:
    m = re.match(r"[A-Z]{1,3}", ref or "")
    if not m:
        return MAX_COLS  # unknown column: not shown
    n = 0
    for ch in m.group(0):
        n = n * 26 + ord(ch) - 64
    return n - 1


def _xlsx(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        _check_zip(z)
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for _, el in _iterparse(z, "xl/sharedStrings.xml"):
                if el.tag == S + "si":
                    shared.append("".join(t.text or "" for t in el.iter(S + "t"))[:1000])
                    el.clear()
                    if len(shared) >= MAX_SHARED:
                        break
        sheet_names = []
        if "xl/workbook.xml" in names:
            for _, el in _iterparse(z, "xl/workbook.xml"):
                if el.tag == S + "sheet":
                    sheet_names.append(str(el.get("name") or "")[:100])
                    if len(sheet_names) >= MAX_SHEETS:
                        break
        sheets = sorted((n for n in names if re.fullmatch(r"xl/worksheets/sheet[0-9]{1,6}\.xml", n)),
                        key=lambda n: int(re.search(r"([0-9]+)", n.rsplit("/", 1)[1]).group(1)))
        out, size = [], 0
        for i, name in enumerate(sheets[:MAX_SHEETS]):
            out.append(f"## Sheet: {sheet_names[i] if i < len(sheet_names) and sheet_names[i] else i + 1}")
            rows = 0
            for _, row in _iterparse(z, name):
                if row.tag != S + "row":
                    continue
                cells = {}
                for j, c in enumerate(row.iter(S + "c")):
                    if j >= MAX_CELLS:
                        break
                    col = _col_index(c.get("r", "A1"))
                    if col >= MAX_COLS:
                        continue
                    v = c.find(S + "v")
                    if c.get("t") == "inlineStr":
                        val = "".join(t.text or "" for t in c.iter(S + "t"))
                    elif v is None:
                        continue
                    elif c.get("t") == "s":
                        try:
                            idx = int(v.text or "")
                        except ValueError:
                            idx = -1
                        val = shared[idx] if 0 <= idx < len(shared) else ""
                    else:
                        val = v.text or ""
                    cells[col] = val[:1000]
                row.clear()
                if cells:
                    width = max(cells) + 1
                    line = " | ".join(cells.get(k, "") for k in range(width))
                    out.append(line); size += len(line) + 1
                rows += 1
                if rows >= MAX_ROWS or size > MAX_CHARS:
                    break
            if size > MAX_CHARS:
                break
        return "\n".join(out)


def _pdf(path: Path) -> str | None:
    try:
        from pypdf import PdfReader  # optional, not a dependency
    except Exception:
        return None
    reader = PdfReader(str(path))
    return "\n\n".join((p.extract_text() or "") for p in reader.pages[:30])


MEDIA_DIRS = (("cache/images", "image_cache"), ("cache/audio", "audio_cache"),
              ("cache/videos", "video_cache"), ("cache/documents", "document_cache"))
OUTSIDE_MEDIA = ("Steward only reads files people sent in the chat (Hermes's incoming media "
                 "folders); this path is outside them")


def allowed_roots(home: Path) -> list[Path]:
    """Hermes's inbound media cache folders under ``home`` (where chat attachments are saved)."""
    home = Path(home)
    try:
        from hermes_constants import get_hermes_dir
    except Exception:  # plain unit tests, no Hermes on the path
        get_hermes_dir = None
    roots = []
    for new, old in MEDIA_DIRS:
        if get_hermes_dir is not None:
            roots.append(Path(get_hermes_dir(new, old, home=home)))
        else:
            roots.append(home / new)
    return roots


def _confined(path: str, home: Path) -> Path:
    """Resolve ``path`` (following symlinks) and refuse it unless it's a regular file inside a media folder."""
    roots = []
    for r in allowed_roots(home):
        try:
            roots.append(r.resolve(strict=True))
        except (OSError, RuntimeError):
            continue
    try:
        p = Path(str(path)).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        raise ValueError(OUTSIDE_MEDIA)
    if not any(p.is_relative_to(r) for r in roots):
        raise ValueError(OUTSIDE_MEDIA)
    try:
        p = p.resolve(strict=True)
    except (OSError, RuntimeError):
        raise FileNotFoundError(f"no file at {path!r}")
    if not any(p.is_relative_to(r) for r in roots):
        raise ValueError(OUTSIDE_MEDIA)
    if not p.is_file():
        raise ValueError("Steward only reads regular files")
    return p


def read_report_file(path: str, home: Path) -> dict:
    p = _confined(path, home)
    if p.stat().st_size > MAX_BYTES:
        raise ValueError("file is larger than 25 MB")
    ext = p.suffix.lower()
    kind, text, hint = ext.lstrip(".") or "file", None, ""
    if ext in IMAGE_EXT:
        kind = "photo"
        hint = ("This is a photo. Read it with vision_analyze (image_url = this path), "
                "including handwriting, whiteboards and printed tables, then call steward_record_report.")
    elif ext in (".docx", ".xlsx"):
        try:
            text = _docx(p) if ext == ".docx" else _xlsx(p)
        except (zipfile.BadZipFile, ET.ParseError, KeyError, EOFError) as e:
            raise ValueError(f"couldn't read this {ext} file ({type(e).__name__}); "
                             "ask for a PDF, a photo or a voice note")
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


FILES_QUOTA = 500 * 1024 * 1024   # everything under HERMES_HOME/steward/files
FILES_MAX_COUNT = 5000
HASH_DIR = ".by-hash"              # <sha256> -> path of the copy already kept


def _used(base: Path) -> tuple[int, int]:
    size = count = 0
    for f in base.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink() and HASH_DIR not in f.relative_to(base).parts:
                size += f.stat().st_size; count += 1
        except OSError:
            continue
    return size, count


def _known_copy(base: Path, digest: str, size: int) -> str | None:
    """A copy with this exact content already kept anywhere under ``base``, if any."""
    marker = base / HASH_DIR / digest
    try:
        kept = (base / marker.read_text(encoding="utf-8").strip()).resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        return None
    if not kept.is_relative_to(base) or not kept.is_file() or kept.stat().st_size != size:
        return None
    return str(kept)


def keep_copy(path: str, dest_dir: Path, home: Path) -> str:
    """Keep the original next to the report so the founder can open it later.

    Only files from Hermes's media folders are copied, and only into ``home/steward/files``.
    Each file is at most MAX_BYTES, identical files are kept once (by sha256), and the whole
    folder stays under FILES_QUOTA.
    """
    src = _confined(path, home)
    if src.stat().st_size > MAX_BYTES:
        raise ValueError("file is larger than 25 MB; Steward doesn't keep copies that big")
    base = Path(home) / "steward" / "files"
    base.mkdir(parents=True, exist_ok=True)
    base = base.resolve(strict=True)
    if not Path(dest_dir).resolve(strict=False).is_relative_to(base):
        raise ValueError("copies can only be kept under Steward's files folder")
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    dest_dir = Path(dest_dir).resolve(strict=True)
    if not dest_dir.is_relative_to(base):
        raise ValueError("copies can only be kept under Steward's files folder")

    # copy once into a temp file, hashing as we go and never past MAX_BYTES
    fd, tmp = tempfile.mkstemp(dir=dest_dir, prefix=".keep-")
    tmp = Path(tmp)
    try:
        h, n = hashlib.sha256(), 0
        with open(src, "rb") as fin, os.fdopen(fd, "wb") as fout:
            while True:
                chunk = fin.read(1024 * 1024)
                if not chunk:
                    break
                n += len(chunk)
                if n > MAX_BYTES:
                    raise ValueError("file is larger than 25 MB; Steward doesn't keep copies that big")
                h.update(chunk); fout.write(chunk)
        digest = h.hexdigest()
        known = _known_copy(base, digest, n)
        if known:
            tmp.unlink()
            return known
        used, count = _used(base)
        used -= n; count -= 1  # the temp file itself
        if used + n > FILES_QUOTA or count >= FILES_MAX_COUNT:
            raise ValueError("Steward's files folder is full (500 MB); ask the founder to clear "
                             "old files in HERMES_HOME/steward/files before keeping more")
        safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", src.name)[:120].lstrip(".") or "report"
        dest = dest_dir / safe
        if dest.exists():
            stem, suffix = dest.stem, re.sub(r"[^A-Za-z0-9.]+", "", dest.suffix)[:12]
            dest = dest_dir / f"{stem[:100]}-{digest[:12]}{suffix}"
        if dest.exists():
            tmp.unlink()  # same name and same content prefix: already kept here
        else:
            shutil.copystat(src, tmp)
            os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    marker_dir = base / HASH_DIR
    marker_dir.mkdir(exist_ok=True)
    (marker_dir / digest).write_text(str(dest.relative_to(base)), encoding="utf-8")
    return str(dest)
