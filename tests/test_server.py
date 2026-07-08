"""Tests for the Rancher MCP server tools (Rancher client fully mocked)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rancher_mcp import server  # noqa: E402


@pytest.fixture()
def mock_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(server, "get_client", lambda: client)
    return client


@pytest.fixture(autouse=True)
def read_only_env(monkeypatch):
    monkeypatch.delenv("RANCHER_MCP_ALLOW_WRITE", raising=False)
    monkeypatch.delenv("RANCHER_MCP_MAX_REPLICAS", raising=False)


def _pod(name, state="running", restarts=0, workload="deployment:demo:web"):
    return {
        "id": f"pod-{name}",
        "name": name,
        "namespaceId": "demo",
        "state": state,
        "nodeId": "node-1",
        "workloadId": workload,
        "containers": [{"restartCount": restarts}],
    }


# ---------------------------------------------------------------------- #
# read-only tools                                                        #
# ---------------------------------------------------------------------- #
def test_list_clusters_summarizes(mock_client):
    mock_client.list_clusters.return_value = [
        {
            "id": "c-abc",
            "name": "prod",
            "state": "active",
            "provider": "rke2",
            "version": {"gitVersion": "v1.28.9"},
            "nodeCount": 3,
            "capacity": {"cpu": "12", "memory": "48Gi"},
        }
    ]
    result = server.list_clusters()
    assert result == [
        {
            "id": "c-abc",
            "name": "prod",
            "state": "active",
            "provider": "rke2",
            "kubernetes_version": "v1.28.9",
            "node_count": 3,
            "cpu_capacity": "12",
            "memory_capacity": "48Gi",
            "transitioning_message": None,
        }
    ]


def test_list_pods_only_unhealthy_filters(mock_client):
    mock_client.list_pods.return_value = [
        _pod("web-1", state="running", restarts=0),
        _pod("web-2", state="crashLoopBackOff", restarts=12),
        _pod("web-3", state="running", restarts=7),
    ]
    result = server.list_pods("c-abc:p-xyz", only_unhealthy=True)
    names = [p["name"] for p in result]
    assert names == ["web-2", "web-3"]
    assert result[0]["restart_count"] == 12


def test_get_pod_logs_caps_tail_lines(mock_client):
    mock_client.get_pod_logs.return_value = "log line"
    server.get_pod_logs("c-abc", "demo", "web-1", tail_lines=99999)
    _, _, _, _, tail_lines, _ = mock_client.get_pod_logs.call_args[0]
    assert tail_lines == 1000


def test_get_events_filters_warnings_and_sorts(mock_client):
    mock_client.list_events.return_value = [
        {
            "type": "Normal",
            "reason": "Scheduled",
            "message": "ok",
            "involvedObject": {"kind": "Pod", "name": "web-1", "namespace": "demo"},
            "lastTimestamp": "2026-06-12T10:00:00Z",
        },
        {
            "type": "Warning",
            "reason": "BackOff",
            "message": "Back-off restarting failed container",
            "involvedObject": {"kind": "Pod", "name": "web-2", "namespace": "demo"},
            "lastTimestamp": "2026-06-12T11:00:00Z",
        },
    ]
    result = server.get_events("c-abc")
    assert len(result) == 1
    assert result[0]["reason"] == "BackOff"


def test_diagnose_workload_degraded(mock_client):
    mock_client.get_workload.return_value = {
        "id": "deployment:demo:web",
        "name": "web",
        "namespaceId": "demo",
        "type": "deployment",
        "state": "updating",
        "scale": 3,
        "containers": [{"image": "web:1.2.3"}],
    }
    mock_client.list_pods.return_value = [
        _pod("web-1"),
        _pod("web-2", state="crashLoopBackOff", restarts=9),
    ]
    mock_client.list_events.return_value = [
        {
            "type": "Warning",
            "reason": "BackOff",
            "message": "restarting",
            "involvedObject": {"kind": "Pod", "name": "web-2", "namespace": "demo"},
            "lastTimestamp": "2026-06-12T11:00:00Z",
        }
    ]
    result = server.diagnose_workload("c-abc:p-xyz", "deployment:demo:web")
    assert result["verdict"] == "degraded"
    assert result["pods_total"] == 2
    assert len(result["pods_unhealthy"]) == 1
    assert result["recent_warning_events"][0]["reason"] == "BackOff"


def test_diagnose_workload_healthy(mock_client):
    mock_client.get_workload.return_value = {
        "id": "deployment:demo:web",
        "name": "web",
        "namespaceId": "demo",
        "type": "deployment",
        "state": "active",
        "scale": 2,
        "containers": [{"image": "web:1.2.3"}],
    }
    mock_client.list_pods.return_value = [_pod("web-1"), _pod("web-2")]
    mock_client.list_events.return_value = []
    result = server.diagnose_workload("c-abc:p-xyz", "deployment:demo:web")
    assert result["verdict"] == "healthy"
    assert result["pods_unhealthy"] == []


# ---------------------------------------------------------------------- #
# write gating                                                           #
# ---------------------------------------------------------------------- #
def test_scale_blocked_by_default(mock_client):
    result = server.scale_workload("c-abc:p-xyz", "deployment:demo:web", 5)
    assert "error" in result
    mock_client.scale_workload.assert_not_called()


def test_redeploy_blocked_by_default(mock_client):
    result = server.redeploy_workload("c-abc:p-xyz", "deployment:demo:web")
    assert "error" in result
    mock_client.redeploy_workload.assert_not_called()


def test_scale_allowed_with_flag(mock_client, monkeypatch):
    monkeypatch.setenv("RANCHER_MCP_ALLOW_WRITE", "true")
    mock_client.scale_workload.return_value = {
        "id": "deployment:demo:web",
        "name": "web",
        "namespaceId": "demo",
        "type": "deployment",
        "state": "active",
        "scale": 5,
        "containers": [],
    }
    result = server.scale_workload("c-abc:p-xyz", "deployment:demo:web", 5)
    assert result["status"] == "scaled"
    mock_client.scale_workload.assert_called_once_with(
        "c-abc:p-xyz", "deployment:demo:web", 5
    )


def test_scale_respects_max_replicas(mock_client, monkeypatch):
    monkeypatch.setenv("RANCHER_MCP_ALLOW_WRITE", "true")
    result = server.scale_workload("c-abc:p-xyz", "deployment:demo:web", 999)
    assert "error" in result
    mock_client.scale_workload.assert_not_called()
