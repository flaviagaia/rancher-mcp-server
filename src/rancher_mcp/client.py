"""HTTP client for the Rancher v3 API and the Kubernetes proxy.

Authentication uses a Rancher API token (Bearer). Credentials come
exclusively from environment variables; nothing is ever hardcoded.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

DEFAULT_TIMEOUT = 20.0


class RancherConfigError(RuntimeError):
    """Raised when required configuration is missing."""


class RancherClient:
    """Thin wrapper over the Rancher management API (/v3) and k8s proxy."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        verify_tls: bool | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        base_url = base_url or os.environ.get("RANCHER_URL", "")
        token = token or os.environ.get("RANCHER_TOKEN", "")
        if not base_url or not token:
            raise RancherConfigError(
                "Defina as variáveis de ambiente RANCHER_URL e RANCHER_TOKEN "
                "(ex.: RANCHER_URL=https://rancher.example.com, "
                "RANCHER_TOKEN=token-xxxxx:yyyyy)."
            )
        if verify_tls is None:
            verify_tls = os.environ.get("RANCHER_VERIFY_TLS", "true").lower() != "false"

        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            verify=verify_tls,
            timeout=timeout,
        )

    # ------------------------------------------------------------------ #
    # low level                                                          #
    # ------------------------------------------------------------------ #
    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._http.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def _get_text(self, path: str, params: dict[str, Any] | None = None) -> str:
        response = self._http.get(path, params=params)
        response.raise_for_status()
        return response.text

    def _post(self, path: str, json: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self._http.post(path, json=json or {})
        response.raise_for_status()
        return response.json() if response.content else {}

    def _put(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        response = self._http.put(path, json=json)
        response.raise_for_status()
        return response.json() if response.content else {}

    # ------------------------------------------------------------------ #
    # management API (/v3)                                               #
    # ------------------------------------------------------------------ #
    def list_clusters(self) -> list[dict[str, Any]]:
        return self._get("/v3/clusters").get("data", [])

    def get_cluster(self, cluster_id: str) -> dict[str, Any]:
        return self._get(f"/v3/clusters/{cluster_id}")

    def list_nodes(self, cluster_id: str) -> list[dict[str, Any]]:
        return self._get("/v3/nodes", params={"clusterId": cluster_id}).get("data", [])

    def list_projects(self, cluster_id: str) -> list[dict[str, Any]]:
        return self._get("/v3/projects", params={"clusterId": cluster_id}).get("data", [])

    # ------------------------------------------------------------------ #
    # project API (workloads / pods)                                     #
    # ------------------------------------------------------------------ #
    def list_workloads(
        self, project_id: str, namespace: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if namespace:
            params["namespaceId"] = namespace
        return self._get(f"/v3/project/{project_id}/workloads", params=params).get(
            "data", []
        )

    def get_workload(self, project_id: str, workload_id: str) -> dict[str, Any]:
        return self._get(f"/v3/project/{project_id}/workloads/{workload_id}")

    def list_pods(
        self, project_id: str, namespace: str | None = None
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if namespace:
            params["namespaceId"] = namespace
        return self._get(f"/v3/project/{project_id}/pods", params=params).get("data", [])

    def scale_workload(
        self, project_id: str, workload_id: str, replicas: int
    ) -> dict[str, Any]:
        workload = self.get_workload(project_id, workload_id)
        workload["scale"] = replicas
        return self._put(f"/v3/project/{project_id}/workloads/{workload_id}", workload)

    def redeploy_workload(self, project_id: str, workload_id: str) -> dict[str, Any]:
        return self._post(
            f"/v3/project/{project_id}/workloads/{workload_id}?action=redeploy"
        )

    # ------------------------------------------------------------------ #
    # kubernetes proxy (/k8s/clusters/<id>)                              #
    # ------------------------------------------------------------------ #
    def get_pod_logs(
        self,
        cluster_id: str,
        namespace: str,
        pod_name: str,
        container: str | None = None,
        tail_lines: int = 100,
        previous: bool = False,
    ) -> str:
        params: dict[str, Any] = {"tailLines": tail_lines}
        if container:
            params["container"] = container
        if previous:
            params["previous"] = "true"
        return self._get_text(
            f"/k8s/clusters/{cluster_id}/api/v1/namespaces/{namespace}/pods/{pod_name}/log",
            params=params,
        )

    def list_events(
        self, cluster_id: str, namespace: str | None = None
    ) -> list[dict[str, Any]]:
        if namespace:
            path = f"/k8s/clusters/{cluster_id}/api/v1/namespaces/{namespace}/events"
        else:
            path = f"/k8s/clusters/{cluster_id}/api/v1/events"
        return self._get(path).get("items", [])

    def close(self) -> None:
        self._http.close()
