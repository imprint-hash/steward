"""Run from the repo root:  python -m unittest discover -s tests -v"""
import json
import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
steward = __import__(ROOT.name)
core, files, scheduler = steward.core, steward.files, steward.scheduler

UTC = timezone.utc
# Thursday 12 Nov 2026 is in ISO week 2026-W46; Lagos is UTC+1
THIS_WEEK = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
LAST_WEEK = datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
TWO_AGO = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)


def company():
    s = core.empty_state()
    core.configure(s, {
        "company": "Adebayo Build Co", "timezone": "Africa/Lagos",
        "founder_target": "whatsapp:2348000000000@s.whatsapp.net",
        "all_staff_target": "whatsapp:120363000000000001@g.us",
        "departments": [
            {"name": "Site Operations", "lead": "Tunde", "target": "whatsapp:120363000000000002@g.us"},
            {"name": "Procurement", "lead": "Ngozi", "target": "whatsapp:120363000000000003@g.us"},
            {"name": "Safety", "lead": "Musa", "target": "whatsapp:120363000000000004@g.us"},
            {"name": "Finance", "lead": "Bisi", "language": "French"},
        ],
        "quiet_hours": {"start": "19:00", "end": "08:00"}, "off_days": ["sun"],
    })
    return s


class TimeTests(unittest.TestCase):
    def test_week_key_uses_company_timezone(self):
        s = company()
        # Sunday 23:30 UTC = Monday 00:30 in Lagos -> next ISO week
        late_sunday = datetime(2026, 11, 15, 23, 30, tzinfo=UTC)
        self.assertEqual(core.week_key(core.now_in(s, late_sunday)), "2026-W47")
        self.assertEqual(core.week_key(core.now_in(s, THIS_WEEK)), "2026-W46")

    def test_shift_week_across_year(self):
        self.assertEqual(core.shift_week("2026-W01", -1), "2025-W52")
        self.assertEqual(core.shift_week("2026-W53", 1), "2027-W01")

    def test_cron_expr(self):
        self.assertEqual(core.cron_expr("thu", "10:00"), "0 10 * * 4")
        self.assertEqual(core.cron_expr("sunday", "07:05"), "5 7 * * 0")
        with self.assertRaises(ValueError):
            core.cron_expr("thu", "25:00")

    def test_quiet_hours_wrap_midnight(self):
        s = company()
        self.assertTrue(core.in_quiet_hours(s, datetime(2026, 1, 1, 22, 0)))
        self.assertTrue(core.in_quiet_hours(s, datetime(2026, 1, 1, 7, 59)))
        self.assertFalse(core.in_quiet_hours(s, datetime(2026, 1, 1, 13, 0)))

    def test_setup_rejects_send_inside_quiet_hours(self):
        s = company()
        with self.assertRaises(ValueError):
            core.configure(s, {"pack_time": "21:00"})

    def test_setup_rejects_bad_timezone_and_duplicates(self):
        with self.assertRaises(ValueError):
            core.configure(core.empty_state(), {"timezone": "Lagos/Nowhere"})
        with self.assertRaises(ValueError):
            core.configure(core.empty_state(), {"departments": ["HR", "hr"]})


