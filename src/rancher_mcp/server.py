"""Rancher MCP Server: expose Rancher clusters as MCP tools (SRE copilot).

Design principles
-----------------
1. Read-only by default. Mutating tools (scale, redeploy) only work when
   RANCHER_MCP_ALLOW_WRITE=true, and scaling is capped by
   RANCHER_MCP_MAX_REPLICAS (default 20).
2. Credentials via environment variables only (RANCHER_URL, RANCHER_TOKEN).
3. Compact, summarized responses: tools return the fields an SRE needs,
   not raw multi-kilobyte API payloads.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import RancherClient

mcp = FastMCP("rancher")

MAX_REPLICAS_DEFAULT = 20

UNHEALTHY_POD_STATES = {
    "crashloopbackoff",
    "error",
    "failed",
    "imagepullbackoff",
    "errimagepull",
    "oomkilled",
    "pending",
    "terminating",
    "unschedulable",
}


@lru_cache(maxsize=1)
def get_client() -> RancherClient:
    return RancherClient()


def _writes_allowed() -> bool:
    return os.environ.get("RANCHER_MCP_ALLOW_WRITE", "false").lower() == "true"


def _write_blocked_message() -> dict[str, str]:
    return {
        "error": (
            "Ações de escrita estão desabilitadas. Este servidor roda em modo "
            "somente leitura por padrão. Para habilitar, defina "
            "RANCHER_MCP_ALLOW_WRITE=true no ambiente do servidor."
        )
    }


# ---------------------------------------------------------------------- #
# summarizers                                                            #
# ---------------------------------------------------------------------- #
def _summarize_cluster(cluster: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": cluster.get("id"),
        "name": cluster.get("name"),
        "state": cluster.get("state"),
        "provider": cluster.get("provider") or cluster.get("driver"),
        "kubernetes_version": (cluster.get("version") or {}).get("gitVersion"),
        "node_count": cluster.get("nodeCount"),
        "cpu_capacity": (cluster.get("capacity") or {}).get("cpu"),
        "memory_capacity": (cluster.get("capacity") or {}).get("memory"),
        "transitioning_message": cluster.get("transitioningMessage") or None,
    }


def _summarize_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node.get("id"),
        "hostname": node.get("hostname") or node.get("nodeName"),
        "state": node.get("state"),
        "roles": [
            role
            for role, enabled in (
                ("controlplane", node.get("controlPlane")),
                ("etcd", node.get("etcd")),
                ("worker", node.get("worker")),
            )
            if enabled
        ],
        "cpu_requested": (node.get("requested") or {}).get("cpu"),
        "memory_requested": (node.get("requested") or {}).get("memory"),
        "transitioning_message": node.get("transitioningMessage") or None,
    }


def _summarize_workload(workload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": workload.get("id"),
        "name": workload.get("name"),
        "namespace": workload.get("namespaceId"),
        "type": workload.get("type"),
        "state": workload.get("state"),
        "scale": workload.get("scale"),
        "images": [c.get("image") for c in workload.get("containers", [])],
        "transitioning_message": workload.get("transitioningMessage") or None,
    }


def _pod_restart_count(pod: dict[str, Any]) -> int:
    return sum(
        int(status.get("restartCount") or 0)
        for status in pod.get("containers", [])
        if isinstance(status, dict)
    )


def _summarize_pod(pod: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": pod.get("id"),
        "name": pod.get("name"),
        "namespace": pod.get("namespaceId"),
        "state": pod.get("state"),
        "node": pod.get("nodeId"),
        "restart_count": _pod_restart_count(pod),
        "workload_id": pod.get("workloadId"),
        "transitioning_message": pod.get("transitioningMessage") or None,
    }


def _is_unhealthy(pod: dict[str, Any]) -> bool:
    state = str(pod.get("state") or "").lower()
    return state in UNHEALTHY_POD_STATES or _pod_restart_count(pod) > 3


def _summarize_event(event: dict[str, Any]) -> dict[str, Any]:
    involved = event.get("involvedObject") or {}
    return {
        "type": event.get("type"),
        "reason": event.get("reason"),
        "message": event.get("message"),
        "object": f"{involved.get('kind')}/{involved.get('name')}",
        "namespace": involved.get("namespace"),
        "count": event.get("count"),
        "last_seen": event.get("lastTimestamp") or event.get("eventTime"),
    }


# ---------------------------------------------------------------------- #
# read-only tools                                                        #
# ---------------------------------------------------------------------- #
@mcp.tool()
def list_clusters() -> list[dict[str, Any]]:
    """List all Rancher-managed clusters with state and capacity summary."""
    return [_summarize_cluster(c) for c in get_client().list_clusters()]


@mcp.tool()
def list_nodes(cluster_id: str) -> list[dict[str, Any]]:
    """List the nodes of a cluster with roles, state and requested resources."""
    return [_summarize_node(n) for n in get_client().list_nodes(cluster_id)]


@mcp.tool()
def list_projects(cluster_id: str) -> list[dict[str, Any]]:
    """List Rancher projects of a cluster (project ids are needed to inspect workloads)."""
    return [
        {"id": p.get("id"), "name": p.get("name"), "state": p.get("state")}
        for p in get_client().list_projects(cluster_id)
    ]


@mcp.tool()
def list_workloads(project_id: str, namespace: str | None = None) -> list[dict[str, Any]]:
    """List workloads (deployments, daemonsets...) of a project, optionally filtered by namespace."""
    return [
        _summarize_workload(w) for w in get_client().list_workloads(project_id, namespace)
    ]


@mcp.tool()
def list_pods(
    project_id: str,
    namespace: str | None = None,
    only_unhealthy: bool = False,
) -> list[dict[str, Any]]:
    """List pods of a project. Set only_unhealthy=True to see only pods in
    CrashLoopBackOff/Error/Pending states or with more than 3 restarts."""
    pods = get_client().list_pods(project_id, namespace)
    if only_unhealthy:
        pods = [p for p in pods if _is_unhealthy(p)]
    return [_summarize_pod(p) for p in pods]


@mcp.tool()
def get_pod_logs(
    cluster_id: str,
    namespace: str,
    pod_name: str,
    container: str | None = None,
    tail_lines: int = 100,
    previous: bool = False,
) -> str:
    """Fetch the last N log lines of a pod. Use previous=True to read logs
    from the previous (crashed) container instance."""
    tail_lines = max(1, min(int(tail_lines), 1000))
    return get_client().get_pod_logs(
        cluster_id, namespace, pod_name, container, tail_lines, previous
    )


@mcp.tool()
def get_events(
    cluster_id: str,
    namespace: str | None = None,
    only_warnings: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List recent Kubernetes events of a cluster (Warning events by default)."""
    events = get_client().list_events(cluster_id, namespace)
    if only_warnings:
        events = [e for e in events if e.get("type") == "Warning"]
    events = sorted(
        events,
        key=lambda e: str(e.get("lastTimestamp") or e.get("eventTime") or ""),
        reverse=True,
    )
    return [_summarize_event(e) for e in events[: max(1, min(int(limit), 200))]]


