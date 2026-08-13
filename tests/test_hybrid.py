from __future__ import annotations

import unittest
from argparse import Namespace

from scripts.run_hybrid import _remote_command


class HybridCommandTests(unittest.TestCase):
    def test_default_remote_dir_is_used_for_understand_and_report(self) -> None:
        args = Namespace(
            remote_project="/root/vspider",
            remote_python="/root/miniconda3/bin/python",
            profile="gpu",
            device="cuda:0",
            model="",
            digest=True,
            fast=True,
        )
        command = _remote_command(args, "/root/autodl-tmp/data/handoff/hybrid_xhs")
        self.assertIn("/root/autodl-tmp/data/handoff/hybrid_xhs", command)
        self.assertIn("/report.html", command)
        self.assertIn("--fast", command)


if __name__ == "__main__":
    unittest.main()
