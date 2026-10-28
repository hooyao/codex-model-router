"""Historical plugin instructions remain verifiable without a CLI whitelist."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from evals.scripts import live_evidence as live


class LegacyContextPathTests(unittest.TestCase):
    def test_explicit_historical_skill_digest_keeps_path_and_manifest_checks(self):
        source = live.REPO_ROOT / "plugins/codex-model-router"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolver = root / "installed/hooks/execution_decision.py"
            resolver.parent.mkdir(parents=True)
            shutil.copyfile(source / "hooks/execution_decision.py", resolver)
            skill = root / "installed/skills/model-router/SKILL.md"
            skill.parent.mkdir(parents=True)
            skill.write_text("Historical skill fixture.\n", encoding="utf-8")
            manifest = root / "installed/.codex-plugin/plugin.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps(live.MANIFEST_V2), encoding="utf-8")
            captured = root / "captured-manifest.json"
            shutil.copyfile(manifest, captured)
            expected = (f"Router skill: {source / 'skills/model-router/SKILL.md'}\n"
                        f"Decision resolver program: {source / 'hooks/execution_decision.py'}")
            observed = f"Router skill: {skill}\nDecision resolver program: {resolver}"
            self.assertFalse(live.installed_hook_block_matches(observed, expected, captured))
            old_hash = live.sha256(skill)
            self.assertTrue(live.installed_hook_block_matches(observed, expected, captured, {old_hash}))
            skill.write_text("Changed instructions.\n", encoding="utf-8")
            self.assertFalse(live.installed_hook_block_matches(observed, expected, captured, {old_hash}))


if __name__ == "__main__":
    unittest.main()