class ReportTests(unittest.TestCase):
    def test_record_validates_and_replaces(self):
        s = company()
        with self.assertRaises(ValueError):
            core.record_report(s, "Safety", {}, now=THIS_WEEK)
        with self.assertRaises(ValueError):
            core.record_report(s, "Safety", {"summary": "ok", "numbers": [{"label": "x", "value": "lots"}]}, now=THIS_WEEK)
        r1 = core.record_report(s, "Safety", {"summary": "No incidents"}, now=THIS_WEEK)
        r2 = core.record_report(s, "safety", {"summary": "No incidents, 2 toolbox talks",
                                               "numbers": [{"label": "Incidents", "value": "0"}]}, now=THIS_WEEK)
        self.assertFalse(r1["updated"]); self.assertTrue(r2["updated"])
        self.assertEqual(s["weeks"]["2026-W46"]["reports"]["safety"]["numbers"][0]["value"], 0)

    def test_none_fields_are_blank(self):
        s = company()
        r = \
            core.record_report(s, "Safety", {"summary": "ok", "language": None, "source": None}, now=THIS_WEEK)
        self.assertEqual(r["report"]["language"], ""); self.assertEqual(r["report"]["source"], "text")

    def test_find_by_group_target(self):
        s = company()
        d = core.find_dept(s, "whatsapp:120363000000000003@g.us")
        self.assertEqual(d["name"], "Procurement")
        with self.assertRaises(KeyError):
            core.find_dept(s, "Marketing")

    def test_missing_and_status(self):
        s = company()
        core.record_report(s, "Site Operations", {"summary": "Slab on level 2 poured"}, now=THIS_WEEK)
        self.assertEqual([d["name"] for d in core.missing(s, "2026-W46")], ["Procurement", "Safety", "Finance"])
        self.assertIn("✅ Site Operations", core.status_text(s, now=THIS_WEEK))


class NudgeTests(unittest.TestCase):
    def test_nudge_only_missing_and_respects_quiet_hours(self):
        s = company()
        core.record_report(s, "Site Operations", {"summary": "on track"}, now=THIS_WEEK)
        fri_1pm = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)   # 13:00 Lagos
        self.assertEqual(core.nudge_message(s, "Site Operations", now=fri_1pm), "")
        msg = core.nudge_message(s, "Procurement", now=fri_1pm)
        self.assertIn("Ngozi", msg); self.assertIn("1 of 4", msg)
        fri_9pm = datetime(2026, 11, 13, 20, 0, tzinfo=UTC)   # 21:00 Lagos
        self.assertEqual(core.nudge_message(s, "Procurement", now=fri_9pm), "")
        sunday = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
        self.assertEqual(core.nudge_message(s, "Procurement", now=sunday), "")


def three_weeks():
    s = company()
    core.record_report(s, "Procurement", {"summary": "Ordered steel",
        "blockers": ["Waiting on finance approval for the cement order"],
        "next_week": ["Get cement delivered to site"],
        "numbers": [{"label": "Cement bags received", "value": 200}]}, now=TWO_AGO)
    core.record_report(s, "Procurement", {"summary": "Chased approvals",
        "blockers": ["Still waiting for finance to approve cement order"],
        "next_week": ["Get cement delivered to site", "Book crane for level 3"],
        "numbers": [{"label": "Cement bags received", "value": 250}]}, now=LAST_WEEK)
    core.record_report(s, "Site Operations", {"summary": "Formwork level 2",
        "next_week": ["Pour slab on level 2"],
        "numbers": [{"label": "Overall progress", "value": 38, "unit": "%"}]}, now=LAST_WEEK)
    core.record_report(s, "Procurement", {"summary": "Cement finally ordered",
        "done": ["Crane booked for level 3"],
        "blockers": ["Cement order waiting finance approval"],
        "promise_updates": [{"promise": "Get cement delivered to site", "status": "not_done",
                             "note": "approval still pending"}],
        "next_week": ["Receive 400 bags of cement"],
        "numbers": [{"label": "Cement bags received", "value": 180}],
        "founder_only": ["Supplier hinted prices rise 8% next month"]}, now=THIS_WEEK)
    core.record_report(s, "Site Operations", {"summary": "Slab poured on level 2",
        "done": ["Poured slab on level 2"], "next_week": ["Start blockwork on level 2"],
        "numbers": [{"label": "Overall progress", "value": 42, "unit": "%"}],
        "attachments": [{"file": "site-diary.jpg", "kind": "photo", "path": "/x/site-diary.jpg"}]},
        now=THIS_WEEK)
    return s


