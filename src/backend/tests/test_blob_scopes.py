import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blob_scope_config import configured_blob_scopes  # noqa: E402


class BlobScopeConfigTests(unittest.TestCase):
    def test_returns_configured_scopes_when_multiple_prefixes_exist(self):
        scopes = configured_blob_scopes(
            "",
            "alta_tension",
            "baja_tension/",
            " guias_tecnicas ",
            "rite",
        )

        self.assertEqual(
            scopes,
            [
                ("alta_tension", "alta_tension/"),
                ("baja_tension", "baja_tension/"),
                ("guias_tecnicas", "guias_tecnicas/"),
                ("rite", "rite/"),
            ],
        )

    def test_falls_back_to_single_prefix_when_scoped_prefixes_are_missing(self):
        scopes = configured_blob_scopes("documentacion", "", "", "", "")

        self.assertEqual(scopes, [("", "documentacion/")])


if __name__ == "__main__":
    unittest.main()
