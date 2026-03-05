from __future__ import annotations

from typing import Any

import requests

from .types import Priority, Status


class PlaneTaskService:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        workspace_slug: str,
        project_id: str,
        timeout_seconds: int = 20,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.workspace_slug = workspace_slug
        self.project_id = project_id
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_token}",
                "x-api-key": api_token,
            }
        )

    def _request(self, method: str, path: str, json_payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        response = self.session.request(
            method=method,
            url=url,
            json=json_payload,
            timeout=self.timeout_seconds,
        )
        if response.status_code >= 400:
            raise ValueError(f"Plane API error ({response.status_code}): {response.text}")
        if not response.content:
            return {}
        return response.json()

    @staticmethod
    def _normalize(value: Any) -> str:
        return str(value or "").strip().lower().replace("_", " ")

    @staticmethod
    def _extract_state_group(state: dict[str, Any] | None) -> str:
        if not isinstance(state, dict):
            return ""
        raw_group = state.get("group") or state.get("type") or state.get("state_group")
        if isinstance(raw_group, dict):
            raw_group = raw_group.get("name") or raw_group.get("key") or raw_group.get("value")
        return PlaneTaskService._normalize(raw_group)

    @staticmethod
    def _map_status_from_name_group(name: str, group: str) -> Status | None:
        if name in {"backlog", "unstarted"}:
            return "backlog"
        if name in {"todo", "to do", "por hacer", "por_hacer"}:
            return "todo"
        if name in {"in progress", "started", "active", "doing", "ejecutando"}:
            return "in_progress"
        if name in {"done", "completed", "closed"}:
            return "done"
        if name in {"cancelled", "canceled", "cancelado"}:
            return "cancelled"
        if name == "blocked":
            return "blocked"

        if group == "backlog":
            return "backlog"
        if group == "unstarted":
            return "todo"
        if group == "started":
            return "in_progress"
        if group == "completed":
            return "done"
        if group in {"cancelled", "canceled"}:
            return "cancelled"
        return None

    def _resolve_status_from_state_id(self, state_id: str) -> Status | None:
        payload = self._request("GET", self._states_path())
        states = self._safe_results(payload)
        for state in states:
            if str(state.get("id", "")).strip() != state_id.strip():
                continue
            name = self._normalize(state.get("name"))
            group = self._extract_state_group(state)
            return self._map_status_from_name_group(name, group)
        return None

    @staticmethod
    def _safe_results(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            return payload["results"]
        return []

    def _states_path(self) -> str:
        return f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/states/"

    def _issues_path(self) -> str:
        return f"/api/v1/workspaces/{self.workspace_slug}/projects/{self.project_id}/issues/"

    def _issue_path(self, issue_id: str) -> str:
        return f"{self._issues_path()}{issue_id}/"

    def _comments_path(self, issue_id: str) -> str:
        return f"{self._issue_path(issue_id)}comments/"

    def _resolve_state_id(self, status: Status) -> str:
        payload = self._request("GET", self._states_path())
        states = self._safe_results(payload)

        desired_by_status = {
            "backlog": {"backlog", "unstarted"},
            "todo": {"todo", "to do", "por hacer", "por_hacer"},
            "in_progress": {"in progress", "started", "active", "doing"},
            "done": {"done", "completed", "closed"},
            "cancelled": {"cancelled", "canceled", "cancelado"},
            "blocked": {"blocked"},
        }
        desired_groups_by_status = {
            "backlog": {"backlog"},
            "todo": {"unstarted"},
            "in_progress": {"started"},
            "done": {"completed"},
            "cancelled": {"cancelled", "canceled"},
            "blocked": {"started"},
        }

        candidates = desired_by_status[status]
        for state in states:
            name = self._normalize(state.get("name"))
            if name in candidates and state.get("id"):
                return state["id"]

        desired_groups = desired_groups_by_status[status]
        for state in states:
            group = self._extract_state_group(state)
            if group in desired_groups and state.get("id"):
                return state["id"]

        available = [
            f"{self._normalize(s.get('name'))}|group={self._extract_state_group(s)}"
            for s in states
            if s.get("id")
        ]
        raise ValueError(f"Could not resolve Plane state id for '{status}'. Available states: {available}")

    @staticmethod
    def _map_priority(priority: Priority) -> str:
        return priority

    def _from_plane_issue(self, issue: dict[str, Any]) -> dict[str, Any]:
        state = issue.get("state")
        state_name = self._normalize(state.get("name") if isinstance(state, dict) else "")
        state_group = self._extract_state_group(state if isinstance(state, dict) else None)

        status = self._map_status_from_name_group(state_name, state_group)
        if not status and isinstance(state, str) and state.strip():
            status = self._resolve_status_from_state_id(state)
        if not status:
            status = "backlog"

        assignee = None
        assignees = issue.get("assignees")
        if isinstance(assignees, list) and assignees:
            first = assignees[0]
            if isinstance(first, dict):
                assignee = first.get("display_name") or first.get("email") or first.get("id")

        return {
            "id": issue.get("id"),
            "title": issue.get("name", ""),
            "description": issue.get("description_html") or issue.get("description_stripped", ""),
            "status": status,
            "priority": issue.get("priority", "medium"),
            "assignee": assignee,
            "created_at": issue.get("created_at"),
            "updated_at": issue.get("updated_at"),
            "external": issue,
        }

    def create_task(
        self,
        title: str,
        description: str = "",
        assignee: str | None = None,
        priority: Priority = "medium",
    ) -> dict[str, Any]:
        state_id = self._resolve_state_id("backlog")
        payload = {
            "name": title.strip(),
            "description_html": description.strip(),
            "priority": self._map_priority(priority),
            "state": state_id,
        }
        issue = self._request("POST", self._issues_path(), json_payload=payload)
        created = self._from_plane_issue(issue)
        if assignee:
            created = self.assign_task(task_id=str(created["id"]), assignee=assignee)
        return created

    def list_tasks(
        self,
        status: Status | None = None,
        assignee: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        payload = self._request("GET", self._issues_path())
        issues = self._safe_results(payload)
        tasks = [self._from_plane_issue(issue) for issue in issues]

        filtered = tasks
        if status:
            filtered = [task for task in filtered if task["status"] == status]
        if assignee:
            assignee_lower = assignee.strip().lower()
            filtered = [
                task
                for task in filtered
                if isinstance(task.get("assignee"), str) and task["assignee"].strip().lower() == assignee_lower
            ]

        return filtered[: max(1, min(limit, 500))]

    def get_task(self, task_id: str) -> dict[str, Any]:
        issue = self._request("GET", self._issue_path(task_id.strip()))
        return self._from_plane_issue(issue)

    def update_task_status(
        self,
        task_id: str,
        new_status: Status,
        actor: str = "mcp-bot",
    ) -> dict[str, Any]:
        del actor
        state_id = self._resolve_state_id(new_status)
        issue = self._request("PATCH", self._issue_path(task_id.strip()), json_payload={"state": state_id})
        return self._from_plane_issue(issue)

    def assign_task(self, task_id: str, assignee: str, actor: str = "mcp-bot") -> dict[str, Any]:
        del actor
        issue = self._request(
            "PATCH",
            self._issue_path(task_id.strip()),
            json_payload={"assignee_names": [assignee.strip()]},
        )
        return self._from_plane_issue(issue)

    def add_comment(self, task_id: str, comment: str, author: str = "mcp-bot") -> dict[str, Any]:
        del author
        payload = {"comment_html": comment.strip()}
        self._request("POST", self._comments_path(task_id.strip()), json_payload=payload)
        issue = self._request("GET", self._issue_path(task_id.strip()))
        return self._from_plane_issue(issue)
