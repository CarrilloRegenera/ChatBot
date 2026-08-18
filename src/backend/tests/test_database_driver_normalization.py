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

    def test_connection_retries_transient_login_failure(self):
        class FakeConnection:
            pass

        original_attempts = os.environ.get("SQL_CONNECTION_RETRY_ATTEMPTS")
        original_delay = os.environ.get("SQL_CONNECTION_RETRY_DELAY_SECS")
        try:
            os.environ["SQL_CONNECTION_RETRY_ATTEMPTS"] = "3"
            os.environ["SQL_CONNECTION_RETRY_DELAY_SECS"] = "0"
            login_timeout = database.pyodbc.Error("HYT00", "Login timeout expired")
            with patch.object(database.pyodbc, "connect", side_effect=[login_timeout, FakeConnection()]) as connect:
                with patch.object(database.time, "sleep") as sleep:
                    connection = database._connect_with_retry()
        finally:
            if original_attempts is None:
                os.environ.pop("SQL_CONNECTION_RETRY_ATTEMPTS", None)
            else:
                os.environ["SQL_CONNECTION_RETRY_ATTEMPTS"] = original_attempts
            if original_delay is None:
                os.environ.pop("SQL_CONNECTION_RETRY_DELAY_SECS", None)
            else:
                os.environ["SQL_CONNECTION_RETRY_DELAY_SECS"] = original_delay

        self.assertIsInstance(connection, FakeConnection)
        self.assertEqual(connect.call_count, 2)
        sleep.assert_called_once_with(0.0)


if __name__ == "__main__":
    unittest.main()
