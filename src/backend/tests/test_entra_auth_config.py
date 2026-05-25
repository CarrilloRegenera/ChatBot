import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from entra_config import allowed_audiences  # noqa: E402


class EntraAuthConfigTests(unittest.TestCase):
    def test_allowed_audiences_include_client_id_and_api_scope_prefix(self):
        audiences = allowed_audiences(
            "ebbcc0d0-1762-4d34-9229-baee8d9a6ee6",
            "api://ea8827b5-a433-4a75-bbe4-1770aeb16631/access_as_user",
        )

        self.assertEqual(
            audiences,
            (
                "ebbcc0d0-1762-4d34-9229-baee8d9a6ee6",
                "api://ea8827b5-a433-4a75-bbe4-1770aeb16631",
                "ea8827b5-a433-4a75-bbe4-1770aeb16631",
            ),
        )

    def test_allowed_audiences_supports_id_token_only_flow(self):
        audiences = allowed_audiences("ebbcc0d0-1762-4d34-9229-baee8d9a6ee6", "")

        self.assertEqual(audiences, ("ebbcc0d0-1762-4d34-9229-baee8d9a6ee6",))


if __name__ == "__main__":
    unittest.main()
