"""Contract regressions; fixtures are disposable copies, never the live workspace."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
SOURCE_VERSION = json.loads((SOURCE / "kit.json").read_text(encoding="utf-8"))["version"]
_version_parts = [int(part) for part in SOURCE_VERSION.split(".")]
NEXT_VERSION = ".".join(map(str, [*_version_parts[:2], _version_parts[2] + 1]))
sys.path.insert(0, str(SOURCE))
from core import config, ledger, paths, policy, trash, artifacts, experience


def module(name, p):
    spec = importlib.util.spec_from_file_location(name, p)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj


cli = module("contract_cli", SOURCE / "bin/kit.py")
gate = module("contract_gate", SOURCE / "bin/check.py")
up = module("contract_upgrade", SOURCE / "bin/upgrade.py")
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
TEST_ROOT = Path(tempfile.mkdtemp(prefix="work-kit-proof-", dir=SOURCE.parents[1]))


class Contract(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="case-", dir=TEST_ROOT))
        self.kit = self.ws / "work-kit"
        shutil.copytree(SOURCE, self.kit, ignore=shutil.ignore_patterns("__pycache__", "data", "config", ".runtime"))
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in {"KIT": self.kit, "DATA": self.kit / "data",
                            "CONFIG_DIR": self.kit / "config", "CONFIG": self.kit / "config/kit.config.json",
                            "STATE": self.kit / ".runtime/kit.state.json"}.items():
            self.stack.enter_context(patch.object(config, name, value))
        for obj in (cli, gate, up):
            self.stack.enter_context(patch.object(obj, "KIT", self.kit))
        config.DATA.mkdir()
        config.CONFIG_DIR.mkdir()
        self.cfg = {"workspace": str(self.ws), "readonly_roots": [], "privacy_tokens": []}
        config.CONFIG.write_text(json.dumps(self.cfg), encoding="utf-8")
        for name in ledger.LEDGERS:
            ledger.path(name).write_text("", encoding="utf-8")
        (config.DATA / "negative-examples.json").write_text('{"cases": []}', encoding="utf-8")
        (config.DATA / "patterns.json").write_text('{"patterns": []}', encoding="utf-8")
        man = json.loads((self.kit / "kit.json").read_text(encoding="utf-8"))
        config.save_state({"version": man["version"], "installed": {r: up.sha(self.kit / r) for r in man["shell"]}})

    def command(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(["kit", *args])
        return rc, out.getvalue()

    def check(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = gate.main(["check", "all"])
        return rc, out.getvalue()

    def begin(self, rel="out.txt"):
        self.assertEqual(self.command("task", "new", "fixture task")[0], 0)
        self.assertEqual(self.command("change", "new", "T-001", "--files", rel)[0], 0)
        self.assertEqual(self.command("change", "approve", "C-001")[0], 0)

    def finish(self, rel="out.txt"):
        (self.ws / rel).write_text("actual output", encoding="utf-8")
        self.assertEqual(self.command("log", "C-001", "--created", rel, "--complete")[0], 0)

    def new_release(self):
        src = self.ws / "release"
        shutil.copytree(SOURCE, src, ignore=shutil.ignore_patterns("__pycache__", "data", "config", ".runtime"))
        man = json.loads((src / "kit.json").read_text(encoding="utf-8"))
        man["version"] = NEXT_VERSION
        (src / "kit.json").write_text(json.dumps(man, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (src / "VERSION").write_text(NEXT_VERSION + "\n", encoding="utf-8")
        return src

    def test_01_empty_instance_checks(self):
        rc, output = self.check()
        self.assertEqual(rc, 0, output)

    def test_02_real_cli_completion(self):
        self.begin(); self.finish()
        rc, output = self.check()
        self.assertEqual(rc, 0, output)
        self.assertEqual(ledger.read("task")[0][0]["status"], "done")

    def test_03_space_filename_preserved(self):
        self.begin("My Notes.txt"); self.finish("My Notes.txt")
        self.assertEqual(cli._split(["My Notes.txt", "other.txt"]), ["My Notes.txt", "other.txt"])
        self.assertEqual(self.check()[0], 0, self.check()[1])

    def test_04_fake_output_rejected_before_commit(self):
        self.begin()
        before = {n: ledger.path(n).read_bytes() for n in ledger.LEDGERS}
        rc, output = self.command("log", "C-001", "--created", "out.txt", "--complete")
        self.assertNotEqual(rc, 0)
        self.assertIn("不存在", output)
        self.assertEqual(before, {n: ledger.path(n).read_bytes() for n in ledger.LEDGERS})

    def test_05_false_consistent_records_fail_gate(self):
        self.begin(); self.finish()
        (self.ws / "out.txt").unlink()
        rc, output = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("不存在", output)

    def test_06_changed_bytes_fail_gate(self):
        self.begin(); self.finish()
        (self.ws / "out.txt").write_text("changed later", encoding="utf-8")
        self.assertEqual(self.check()[0], 1)
        self.assertIn("哈希", self.check()[1])

    def test_07_unapproved_log_rejected(self):
        self.command("task", "new", "test")
        self.command("change", "new", "T-001", "--files", "out.txt")
        (self.ws / "out.txt").write_text("x")
        rc, output = self.command("log", "C-001", "--created", "out.txt", "--complete")
        self.assertNotEqual(rc, 0)
        self.assertIn("批准", output)

    def test_08_terminal_change_cannot_reopen(self):
        self.begin(); self.finish()
        self.assertNotEqual(self.command("change", "approve", "C-001")[0], 0)

    def test_09_task_transition_and_stale_revision(self):
        self.begin()
        current = ledger.read("task")[0][0]
        bad = dict(current, status="verified", rev=current["rev"] + 1, evidence=["file:out.txt"], verified_by="user")
        with self.assertRaisesRegex(ValueError, "非法状态"):
            ledger.replace("task", bad)
        bad = dict(current, title="stale edit")
        with self.assertRaisesRegex(ValueError, "rev"):
            ledger.replace("task", bad)

    def test_10_new_task_cannot_start_done(self):
        with self.assertRaisesRegex(ValueError, "todo"):
            ledger.create("task", "T", {"status": "done", "rev": 1, "evidence": ["fake"]})

    def test_11_duplicate_id_rejected(self):
        ledger.append("task", {"id": "T-001", "status": "todo", "rev": 1})
        with self.assertRaisesRegex(ValueError, "id"):
            ledger.append("task", {"id": "T-001", "status": "todo", "rev": 1})

    def test_12_concurrent_real_processes_allocate_unique_ids(self):
        def create(i):
            return subprocess.run([sys.executable, "-B", str(self.kit / "bin/kit.py"), "task", "new", f"task {i}"],
                                  capture_output=True, text=True, encoding="utf-8", env=ENV, timeout=30)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(create, range(10)))
        self.assertTrue(all(r.returncode == 0 for r in results), [r.stdout + r.stderr for r in results])
        rows, bad = ledger.read("task")
        self.assertFalse(bad)
        self.assertEqual(len(rows), 10)
        self.assertEqual(len({r["id"] for r in rows}), 10)

    def test_13_live_lock_does_not_expire(self):
        with ledger.Lock(stale=0):
            with self.assertRaises(TimeoutError):
                with ledger.Lock(timeout=0.05, stale=0):
                    pass
        with ledger.Lock(timeout=0.05):
            pass

    def test_14_interrupted_commit_is_visible_and_recoverable(self):
        original = ledger.atomic
        def fail(p, text):
            if p == ledger.path("change"):
                raise OSError("injected write failure")
            return original(p, text)
        with patch.object(ledger, "atomic", fail):
            with self.assertRaises(OSError):
                ledger.create("task", "T", {"status": "todo", "rev": 1})
        self.assertTrue(ledger.read("task")[1])
        self.assertTrue(ledger.recover())
        self.assertEqual(ledger.read("task")[0][0]["id"], "T-001")
        self.assertFalse(ledger.journal().exists())

    def test_15_workspace_and_kit_ancestors_rejected(self):
        ok, errors = trash.plan(self.ws, self.kit, ["."])
        self.assertFalse(ok); self.assertTrue(errors)
        ok, errors = trash.plan(self.ws, self.kit, ["work-kit"])
        self.assertFalse(ok); self.assertTrue(errors)

    def test_16_readonly_and_parent_rejected(self):
        (self.ws / "assets/private").mkdir(parents=True)
        self.cfg["readonly_roots"] = ["assets/private"]
        config.CONFIG.write_text(json.dumps(self.cfg), encoding="utf-8")
        self.assertTrue(trash.plan(self.ws, self.kit, ["assets"])[1])

    def test_17_overlap_and_git_subtree_rejected(self):
        (self.ws / "folder/.git").mkdir(parents=True)
        self.assertTrue(trash.plan(self.ws, self.kit, ["folder"])[1])
        (self.ws / "plain").mkdir()
        (self.ws / "plain/file.txt").write_text("x")
        self.assertTrue(trash.plan(self.ws, self.kit, ["plain", "plain/file.txt"])[1])

    def test_18_directory_roundtrip_and_idempotent_restore(self):
        (self.ws / "folder/empty").mkdir(parents=True)
        (self.ws / "folder/text.txt").write_text("receipt")
        before = trash.snapshot(self.ws / "folder")
        ok, errors = trash.plan(self.ws, self.kit, ["folder"])
        self.assertFalse(errors)
        manifest = trash.trash(self.ws, self.kit, ok)
        self.assertFalse((self.ws / "folder").exists())
        trash.restore(self.ws, self.kit, manifest["stamp"])
        trash.restore(self.ws, self.kit, manifest["stamp"])
        self.assertEqual(trash.snapshot(self.ws / "folder"), before)

    def test_19_bad_backup_does_not_restore(self):
        (self.ws / "x.txt").write_text("original")
        manifest = trash.trash(self.ws, self.kit, [self.ws / "x.txt"])
        (trash.root(self.kit) / manifest["stamp"] / "payload/x.txt").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "哈希"):
            trash.restore(self.ws, self.kit, manifest["stamp"])
        self.assertFalse((self.ws / "x.txt").exists())

    def test_20_restore_conflict_preflight_writes_nothing(self):
        for rel in ("a.txt", "b.txt"):
            (self.ws / rel).write_text("original")
        manifest = trash.trash(self.ws, self.kit, [self.ws / "a.txt", self.ws / "b.txt"])
        (self.ws / "b.txt").write_text("new user content")
        with self.assertRaises(FileExistsError):
            trash.restore(self.ws, self.kit, manifest["stamp"])
        self.assertFalse((self.ws / "a.txt").exists())
        self.assertEqual((self.ws / "b.txt").read_text(), "new user content")

    def test_21_batch_names_unique_and_escape_rejected(self):
        self.assertNotEqual(trash.stamp(), trash.stamp())
        with self.assertRaises(ValueError):
            trash.restore(self.ws, self.kit, "../outside")

    def test_22_upgrade_escape_and_protected_paths_rejected(self):
        src = self.new_release()
        man = json.loads((src / "kit.json").read_text(encoding="utf-8"))
        for bad in ("../outside.txt", "config\\kit.config.json", "data/x", "packs/x", chr(67) + ":/escape"):
            bad_man = dict(man, shell=man["shell"] + [bad])
            (src / "kit.json").write_text(json.dumps(bad_man), encoding="utf-8")
            with self.assertRaises(ValueError, msg=bad):
                up.shell_of(src)

    def test_23_upgrade_preserves_user_data_and_config(self):
        self.begin(); self.finish()
        data = {p.relative_to(config.DATA).as_posix(): p.read_bytes() for p in config.DATA.rglob("*") if p.is_file()}
        cfg = config.CONFIG.read_bytes()
        src = self.new_release()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(up.upgrade(src, apply=True), 0)
        self.assertEqual(config.CONFIG.read_bytes(), cfg)
        self.assertEqual(data, {p.relative_to(config.DATA).as_posix(): p.read_bytes() for p in config.DATA.rglob("*") if p.is_file()})
        self.assertEqual(config.state()["version"], NEXT_VERSION)

    def test_24_upgrade_conflict_does_not_activate_mixed_release(self):
        src = self.new_release()
        local = self.kit / "core/views.py"
        local.write_text(local.read_text(encoding="utf-8") + "\n# local change\n", encoding="utf-8")
        before = {p.relative_to(self.kit).as_posix(): p.read_bytes() for p in self.kit.rglob("*") if p.is_file()}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(up.upgrade(src, apply=True), 3)
        for rel, data in before.items():
            self.assertEqual((self.kit / rel).read_bytes(), data)
        self.assertEqual(config.state()["version"], SOURCE_VERSION)

    def test_25_upgrade_injected_failure_rolls_back(self):
        src = self.new_release()
        man = json.loads((self.kit / "kit.json").read_text(encoding="utf-8"))
        before = {rel: (self.kit / rel).read_bytes() for rel in man["shell"]}
        state = config.STATE.read_bytes()
        original = up.os.replace
        count = [0]
        def fail(src, dst):
            if ".upgrade-" in Path(src).name:
                count[0] += 1
                if count[0] == 3:
                    raise OSError("injected install failure")
            return original(src, dst)
        with patch.object(up.os, "replace", fail), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(OSError):
                up.upgrade(src, apply=True)
        self.assertEqual(before, {rel: (self.kit / rel).read_bytes() for rel in man["shell"]})
        self.assertEqual(config.STATE.read_bytes(), state)

    def test_26_relative_do_not_touch_resolves_against_workspace(self):
        rows = {"change": [{"id": "C-001", "do_not_touch": ["protected"]}],
                "modification": [{"id": "M-001", "via_change_list": "C-001", "files_modified": ["protected/a.txt"]}]}
        self.assertFalse(gate.check_boundary(rows, self.ws, [])[0])

    def test_27_plain_evidence_without_slash_is_checked(self):
        with self.assertRaisesRegex(ValueError, "不存在"):
            artifacts.evidence(self.ws, ["missing.txt"])

    def test_28_unknown_gate_command_is_not_success(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertNotEqual(gate.main(["check", "typo"]), 0)

    def test_29_fresh_install_in_real_subprocess(self):
        ws = self.ws / "fresh"
        kit = ws / "work-kit"
        shutil.copytree(SOURCE, kit, ignore=shutil.ignore_patterns("__pycache__", "data", "config", ".runtime"))
        result = subprocess.run([sys.executable, "-B", str(kit / "bin/init.py"), "--auto"],
                                capture_output=True, text=True, encoding="utf-8", env=ENV, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        checked = subprocess.run([sys.executable, "-B", str(kit / "bin/check.py"), "all"],
                                 capture_output=True, text=True, encoding="utf-8", env=ENV, timeout=30)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn("work-kit:begin", (ws / "AGENTS.md").read_text(encoding="utf-8"))

    def test_30_recall_returns_source_without_mutation(self):
        p = config.DATA / "patterns.json"
        p.write_text(json.dumps({"patterns": [{"id": "P-001", "scenario": "删除前", "action": "先看计划"}]}, ensure_ascii=False), encoding="utf-8")
        before = p.read_bytes()
        hits = experience.recall("删除")
        self.assertEqual(hits[0]["id"], "P-001")
        self.assertEqual(hits[0]["source"], "data/patterns.json")
        self.assertEqual(p.read_bytes(), before)

    def test_31_recall_broken_library_is_explicit(self):
        (config.DATA / "patterns.json").write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            experience.recall("query")

    def test_32_recall_empty_query_is_not_success(self):
        with self.assertRaises(ValueError):
            experience.recall("  ")

    def test_33_package_requires_no_instance_configuration(self):
        config.CONFIG.unlink()
        for name in ledger.LEDGERS:
            ledger.path(name).unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main(["check", "package"]), 0)

    def test_34_invalid_manifest_rejects_without_writing_release(self):
        src = self.new_release()
        man = json.loads((src / "kit.json").read_text(encoding="utf-8"))
        (src / "kit.json").write_text(json.dumps(dict(man, shell=man["shell"] + ["config./x"])), encoding="utf-8")
        with self.assertRaises(ValueError):
            up.shell_of(src)

    def test_35_attach_preserves_text_crlf_and_backups(self):
        attach = cli.attach_mod
        p = self.ws / "AGENTS.md"
        original = b"# User rules\r\nKeep this line.\r\n"
        p.write_bytes(original)
        self.assertEqual(attach.attach(self.ws, self.kit), "appended")
        self.assertTrue(p.read_bytes().startswith(original))
        backups = list((self.kit / ".runtime/attach").glob("*.md"))
        self.assertEqual(backups[0].read_bytes(), original)
        self.assertEqual(attach.attach(self.ws, self.kit), "unchanged")

    def test_36_attach_broken_markers_do_not_overwrite(self):
        p = self.ws / "AGENTS.md"
        p.write_text(cli.attach_mod.BEGIN, encoding="utf-8")
        before = p.read_bytes()
        with self.assertRaises(ValueError):
            cli.attach_mod.attach(self.ws, self.kit)
        self.assertEqual(p.read_bytes(), before)

    def test_37_wrong_task_reference_is_rejected_by_gate(self):
        rows = {"task": [{"id": "T-001"}, {"id": "T-002"}],
                "change": [{"id": "C-001", "for_task": "T-001", "status": "done"}],
                "modification": [{"id": "M-001", "for_task": "T-002", "via_change_list": "C-001"}]}
        self.assertFalse(gate.check_links(rows)[0])

    def test_38_pending_upgrade_prevents_green(self):
        p = self.kit / ".runtime/upgrades/incomplete/upgrade.json"
        p.parent.mkdir(parents=True)
        p.write_text('{"state":"prepared"}', encoding="utf-8")
        rc, output = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("未完成升级", output)

    @unittest.skipUnless(os.name == "nt", "Windows junction test")
    def test_39_real_windows_junction_is_rejected(self):
        target, alias = self.ws / "actual", self.ws / "alias"
        target.mkdir()
        (target / "file.txt").write_text("keep")
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)],
                                capture_output=True, text=True, errors="replace", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(paths.linked(alias))
        with self.assertRaisesRegex(ValueError, "链接|联接"):
            paths.resolve(self.ws, "alias/file.txt")
        self.assertTrue(trash.plan(self.ws, self.kit, ["alias"])[1])
        self.assertEqual((target / "file.txt").read_text(), "keep")

    def test_40_partial_removal_failure_has_recoverable_backups(self):
        for rel in ("a.txt", "b.txt"):
            (self.ws / rel).write_text("original")
        original = Path.unlink
        def fail(p, *args, **kwargs):
            if p == self.ws / "b.txt":
                raise OSError("injected removal failure")
            return original(p, *args, **kwargs)
        with patch.object(Path, "unlink", fail):
            with self.assertRaises(OSError):
                trash.trash(self.ws, self.kit, [self.ws / "a.txt", self.ws / "b.txt"])
        batch = trash.listing(self.kit)[0]
        self.assertTrue(batch["items"][0]["removed"])
        self.assertFalse(batch["items"][1]["removed"])
        trash.restore(self.ws, self.kit, batch["stamp"])
        self.assertEqual((self.ws / "a.txt").read_text(), "original")
        self.assertEqual((self.ws / "b.txt").read_text(), "original")

    def test_41_empty_workspace_config_is_not_accepted(self):
        config.CONFIG.write_text('{"workspace":""}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "workspace"):
            config.load()
        self.assertNotEqual(self.check()[0], 0)

    def test_42_partial_directory_removal_is_recoverable(self):
        (self.ws / "partial/sub").mkdir(parents=True)
        (self.ws / "partial/a.txt").write_text("a")
        (self.ws / "partial/sub/b.txt").write_text("b")
        original = shutil.rmtree
        def partial(p, *args, **kwargs):
            if Path(p) == self.ws / "partial":
                (Path(p) / "a.txt").unlink()
                raise OSError("injected mid-tree remove failure")
            return original(p, *args, **kwargs)
        with patch.object(trash.shutil, "rmtree", partial):
            with self.assertRaisesRegex(OSError, "injected"):
                trash.trash(self.ws, self.kit, [self.ws / "partial"])
        batch = trash.listing(self.kit)[0]
        trash.restore(self.ws, self.kit, batch["stamp"])
        self.assertEqual(trash.snapshot(self.ws / "partial"), batch["items"][0]["snapshot"])

    def test_43_changed_partial_restore_target_is_not_overwritten(self):
        (self.ws / "partial/sub").mkdir(parents=True)
        (self.ws / "partial/a.txt").write_text("a")
        (self.ws / "partial/sub/b.txt").write_text("b")
        original = shutil.rmtree
        def partial(p, *args, **kwargs):
            if Path(p) == self.ws / "partial":
                (Path(p) / "a.txt").unlink()
                raise OSError("injected")
            return original(p, *args, **kwargs)
        with patch.object(trash.shutil, "rmtree", partial):
            with self.assertRaises(OSError):
                trash.trash(self.ws, self.kit, [self.ws / "partial"])
        batch = trash.listing(self.kit)[0]
        (self.ws / "partial/sub/b.txt").write_text("new user data")
        with self.assertRaises(FileExistsError):
            trash.restore(self.ws, self.kit, batch["stamp"])
        self.assertEqual((self.ws / "partial/sub/b.txt").read_text(), "new user data")


    def start_fixture(self, readonly=False):
        ws = self.ws / "fresh"
        kit = ws / "work-kit"
        shutil.copytree(SOURCE, kit, ignore=shutil.ignore_patterns("__pycache__", "data", "config", ".runtime"))
        if readonly:
            (ws / "vendor").mkdir()
        return ws, kit

    def start_command(self, kit, *args):
        return subprocess.run([sys.executable, str(kit / "bin/start.py"), *args], env=ENV,
                              capture_output=True, encoding="utf-8")

    def test_44_start_empty_project_is_ready(self):
        ws, kit = self.start_fixture()
        result = self.start_command(kit)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[READY]", result.stdout)
        self.assertIn("work-kit/AGENTS.md", (ws / "AGENTS.md").read_text(encoding="utf-8"))

    def test_45_start_candidates_do_not_write_configuration(self):
        ws, kit = self.start_fixture(readonly=True)
        result = self.start_command(kit)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertFalse((kit / "config").exists())
        self.assertFalse((kit / "data").exists())
        self.assertFalse((ws / "AGENTS.md").exists())
        self.assertNotIn("[READY]", result.stdout)

    def test_46_start_confirmed_candidates_are_protected(self):
        ws, kit = self.start_fixture(readonly=True)
        result = self.start_command(kit, "--confirm-readonly")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        cfg = json.loads((kit / "config/kit.config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["readonly_roots"], [str(ws / "vendor").replace("\\", "/")])

    def test_47_start_repeat_preserves_configuration_and_data(self):
        ws, kit = self.start_fixture()
        self.assertEqual(self.start_command(kit).returncode, 0)
        config_bytes = (kit / "config/kit.config.json").read_bytes()
        data = {p.name: p.read_bytes() for p in (kit / "data").iterdir() if p.is_file()}
        self.assertEqual(self.start_command(kit).returncode, 0)
        self.assertEqual(config_bytes, (kit / "config/kit.config.json").read_bytes())
        self.assertEqual(data, {p.name: p.read_bytes() for p in (kit / "data").iterdir() if p.is_file()})

    def test_48_start_checks_failure_never_reports_ready(self):
        ws, kit = self.start_fixture()
        self.assertEqual(self.start_command(kit).returncode, 0)
        (kit / "data/task-ledger.jsonl").write_text("invalid JSON", encoding="utf-8")
        result = self.start_command(kit)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("[READY]", result.stdout)

    def test_49_start_wrong_project_refused_without_writes(self):
        ws, kit = self.start_fixture()
        result = self.start_command(kit, "--workspace", str(self.ws))
        self.assertEqual(result.returncode, 2)
        self.assertFalse((kit / "config").exists())


if __name__ == "__main__":
    print("Fixture evidence:", TEST_ROOT, flush=True)
    unittest.main(verbosity=2)
