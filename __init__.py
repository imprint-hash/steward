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
import re
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


def _week_arg(args: dict, state: dict) -> str:
    """The model-supplied week (validated) or this week. An empty string is refused, not defaulted."""
    week = args.get("week")
    if week is None:
        return core.week_key(core.now_in(state))
    return core.validate_week(week)


def _ok(**data) -> str:
    return json.dumps({"success": True, **data}, ensure_ascii=False, default=str)


def _err(msg: str, **data) -> str:
    return json.dumps({"success": False, "error": msg, **data}, ensure_ascii=False)


def _dispatch(name: str, args: dict) -> str:
    if _CTX is None:
        raise RuntimeError("Steward is not registered with Hermes")
    return _CTX.dispatch_tool(name, args)


# ─── who is asking (publish / cron safety) ─────────────────────────────────────

# The trusted operator is a person at this machine: CLI, TUI, desktop app or the gateway's LOCAL platform.
# Webhooks, the API server, Microsoft Graph webhooks and cron runs are NOT the operator. An empty
# platform and source (plain unit tests, Hermes not importable) counts as local only outside cron.
_OPERATOR_SURFACES = frozenset({"", "cli", "tui", "desktop", "local"})
_PRIVATE_CHAT_TYPES = frozenset({"dm", "private", "direct"})
_GROUP_CHAT_TYPES = frozenset({"group", "supergroup", "channel", "forum", "guild", "thread"})
_TRUTHY = frozenset({"1", "true", "yes", "on"})
NOT_FOUNDER = "Only the founder can publish: reply send in the founder's private chat with Steward."
NOT_FOUNDER_SETUP = ("Only the founder can change Steward's setup: do it in the founder's private chat "
                     "with Steward or on the computer running Hermes.")
NOT_FOUNDER_PACK = ("The founder pack, past weeks and status are only for the founder: ask in the "
                    "founder's private chat with Steward. Anyone can see the staff version.")
FOUNDER_TARGET_LOCAL_ONLY = ("The founder chat can only be set or changed on the computer running Hermes "
                             "(hermes CLI or desktop app), not from a chat.")
_BAD_TARGET_PLATFORMS = frozenset({"all", "local", "origin", "room"})
_TARGET_RE = re.compile(r"[a-z_]+:[^,\s]+")
MAX_ATTACHMENTS = 10
MAX_PUBLISH_CHARS = 6000
_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>()\[\]\"'*_`]+", re.IGNORECASE)


def _get_session_env(name: str) -> str:
    """Hermes' per-turn session value; "" when Hermes isn't importable (plain tests = local)."""
    try:
        from gateway.session_context import get_session_env
    except Exception:
        return ""
    return str(get_session_env(name, "") or "")


def _load_hermes_config() -> dict:
    from hermes_cli.config import load_config
    return load_config() or {}


def _session_identity() -> dict:
    keys = {"platform": "PLATFORM", "chat_id": "CHAT_ID", "user_id": "USER_ID",
            "user_id_alt": "USER_ID_ALT", "chat_type": "CHAT_TYPE", "source": "SOURCE"}
    return {k: _get_session_env(f"HERMES_SESSION_{v}").strip() for k, v in keys.items()}


def _norm_id(value: str) -> str:
    """Lower-case a chat/user id; for jids drop the device suffix ("234:12@s.whatsapp.net")."""
    v = str(value or "").strip().lower()
    if "@" in v:
        user, domain = v.split("@", 1)
        v = f"{user.split(':', 1)[0]}@{domain}"
    return v


def _same_id(a: str, b: str) -> bool:
    a, b = _norm_id(a), _norm_id(b)
    if not a or not b:
        return False
    if a == b:
        return True
    if ("@" in a) != ("@" in b):   # bare digits vs full jid
        return a.split("@", 1)[0] == b.split("@", 1)[0]
    return False


def _in_cron() -> bool:
    return _get_session_env("HERMES_CRON_SESSION").strip().lower() in _TRUTHY


def _is_operator() -> bool:
    """A person at the machine running Hermes (CLI, TUI, desktop, LOCAL). Never a cron run."""
    if _in_cron():
        return False
    s = _session_identity()
    return s["platform"].lower() in _OPERATOR_SURFACES and s["source"].lower() in _OPERATOR_SURFACES


def _in_group(s: dict) -> bool:
    return s["chat_type"].lower() in _GROUP_CHAT_TYPES or s["chat_id"].lower().endswith("@g.us")


