import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault(
    "pyodbc",
    types.SimpleNamespace(
        drivers=lambda: [],
        connect=lambda *args, **kwargs: None,
        pooling=False,
        Connection=object,
    ),
)
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None))

import database  # noqa: E402


class DatabaseDriverNormalizationTests(unittest.TestCase):
    def test_preserves_explicit_driver_when_available(self):
        connection_string = (
            "Driver={ODBC Driver 18 for SQL Server};"
            "Server=tcp:example.database.windows.net,1433;"
            "Database=ChatBot;"
        )
        with patch.object(database, "_available_sql_drivers", return_value=["ODBC Driver 18 for SQL Server"]):
            self.assertEqual(database._normalize_sql_driver(connection_string), connection_string)

    def test_rewrites_explicit_driver_when_only_driver_17_is_available(self):
        connection_string = (
            "Driver={ODBC Driver 18 for SQL Server};"
            "Server=tcp:example.database.windows.net,1433;"
            "Database=ChatBot;"
        )
        with patch.object(database, "_available_sql_drivers", return_value=["ODBC Driver 17 for SQL Server"]):
            normalized = database._normalize_sql_driver(connection_string)

        self.assertIn("Driver={ODBC Driver 17 for SQL Server}", normalized)
        self.assertNotIn("Driver={ODBC Driver 18 for SQL Server}", normalized)

    def test_build_connection_string_uses_local_installed_driver(self):
        original_env = os.environ.copy()
        try:
            os.environ["SQL_DRIVER"] = "ODBC Driver 18 for SQL Server"
            os.environ["SQL_SERVER"] = "tcp:example.database.windows.net,1433"
            os.environ["SQL_DATABASE"] = "ChatBot"
            os.environ["SQL_USER"] = "user"
            os.environ["SQL_PASSWORD"] = "pass"
            with patch.object(database, "_available_sql_drivers", return_value=["ODBC Driver 17 for SQL Server"]):
                connection_string = database._build_connection_string()
        finally:
            os.environ.clear()
            os.environ.update(original_env)

        self.assertIn("Driver={ODBC Driver 17 for SQL Server}", connection_string)


if __name__ == "__main__":
    unittest.main()
