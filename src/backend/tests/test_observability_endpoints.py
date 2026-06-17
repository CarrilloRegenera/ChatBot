import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class FakeCursor:
    def __init__(self, results):
        self._results = list(results)
        self._index = 0

    def execute(self, *args, **kwargs):
        pass

    def fetchone(self):
        if self._index < len(self._results):
            row = self._results[self._index]
            self._index += 1
            return row
        return None

    def fetchall(self):
        if self._index < len(self._results):
            rows = self._results[self._index]
            self._index += 1
            return rows
        return []


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def _make_client():
    import config as cfg
    cfg.ADMIN_API_KEY = "test-admin-key"
    cfg.ENTRA_ENABLED = False

    import importlib
    import main
    importlib.reload(main)

    from fastapi.testclient import TestClient
    return TestClient(main.app, raise_server_exceptions=False)


def _admin_headers():
    return {"x-admin-key": "test-admin-key"}


class TestRetrievalStatsEndpoint:
    def test_returns_summary_metrics(self):
        summary_row = (100, 10, 50, 40, 0.82, 5, 3)
        route_rows = [
            ("knowledge", 60, 5, 0.85),
            ("business_licitaciones", 40, 5, 0.78),
        ]
        cursor = FakeCursor([summary_row, route_rows])
        conn = FakeConn(cursor)

        with mock.patch("database.pyodbc") as mock_pyodbc:
            mock_pyodbc.connect.return_value = conn
            client = _make_client()
            res = client.get("/admin/retrieval-stats?days=7", headers=_admin_headers())

        assert res.status_code == 200
        data = res.json()
        assert data["total"] == 100
        assert data["rechazadas"] == 10
        assert data["validadas"] == 50
        assert data["sin_info"] == 3
        assert len(data["by_route"]) == 2

    def test_requires_admin(self):
        client = _make_client()
        res = client.get("/admin/retrieval-stats?days=7")
        assert res.status_code in (401, 403)


class TestRetrievalTimelineEndpoint:
    def test_returns_timeline_and_deploys(self):
        timeline_rows = [
            ("2025-06-02", 50, 3, 20, 0.84, 1),
            ("2025-06-09", 60, 5, 25, 0.81, 2),
        ]
        deploy_rows = [
            (1, "sandbox", "success", "2025-06-03 10:00:00", "2025-06-03 10:15:00"),
        ]
        cursor = FakeCursor([timeline_rows, deploy_rows])
        conn = FakeConn(cursor)

        with mock.patch("database.pyodbc") as mock_pyodbc:
            mock_pyodbc.connect.return_value = conn
            client = _make_client()
            res = client.get("/admin/retrieval-stats/timeline?weeks=4", headers=_admin_headers())

        assert res.status_code == 200
        data = res.json()
        assert len(data["timeline"]) == 2
        assert len(data["deployments"]) == 1
        assert data["timeline"][0]["week"] == "2025-06-02"


class TestRetrievalCompareEndpoint:
    def test_requires_both_deploy_ids(self):
        client = _make_client()
        res = client.get("/admin/retrieval-stats/compare", headers=_admin_headers())
        assert res.status_code == 400

    def test_compares_two_deploys(self):
        deploy_rows = [
            (1, "2025-06-01 10:00:00"),
            (2, "2025-06-08 10:00:00"),
        ]
        next_deploy_row = ("2025-06-15 10:00:00",)
        stats_a_row = (40, 3, 20, 0.84, 1)
        stats_b_row = (50, 2, 30, 0.88, 0)
        cursor = FakeCursor([deploy_rows, next_deploy_row, stats_a_row, stats_b_row])
        conn = FakeConn(cursor)

        with mock.patch("database.pyodbc") as mock_pyodbc:
            mock_pyodbc.connect.return_value = conn
            client = _make_client()
            res = client.get(
                "/admin/retrieval-stats/compare?deploy_a=1&deploy_b=2",
                headers=_admin_headers(),
            )

        assert res.status_code == 200
        data = res.json()
        assert "period_a" in data
        assert "period_b" in data
        assert "deltas" in data