def _require_founder(state: dict, msg: str = NOT_FOUNDER) -> None:
    """Allow the local operator, or the founder in their own private chat; refuse everything else
    (groups, other people's chats, webhooks, the API server and every cron run)."""
    if _is_operator():
        return
    if _in_cron():
        raise ValueError(msg)
    s = _session_identity()
    platform = s["platform"].lower()
    founder = str(state.get("founder_target") or "").strip()
    f_platform, _, f_id = founder.partition(":")
    chat_type, chat_id = s["chat_type"].lower(), s["chat_id"]
    if not founder or not f_id or _in_group(s):
        raise ValueError(msg)
    if f"{platform}:{chat_id}" == founder:
        return
    if (platform == f_platform.lower() and chat_type in _PRIVATE_CHAT_TYPES
            and any(_same_id(f_id, x) for x in (chat_id, s["user_id"], s["user_id_alt"]))):
        return
    raise ValueError(msg)


def _is_founder(state: dict) -> bool:
    try:
        _require_founder(state)
        return True
    except ValueError:
        return False


def _target_is_this_chat(target: str, s: dict) -> bool:
    """Does a 'platform:chat_id[:thread]' target point at the chat this turn came from?"""
    t_platform, _, rest = str(target or "").strip().partition(":")
    if not rest or t_platform.lower() != s["platform"].lower():
        return False
    ids = {rest}
    if ":" in rest and "@" not in rest:   # telegram:-100123:45 -> chat -100123, thread 45
        ids.add(rest.split(":", 1)[0])
    return any(_same_id(i, s["chat_id"]) for i in ids)


def _chat_dept(state: dict, s: dict):
    """The department whose group this turn came from, or None."""
    for d in state.get("departments") or []:
        if d.get("target") and _target_is_this_chat(d["target"], s):
            return d
    return None


def _cron_job_id() -> str:
    """Id of the Hermes cron job running right now ("" outside cron). Set by Hermes, not the model."""
    try:
        if _CTX is not None and hasattr(_CTX, "current_cron_execution"):
            ex = _CTX.current_cron_execution()
        else:
            from cron.execution_identity import current_cron_execution
            ex = current_cron_execution()
    except Exception:
        return ""
    return str(getattr(ex, "job_id", "") or "")


def _is_steward_pack_job(state: dict) -> bool:
    """True inside Steward's own weekly-pack cron job, which delivers only to the founder chat."""
    if not _in_cron():
        return False
    job = str((state.get("jobs") or {}).get("pack") or "")
    return bool(job) and _cron_job_id() == job


def _check_target(field: str, value) -> str:
    """A delivery target must be one concrete chat: 'platform:chat_id' (optionally ':thread')."""
    raw = str(value or "")
    v = raw.strip()
    platform = v.split(":", 1)[0]
    if (not _TARGET_RE.fullmatch(v) or not v.isprintable() or any(c in raw for c in "\r\n\t\v\f")
            or platform in _BAD_TARGET_PLATFORMS or platform.startswith("bot-chat")):
        raise ValueError(f"{field} must be one chat like 'whatsapp:120363...@g.us' or "
                         f"'telegram:-100123' (no commas, spaces, 'all', 'local' or 'origin'); got {v!r}")
    return v


def _check_encodable(obj, what: str) -> None:
    try:
        json.dumps(obj, ensure_ascii=False).encode("utf-8")
    except (UnicodeError, TypeError, ValueError):
        raise ValueError(f"{what} contains characters that can't be saved; retype it and try again")


def _cron_scheduling_blocked() -> bool:
    """Inside a cron run Hermes withholds its scheduler unless cron.allow_agent_scheduling is on."""
    if not _in_cron():
        return False
    try:
        allowed = (_load_hermes_config().get("cron") or {}).get("allow_agent_scheduling")
    except Exception:
        return True   # fail closed
    return not (allowed is True or str(allowed).strip().lower() in _TRUTHY)


def _require_cron_scheduling(what: str) -> None:
    if _cron_scheduling_blocked():
        raise ValueError(f"{what} needs Hermes' scheduler, which is off inside cron runs; turn on "
                         "cron.allow_agent_scheduling in config.yaml or run it from a chat")


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

def _authorize_setup(state: dict, args: dict) -> None:
    """Who may change Steward's settings.

    * local operator: anything, including the first setup and the founder chat
    * the founder in their private chat (or a cron run with cron.allow_agent_scheduling on):
      everything except the founder chat, and only once a founder chat exists
    * anyone else (group members, other DMs, webhooks, API server): nothing
    """
    if _is_operator():
        return
    founder = str(state.get("founder_target") or "").strip()
    if not founder:
        raise ValueError("First-time setup has to be done on the computer running Hermes "
                         "(hermes CLI or desktop app), where the founder chat is set.")
    if not _in_cron():   # a cron run got here only because cron.allow_agent_scheduling is on
        _require_founder(state, NOT_FOUNDER_SETUP)
    new = args.get("founder_target")
    if new is not None and str(new).strip() and str(new).strip() != founder:
        raise ValueError(FOUNDER_TARGET_LOCAL_ONLY)


