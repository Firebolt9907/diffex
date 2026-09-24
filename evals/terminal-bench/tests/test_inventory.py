"""Evidence inventory accepts portable cache links without weakening containment."""

import os
from pathlib import Path
import tempfile
import unittest

from diffex_terminal_bench.gate import inventory


class InventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.root = self.parent / "trial"
        self.cache = self.root / "agent/state/home/.cache/huggingface/hub/model"
        self.blob = self.cache / "blobs/weights"
        self.blob.parent.mkdir(parents=True)
        self.blob.write_bytes(b"model weights")
        self.link = self.cache / "snapshots/revision/model.safetensors"
        self.link.parent.mkdir(parents=True)

    def test_cache_link_binds_target_text_type_and_contents(self):
        self.link.symlink_to("../../blobs/weights")
        before = inventory(self.root)
        self.assertTrue(before[self.link.relative_to(self.root).as_posix()].startswith("symlink:"))
        self.assertEqual(inventory(self.root), before)
        self.assertEqual(os.readlink(self.link), "../../blobs/weights")
        self.blob.write_bytes(b"changed weights")
        self.assertNotEqual(inventory(self.root), before)
        self.blob.write_bytes(b"model weights")
        self.link.unlink()
        self.link.symlink_to("../../blobs/./weights")
        self.assertNotEqual(inventory(self.root), before)
        self.link.unlink()
        self.link.write_bytes(self.blob.read_bytes())
        self.assertNotEqual(inventory(self.root), before)

    def test_unsafe_link_targets_are_rejected(self):
        outside = self.parent / "secret"
        outside.write_text("must not be read")
        cases = {
            "absolute": str(self.blob),
            "escape": "../secret",
            "escape-and-reenter": "../trial/agent/state/home/.cache/huggingface/hub/model/blobs/weights",
            "dangling": "missing",
            "directory": "agent",
            "chain": "alias",
            "cycle": "link",
            "file-traversal": "agent/state/home/.cache/huggingface/hub/model/blobs/weights/../weights",
        }
        # Test one link at a time so a different invalid link cannot mask it.
        link = self.root / "link"
        for name, target in cases.items():
            with self.subTest(name=name):
                if name == "chain":
                    (self.root / "alias").symlink_to(self.blob.relative_to(self.root))
                link.symlink_to(target)
                with self.assertRaisesRegex(ValueError, "symlinks"):
                    inventory(self.root)
                link.unlink()
                (self.root / "alias").unlink(missing_ok=True)

    def test_directory_link_traversal_is_rejected(self):
        (self.root / "z-alias").symlink_to(self.cache / "blobs", target_is_directory=True)
        (self.root / "a-link").symlink_to("z-alias/weights")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            inventory(self.root)

    def test_linked_root_is_rejected(self):
        alias = self.parent / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "root must not be a symlink"):
            inventory(alias)


if __name__ == "__main__":
    unittest.main()
