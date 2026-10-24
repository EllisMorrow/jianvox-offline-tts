import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_release import release_files
from setup_offline import verify_payload
import hashlib


class DeliveryTests(unittest.TestCase):
    def test_release_allowlist_excludes_local_payload(self):
        files = [p.relative_to(ROOT).as_posix() for p in release_files()]
        self.assertTrue("app/reader_app.py" in files)
        self.assertTrue("scripts/bootstrap.ps1" in files)
        self.assertFalse(any(p.startswith((".local/", ".downloads/", "runtime/", "dist/")) for p in files))
        self.assertFalse(any(p.endswith((".exe", ".whl", ".wav", ".safetensors")) for p in files))

    def test_setup_rejects_corrupt_or_missing_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = {"path": "nested/file.whl", "size": 5, "sha256": hashlib.sha256(b"hello").hexdigest()}
            manifest = {"files": [item]}
            with self.assertRaises(RuntimeError):
                verify_payload(root, manifest)
            path = root / item["path"]
            path.parent.mkdir()
            path.write_bytes(b"wrong")
            with self.assertRaises(RuntimeError):
                verify_payload(root, manifest)
            path.write_bytes(b"hello")
            verify_payload(root, manifest)


if __name__ == "__main__":
    unittest.main()
