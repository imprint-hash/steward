"""Who may change setup, read the founder pack/status, and save which department's report."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
steward = __import__(ROOT.name)
core = steward.core

FOUNDER_ID = "2348000000000@s.whatsapp.net"
FOUNDER = "whatsapp:" + FOUNDER_ID
ALL_STAFF = "whatsapp:120363000000000001@g.us"
OPS_GROUP = "120363000000000002@g.us"
FIN_GROUP = "120363000000000003@g.us"
MEMBER = "2348099999999@s.whatsapp.net"   # a member of the Site Operations group


class FakeCron:
    def __init__(self):
        self.calls, self.n = [], 0

    def __call__(self, name, args):
        self.calls.append((name, dict(args)))
        if args.get("action") == "create":
            self.n += 1
            return json.dumps({"success": True, "job_id": f"job{self.n}"})
        return json.dumps({"success": True})


class Base(unittest.TestCase):
    SETUP = dict(company="Adebayo Build Co", timezone="Africa/Lagos", founder_target=FOUNDER,
                 all_staff_target=ALL_STAFF,
                 departments=[{"name": "Site Operations", "lead": "Tunde", "target": "whatsapp:" + OPS_GROUP},
                              {"name": "Finance", "lead": "Ngozi", "target": "whatsapp:" + FIN_GROUP}])

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env, self.config, self.job_id = {}, {}, ""
        self._saved = (steward._home, steward._get_session_env, steward._load_hermes_config,
                       steward._cron_job_id)
        steward._home = lambda: self.tmp
        steward._get_session_env = lambda name: self.env.get(name, "")
        steward._load_hermes_config = lambda: self.config
        steward._cron_job_id = lambda: self.job_id
        self.cron = FakeCron()

        class Ctx:
            dispatch_tool = staticmethod(self.cron)
        steward._CTX = Ctx()
        self.local()
        r = self.call("steward_setup", **self.SETUP)
        self.assertTrue(r["success"], r)
        r = self.call("steward_record_report", department="Finance", summary="Cash is tight.",
                      done=["Paid suppliers"], blockers=["Bank loan still not approved"],
                      founder_only=["Ngozi is resigning next month"])
        self.assertTrue(r["success"], r)
        self.cron.calls.clear()

    def tearDown(self):
        (steward._home, steward._get_session_env, steward._load_hermes_config,
         steward._cron_job_id) = self._saved
        steward._CTX = None

    def call(self, name, **args):
        return json.loads(steward.HANDLERS[name](args))

    def local(self):
        self.env = {"HERMES_SESSION_PLATFORM": "", "HERMES_SESSION_SOURCE": "cli"}

    def session(self, platform="whatsapp", chat_id="", chat_type="dm", user_id="", source=""):
        self.env = {"HERMES_SESSION_PLATFORM": platform, "HERMES_SESSION_CHAT_ID": chat_id,
                    "HERMES_SESSION_CHAT_TYPE": chat_type, "HERMES_SESSION_USER_ID": user_id,
                    "HERMES_SESSION_SOURCE": source}

    def in_group(self, group=OPS_GROUP, user=MEMBER):
        self.session(chat_id=group, chat_type="group", user_id=user)

    def founder_dm(self):
        self.session(chat_id=FOUNDER_ID, user_id=FOUNDER_ID)

    def cron_run(self, allow=False):
        self.env = {"HERMES_SESSION_PLATFORM": "", "HERMES_SESSION_SOURCE": "", "HERMES_CRON_SESSION": "1"}
        self.config = {"cron": {"allow_agent_scheduling": True}} if allow else {}

    def state(self):
        return json.loads((self.tmp / "steward" / "state.json").read_text())

    def refused(self, r, *words):
        self.assertFalse(r["success"], r)
        for w in words:
            self.assertIn(w, r["error"])


class SetupAuthzTests(Base):
    def test_group_member_cannot_change_setup(self):
        before = self.state()
        self.in_group()
        for args in ({"founder_target": "whatsapp:" + MEMBER},
                     {"all_staff_target": "whatsapp:" + MEMBER, "schedule_jobs": False},
                     {"departments": [{"name": "Nobody"}]},
                     {"company": "x", "schedule_jobs": False}):
            self.refused(self.call("steward_setup", **args), "Only the founder")
        self.assertEqual(self.state(), before)
        self.assertEqual(self.cron.calls, [])

    def test_someone_elses_dm_cannot_change_setup(self):
        self.session(chat_id=MEMBER, user_id=MEMBER)
        self.refused(self.call("steward_setup", departments=[{"name": "HR"}], schedule_jobs=False),
                     "Only the founder")
        self.assertEqual(self.cron.calls, [])

    def test_founder_dm_can_change_departments_not_founder_target(self):
        self.founder_dm()
        r = self.call("steward_setup", departments=self.SETUP["departments"] + [
            {"name": "HR", "target": "whatsapp:120363000000000009@g.us"}])
        self.assertTrue(r["success"], r)
        self.assertIn("HR", [d["name"] for d in self.state()["departments"]])
        self.cron.calls.clear()
        self.refused(self.call("steward_setup", founder_target="whatsapp:" + MEMBER), "only be set or changed")
        self.assertEqual(self.state()["founder_target"], FOUNDER)
        self.assertEqual(self.cron.calls, [])
        r = self.call("steward_setup", founder_target=FOUNDER, schedule_jobs=False)   # unchanged: fine
        self.assertTrue(r["success"], r)

    def test_local_operator_can_change_founder_target(self):
        r = self.call("steward_setup", founder_target="telegram:12345", schedule_jobs=False)
        self.assertTrue(r["success"], r)
        self.assertEqual(self.state()["founder_target"], "telegram:12345")

    def test_first_setup_only_local(self):
        (self.tmp / "steward" / "state.json").unlink()
        self.founder_dm()
        self.refused(self.call("steward_setup", **self.SETUP), "First-time setup")
        self.assertFalse((self.tmp / "steward" / "state.json").exists())
        self.assertEqual(self.cron.calls, [])

    def test_bad_targets_refused(self):
        before = self.state()
        bad = ["all", "all:x", "local", "local:x", "origin", "origin:x", "bot-chat:1", "bot-chat-x:1",
               "room:abc", "whatsapp:1@g.us,whatsapp:2@g.us", "whatsapp:1@g.us\n", "whatsapp:1@g.us\nx",
               "whatsapp:1 2", "WhatsApp:1@g.us", "whatsapp:", ":123", "whatsapp:1\x00"]
        for t in bad:
            for args in ({"all_staff_target": t}, {"founder_target": t},
                         {"departments": [{"name": "HR", "target": t}]}):
                r = self.call("steward_setup", **args)
                self.assertFalse(r["success"], (t, args))
        self.assertEqual(self.state(), before)
        self.assertEqual(self.cron.calls, [])

    def test_good_targets_accepted(self):
        r = self.call("steward_setup", all_staff_target="telegram:-100123:45", schedule_jobs=False,
                      departments=[{"name": "Sales", "target": "slack:C0123"}, {"name": "Ops"}])
        self.assertTrue(r["success"], r)

    def test_surrogate_input_refused_before_any_job_change(self):
        before = self.state()
        for args in ({"company": "Ade\ud800bayo"},
                     {"departments": [{"name": "Fin\udc00ance", "target": "whatsapp:9@g.us"}]},
                     {"departments": [{"name": "HR", "lead": "\ud83d"}]}):
            r = self.call("steward_setup", **args)
            self.assertFalse(r["success"], args)
        self.assertEqual(self.cron.calls, [])
        self.assertEqual(self.state(), before)

    def test_state_saved_before_jobs_are_touched(self):
        seen = []

        def dispatch(name, args):
            seen.append([d["name"] for d in self.state()["departments"]])
            return self.cron(name, args)
        steward._CTX.dispatch_tool = staticmethod(dispatch)
        r = self.call("steward_setup", departments=[{"name": "HR", "target": "whatsapp:9@g.us"}])
        self.assertTrue(r["success"], r)
        self.assertTrue(seen and all(s == ["HR"] for s in seen), seen)
        self.assertEqual(set(self.state()["jobs"].values()),
                         {c["job_id"] for c in r["jobs"]["created"]})

    def test_cron_setup_refused_without_allow_even_settings_only(self):
        self.cron_run()
        self.refused(self.call("steward_setup", all_staff_target="whatsapp:" + MEMBER, schedule_jobs=False),
                     "allow_agent_scheduling")
        self.assertEqual(self.state()["all_staff_target"], ALL_STAFF)

    def test_cron_setup_with_allow_never_changes_founder(self):
        self.cron_run(allow=True)
        self.refused(self.call("steward_setup", founder_target="whatsapp:" + MEMBER, schedule_jobs=False),
                     "only be set or changed")
        self.assertEqual(self.state()["founder_target"], FOUNDER)


class OperatorTests(Base):
    def test_who_is_the_operator(self):
        cases = [({"HERMES_SESSION_PLATFORM": "", "HERMES_SESSION_SOURCE": ""}, True),
                 ({"HERMES_SESSION_SOURCE": "cli"}, True),
                 ({"HERMES_SESSION_SOURCE": "tui"}, True),
                 ({"HERMES_SESSION_SOURCE": "desktop"}, True),
                 ({"HERMES_SESSION_PLATFORM": "local"}, True),
                 ({"HERMES_CRON_SESSION": "1"}, False),
                 ({"HERMES_SESSION_SOURCE": "cli", "HERMES_CRON_SESSION": "true"}, False),
                 ({"HERMES_SESSION_PLATFORM": "webhook"}, False),
                 ({"HERMES_SESSION_PLATFORM": "api_server"}, False),
                 ({"HERMES_SESSION_PLATFORM": "msgraph_webhook"}, False),
                 ({"HERMES_SESSION_PLATFORM": "whatsapp", "HERMES_SESSION_SOURCE": "cli"}, False),
                 ({"HERMES_SESSION_PLATFORM": "", "HERMES_SESSION_SOURCE": "whatsapp"}, False)]
        for env, want in cases:
            self.env = env
            self.assertEqual(steward._is_operator(), want, env)

    def test_webhook_and_api_server_cannot_setup_publish_or_read_pack(self):
        for platform in ("webhook", "api_server", "msgraph_webhook"):
            self.session(platform=platform, chat_id="anything", chat_type="")
            self.refused(self.call("steward_setup", departments=[{"name": "X"}], schedule_jobs=False))
            self.refused(self.call("steward_publish", confirm=True), "Only the founder")
            self.refused(self.call("steward_build_pack", audience="founder"))
            self.refused(self.call("steward_status"))
        self.assertEqual(self.cron.calls, [])


class PackAuthzTests(Base):
    def test_group_cannot_read_founder_pack_or_status(self):
        self.in_group()
        self.refused(self.call("steward_build_pack", audience="founder"), "only for the founder")
        self.refused(self.call("steward_build_pack"), "only for the founder")   # default is founder
        self.refused(self.call("steward_status"), "only for the founder")
        wk = core.week_key(core.now_in(self.state()))
        self.refused(self.call("steward_build_pack", audience="staff", week=wk))
        for sub in ("pack", "status"):
            out = steward._slash(sub)
            self.assertIn("only for the founder", out)
            self.assertNotIn("resigning", out)
        staff = steward._slash("staff")
        self.assertIn("Paid suppliers", staff)
        self.assertNotIn("resigning", staff)
        self.assertTrue(self.call("steward_build_pack", audience="staff")["success"])

    def test_founder_dm_reads_everything(self):
        self.founder_dm()
        r = self.call("steward_build_pack", audience="founder")
        self.assertTrue(r["success"], r); self.assertIn("resigning", r["text"])
        self.assertTrue(self.call("steward_status")["success"])
        self.assertTrue(self.call("steward_build_pack", audience="founder", week="2026-W40")["success"])

    def test_cron_only_steward_pack_job_gets_founder_pack(self):
        self.call("steward_setup", schedule_jobs=True)   # local: creates the pack job
        pack_job = self.state()["jobs"]["pack"]
        self.cron_run()
        self.job_id = "someone-elses-job"
        self.refused(self.call("steward_build_pack", audience="founder"))
        self.job_id = pack_job
        r = self.call("steward_build_pack", audience="founder")
        self.assertTrue(r["success"], r)
        self.refused(self.call("steward_build_pack", audience="founder", week="2026-W40"))
        self.refused(self.call("steward_status"))

    def test_checkin_in_group_only_for_own_department(self):
        self.in_group()
        self.refused(self.call("steward_checkin", department="Finance"), "own department")
        self.assertTrue(self.call("steward_checkin", department="Site Operations")["success"])
        self.local()
        self.assertTrue(self.call("steward_checkin", department="Finance")["success"])


class RecordAuthzTests(Base):
    def test_cannot_write_another_departments_report(self):
        before = self.state()
        self.in_group()
        for dept in ("Finance", "finance", "whatsapp:" + FIN_GROUP):
            self.refused(self.call("steward_record_report", department=dept, summary="All good."),
                         "Site Operations group")
        self.assertEqual(self.state()["weeks"], before["weeks"])

    def test_own_department_allowed(self):
        self.in_group()
        r = self.call("steward_record_report", department="Site Operations", summary="Slab poured",
                      done=["Poured slab"])
        self.assertTrue(r["success"], r)
        r = self.call("steward_record_report", department="whatsapp:" + OPS_GROUP, summary="Slab poured",
                      done=["Poured slab"])
        self.assertTrue(r["success"], r)

    def test_group_week_limited_to_this_and_last(self):
        wk = core.week_key(core.now_in(self.state()))
        self.in_group()
        for w in (core.shift_week(wk, -2), core.shift_week(wk, 1), "2020-W01"):
            self.refused(self.call("steward_record_report", department="Site Operations", summary="x",
                                   done=["x"], week=w), "this week")
        r = self.call("steward_record_report", department="Site Operations", summary="late",
                      done=["x"], week=core.shift_week(wk, -1))
        self.assertTrue(r["success"], r)

    def test_non_department_chats_refused(self):
        for chat, kind in ((MEMBER, "dm"), ("120363000000000001@g.us", "group")):
            self.session(chat_id=chat, chat_type=kind, user_id=MEMBER)
            self.refused(self.call("steward_record_report", department="Site Operations", summary="x"),
                         "own group")
        self.session(platform="telegram", chat_id=OPS_GROUP, chat_type="group")   # same id, other platform
        self.refused(self.call("steward_record_report", department="Site Operations", summary="x"))

    def test_founder_can_fix_any_department_and_week(self):
        self.founder_dm()
        r = self.call("steward_record_report", department="Finance", summary="Fixed", done=["x"],
                      week="2026-W40")
        self.assertTrue(r["success"], r)

    def test_only_first_ten_attachments_copied(self):
        imgs = self.tmp / "cache" / "images"
        imgs.mkdir(parents=True)
        atts = []
        for i in range(15):
            p = imgs / f"p{i:02d}.jpg"; p.write_bytes(b"\xff\xd8\xff")
            atts.append({"path": str(p)})
        r = self.call("steward_record_report", department="Finance", summary="x", done=["x"],
                      attachments=atts)
        self.assertTrue(r["success"], r)
        copied = sorted(f.name for f in (self.tmp / "steward" / "files").rglob("*.jpg"))
        self.assertEqual(copied, [f"p{i:02d}.jpg" for i in range(10)])


class PublishAuthzTests(Base):
    def test_cron_publish_refused_even_with_allow_agent_scheduling(self):
        self.cron_run(allow=True)
        self.job_id = "anything"
        self.refused(self.call("steward_publish", confirm=True), "never runs inside a cron run")
        self.assertEqual(self.cron.calls, [])

    def test_translated_text_checked_and_returned(self):
        self.call("steward_setup", staff_language="French", schedule_jobs=False)
        self.founder_dm()
        self.refused(self.call("steward_publish", confirm=True, text="x" * 6001), "6000")
        self.refused(self.call("steward_publish", confirm=True,
                               text="Mise à jour. Payez ici: https://evil.example/pay"), "links")
        self.refused(self.call("steward_publish", confirm=True, text="  "))
        self.assertEqual(self.cron.calls, [])
        text = "*Mise à jour* Fournisseurs payés"
        r = self.call("steward_publish", confirm=True, text=text)
        self.assertTrue(r["success"], r)
        self.assertEqual(r["posted_text"], text)
        wk = r["week"]
        self.assertEqual(self.state()["weeks"][wk]["published_text"], text)


if __name__ == "__main__":
    unittest.main()