class PackTests(unittest.TestCase):
    def test_promised_vs_done(self):
        s = three_weeks()
        proc = {r["promise"]: r["status"] for r in core.promise_review(s, "procurement", "2026-W46")}
        self.assertEqual(proc["Get cement delivered to site"], "not_done")   # explicit update
        self.assertEqual(proc["Book crane for level 3"], "done")             # matched from 'done'
        site = core.promise_review(s, "site-operations", "2026-W46")
        self.assertEqual(site[0]["status"], "done")

    def test_stuck_radar_counts_consecutive_weeks(self):
        s = three_weeks()
        stuck = core.stuck_radar(s, "2026-W46")
        self.assertEqual(len(stuck), 1)
        self.assertEqual(stuck[0]["department"], "Procurement")
        self.assertEqual(stuck[0]["weeks"], 3)
        self.assertEqual(stuck[0]["since"], "2026-W44")

    def test_numbers_change(self):
        s = three_weeks()
        rows = {r["label"]: r for r in core.numbers_table(s, "2026-W46")}
        self.assertEqual(rows["Cement bags received"]["change"], -70)
        self.assertEqual(rows["Overall progress"]["change"], 4)

    def test_founder_pack_has_everything(self):
        s = three_weeks()
        p = core.build_pack(s, "2026-W46", "founder")
        t = p["text"]
        for needle in ("Adebayo Build Co", "Reports in:* 2 of 4", "waiting on Safety, Finance",
                       "Promised vs done: 1 of 2", "stuck 3 weeks in a row", "▼ 70",
                       "For you only", "📎 site-diary.jpg", "Start blockwork on level 2"):
            self.assertIn(needle, t)
        self.assertLessEqual(len(p["voice_script"].split()), core.VOICE_WORD_LIMIT + 15)
        self.assertTrue(p["voice_script"].endswith("post it to everyone."))
        self.assertIn("stuck on cement order", p["voice_script"])
        self.assertIn("Formwork", core.build_pack(s, "2026-W45", "founder")["text"])
        self.assertNotIn("2 Procurement", p["voice_script"])  # sentences end with a full stop

    def test_founder_and_staff_languages(self):
        s = three_weeks()
        self.assertEqual(core.build_pack(s, "2026-W46", "founder")["post_instruction"], "Use the text as it is.")
        core.configure(s, {"founder_language": "Chinese"})
        p = core.build_pack(s, "2026-W46", "founder")
        self.assertEqual(p["language"], "Chinese"); self.assertIn("into Chinese", p["post_instruction"])
        self.assertIn("into Chinese", core.build_pack(s, "2026-W46", "staff")["post_instruction"])
        core.configure(s, {"staff_language": "English"})
        self.assertEqual(core.build_pack(s, "2026-W46", "staff")["language"], "English")

    def test_staff_pack_hides_private_things(self):
        s = three_weeks()
        t = core.build_pack(s, "2026-W46", "staff")["text"]
        for hidden in ("prices rise", "Stuck radar", "waiting on", "Promised vs done", "📎", "finance approval"):
            self.assertNotIn(hidden, t)
        self.assertIn("Slab poured on level 2", t)
        self.assertNotIn("Reports in", t); self.assertNotIn("Numbers", t)
        s["share_numbers_with_staff"] = True
        self.assertIn("42% (▲ 4)", core.build_pack(s, "2026-W46", "staff")["text"])
        self.assertEqual(core.build_pack(s, "2026-W46", "staff")["voice_script"], "")

    def test_checkin_lists_last_weeks_promises(self):
        s = three_weeks()
        m = core.checkin_message(s, "Procurement", now=THIS_WEEK)
        self.assertIn("Book crane for level 3", m["message"])
        self.assertTrue(m["already_reported"])
        self.assertIn("photo/file", m["message"])
        self.assertIn("Post the message as it is", m["post_instruction"])
        fin = core.checkin_message(s, "Finance", now=THIS_WEEK)
        self.assertEqual(fin["language"], "French"); self.assertIn("into French", fin["post_instruction"])


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _zip(self, name, members):
        p = self.tmp / name
        with zipfile.ZipFile(p, "w") as z:
            for k, v in members.items():
                z.writestr(k, v)
        return p

    def test_docx(self):
        W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        doc = (f'<w:document {W}><w:body><w:p><w:r><w:t>Weekly site report</w:t></w:r></w:p>'
               '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Item</w:t></w:r></w:p></w:tc>'
               '<w:tc><w:p><w:r><w:t>Status</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
        r = files.read_report_file(str(self._zip("report.docx", {"word/document.xml": doc})))
        self.assertIn("Weekly site report", r["text"]); self.assertIn("Item | Status", r["text"])

    def test_xlsx(self):
        S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        wb = f'<workbook {S}><sheets><sheet name="Week 46" sheetId="1"/></sheets></workbook>'
        ss = f'<sst {S}><si><t>Cement bags</t></si></sst>'
        sh = (f'<worksheet {S}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c>'
              '<c r="C1"><v>400</v></c></row></sheetData></worksheet>')
        r = files.read_report_file(str(self._zip("numbers.xlsx", {
            "xl/workbook.xml": wb, "xl/sharedStrings.xml": ss, "xl/worksheets/sheet1.xml": sh})))
        self.assertIn("Sheet: Week 46", r["text"]); self.assertIn("Cement bags |  | 400", r["text"])

    def test_csv_photo_and_unknown(self):
        c = self.tmp / "n.csv"; c.write_text("label,value\nIncidents,0\n")
        self.assertIn("Incidents | 0", files.read_report_file(str(c))["text"])
        ph = self.tmp / "diary.jpg"; ph.write_bytes(b"\xff\xd8\xff")
        r = files.read_report_file(str(ph))
        self.assertEqual(r["kind"], "photo"); self.assertIn("vision_analyze", r["hint"])
        with self.assertRaises(FileNotFoundError):
            files.read_report_file(str(self.tmp / "nope.pdf"))

    def test_keep_copy_no_overwrite(self):
        a = self.tmp / "r.txt"; a.write_text("one")
        dest = self.tmp / "kept"
        p1 = files.keep_copy(str(a), dest)
        a.write_text("two")
        p2 = files.keep_copy(str(a), dest)
        self.assertNotEqual(p1, p2)
        self.assertEqual(Path(p1).read_text(), "one")


class FakeCron:
    def __init__(self):
        self.calls, self.n = [], 0

    def __call__(self, name, args):
        self.calls.append((name, dict(args)))
        if args["action"] == "create":
            self.n += 1
            return json.dumps({"success": True, "job_id": f"job{self.n}"})
        return json.dumps({"success": True})


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_path = self.tmp / "steward" / "state.json"

    def test_plan_and_replace_jobs(self):
        s = company(); cron = FakeCron()
        out = scheduler.apply_jobs(s, self.state_path, self.tmp / "scripts", cron)
        # 3 depts with groups x (check-in + nudge) + founder status + founder pack
        self.assertEqual(len(out["created"]), 8)
        creates = [a for _, a in cron.calls if a["action"] == "create"]
        checkin = next(a for a in creates if a["name"].startswith("Steward check-in · Site"))
        self.assertEqual(checkin["schedule"], "0 10 * * 4")
        self.assertEqual(checkin["timezone"], "Africa/Lagos")
        self.assertTrue(checkin["attach_to_session"])
        self.assertEqual(checkin["failure_deliver"], s["founder_target"])
        nudge = next(a for a in creates if a["name"].startswith("Steward nudge · Safety"))
        self.assertTrue(nudge["no_agent"]); self.assertTrue((self.tmp / "scripts" / nudge["script"]).exists())
        self.assertFalse(any("Finance" in a["name"] for a in creates))  # no group set
        out2 = scheduler.apply_jobs(s, self.state_path, self.tmp / "scripts", cron)
        self.assertEqual(len(out2["removed"]), 8)
        self.assertEqual(set(s["jobs"].values()), {f"job{i}" for i in range(9, 17)})

    def test_nudge_script_runs_standalone(self):
        import subprocess
        s = company(); core.save_state(self.state_path, s)
        scheduler.apply_jobs(s, self.state_path, self.tmp / "scripts", FakeCron())
        script = self.tmp / "scripts" / scheduler.script_name("nudge", "procurement")
        out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)  # prints a nudge or nothing (quiet hours)


