"""Steward — the chief of staff for your team chats.

Every week Steward asks each department for its update in its own group (voice
note, text, photo or file), chases only the teams that haven't replied, and gives
the founder one weekly pack — last week, promised vs done, what's stuck, numbers,
next week's agenda — plus a 60-second voice brief. The founder replies "send" and
the staff version goes to the all-staff group.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from . import core, files, scheduler

logger = logging.getLogger(__name__)
PLUGIN_DIR = Path(__file__).resolve().parent
TOOLSET = "steward"
_LOCK = threading.RLock()
_CTX = None


# ─── paths and helpers ─────────────────────────────────────────────────────────

def _home() -> Path:
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home())


def _state_path() -> Path:
    return _home() / "steward" / "state.json"


def _scripts_dir() -> Path:
    return _home() / "scripts"


def _ok(**data) -> str:
    return json.dumps({"success": True, **data}, ensure_ascii=False, default=str)


def _err(msg: str, **data) -> str:
    return json.dumps({"success": False, "error": msg, **data}, ensure_ascii=False)


def _dispatch(name: str, args: dict) -> str:
    if _CTX is None:
        raise RuntimeError("Steward is not registered with Hermes")
    return _CTX.dispatch_tool(name, args)


def _guard(fn):
    def wrapper(args, **kw):
        try:
            with _LOCK:
                return fn(args or {}, **kw)
        except (ValueError, KeyError, FileNotFoundError) as e:
            return _err(str(e).strip("'\""))
        except Exception as e:  # never crash the agent loop
            logger.exception("steward tool failed")
            return _err(f"Steward hit an unexpected error: {e}")
    wrapper.__name__ = fn.__name__
    return wrapper


# ─── tool handlers ─────────────────────────────────────────────────────────────

@_guard
def setup(args, **kw):
    path = _state_path()
    state = core.load_state(path)
    core.configure(state, args)
    if not state["departments"]:
        raise ValueError("add at least one department, e.g. {name: 'Site Operations', "
                         "lead: 'Tunde', target: 'whatsapp:1203630...@g.us'}")
    jobs = None
    if args.get("schedule_jobs", True):
        jobs = scheduler.apply_jobs(state, path, _scripts_dir(), _dispatch)
    core.save_state(path, state)
    no_target = [d["name"] for d in state["departments"] if not d.get("target")]
    notes = []
    if no_target:
        notes.append(f"No group set for: {', '.join(no_target)} — they won't get check-ins yet.")
    if not state.get("founder_target"):
        notes.append("No founder chat set — the weekly pack and status won't be delivered.")
    if not state.get("all_staff_target"):
        notes.append("No all-staff group set — 'send' can't publish the pack yet.")
    if jobs and jobs["failed"]:
        return _err(f"settings saved, but {len(jobs['failed'])} schedule(s) failed — "
                    "tell the user and show the errors", jobs=jobs, notes=notes)
    return _ok(company=state["company"], timezone=state["timezone"],
               departments=state["departments"], schedule=state["schedule"],
               quiet_hours=state["quiet_hours"], off_days=state["off_days"],
               jobs=jobs, notes=notes)


@_guard
def checkin(args, **kw):
    state = core.load_state(_state_path())
    return _ok(**core.checkin_message(state, args.get("department", "")))


@_guard
def read_file(args, **kw):
    info = files.read_report_file(args.get("path", ""))
    return _ok(**info, next_step="Turn this into the report fields and call steward_record_report, "
               "passing this file in attachments. Ask the sender only about what's unclear.")


@_guard
def record(args, **kw):
    path = _state_path()
    state = core.load_state(path)
    dept = core.find_dept(state, args.get("department", ""))
    week = args.get("week") or core.week_key(core.now_in(state))
    kept = []
    for a in args.get("attachments") or []:
        if isinstance(a, str):
            a = {"path": a}
        src = a.get("path")
        if not src:
            continue
        try:
            copy = files.keep_copy(src, path.parent / "files" / week / dept["id"])
        except OSError as e:
            raise ValueError(f"couldn't keep a copy of {src}: {e}")
        name = Path(src).name
        kind = a.get("kind") or ("photo" if Path(src).suffix.lower() in files.IMAGE_EXT
                                 else Path(src).suffix.lstrip(".") or "file")
        kept.append({"file": name, "kind": kind, "path": copy})
    report = {k: args.get(k) for k in ("summary", "done", "blockers", "next_week", "numbers",
                                        "promise_updates", "founder_only", "language", "source")}
    report["attachments"] = kept
    result = core.record_report(state, dept["id"], report, week=args.get("week"))
    core.save_state(path, state)
    left = [d["name"] for d in core.missing(state, result["week"])]
    return _ok(week=result["week"], department=result["department"], updated=result["updated"],
               still_waiting_on=left,
               reply_hint=f"Thank {dept.get('lead') or 'them'} in one short line and read back the "
                          "2–3 key points so they can correct anything.")


@_guard
def status(args, **kw):
    state = core.load_state(_state_path())
    key = core.week_key(core.now_in(state))
    return _ok(week=key, text=core.status_text(state),
               missing=[d["name"] for d in core.missing(state, key)])


@_guard
def build_pack(args, **kw):
    state = core.load_state(_state_path())
    pack = core.build_pack(state, args.get("week"), args.get("audience", "founder"))
    return _ok(**pack)


@_guard
def publish(args, **kw):
    if not args.get("confirm"):
        raise ValueError("publishing needs the founder's approval: call again with confirm=true "
                         "only after the founder said send")
    path = _state_path()
    state = core.load_state(path)
    if not state.get("all_staff_target"):
        raise ValueError("no all-staff group set; run steward_setup with all_staff_target first")
    key = args.get("week") or core.week_key(core.now_in(state))
    bucket = core.week_bucket(state, key)
    if bucket.get("sent") and not args.get("resend"):
        raise ValueError(f"this week's pack was already sent at {bucket['sent']}; pass resend=true to send again")
    pack = core.build_pack(state, key, "staff")
    text = pack["text"]
    if core.language_for(state, "staff"):
        text = str(args.get("text") or "").strip()
        if not text:
            raise ValueError(f"all staff read {pack['language']}: call steward_build_pack with "
                             "audience='staff', translate its text as post_instruction says, and pass "
                             "it here as text")
    bucket["published_text"] = text[:6000]
    core.save_state(path, state)
    r = scheduler.publish_job(state, key, path, _scripts_dir(), _dispatch)
    if not r.get("success"):
        raise ValueError(f"couldn't schedule the post: {r.get('error')}")
    bucket["sent"] = core.now_in(state).isoformat(timespec="minutes")
    core.save_state(path, state)
    return _ok(week=key, to=state["all_staff_target"], job_id=r.get("job_id"),
               note="Posting to the all-staff group within a minute.", preview=text)


# ─── slash command ─────────────────────────────────────────────────────────────

HELP = """*Steward* — your chief of staff
/steward status — who has sent this week's update
/steward pack — preview the founder pack
/steward staff — preview what all staff will see
/steward send — post the staff pack to the all-staff group
/steward checkin <department> — show the check-in message"""


def _slash(raw: str):
    parts = (raw or "").strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else "help"
    rest = parts[1] if len(parts) > 1 else ""
    if sub == "status":
        r = json.loads(status({}))
        return r.get("text") or r.get("error")
    if sub in ("pack", "staff"):
        r = json.loads(build_pack({"audience": "founder" if sub == "pack" else "staff"}))
        return r.get("text") or r.get("error")
    if sub == "send":
        r = json.loads(publish({"confirm": True}))
        return (r.get("note") if r.get("success") else f"Not sent: {r.get('error')}")
    if sub == "checkin" and rest:
        r = json.loads(checkin({"department": rest}))
        return r.get("message") or r.get("error")
    return HELP


# ─── schemas ───────────────────────────────────────────────────────────────────

_STR_LIST = {"type": "array", "items": {"type": "string"}}

SCHEMAS = {
    "steward_setup": ({
        "name": "steward_setup",
        "description": (
            "Set up or change Steward, the weekly department-report chief of staff. Saves the company, "
            "departments and their group chats, and schedules: a check-in in each department group, "
            "a nudge only to teams that haven't replied, a status note and a weekly pack (with voice "
            "brief) to the founder. Re-running replaces Steward's old jobs. Chat targets use Hermes "
            "delivery format 'platform:chat_id' (e.g. 'whatsapp:120363...@g.us', 'telegram:-100123', "
            "'slack:C0123'). Ask the user for anything missing; don't invent chat ids."),
        "parameters": {"type": "object", "properties": {
            "company": {"type": "string"},
            "timezone": {"type": "string", "description": "IANA zone, e.g. 'Africa/Lagos'"},
            "founder_target": {"type": "string", "description": "Founder's private chat (gets status and the pack)"},
            "all_staff_target": {"type": "string", "description": "All-staff group for the approved pack"},
            "departments": {"type": "array", "items": {"type": "object", "properties": {
                "name": {"type": "string"}, "lead": {"type": "string"},
                "target": {"type": "string", "description": "The department's group chat"},
                "language": {"type": "string", "description": "Language the team prefers, e.g. 'French', 'Hindi'. Default English."}},
                "required": ["name"]}},
            "checkin_day": {"type": "string"}, "checkin_time": {"type": "string", "description": "HH:MM"},
            "nudge_day": {"type": "string"}, "nudge_time": {"type": "string"},
            "status_day": {"type": "string"}, "status_time": {"type": "string"},
            "pack_day": {"type": "string"}, "pack_time": {"type": "string"},
            "quiet_hours": {"type": "object", "properties": {
                "start": {"type": "string"}, "end": {"type": "string"}},
                "description": "No nudges or status in this window, e.g. {start: '19:00', end: '08:00'}"},
            "off_days": {**_STR_LIST, "description": "Days with no nudges, e.g. ['sun']"},
            "founder_language": {"type": "string", "description": "Language for the founder's pack and voice brief, e.g. 'French', 'Chinese'. Default English."},
            "staff_language": {"type": "string", "description": "Language of the all-staff update. Default: same as founder_language."},
            "share_numbers_with_staff": {"type": "boolean", "description": "Default false: numbers appear only in the founder pack."},
            "schedule_jobs": {"type": "boolean", "description": "Default true. False = save settings only."},
        }}}, "📋"),
    "steward_checkin": ({
        "name": "steward_checkin",
        "description": "Get this week's check-in message for a department, including what they promised last week.",
        "parameters": {"type": "object", "properties": {"department": {"type": "string"}},
                       "required": ["department"]}}, "🗣️"),
    "steward_read_file": ({
        "name": "steward_read_file",
        "description": (
            "Read a report someone sent as a file instead of a voice note: Word (.docx), Excel (.xlsx), "
            "CSV, text or PDF. Returns its text. For photos (site diary, whiteboard, printed sheet) it "
            "says to use vision_analyze. Use the saved path from the attachment note."),
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}, "📎"),
    "steward_record_report": ({
        "name": "steward_record_report",
        "description": (
            "Save a department's weekly update after you've heard it (voice note, text, photo or file). "
            "Extract short plain sentences in English even if they spoke another language. "
            "promise_updates = how each of last week's planned items went. founder_only = anything "
            "they asked to keep private (shown to the founder, never to all staff). Re-saving the same "
            "week replaces the earlier report."),
        "parameters": {"type": "object", "properties": {
            "department": {"type": "string", "description": "Department name, id or its group's chat target"},
            "summary": {"type": "string", "description": "One or two sentences"},
            "done": _STR_LIST, "blockers": _STR_LIST, "next_week": _STR_LIST,
            "numbers": {"type": "array", "items": {"type": "object", "properties": {
                "label": {"type": "string"}, "value": {"type": "number"}, "unit": {"type": "string"}},
                "required": ["label", "value"]}},
            "promise_updates": {"type": "array", "items": {"type": "object", "properties": {
                "promise": {"type": "string"},
                "status": {"type": "string", "enum": list(core.PROMISE_STATUSES)},
                "note": {"type": "string"}}, "required": ["promise", "status"]}},
            "founder_only": _STR_LIST,
            "attachments": {"type": "array", "items": {"type": "object", "properties": {
                "path": {"type": "string"}, "kind": {"type": "string"}}, "required": ["path"]},
                "description": "Files or photos the report came in, so the founder can open them"},
            "language": {"type": "string", "description": "Language they used, e.g. 'Yoruba', 'Pidgin'"},
            "source": {"type": "string", "enum": ["voice", "text", "photo", "file"]},
            "week": {"type": "string", "description": "ISO week like 2026-W46; default this week"},
        }, "required": ["department"]}}, "📝"),
    "steward_status": ({
        "name": "steward_status",
        "description": "Which departments have sent this week's update and which haven't.",
        "parameters": {"type": "object", "properties": {}}}, "📊"),
    "steward_build_pack": ({
        "name": "steward_build_pack",
        "description": (
            "Build the weekly pack: last week per department, promised vs done, stuck radar (blockers "
            "repeated week after week), numbers with week-on-week change, next week's agenda, and a "
            "voice_script under 150 words for text_to_speech. audience 'founder' shows everything; "
            "'staff' hides blockers, private notes, who's late and (unless enabled) numbers."),
        "parameters": {"type": "object", "properties": {
            "audience": {"type": "string", "enum": ["founder", "staff"]},
            "week": {"type": "string"}}}}, "📦"),
    "steward_publish": ({
        "name": "steward_publish",
        "description": (
            "Post the staff version of this week's pack to the all-staff group. ONLY after the founder "
            "clearly approves (e.g. replies 'send'). Never on your own initiative."),
        "parameters": {"type": "object", "properties": {
            "confirm": {"type": "boolean"}, "resend": {"type": "boolean"}, "week": {"type": "string"},
            "text": {"type": "string", "description": "Only when staff use a language other than English: the staff pack translated per post_instruction"}},
            "required": ["confirm"]}}, "📣"),
}

HANDLERS = {
    "steward_setup": setup, "steward_checkin": checkin, "steward_read_file": read_file,
    "steward_record_report": record, "steward_status": status,
    "steward_build_pack": build_pack, "steward_publish": publish,
}


def register(ctx) -> None:
    global _CTX
    _CTX = ctx
    for name, (schema, emoji) in SCHEMAS.items():
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=HANDLERS[name],
                          description=schema["description"].split(".")[0], emoji=emoji)
    ctx.register_command("steward", handler=_slash, args_hint="status|pack|staff|send|checkin <dept>",
                         description="Steward: weekly department reports and the founder pack")
    for skill, desc in (("checkin", "Run a 2-minute weekly check-in with a department (voice, text, photo or file)"),
                        ("weekly-pack", "Build and deliver the founder's weekly pack and voice brief")):
        try:
            ctx.register_skill(skill, PLUGIN_DIR / "skills" / skill / "SKILL.md", description=desc)
        except Exception as e:
            logger.warning("steward: skill %s not registered: %s", skill, e)
    try:
        ctx.register_automation_blueprint(
            "weekly-reports", title="Weekly department reports (Steward)",
            description=("Collect every department's weekly update from its group chat and get one "
                         "founder pack with a voice brief. Run steward_setup first."),
            category="weekly", schedule_template="{minute} {hour} * * {dow}",
            prompt_template=scheduler.pack_prompt({"company": ""}),
            slots=[{"name": "time", "type": "time", "label": "What time?", "default": "10:00"},
                   {"name": "day", "type": "enum", "label": "Which day?", "default": "saturday",
                    "options": ["friday", "saturday", "sunday", "monday"]},
                   {"name": "deliver", "type": "enum", "label": "Where to deliver?",
                    "default": "origin", "options": ["origin", "whatsapp", "telegram", "slack"],
                    "strict": False}],
            skills=["steward:weekly-pack"], tags=["weekly", "team", "reports"])
    except Exception as e:
        logger.warning("steward: blueprint not registered: %s", e)