def _validate_setup_targets(args: dict) -> None:
    for k in ("founder_target", "all_staff_target"):
        if args.get(k):
            _check_target(k, args[k])
    depts = args.get("departments")
    if depts is not None:
        if not isinstance(depts, list):
            raise ValueError("departments must be a list")
        for d in depts:
            if isinstance(d, dict) and str(d.get("target") or "").strip():
                _check_target(f"target for {str(d.get('name') or '?')[:40]!r}", d["target"])


@_guard
def setup(args, **kw):
    schedule = args.get("schedule_jobs", True)
    if _cron_scheduling_blocked():
        raise ValueError("Steward's setup can't be changed inside a cron run unless "
                         "cron.allow_agent_scheduling is on in config.yaml; change it from the "
                         "founder's private chat or the Hermes CLI")
    _check_encodable(args, "the setup")
    path = _state_path()
    state = core.load_state(path)
    _authorize_setup(state, args)
    _validate_setup_targets(args)
    core.configure(state, args)
    if not state["departments"]:
        raise ValueError("add at least one department, e.g. {name: 'Site Operations', "
                         "lead: 'Tunde', target: 'whatsapp:1203630...@g.us'}")
    _check_encodable(state, "the setup")
    jobs = None
    if schedule:
        # dry run first: any bad value fails here, before a single old job is removed
        _check_encodable(scheduler.plan_jobs(state, path, _scripts_dir()), "the schedule")
    core.save_state(path, state)
    if schedule:
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
    s = _session_identity()
    if not _in_cron() and _in_group(s) and not _is_founder(state):
        here, asked = _chat_dept(state, s), core.find_dept(state, args.get("department", ""))
        if not here or here["id"] != asked["id"]:
            raise ValueError("In a group, Steward only shows the check-in for that group's own department"
                             + (f" ({here['name']})." if here else "."))
    return _ok(**core.checkin_message(state, args.get("department", "")))


@_guard
def read_file(args, **kw):
    info = files.read_report_file(args.get("path", ""), _home())
    return _ok(**info, next_step="Turn this into the report fields and call steward_record_report, "
               "passing this file in attachments. Ask the sender only about what's unclear.")


@_guard
def record(args, **kw):
    path = _state_path()
    state = core.load_state(path)
    week = _week_arg(args, state)
    dept = core.find_dept(state, args.get("department", ""))
    if not _is_founder(state):
        # a group member may only file their own department's update, for this week or last week
        s = _session_identity()
        here = _chat_dept(state, s)
        if not here:
            raise ValueError("Department updates can only be saved from that department's own group "
                             "(or by the founder).")
        if here["id"] != dept["id"]:
            raise ValueError(f"This is the {here['name']} group: it can only save the {here['name']} "
                             f"update, not {dept['name']}'s. {dept['name']} sends theirs in their own group.")
        this_week = core.week_key(core.now_in(state))
        if week not in (this_week, core.shift_week(this_week, -1)):
            raise ValueError(f"From a group, updates can only be saved for this week ({this_week}) or "
                             "last week; older or future weeks are for the founder.")
    raw = args.get("attachments") or []
    if not isinstance(raw, list):
        raise ValueError("attachments must be a list")
    kept = []
    for a in raw[:MAX_ATTACHMENTS]:
        if isinstance(a, str):
            a = {"path": a}
        src = a.get("path")
        if not src:
            continue
        try:
            copy = files.keep_copy(src, path.parent / "files" / week / dept["id"], _home())
        except OSError as e:
            raise ValueError(f"couldn't keep a copy of {src}: {e}")
        name = Path(src).name
        kind = a.get("kind") or ("photo" if Path(src).suffix.lower() in files.IMAGE_EXT
                                 else Path(src).suffix.lstrip(".") or "file")
        kept.append({"file": name, "kind": kind, "path": copy})
    report = {k: args.get(k) for k in ("summary", "done", "blockers", "next_week", "numbers",
                                        "promise_updates", "founder_only", "language", "source")}
    report["attachments"] = kept
    result = core.record_report(state, dept["id"], report, week=week,
                                add_to_existing=bool(args.get("add_to_existing")))
    core.save_state(path, state)
    fix = []
    entry = result["report"]
    if not entry["done"] and not entry["next_week"]:
        fix.append("done and next_week are empty: split the update into the lists and call again")
    if any(ch.isalpha() and ord(ch) > 0x24F   # non-Latin script: Chinese, Hindi, Arabic...
           for ch in entry["summary"] + " ".join(entry["done"] + entry["next_week"] + entry["blockers"])):
        fix.append("fields must be in English: translate them and call again (keep `language`)")
    left = [d["name"] for d in core.missing(state, result["week"])]
    return _ok(week=result["week"], department=result["department"], updated=result["updated"],
               still_waiting_on=left, **({"fix_and_resave": fix} if fix else {}),
               reply_hint=f"Thank {dept.get('lead') or 'them'} in one short line and read back the "
                          "2–3 key points so they can correct anything.")