class ToolTests(unittest.TestCase):
    """The Hermes-facing handlers, with HERMES_HOME pointed at a temp dir and a fake cron."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._home = steward._home
        steward._home = lambda: self.tmp
        self.cron = FakeCron()

        class Ctx:
            dispatch_tool = staticmethod(self.cron)
        steward._CTX = Ctx()

    def tearDown(self):
        steward._home = self._home
        steward._CTX = None

    def call(self, name, **args):
        return json.loads(steward.HANDLERS[name](args))

    def test_full_week(self):
        r = self.call("steward_setup", company="Adebayo Build Co", timezone="Africa/Lagos",
                      founder_target="whatsapp:234800@s.whatsapp.net",
                      all_staff_target="whatsapp:1203@g.us",
                      departments=[{"name": "Site Operations", "lead": "Tunde", "target": "whatsapp:1@g.us"}])
        self.assertTrue(r["success"], r); self.assertEqual(len(r["jobs"]["created"]), 4)
        diary = self.tmp / "diary.jpg"; diary.write_bytes(b"\xff\xd8\xff")
        r = self.call("steward_record_report", department="whatsapp:1@g.us",
                      summary="Level 2 slab poured", done=["Poured slab"], source="photo",
                      language="Pidgin", attachments=[{"path": str(diary)}])
        self.assertTrue(r["success"], r)
        self.assertEqual(r["still_waiting_on"], [])
        week = r["week"]
        kept = self.tmp / "steward" / "files" / week / "site-operations" / "diary.jpg"
        self.assertTrue(kept.exists())
        self.assertFalse(self.call("steward_publish", confirm=False)["success"])
        r = self.call("steward_publish", confirm=True)
        self.assertTrue(r["success"], r)
        self.assertIn("Level 2 slab poured", r["preview"])
        self.assertFalse(self.call("steward_publish", confirm=True)["success"])  # no double send
        state = json.loads((self.tmp / "steward" / "state.json").read_text())
        self.assertTrue(state["weeks"][week]["sent"])
        self.assertIn("Level 2 slab poured", state["weeks"][week]["published_text"])

    def test_setup_reports_failed_schedules(self):
        steward._CTX.dispatch_tool = staticmethod(lambda n, a: json.dumps({"error": "Unknown tool: cronjob_manage"}))
        r = self.call("steward_setup", departments=[{"name": "HR", "target": "whatsapp:9@g.us"}])
        self.assertFalse(r["success"]); self.assertIn("failed", r["error"])
        self.assertTrue((self.tmp / "steward" / "state.json").exists())  # settings still saved

    def test_publish_needs_translation_when_staff_language_set(self):
        self.call("steward_setup", all_staff_target="whatsapp:1203@g.us", staff_language="French",
                  departments=[{"name": "Finance", "target": "whatsapp:2@g.us"}])
        self.call("steward_record_report", department="Finance", summary="Fournisseurs payés")
        self.assertFalse(self.call("steward_publish", confirm=True)["success"])
        r = self.call("steward_publish", confirm=True, text="*Mise à jour* Fournisseurs payés")
        self.assertTrue(r["success"], r); self.assertIn("Mise à jour", r["preview"])

    def test_errors_are_json_not_exceptions(self):
        r = self.call("steward_record_report", department="Nowhere", summary="x")
        self.assertFalse(r["success"]); self.assertIn("no department", r["error"])
        self.assertIn("/steward send", steward._slash("help"))


if __name__ == "__main__":
    unittest.main()