@mcp.tool()
def diagnose_workload(project_id: str, workload_id: str) -> dict[str, Any]:
    """One-shot diagnosis of a workload: state, unhealthy pods, restart counts
    and related Warning events. Ideal first call when something is failing."""
    client = get_client()
    workload = _summarize_workload(client.get_workload(project_id, workload_id))

    namespace = workload.get("namespace")
    pods = [
        p
        for p in client.list_pods(project_id, namespace)
        if p.get("workloadId") == workload_id
    ]
    unhealthy = [_summarize_pod(p) for p in pods if _is_unhealthy(p)]

    cluster_id = project_id.split(":", 1)[0]
    events = [
        _summarize_event(e)
        for e in client.list_events(cluster_id, namespace)
        if e.get("type") == "Warning"
        and workload.get("name", "")
        and workload["name"] in str((e.get("involvedObject") or {}).get("name", ""))
    ]

    verdict = "healthy"
    if unhealthy or str(workload.get("state", "")).lower() not in {"active", "healthy"}:
        verdict = "degraded"

    return {
        "verdict": verdict,
        "workload": workload,
        "pods_total": len(pods),
        "pods_unhealthy": unhealthy,
        "recent_warning_events": events[:20],
    }


# ---------------------------------------------------------------------- #
# write tools (gated)                                                    #
# ---------------------------------------------------------------------- #
@mcp.tool()
def scale_workload(project_id: str, workload_id: str, replicas: int) -> dict[str, Any]:
    """Scale a workload to N replicas. Requires RANCHER_MCP_ALLOW_WRITE=true.
    Capped by RANCHER_MCP_MAX_REPLICAS (default 20)."""
    if not _writes_allowed():
        return _write_blocked_message()

    max_replicas = int(os.environ.get("RANCHER_MCP_MAX_REPLICAS", MAX_REPLICAS_DEFAULT))
    if not 0 <= replicas <= max_replicas:
        return {
            "error": f"replicas deve estar entre 0 e {max_replicas} "
            f"(ajuste RANCHER_MCP_MAX_REPLICAS se necessário)."
        }

    result = get_client().scale_workload(project_id, workload_id, replicas)
    return {
        "status": "scaled",
        "workload": _summarize_workload(result) if result else workload_id,
        "replicas": replicas,
    }


@mcp.tool()
def redeploy_workload(project_id: str, workload_id: str) -> dict[str, Any]:
    """Trigger a rolling redeploy of a workload (equivalent to the Rancher UI
    'Redeploy' button). Requires RANCHER_MCP_ALLOW_WRITE=true."""
    if not _writes_allowed():
        return _write_blocked_message()

    get_client().redeploy_workload(project_id, workload_id)
    return {"status": "redeploy_triggered", "workload_id": workload_id}


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
