import unittest
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from security import hash_password, is_hashed_password, verify_password


class SecurityTests(unittest.TestCase):
    def test_hash_password_roundtrip(self):
        hashed = hash_password("secreto123")
        self.assertTrue(is_hashed_password(hashed))
        self.assertTrue(verify_password("secreto123", hashed))
        self.assertFalse(verify_password("incorrecta", hashed))

    def test_legacy_plaintext_password_still_verifies(self):
        self.assertTrue(verify_password("legacy", "legacy"))
        self.assertFalse(verify_password("otro", "legacy"))


if __name__ == "__main__":
    unittest.main()
