"""Turn Steward's settings into Hermes cron jobs.

Every message Steward sends goes out through Hermes's own scheduler
(``cronjob_manage``), so it works on any platform the gateway is connected to:
WhatsApp, Telegram, Slack, Microsoft Teams, Discord, Google Chat and more.

Job plan (company timezone):
  * check-in      one per department, agent job, CONTINUABLE (attach_to_session),
                  so the lead can answer by voice note, text, photo or file
  * nudge         one per department, no-LLM script: prints only if that team
                  is still missing, outside quiet hours and off days
  * status        no-LLM script to the founder: who is in, who is not
  * pack          agent job to the founder, CONTINUABLE: weekly pack + voice brief,
                  the founder replies "send" to publish
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Dict, List

from . import core

PLUGIN_DIR = Path(__file__).resolve().parent

SCRIPT_TEMPLATE = '''# Written by the Steward Hermes plugin. Safe to delete; re-run steward_setup to recreate.
import importlib.util, sys
_spec = importlib.util.spec_from_file_location("steward_core", {core_path!r})
core = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(core)
state = core.load_state({state_path!r})
{body}
'''

NUDGE_BODY = "print(core.nudge_message(state, {dept!r}))"
STATUS_BODY = (
    "local = core.now_in(state)\n"
    "if not (core.in_quiet_hours(state, local) or core.is_off_day(state, local)):\n"
    "    print(core.status_text(state))"
)
CHECKIN_BODY = (
    "m = core.checkin_message(state, {dept!r})\n"
    "print(f\"Thanks {{m['department']}} — this week's update is already in. 🙏\" if m['already_reported'] "
    "else m['message'])"
)
# English founder pack without an LLM: pack text + a voice brief made with Hermes's own TTS,
# attached as a WhatsApp/Telegram voice note via the scheduler's MEDIA tag.
PACK_BODY = (
    "pack = core.build_pack(state, None, 'founder')\n"
    "out = pack['text'] + '\\n\\nReply *send* to post the staff version to everyone, or tell me what to change.'\n"
    "try:\n"
    "    import json, sys\n"
    "    sys.path.insert(0, {hermes_root!r})\n"
    "    import hermes_bootstrap  # noqa: F401  (Hermes's own dependency setup)\n"
    "    from tools.tts_tool import text_to_speech_tool\n"
    "    r = json.loads(text_to_speech_tool(pack['voice_script']))\n"
    "    path = r.get('file_path') or r.get('path') or (r.get('files') or [None])[0]\n"
    "    if path:\n"
    "        out += '\\n[[audio_as_voice]]\\nMEDIA:' + str(path)\n"
    "except Exception as e:\n"
    "    out += '\\n(voice brief unavailable: ' + type(e).__name__ + ')'\n"
    "print(out)"
)
PUBLISH_BODY = (
    "key = {week!r}\n"
    "bucket = state.get('weeks', {{}}).get(key, {{}})\n"
    "text = bucket.get('published_text') or ''\n"
    "print(text)"
)


def script_name(kind: str, extra: str = "") -> str:
    tail = f"_{re.sub(r'[^a-z0-9]+', '_', extra.lower())}" if extra else ""
    return f"steward_{kind}{tail}.py"


def write_script(scripts_dir: Path, name: str, state_path: Path, body: str) -> str:
    scripts_dir.mkdir(parents=True, exist_ok=True)
    src = SCRIPT_TEMPLATE.format(core_path=str(PLUGIN_DIR / "core.py"),
                                 state_path=str(state_path), body=body)
    (scripts_dir / name).write_text(src, encoding="utf-8")
    return name  # relative: Hermes resolves it under HERMES_HOME/scripts/


def _hermes_root() -> str:
    try:
        import hermes_constants  # public plugin API module; absent in plain unit tests
        return str(Path(hermes_constants.__file__).resolve().parent)
    except Exception:
        return ""


def _english(lang: str) -> bool:
    return core.is_english(lang or "")


def checkin_prompt(state: dict, dept: dict) -> str:
    return (
        f"You are Steward, chief of staff for {state.get('company') or 'the company'}. "
        f"Weekly check-in for the {dept['name']} department. "
        f"Call steward_checkin with department='{dept['id']}' and post its 'message' as your final "
        f"answer, following its post_instruction (it may ask you to translate it into the team's "
        f"language), nothing else. If already_reported is true, instead post one short line "
        f"thanking {dept.get('lead') or 'the team'} for sending this week's update early."
    )


def pack_prompt(state: dict) -> str:
    return (
        f"You are Steward, chief of staff for {state.get('company') or 'the company'}. "
        "Build this week's founder pack: call steward_build_pack with audience='founder'. "
        "Follow its post_instruction (it may ask you to translate into the founder's language). "
        "Then call text_to_speech with the voice_script (translated if asked) so the founder gets a "
        "short voice brief. Your final answer is the pack text, followed by one line in the same "
        "language: 'Reply *send* to post the staff version to everyone, or tell me what to change.'"
    )


def plan_jobs(state: dict, state_path: Path, scripts_dir: Path) -> List[dict]:
    """Return the cronjob_manage 'create' payloads (also writes the no-LLM scripts)."""
    s = state["schedule"]
    zone = state.get("timezone") or "UTC"
    jobs: List[dict] = []
    # errors never land in a staff group: they go to the founder, or stay local
    quiet_fail = state.get("founder_target") or "local"
    for d in state["departments"]:
        if not d.get("target"):
            continue
        checkin = {"action": "create", "name": f"Steward check-in · {d['name']}",
                   "schedule": core.cron_expr(s["checkin_day"], s["checkin_time"]), "timezone": zone,
                   "deliver": d["target"], "attach_to_session": True,
                   "failure_deliver": quiet_fail, "catch_up": True}
        if _english(d.get("language")):
            # same text every week: no LLM, nothing to get wrong, zero tokens
            checkin.update(no_agent=True, script=write_script(
                scripts_dir, script_name("checkin", d["id"]), state_path, CHECKIN_BODY.format(dept=d["id"])))
        else:
            checkin.update(prompt=checkin_prompt(state, d), skills=["steward:checkin"])
        jobs.append({"key": f"checkin:{d['id']}", "args": checkin})
        name = write_script(scripts_dir, script_name("nudge", d["id"]), state_path,
                            NUDGE_BODY.format(dept=d["id"]))
        jobs.append({"key": f"nudge:{d['id']}", "args": {
            "action": "create", "name": f"Steward nudge · {d['name']}",
            "schedule": core.cron_expr(s["nudge_day"], s["nudge_time"]), "timezone": zone,
            "script": name, "no_agent": True, "deliver": d["target"],
            "failure_deliver": quiet_fail, "catch_up": False}})
    if state.get("founder_target"):
        name = write_script(scripts_dir, script_name("status"), state_path, STATUS_BODY)
        jobs.append({"key": "status", "args": {
            "action": "create", "name": "Steward status · founder",
            "schedule": core.cron_expr(s["status_day"], s["status_time"]), "timezone": zone,
            "script": name, "no_agent": True, "deliver": state["founder_target"]}})
        pack = {"action": "create", "name": "Steward weekly pack · founder",
                "schedule": core.cron_expr(s["pack_day"], s["pack_time"]), "timezone": zone,
                "deliver": state["founder_target"], "attach_to_session": True}
        if not core.language_for(state, "founder"):
            pack.update(no_agent=True, script=write_script(
                scripts_dir, script_name("pack"), state_path, PACK_BODY.format(hermes_root=_hermes_root())))
        else:
            pack.update(prompt=pack_prompt(state), skills=["steward:weekly-pack"])
        jobs.append({"key": "pack", "args": pack})
    return jobs


def _parse(result) -> dict:
    if isinstance(result, dict):
        return result
    try:
        return json.loads(result)
    except Exception:
        return {"success": False, "error": str(result)[:300]}


def apply_jobs(state: dict, state_path: Path, scripts_dir: Path,
               dispatch: Callable[[str, dict], str]) -> Dict[str, list]:
    """Remove Steward's old jobs, then create the current plan. Records job ids in state."""
    removed, created, failed = [], [], []
    for key, job_id in list((state.get("jobs") or {}).items()):
        r = _parse(dispatch("cronjob_manage", {"action": "remove", "job_id": job_id}))
        (removed if r.get("success", True) and not r.get("error") else failed).append(
            {"job": key, "job_id": job_id, **({"error": r.get("error")} if r.get("error") else {})})
    state["jobs"] = {}
    for job in plan_jobs(state, state_path, scripts_dir):
        r = _parse(dispatch("cronjob_manage", job["args"]))
        if r.get("success") and r.get("job_id"):
            state["jobs"][job["key"]] = r["job_id"]
            created.append({"job": job["key"], "job_id": r["job_id"], "name": job["args"]["name"]})
        else:
            failed.append({"job": job["key"], "error": r.get("error") or "unknown error"})
    return {"removed": removed, "created": created, "failed": failed}


def publish_job(state: dict, week: str, state_path: Path, scripts_dir: Path,
                dispatch: Callable[[str, dict], str]) -> dict:
    """One-shot no-LLM job that posts the approved staff pack to the all-staff group."""
    name = write_script(scripts_dir, script_name("publish"), state_path, PUBLISH_BODY.format(week=week))
    return _parse(dispatch("cronjob_manage", {
        "action": "create", "name": f"Steward publish · {week}", "schedule": "in 1m",
        "script": name, "no_agent": True, "deliver": state["all_staff_target"],
        "failure_deliver": state.get("founder_target") or "local"}))
