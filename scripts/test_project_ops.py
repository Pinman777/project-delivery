import json
from pathlib import Path
import tempfile
import unittest

import project_ops


class ProjectDeliveryTests(unittest.TestCase):
    def test_full_local_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            obsidian = Path(tmp) / "obsidian"
            args = project_ops.argparse.Namespace(
                root=root,
                slug="demo-project",
                mode="new",
                environment="local",
                goal="Verify lifecycle",
                verify_cmd="true",
                obsidian_root=obsidian,
                repo_url="",
                active_users=False,
                data_class="public",
                adopt=False,
            )
            project_ops.scaffold(args)
            self.assertTrue(project_ops.doctor(root)["ok"])
            project_ops.backup(root)
            project_ops.verify(root, "baseline-verified")
            project_ops.verify(root, "verifying")
            project_ops.verify(root, "review")
            project_ops.verify(root, "ready-for-release")
            project_ops.commit(root, "chore: verify lifecycle")
            project_ops.verify(root, "released", project_ops.head(root))
            project = project_ops.load_project(root)
            self.assertEqual(project["stage"], "released")
            self.assertTrue(project["last_backup"]["restore_check"] == "passed")
            self.assertTrue(project_ops.doctor(root)["ok"])
            self.assertTrue((obsidian / "PROJECT.md").exists())
            self.assertIn("scaffold", (root / "docs" / "DEVLOG.md").read_text())

    def test_adopt_preserves_release_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            obsidian = Path(tmp) / "obsidian"
            args = project_ops.argparse.Namespace(
                root=root, slug="demo-project", mode="new", environment="local",
                goal="Verify lifecycle", verify_cmd="true", obsidian_root=obsidian,
                repo_url="", active_users=False, data_class="public", adopt=False,
            )
            project_ops.scaffold(args)
            project_ops.backup(root)
            project_ops.verify(root, "baseline-verified")
            project_ops.verify(root, "verifying")
            project_ops.verify(root, "review")
            project_ops.verify(root, "ready-for-release")
            project_ops.commit(root, "chore: verify lifecycle")
            project_ops.verify(root, "released", project_ops.head(root))
            before = project_ops.load_project(root)
            args.mode = "existing"
            args.adopt = True
            project_ops.scaffold(args)
            after = project_ops.load_project(root)
            self.assertEqual(after["stage"], "released")
            self.assertEqual(after["last_good_commit"], before["last_good_commit"])
            self.assertEqual(after["created_at"], before["created_at"])
            self.assertEqual(after["last_backup"]["path"], before["last_backup"]["path"])

    def test_environment_lock_rejects_moved_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            obsidian = Path(tmp) / "obsidian"
            args = project_ops.argparse.Namespace(
                root=root, slug="demo-project", mode="new", environment="local",
                goal="Verify lock", verify_cmd="true", obsidian_root=obsidian,
                repo_url="", active_users=False, data_class="public", adopt=False,
            )
            project_ops.scaffold(args)
            moved = Path(tmp) / "other"
            moved.mkdir()
            (moved / "project.json").write_text((root / "project.json").read_text())
            with self.assertRaises(RuntimeError):
                project_ops.require_project(moved)


if __name__ == "__main__":
    unittest.main()
