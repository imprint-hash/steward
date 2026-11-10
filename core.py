"""Steward core: pure logic, no Hermes imports.

Everything here works on a plain dict ("state") so it can be tested without
Hermes and reused by the small cron scripts Steward writes.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

STATE_VERSION = 1
SIMILAR = 0.5          # Jaccard score at which two lines mean "the same thing"
STUCK_WEEKS = 2        # a blocker seen this many weeks in a row is "stuck"
VOICE_WORD_LIMIT = 150 # about 60 seconds spoken

DEFAULT_SCHEDULE = {
    "checkin_day": "thu", "checkin_time": "10:00",
    "nudge_day": "fri", "nudge_time": "13:00",
    "status_day": "fri", "status_time": "15:00",
    "pack_day": "sat", "pack_time": "10:00",
}
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

STOPWORDS = set("""
a an and are as at be been but by for from has have in into is it its of on or our so
that the their them then this to was we were will with still again waiting wait yet
""".split())


# ─── time ──────────────────────────────────────────────────────────────────────

def tz(name: str):
    if not name or ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        raise ValueError(f"unknown timezone {name!r} (use an IANA name like 'Africa/Lagos')")


def now_in(state: dict, now: Optional[datetime] = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(tz(state.get("timezone", "UTC")))


def week_key(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def shift_week(key: str, n: int) -> str:
    y, w = key.split("-W")
    monday = datetime.fromisocalendar(int(y), int(w), 1)
    return week_key(monday + timedelta(weeks=n))


def week_label(key: str) -> str:
    y, w = key.split("-W")
    monday = datetime.fromisocalendar(int(y), int(w), 1)
    friday = monday + timedelta(days=4)
    return f"{monday:%d %b} – {friday:%d %b %Y}"


def parse_hhmm(s: str) -> Tuple[int, int]:
    m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(s).strip())
    if not m:
        raise ValueError(f"time {s!r} must be HH:MM (24-hour)")
    return int(m.group(1)), int(m.group(2))


def in_quiet_hours(state: dict, dt: datetime) -> bool:
    q = state.get("quiet_hours") or {}
    if not q.get("start") or not q.get("end"):
        return False
    sh, sm = parse_hhmm(q["start"]); eh, em = parse_hhmm(q["end"])
    t = dt.hour * 60 + dt.minute
    s, e = sh * 60 + sm, eh * 60 + em
    if s == e:
        return False
    return (s <= t < e) if s < e else (t >= s or t < e)


def is_off_day(state: dict, dt: datetime) -> bool:
    return DAYS[dt.weekday()] in set(state.get("off_days") or [])


def cron_expr(day: str, hhmm: str) -> str:
    day = day.lower()[:3]
    if day not in DAYS:
        raise ValueError(f"day {day!r} must be one of {', '.join(DAYS)}")
    h, m = parse_hhmm(hhmm)
    return f"{m} {h} * * {(DAYS.index(day) + 1) % 7}"


# ─── state ─────────────────────────────────────────────────────────────────────

def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s or "dept"


def empty_state() -> dict:
    return {"version": STATE_VERSION, "company": "", "timezone": "UTC",
            "founder_target": "", "all_staff_target": "", "departments": [],
            "schedule": dict(DEFAULT_SCHEDULE), "quiet_hours": {}, "off_days": [], "share_numbers_with_staff": False,
            "jobs": {}, "weeks": {}}


def load_state(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            base = empty_state(); base.update(data)
            return base
    except FileNotFoundError:
        pass
    return empty_state()


def save_state(path: Path, state: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".state-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)  # reports can hold private notes
    except OSError:
        pass


def configure(state: dict, cfg: dict) -> dict:
    """Validate and apply setup fields. Returns the state (mutated)."""
    if cfg.get("company"):
        state["company"] = str(cfg["company"]).strip()
    if cfg.get("timezone"):
        tz(cfg["timezone"]); state["timezone"] = cfg["timezone"]
    for k in ("founder_target", "all_staff_target"):
        if cfg.get(k):
            state[k] = str(cfg[k]).strip()
    if cfg.get("departments") is not None:
        depts, seen = [], set()
        for d in cfg["departments"]:
            if isinstance(d, str):
                d = {"name": d}
            name = str(d.get("name", "")).strip()
            if not name:
                raise ValueError("every department needs a name")
            did = slug(d.get("id") or name)
            if did in seen:
                raise ValueError(f"department {name!r} is listed twice")
            seen.add(did)
            depts.append({"id": did, "name": name, "lead": str(d.get("lead", "")).strip(),
                          "target": str(d.get("target", "")).strip()})
        state["departments"] = depts
    sched = dict(state.get("schedule") or DEFAULT_SCHEDULE)
    for k in DEFAULT_SCHEDULE:
        if cfg.get(k):
            sched[k] = str(cfg[k]).lower() if k.endswith("_day") else str(cfg[k])
    for part in ("checkin", "nudge", "status", "pack"):
        cron_expr(sched[f"{part}_day"], sched[f"{part}_time"])  # validates
    state["schedule"] = sched
    if cfg.get("quiet_hours") is not None:
        q = cfg["quiet_hours"] or {}
        if q:
            parse_hhmm(q.get("start", "")); parse_hhmm(q.get("end", ""))
        state["quiet_hours"] = {"start": q.get("start"), "end": q.get("end")} if q else {}
    if cfg.get("share_numbers_with_staff") is not None:
        state["share_numbers_with_staff"] = bool(cfg["share_numbers_with_staff"])
    if cfg.get("off_days") is not None:
        bad = [d for d in cfg["off_days"] if d.lower()[:3] not in DAYS]
        if bad:
            raise ValueError(f"unknown off days: {bad}")
        state["off_days"] = [d.lower()[:3] for d in cfg["off_days"]]
    # scheduled sends must not land in quiet hours
    if state.get("quiet_hours"):
        for part in ("checkin", "nudge", "status", "pack"):
            h, m = parse_hhmm(sched[f"{part}_time"])
            probe = datetime(2026, 1, 5, h, m)
            if in_quiet_hours(state, probe):
                raise ValueError(f"{part} time {sched[part + '_time']} falls inside quiet hours")
    return state


def find_dept(state: dict, name_or_id: str) -> dict:
    key = slug(name_or_id)
    for d in state.get("departments", []):
        if d["id"] == key or slug(d["name"]) == key:
            return d
    # also accept a chat target, so a reply in a group maps to its department
    for d in state.get("departments", []):
        if d.get("target") and d["target"] == str(name_or_id).strip():
            return d
    names = ", ".join(d["name"] for d in state.get("departments", [])) or "none set up yet"
    raise KeyError(f"no department called {name_or_id!r} (departments: {names})")


def week_bucket(state: dict, key: str) -> dict:
    return state.setdefault("weeks", {}).setdefault(key, {"reports": {}, "sent": None})


# ─── text similarity ───────────────────────────────────────────────────────────

def tokens(text: str) -> set:
    words = re.findall(r"[a-z0-9]+", str(text).lower())
    out = set()
    for w in words:
        if w in STOPWORDS or len(w) < 2:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if len(w) > 4 and w.endswith(suf):
                w = w[: -len(suf)]; break
        out.add(w)
    return out


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def best_match(item: str, pool: Iterable[str]) -> Tuple[Optional[str], float]:
    best, score = None, 0.0
    for p in pool:
        s = similarity(item, p)
        if s > score:
            best, score = p, s
    return best, score


# ─── reports ───────────────────────────────────────────────────────────────────

def _str_list(v: Any, field: str, limit: int = 20) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        raise ValueError(f"{field} must be a list of short sentences")
    out = [re.sub(r"\s+", " ", str(x)).strip() for x in v if str(x).strip()]
    return [x[:300] for x in out[:limit]]


PROMISE_STATUSES = ("done", "partly", "not_done", "dropped")


def record_report(state: dict, department: str, report: dict,
                  now: Optional[datetime] = None, week: Optional[str] = None) -> dict:
    dept = find_dept(state, department)
    local = now_in(state, now)
    key = week or week_key(local)
    summary = re.sub(r"\s+", " ", str(report.get("summary", ""))).strip()[:600]
    done = _str_list(report.get("done"), "done")
    if not summary and not done:
        raise ValueError("a report needs at least a summary or one thing done")
    numbers = []
    for n in report.get("numbers") or []:
        if not isinstance(n, dict) or not str(n.get("label", "")).strip():
            raise ValueError("each number needs a label and a value, e.g. {label: 'New customers', value: 12}")
        val = n.get("value")
        try:
            val = float(str(val).replace(",", ""))
            val = int(val) if val.is_integer() else val
        except (TypeError, ValueError):
            raise ValueError(f"number {n.get('label')!r} has a value that is not a number: {val!r}")
        numbers.append({"label": str(n["label"]).strip()[:60], "value": val,
                        "unit": str(n.get("unit", "")).strip()[:12]})
    updates = []
    for u in report.get("promise_updates") or []:
        st = str(u.get("status", "")).lower().replace(" ", "_")
        if st not in PROMISE_STATUSES:
            raise ValueError(f"promise status must be one of {PROMISE_STATUSES}, got {st!r}")
        updates.append({"promise": str(u.get("promise", "")).strip()[:300], "status": st,
                        "note": str(u.get("note", "")).strip()[:300]})
    entry = {
        "department": dept["name"],
        "submitted_at": local.isoformat(timespec="minutes"),
        "summary": summary,
        "done": done,
        "blockers": _str_list(report.get("blockers"), "blockers"),
        "next_week": _str_list(report.get("next_week"), "next_week"),
        "numbers": numbers,
        "promise_updates": updates,
        "founder_only": _str_list(report.get("founder_only"), "founder_only"),
        "language": str(report.get("language", "")).strip()[:30],
        "source": str(report.get("source", "text")).strip().lower()[:20] or "text",
        "attachments": [{"file": str(a.get("file", ""))[:120], "kind": str(a.get("kind", ""))[:20],
                         "path": str(a.get("path", ""))[:400]}
                        for a in (report.get("attachments") or []) if isinstance(a, dict)][:10],
    }
    bucket = week_bucket(state, key)
    previous = bucket["reports"].get(dept["id"])
    bucket["reports"][dept["id"]] = entry
    return {"week": key, "department": dept["name"], "updated": previous is not None, "report": entry}


def missing(state: dict, key: str) -> List[dict]:
    got = state.get("weeks", {}).get(key, {}).get("reports", {})
    return [d for d in state.get("departments", []) if d["id"] not in got]


# ─── check-in, promises, stuck radar, numbers ─────────────────────────────────

def promises_for(state: dict, dept_id: str, key: str) -> List[str]:
    """What this department said last week it would do this week."""
    prev = state.get("weeks", {}).get(shift_week(key, -1), {}).get("reports", {}).get(dept_id)
    return list(prev.get("next_week", [])) if prev else []


def checkin_message(state: dict, department: str, now: Optional[datetime] = None) -> dict:
    dept = find_dept(state, department)
    key = week_key(now_in(state, now))
    promises = promises_for(state, dept["id"], key)
    who = dept.get("lead") or f"{dept['name']} team"
    lines = [f"Hi {who} 👋 It's Steward, collecting this week's {dept['name']} update "
             f"for {state.get('company') or 'the team'}.",
             "",
             "Reply with a voice note (any language is fine), a few lines, or a photo/file of "
             "your report (PDF, Word, Excel, or a picture of the site diary). Takes 2 minutes:",
             "1. What got done this week?",
             "2. Anything stuck or waiting on someone?",
             "3. What's the plan for next week?",
             "4. Any numbers worth sharing? (sales, tickets, hires…)"]
    if promises:
        lines += ["", "Last week you planned:"] + [f"• {p}" for p in promises] + \
                 ["Quick word on how each went?"]
    already = key in state.get("weeks", {}) and dept["id"] in state["weeks"][key]["reports"]
    return {"week": key, "department": dept["name"], "promises": promises,
            "already_reported": already, "message": "\n".join(lines)}


def nudge_message(state: dict, department: str, now: Optional[datetime] = None) -> str:
    """Empty string means: no nudge needed (already in, quiet hours, off day)."""
    dept = find_dept(state, department)
    local = now_in(state, now)
    if in_quiet_hours(state, local) or is_off_day(state, local):
        return ""
    key = week_key(local)
    if dept["id"] in state.get("weeks", {}).get(key, {}).get("reports", {}):
        return ""
    sent_in = len(state.get("departments", [])) - len(missing(state, key))
    who = dept.get("lead") or f"{dept['name']} team"
    return (f"Gentle reminder, {who}: the {dept['name']} update for this week isn't in yet "
            f"({sent_in} of {len(state.get('departments', []))} teams have sent theirs). "
            f"A 1-minute voice note here is enough. Thank you! 🙏")


def promise_review(state: dict, dept_id: str, key: str) -> List[dict]:
    promises = promises_for(state, dept_id, key)
    rep = state.get("weeks", {}).get(key, {}).get("reports", {}).get(dept_id)
    out = []
    for p in promises:
        status, note = "no_update", ""
        if rep:
            upd, s = best_match(p, [u["promise"] for u in rep.get("promise_updates", [])])
            if upd is not None and s >= SIMILAR:
                u = next(x for x in rep["promise_updates"] if x["promise"] == upd)
                status, note = u["status"], u["note"]
            else:
                hit, s2 = best_match(p, rep.get("done", []))
                if hit is not None and s2 >= SIMILAR:
                    status, note = "done", f"matched: “{hit}”"
        out.append({"promise": p, "status": status, "note": note})
    return out


def stuck_radar(state: dict, key: str, min_weeks: int = STUCK_WEEKS) -> List[dict]:
    """Blockers this week that were also reported in the weeks just before."""
    out = []
    weeks = state.get("weeks", {})
    for d in state.get("departments", []):
        rep = weeks.get(key, {}).get("reports", {}).get(d["id"])
        if not rep:
            continue
        for b in rep.get("blockers", []):
            streak, first, k = 1, key, shift_week(key, -1)
            while True:
                prev = weeks.get(k, {}).get("reports", {}).get(d["id"])
                if not prev:
                    break
                hit, s = best_match(b, prev.get("blockers", []))
                if hit is None or s < SIMILAR:
                    break
                streak += 1; first = k; k = shift_week(k, -1)
            if streak >= min_weeks:
                out.append({"department": d["name"], "blocker": b, "weeks": streak, "since": first})
    out.sort(key=lambda x: -x["weeks"])
    return out


def numbers_table(state: dict, key: str) -> List[dict]:
    rows = []
    weeks = state.get("weeks", {})
    prev_key = shift_week(key, -1)
    for d in state.get("departments", []):
        rep = weeks.get(key, {}).get("reports", {}).get(d["id"])
        if not rep:
            continue
        prev = {slug(n["label"]): n for n in
                (weeks.get(prev_key, {}).get("reports", {}).get(d["id"]) or {}).get("numbers", [])}
        for n in rep.get("numbers", []):
            p = prev.get(slug(n["label"]))
            change = None
            if p is not None and isinstance(p["value"], (int, float)):
                diff = n["value"] - p["value"]
                change = int(diff) if float(diff).is_integer() else round(diff, 2)
            rows.append({"department": d["name"], "label": n["label"], "value": n["value"],
                         "unit": n.get("unit", ""), "last_week": p["value"] if p else None,
                         "change": change})
    return rows


# ─── the weekly pack ───────────────────────────────────────────────────────────

_ICON = {"done": "✅", "partly": "🟡", "not_done": "❌", "dropped": "⏸️", "no_update": "❔"}


def _fmt_num(v) -> str:
    return f"{v:,}" if isinstance(v, int) else f"{v:,.2f}".rstrip("0").rstrip(".")


def _fmt_unit(u: str) -> str:
    return "" if not u else (u if u in ("%", "pp") else f" {u}")


def _sentence(t: str) -> str:
    t = t.strip()
    return t if not t or t[-1] in ".!?" else t + "."


def _fmt_change(c) -> str:
    if c is None:
        return "new"
    if c == 0:
        return "same"
    return ("▲ " if c > 0 else "▼ ") + _fmt_num(abs(c))


def build_pack(state: dict, key: Optional[str] = None, audience: str = "founder",
               now: Optional[datetime] = None) -> dict:
    if audience not in ("founder", "staff"):
        raise ValueError("audience must be 'founder' or 'staff'")
    key = key or week_key(now_in(state, now))
    reports = state.get("weeks", {}).get(key, {}).get("reports", {})
    depts = state.get("departments", [])
    miss = missing(state, key)
    stuck = stuck_radar(state, key)
    nums = numbers_table(state, key)
    founder = audience == "founder"
    company = state.get("company") or "Team"

    out = [f"*{company} — weekly update*", f"_{week_label(key)}_", ""]
    if founder:
        out.append(f"*Reports in:* {len(depts) - len(miss)} of {len(depts)}"
                   + (f" (waiting on {', '.join(d['name'] for d in miss)})" if miss else ""))
        out.append("")

    out.append("*Last week*")
    review_all = {}
    for d in depts:
        rep = reports.get(d["id"])
        if not rep:
            continue
        out.append(f"*{d['name']}*")
        if rep.get("summary"):
            out.append(rep["summary"])
        for item in rep.get("done", []):
            out.append(f"• {item}")
        review = promise_review(state, d["id"], key)
        review_all[d["name"]] = review
        if founder and review:
            kept = sum(1 for r in review if r["status"] == "done")
            out.append(f"_Promised vs done: {kept} of {len(review)}_")
            for r in review:
                out.append(f"  {_ICON[r['status']]} {r['promise']}" + (f" — {r['note']}" if r["note"] else ""))
        if founder and rep.get("attachments"):
            out.append("📎 " + ", ".join(a["file"] for a in rep["attachments"]))
        if founder and rep.get("founder_only"):
            out.append("🔒 For you only: " + "; ".join(rep["founder_only"]))
        out.append("")

    if nums and (founder or state.get("share_numbers_with_staff")):
        out.append("*Numbers*")
        for n in nums:
            unit = _fmt_unit(n["unit"])
            out.append(f"• {n['department']} — {n['label']}: {_fmt_num(n['value'])}{unit} ({_fmt_change(n['change'])})")
        out.append("")

    blockers = [(d["name"], b) for d in depts for b in (reports.get(d["id"]) or {}).get("blockers", [])]
    if founder and (stuck or blockers):
        out.append("*Stuck radar*")
        for s in stuck:
            out.append(f"🔴 {s['department']}: {s['blocker']} — stuck {s['weeks']} weeks in a row")
        stuck_set = {(s["department"], s["blocker"]) for s in stuck}
        for name, b in blockers:
            if (name, b) not in stuck_set:
                out.append(f"🟠 {name}: {b}")
        out.append("")

    out.append(f"*Next week ({week_label(shift_week(key, 1))})*")
    any_plan = False
    for d in depts:
        plan = (reports.get(d["id"]) or {}).get("next_week", [])
        if plan:
            any_plan = True
            out.append(f"*{d['name']}*: " + "; ".join(plan))
    if not any_plan:
        out.append("No plans sent yet.")

    voice = voice_script(state, key, stuck, miss, review_all, nums)
    return {"week": key, "audience": audience, "text": "\n".join(out).strip(),
            "voice_script": voice if founder else "", "missing": [d["name"] for d in miss],
            "stuck": stuck, "numbers": nums, "promises": review_all}


def voice_script(state, key, stuck, miss, review_all, nums) -> str:
    depts = state.get("departments", [])
    reports = state.get("weeks", {}).get(key, {}).get("reports", {})
    parts = [f"Here's your week at {state.get('company') or 'the company'}."]
    parts.append(f"{len(depts) - len(miss)} of {len(depts)} teams reported"
                 + (f"; still waiting on {', '.join(d['name'] for d in miss)}." if miss else "."))
    total = sum(len(r) for r in review_all.values())
    kept = sum(1 for r in review_all.values() for x in r if x["status"] == "done")
    if total:
        parts.append(f"Teams kept {kept} of the {total} things they promised last week.")
    if stuck:
        s = stuck[0]
        b = s["blocker"].rstrip(".")
        parts.append(f"The thing to unblock: {s['department']} has been stuck on "
                     f"{b[:1].lower() + b[1:]} for {s['weeks']} weeks.")
    moved = [n for n in nums if n["change"] not in (None, 0)]
    if moved:
        n = max(moved, key=lambda x: abs(x["change"]) / (abs(x["last_week"]) or 1))
        direction = "up" if n["change"] > 0 else "down"
        parts.append(f"Biggest move: {n['department']}, {n['label'].lower()}, is {direction} to "
                     f"{_fmt_num(n['value'])}{_fmt_unit(n['unit'])}.")
    for d in depts:
        rep = reports.get(d["id"])
        if rep and rep.get("summary"):
            parts.append(f"{d['name']}: {_sentence(rep['summary'])}")
    parts.append("Say send when you're happy, and I'll post it to everyone.")
    text, words = [], 0
    for p in parts:
        n = len(p.split())
        if words + n > VOICE_WORD_LIMIT:
            if p.startswith("Say send"):
                text.append(p)
            continue
        text.append(p); words += n
    if not text[-1].startswith("Say send"):
        text.append("Say send when you're happy, and I'll post it to everyone.")
    return " ".join(text)


def status_text(state: dict, now: Optional[datetime] = None) -> str:
    local = now_in(state, now)
    key = week_key(local)
    depts = state.get("departments", [])
    if not depts:
        return "Steward isn't set up yet. Ask me to set up Steward with your departments and groups."
    reports = state.get("weeks", {}).get(key, {}).get("reports", {})
    lines = [f"*Steward status — {week_label(key)}*"]
    for d in depts:
        r = reports.get(d["id"])
        lines.append(f"{'✅' if r else '⏳'} {d['name']}" +
                     (f" — in {r['submitted_at'][11:16]} {r['submitted_at'][:10]}" if r else " — not yet"))
    sent = state.get("weeks", {}).get(key, {}).get("sent")
    lines.append(f"Weekly pack: {'sent ' + sent if sent else 'not sent yet'}")
    return "\n".join(lines)
