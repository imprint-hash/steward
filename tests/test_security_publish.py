"""Who may publish the staff pack, and Steward's scheduling inside Hermes cron runs."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
steward = __import__(ROOT.name)

FOUNDER = "whatsapp:2348000000000@s.whatsapp.net"
DEPT_GROUP = "120363000000000002@g.us"


class FakeCron:
    def __init__(self):
        self.calls, self.n = [], 0

    def __call__(self, name, args):
        self.calls.append((name, dict(args)))
        if args["action"] == "create":
            self.n += 1
            return json.dumps({"success": True, "job_id": f"job{self.n}"})
        return json.dumps({"success": True})


class SecurityBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env, self.config = {}, {}
        self._saved = (steward._home, steward._get_session_env, steward._load_hermes_config)
        steward._home = lambda: self.tmp
        steward._get_session_env = lambda name: self.env.get(name, "")
        steward._load_hermes_config = lambda: self.config
        self.cron = FakeCron()

        class Ctx:
            dispatch_tool = staticmethod(self.cron)
        steward._CTX = Ctx()
        r = self.call("steward_setup", company="Adebayo Build Co", timezone="Africa/Lagos",
                      founder_target=FOUNDER, all_staff_target="whatsapp:120363000000000001@g.us",
                      departments=[{"name": "Site Operations", "target": "whatsapp:" + DEPT_GROUP}],
                      schedule_jobs=False)
        self.assertTrue(r["success"], r)
        r = self.call("steward_record_report", department="Site Operations",
                      summary="Slab poured", done=["Poured slab"])
        self.assertTrue(r["success"], r)

    def tearDown(self):
        steward._home, steward._get_session_env, steward._load_hermes_config = self._saved
        steward._CTX = None

    def call(self, name, **args):
        return json.loads(steward.HANDLERS[name](args))

    def session(self, platform="whatsapp", chat_id="", chat_type="dm", user_id="", user_id_alt="",
                source=None):
        self.env = {"HERMES_SESSION_PLATFORM": platform, "HERMES_SESSION_CHAT_ID": chat_id,
                    "HERMES_SESSION_CHAT_TYPE": chat_type, "HERMES_SESSION_USER_ID": user_id,
                    "HERMES_SESSION_USER_ID_ALT": user_id_alt,
                    "HERMES_SESSION_SOURCE": platform if source is None else source}

    def assert_refused(self):
        r = self.call("steward_publish", confirm=True)
        self.assertFalse(r["success"], r)
        self.assertEqual(r["error"], steward.NOT_FOUNDER)
        self.assertEqual(self.cron.calls, [])

    def assert_published(self):
        r = self.call("steward_publish", confirm=True)
        self.assertTrue(r["success"], r)
        self.assertEqual(len(self.cron.calls), 1)


class FounderOnlyTests(SecurityBase):
    def test_department_member_in_group_refused(self):
        self.session(chat_id=DEPT_GROUP, chat_type="group", user_id="2348011111111@s.whatsapp.net")
        self.assert_refused()

    def test_founder_dm_exact_chat_id_allowed(self):
        self.session(chat_id="2348000000000@s.whatsapp.net", user_id="2348000000000@s.whatsapp.net")
        self.assert_published()

    def test_founder_dm_with_lid_chat_and_phone_alt_allowed(self):
        self.session(chat_id="98765432109876@lid", user_id="98765432109876@lid",
                     user_id_alt="2348000000000:12@s.whatsapp.net")
        self.assert_published()

    def test_someone_elses_dm_refused(self):
        self.session(chat_id="2348011111111@s.whatsapp.net", user_id="2348011111111@s.whatsapp.net")
        self.assert_refused()

    def test_founder_send_in_group_refused(self):
        self.session(chat_id=DEPT_GROUP, chat_type="group", user_id="2348000000000@s.whatsapp.net")
        self.assert_refused()
        self.session(chat_id=DEPT_GROUP, chat_type="dm", user_id="2348000000000@s.whatsapp.net")
        self.assert_refused()   # @g.us is a group whatever the chat type says

    def test_founder_id_on_other_platform_refused(self):
        self.session(platform="telegram", chat_id="2348000000000@s.whatsapp.net",
                     user_id="2348000000000@s.whatsapp.net")
        self.assert_refused()

    def test_unknown_platform_refused(self):
        self.session(platform="carrierpigeon", chat_id="42", chat_type="")
        self.assert_refused()

    def test_local_cli_allowed(self):
        self.session(platform="", chat_id="", chat_type="", source="cli")
        self.assert_published()

    def test_slash_send_from_non_founder_not_sent(self):
        self.session(chat_id=DEPT_GROUP, chat_type="group", user_id="2348011111111@s.whatsapp.net")
        out = steward._slash("send")
        self.assertTrue(out.startswith("Not sent:"), out)
        self.assertIn("Only the founder can publish", out)
        self.assertEqual(self.cron.calls, [])

    def test_slash_send_from_founder_dm(self):
        self.session(chat_id="2348000000000@s.whatsapp.net")
        self.assertIn("all-staff", steward._slash("send"))

    def test_id_matching(self):
        self.assertTrue(steward._same_id("2348000000000@s.whatsapp.net", "2348000000000:7@s.whatsapp.net"))
        self.assertTrue(steward._same_id("2348000000000@s.whatsapp.net", "2348000000000"))
        self.assertFalse(steward._same_id("2348000000000@s.whatsapp.net", "2348000000000@lid"))
        self.assertFalse(steward._same_id("", ""))


class CronSchedulingTests(SecurityBase):
    def setUp(self):
        super().setUp()
        self.env = {"HERMES_CRON_SESSION": "1"}

    def test_blocked_without_allow_agent_scheduling(self):
        r = self.call("steward_setup", departments=[{"name": "HR", "target": "whatsapp:9@g.us"}])
        self.assertFalse(r["success"]); self.assertIn("allow_agent_scheduling", r["error"])
        state = json.loads((self.tmp / "steward" / "state.json").read_text())
        self.assertNotIn("HR", [d["name"] for d in state["departments"]])   # nothing saved
        r = self.call("steward_publish", confirm=True)
        self.assertFalse(r["success"]); self.assertIn("allow_agent_scheduling", r["error"])
        self.assertEqual(self.cron.calls, [])

    def test_settings_only_setup_refused_in_cron(self):
        # a cron run is never the founder: even settings-only setup is refused by default
        r = self.call("steward_setup", departments=[{"name": "HR"}], schedule_jobs=False)
        self.assertFalse(r["success"], r)

    def test_unreadable_config_fails_closed(self):
        def boom():
            raise OSError("no config")
        steward._load_hermes_config = boom
        self.assertTrue(steward._cron_scheduling_blocked())

    def test_allowed_with_allow_agent_scheduling(self):
        self.config = {"cron": {"allow_agent_scheduling": True}}
        r = self.call("steward_setup", departments=[{"name": "HR", "target": "whatsapp:9@g.us"}])
        self.assertTrue(r["success"], r)
        # publishing always needs a person: never from a cron run, even with scheduling allowed
        r = self.call("steward_publish", confirm=True)
        self.assertFalse(r["success"], r)

    def test_not_a_cron_session(self):
        self.env = {"HERMES_CRON_SESSION": ""}
        self.assertFalse(steward._cron_scheduling_blocked())


if __name__ == "__main__":
    unittest.main()