@_guard
def status(args, **kw):
    state = core.load_state(_state_path())
    _require_founder(state, NOT_FOUNDER_PACK)
    key = core.week_key(core.now_in(state))
    return _ok(week=key, text=core.status_text(state),
               missing=[d["name"] for d in core.missing(state, key)])


@_guard
def build_pack(args, **kw):
    state = core.load_state(_state_path())
    audience = args.get("audience", "founder")
    explicit_week = args.get("week") is not None
    if audience != "staff" or explicit_week:
        # Steward's own weekly-pack cron job may build this week's founder pack: it only
        # ever delivers to the founder chat, which only the local operator can set.
        if not (audience == "founder" and not explicit_week and _is_steward_pack_job(state)):
            _require_founder(state, NOT_FOUNDER_PACK)
    pack = core.build_pack(state, _week_arg(args, state), audience)
    return _ok(**pack)


@_guard
def publish(args, **kw):
    if not args.get("confirm"):
        raise ValueError("publishing needs the founder's approval: call again with confirm=true "
                         "only after the founder said send")
    if _in_cron():
        raise ValueError("Publishing never runs inside a cron run, even with "
                         "cron.allow_agent_scheduling on: the founder has to reply send in their "
                         "private chat with Steward.")
    path = _state_path()
    state = core.load_state(path)
    _require_founder(state)
    if not state.get("all_staff_target"):
        raise ValueError("no all-staff group set; run steward_setup with all_staff_target first")
    key = _week_arg(args, state)
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
        foreign = sorted({u.rstrip(".,;:!?") for u in _URL_RE.findall(text)}
                         - {u.rstrip(".,;:!?") for u in _URL_RE.findall(pack["text"])})
        if foreign:
            raise ValueError("the translated text has links that aren't in the staff pack: "
                             f"{', '.join(foreign[:3])}. Translate the pack only; add nothing.")
        if len(text) > MAX_PUBLISH_CHARS:
            raise ValueError(f"the staff text is {len(text)} characters; keep it under "
                             f"{MAX_PUBLISH_CHARS} (translate the pack only, add nothing)")
    text = text[:MAX_PUBLISH_CHARS]
    _check_encodable(text, "the staff text")
    bucket["published_text"] = text
    core.save_state(path, state)
    r = scheduler.publish_job(state, key, path, _scripts_dir(), _dispatch)
    if not r.get("success"):
        raise ValueError(f"couldn't schedule the post: {r.get('error')}")
    bucket["sent"] = core.now_in(state).isoformat(timespec="minutes")
    core.save_state(path, state)
    return _ok(week=key, to=state["all_staff_target"], job_id=r.get("job_id"),
               note="Posting to the all-staff group within a minute. This is exactly what will be "
                    "posted: show it to the founder.", preview=text, posted_text=text)


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
            "says to use vision_analyze. Use the saved path from the attachment note. Only files "
            "people sent in the chat (Hermes's incoming media folders) can be read."),
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}, "📎"),
    "steward_record_report": ({
        "name": "steward_record_report",
        "description": (
            "Save a department's weekly update after you've heard it (voice note, text, photo or file). "
            "ALWAYS write every field in English, even if they spoke French, Chinese, Hindi or any other "
            "language (set `language` to what they spoke). Split the update into the lists: what got done "
            "-> done, what's stuck -> blockers, the plan -> next_week; summary is only 1-2 sentences. "
            "promise_updates = how each of last week's planned items went. founder_only = anything "
            "they asked to keep private (shown to the founder, never to all staff). Re-saving the same "
            "week replaces the earlier report unless add_to_existing=true."),
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
            "add_to_existing": {"type": "boolean", "description": "true when this message adds to an update already saved this week (e.g. a photo after a voice note): lists are merged instead of replaced"},
        }, "required": ["department"]}}, "📝"),
    "steward_status": ({
        "name": "steward_status",
        "description": "Which departments have sent this week's update and which haven't.",
        "parameters": {"type": "object", "properties": {}}}, "📊"),
    "steward_build_pack": ({
        "name": "steward_build_pack",
        "description": (
            "Build the weekly pack. ALWAYS follow the returned post_instruction: when it says translate, "
            "your message to the reader must be fully in that language (headings included). Contents: last week per department, promised vs done, stuck radar (blockers "
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


def _on_pre_llm_call(**kw):
    """Per-turn note on chat platforms so replies in department groups get recorded."""
    try:
        return core.turn_context(core.load_state(_state_path()), kw.get("platform") or "") or None
    except Exception:
        return None


def register(ctx) -> None:
    global _CTX
    _CTX = ctx
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
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
