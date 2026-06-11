import sys
import unittest
from pathlib import Path
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from blob_scope_config import configured_blob_scopes  # noqa: E402


class BlobScopeConfigTests(unittest.TestCase):
    def test_returns_configured_scopes_when_multiple_prefixes_exist(self):
        scopes = configured_blob_scopes(
            "",
            "alta_tension",
            "baja_tension/",
            "fotovoltaica_om/",
            "grupos_electrogenos/",
            " guias_tecnicas ",
            "rite",
        )

        self.assertEqual(
            scopes,
            [
                ("alta_tension", "alta_tension/"),
                ("baja_tension", "baja_tension/"),
                ("fotovoltaica_om", "fotovoltaica_om/"),
                ("grupos_electrogenos", "grupos_electrogenos/"),
                ("guias_tecnicas", "guias_tecnicas/"),
                ("rite", "rite/"),
            ],
        )

    def test_falls_back_to_single_prefix_when_scoped_prefixes_are_missing(self):
        scopes = configured_blob_scopes("documentacion", "", "", "", "", "", "")

        self.assertEqual(scopes, [("", "documentacion/")])

    def test_derives_scopes_from_domains_json_when_no_explicit_prefixes(self):
        import blob_scope_config

        fake_domains = Path(__file__).with_name("_fake_domains.json")
        fake_domains.write_text(
            """
            {
              "domains": {
                "rrhh": {
                  "folder_prefixes": ["rrhh", "personas"]
                },
                "pendiente_ocr": {
                  "blob_sync": false,
                  "folder_prefixes": ["pendiente_ocr"]
                },
                "administracion": {
                  "folder_prefixes": [" administracion/ "]
                }
              }
            }
            """,
            encoding="utf-8",
        )
        try:
            with mock.patch.object(blob_scope_config, "_DOMAINS_CONFIG_PATH", fake_domains):
                scopes = configured_blob_scopes("")
        finally:
            fake_domains.unlink(missing_ok=True)

        self.assertEqual(
            scopes,
            [
                ("rrhh", "rrhh/"),
                ("administracion", "administracion/"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
