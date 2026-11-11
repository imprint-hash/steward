"""File confinement and week validation. Run from the repo root:  python -m unittest discover -s tests"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
steward = __import__(ROOT.name)
core, files = steward.core, steward.files


class FakeCron:
    def __init__(self):
        self.calls = []

    def __call__(self, name, args):
        self.calls.append((name, args))
        return json.dumps({"success": True, "job_id": f"job{len(self.calls)}"})


class Base(unittest.TestCase):
    """HERMES_HOME pointed at a temp dir, like ToolTests in test_steward.py."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.images = self.home / "cache" / "images"
        self.docs = self.home / "cache" / "documents"
        self.images.mkdir(parents=True); self.docs.mkdir(parents=True)
        self.outside = Path(tempfile.mkdtemp())
        self._home = steward._home
        steward._home = lambda: self.home
        self._env = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = str(self.home)

        class Ctx:
            dispatch_tool = staticmethod(FakeCron())
        steward._CTX = Ctx()

    def tearDown(self):
        steward._home = self._home
        steward._CTX = None
        if self._env is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = self._env

    def call(self, name, **args):
        return json.loads(steward.HANDLERS[name](args))

    def setup_company(self):
        r = self.call("steward_setup", company="Co", all_staff_target="whatsapp:1203@g.us",
                      departments=[{"name": "Site Operations", "target": "whatsapp:1@g.us"}],
                      schedule_jobs=False)
        self.assertTrue(r["success"], r)


class ReadConfinementTests(Base):
    def test_auth_json_in_hermes_home_refused(self):
        auth = self.home / "auth.json"; auth.write_text('{"token": "secret"}')
        with self.assertRaises(ValueError) as e:
            files.read_report_file(str(auth), self.home)
        self.assertIn("files people sent in the chat", str(e.exception))
        r = self.call("steward_read_file", path=str(auth))
        self.assertFalse(r["success"]); self.assertNotIn("secret", json.dumps(r))

    def test_outside_file_and_traversal_refused(self):
        f = self.outside / "notes.txt"; f.write_text("private")
        with self.assertRaises(ValueError):
            files.read_report_file(str(f), self.home)
        with self.assertRaises(ValueError):
            files.read_report_file(str(self.docs / ".." / ".." / "auth.json"), self.home)

    def test_symlink_in_documents_pointing_outside_refused(self):
        target = self.outside / "token.txt"; target.write_text("secret")
        link = self.docs / "report.txt"
        os.symlink(target, link)
        with self.assertRaises(ValueError):
            files.read_report_file(str(link), self.home)
        with self.assertRaises(ValueError):
            files.keep_copy(str(link), self.home / "steward" / "files" / "x", self.home)

    def test_directory_refused(self):
        d = self.docs / "folder.txt"; d.mkdir()
        with self.assertRaises(ValueError):
            files.read_report_file(str(d), self.home)

    def test_allowed_files_work(self):
        c = self.docs / "n.csv"; c.write_text("label,value\nIncidents,0\n")
        self.assertIn("Incidents | 0", files.read_report_file(str(c), self.home)["text"])
        ph = self.images / "diary.jpg"; ph.write_bytes(b"\xff\xd8\xff")
        self.assertEqual(files.read_report_file(str(ph), self.home)["kind"], "photo")
        r = self.call("steward_read_file", path=str(c))
        self.assertTrue(r["success"], r); self.assertIn("Incidents | 0", r["text"])


class CopyConfinementTests(Base):
    def test_record_refuses_outside_attachment(self):
        self.setup_company()
        auth = self.home / "auth.json"; auth.write_text('{"token": "secret"}')
        r = self.call("steward_record_report", department="Site Operations", summary="ok",
                      done=["x"], attachments=[{"path": str(auth)}])
        self.assertFalse(r["success"]); self.assertIn("files people sent in the chat", r["error"])
        self.assertFalse((self.home / "steward" / "files").exists()
                         and any((self.home / "steward" / "files").rglob("auth.json")))

    def test_keep_copy_refuses_outside_source(self):
        f = self.outside / "x.txt"; f.write_text("x")
        with self.assertRaises(ValueError):
            files.keep_copy(str(f), self.home / "steward" / "files" / "w", self.home)

    def test_keep_copy_refuses_destination_outside_files(self):
        f = self.docs / "r.txt"; f.write_text("x")
        with self.assertRaises(ValueError):
            files.keep_copy(str(f), self.home / "steward" / "files" / ".." / ".." / "evil", self.home)
        self.assertFalse((self.home / "evil").exists())

    def test_valid_week_copy_lands_under_steward_files(self):
        self.setup_company()
        ph = self.images / "diary.jpg"; ph.write_bytes(b"\xff\xd8\xff")
        r = self.call("steward_record_report", department="Site Operations", summary="ok",
                      done=["Poured slab"], week="2026-W46", attachments=[{"path": str(ph)}])
        self.assertTrue(r["success"], r); self.assertEqual(r["week"], "2026-W46")
        kept = self.home / "steward" / "files" / "2026-W46" / "site-operations" / "diary.jpg"
        self.assertTrue(kept.exists())
        state = json.loads((self.home / "steward" / "state.json").read_text())
        self.assertEqual(state["weeks"]["2026-W46"]["reports"]["site-operations"]["attachments"][0]["path"],
                         str(kept.resolve()))


class WeekValidationTests(Base):
    BAD = ["../../x", "2026-46", "", "2026-W46/../..", "2026-W4", 46]

    def test_validate_week(self):
        self.assertEqual(core.validate_week("2026-W46"), "2026-W46")
        for w in self.BAD:
            with self.assertRaises(ValueError, msg=w):
                core.validate_week(w)

    def test_core_record_report_refuses_bad_week(self):
        s = core.empty_state()
        core.configure(s, {"departments": [{"name": "Safety"}]})
        for w in self.BAD:
            with self.assertRaises(ValueError, msg=w):
                core.record_report(s, "Safety", {"summary": "ok"}, week=w)
        self.assertEqual(core.record_report(s, "Safety", {"summary": "ok"}, week="2026-W46")["week"], "2026-W46")

    def test_record_refuses_bad_week_before_copying(self):
        self.setup_company()
        ph = self.images / "diary.jpg"; ph.write_bytes(b"\xff\xd8\xff")
        for w in ("../../x", "2026-46", ""):
            r = self.call("steward_record_report", department="Site Operations", summary="ok",
                          done=["x"], week=w, attachments=[{"path": str(ph)}])
            self.assertFalse(r["success"], w); self.assertIn("week must look like", r["error"])
        self.assertFalse((self.home / "x").exists())
        self.assertFalse((self.home / "steward" / "files").exists()
                         and any((self.home / "steward" / "files").rglob("diary.jpg")))
        state = json.loads((self.home / "steward" / "state.json").read_text())
        self.assertNotIn("../../x", state.get("weeks", {}))

    def test_build_pack_and_publish_refuse_bad_week(self):
        self.setup_company()
        for w in ("../../x", "2026-46", ""):
            r = self.call("steward_build_pack", week=w)
            self.assertFalse(r["success"], w); self.assertIn("week must look like", r["error"])
            r = self.call("steward_publish", confirm=True, week=w)
            self.assertFalse(r["success"], w); self.assertIn("week must look like", r["error"])
        self.assertTrue(self.call("steward_build_pack", week="2026-W46")["success"])


if __name__ == "__main__":
    unittest.main()
