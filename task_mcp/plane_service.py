from __future__ import annotations

from datetime import date
from typing import Any

import requests

from .types import Priority, Status


class PlaneTaskService:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        workspace_slug: str,
        project_id: str | None,
        timeout_seconds: int = 20,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.workspace_slug = workspace_slug
        self.project_id = project_id.strip() if isinstance(project_id, str) and project_id.strip() else None
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

    def _resolve_status_from_state_id(self, state_id: str, project_id: str | None = None) -> Status | None:
        payload = self._request("GET", self._states_path(project_id=project_id))
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

    def _effective_project_id(self, project_id: str | None = None) -> str:
        effective = project_id.strip() if isinstance(project_id, str) and project_id.strip() else self.project_id
        if not effective:
            projects = self.list_projects(limit=200)
            if not projects:
                raise ValueError("No projects available for this user/workspace in Plane")
            if len(projects) == 1:
                effective = str(projects[0].get("id", "")).strip()
                if not effective:
                    raise ValueError("Could not auto-resolve a Plane project id")
                self.project_id = effective
                return effective

            options = [f"{p.get('name', '')} ({p.get('id', '')})" for p in projects[:20]]
            raise ValueError(
                "Multiple projects found. Select one with set_active_project(project_id, user_id) or pass project_id explicitly. "
                f"Available: {options}"
            )
        return effective

    def _states_path(self, project_id: str | None = None) -> str:
        effective = self._effective_project_id(project_id)
        return f"/api/v1/workspaces/{self.workspace_slug}/projects/{effective}/states/"

    def _issues_path(self, project_id: str | None = None) -> str:
        effective = self._effective_project_id(project_id)
        return f"/api/v1/workspaces/{self.workspace_slug}/projects/{effective}/issues/"

    def _issue_path(self, issue_id: str, project_id: str | None = None) -> str:
        return f"{self._issues_path(project_id=project_id)}{issue_id}/"

    def _comments_path(self, issue_id: str, project_id: str | None = None) -> str:
        return f"{self._issue_path(issue_id, project_id=project_id)}comments/"

    def _labels_path(self, project_id: str | None = None) -> str:
        effective = self._effective_project_id(project_id)
        return f"/api/v1/workspaces/{self.workspace_slug}/projects/{effective}/labels/"

    def _cycles_path(self, project_id: str | None = None) -> str:
        effective = self._effective_project_id(project_id)
        return f"/api/v1/workspaces/{self.workspace_slug}/projects/{effective}/cycles/"

    def _members_path(self) -> str:
        return f"/api/v1/workspaces/{self.workspace_slug}/members/"

    def _projects_path(self) -> str:
        return f"/api/v1/workspaces/{self.workspace_slug}/projects/"

    def _resolve_state_id(self, status: Status, project_id: str | None = None) -> str:
        payload = self._request("GET", self._states_path(project_id=project_id))
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

    @staticmethod
    def _extract_labels(issue: dict[str, Any]) -> list[dict[str, Any]]:
        raw_labels = issue.get("labels")
        if not isinstance(raw_labels, list):
            return []
        labels: list[dict[str, Any]] = []
        for label in raw_labels:
            if isinstance(label, dict) and label.get("id"):
                labels.append(
                    {
                        "id": label.get("id"),
                        "name": label.get("name", ""),
                        "color": label.get("color"),
                    }
                )
        return labels

    def _from_plane_issue(self, issue: dict[str, Any], project_id: str | None = None) -> dict[str, Any]:
        state = issue.get("state")
        state_name = self._normalize(state.get("name") if isinstance(state, dict) else "")
        state_group = self._extract_state_group(state if isinstance(state, dict) else None)

        status = self._map_status_from_name_group(state_name, state_group)
        if not status and isinstance(state, str) and state.strip():
            status = self._resolve_status_from_state_id(state, project_id=project_id)
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
            "start_date": issue.get("start_date"),
            "due_date": issue.get("target_date") or issue.get("due_date"),
            "labels": self._extract_labels(issue),
            "cycle_id": issue.get("cycle_id") or issue.get("cycle"),
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
        start_date: str | None = None,
        due_date: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        state_id = self._resolve_state_id("backlog", project_id=project_id)
        payload = {
            "name": title.strip(),
            "description_html": description.strip(),
            "priority": self._map_priority(priority),
            "state": state_id,
        }
        if isinstance(start_date, str) and start_date.strip():
            payload["start_date"] = start_date.strip()
        if isinstance(due_date, str) and due_date.strip():
            payload["target_date"] = due_date.strip()
        issue = self._request("POST", self._issues_path(project_id=project_id), json_payload=payload)
        created = self._from_plane_issue(issue, project_id=project_id)
        if assignee:
            created = self.assign_task(task_id=str(created["id"]), assignee=assignee, project_id=project_id)
        return created

    def list_tasks(
        self,
        status: Status | None = None,
        assignee: str | None = None,
        limit: int = 50,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        payload = self._request("GET", self._issues_path(project_id=project_id))
        issues = self._safe_results(payload)
        tasks = [self._from_plane_issue(issue, project_id=project_id) for issue in issues]

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

    def get_task(self, task_id: str, project_id: str | None = None) -> dict[str, Any]:
        issue = self._request("GET", self._issue_path(task_id.strip(), project_id=project_id))
        return self._from_plane_issue(issue, project_id=project_id)

    def update_task_status(
        self,
        task_id: str,
        new_status: Status,
        actor: str = "mcp-bot",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        del actor
        state_id = self._resolve_state_id(new_status, project_id=project_id)
        issue = self._request(
            "PATCH",
            self._issue_path(task_id.strip(), project_id=project_id),
            json_payload={"state": state_id},
        )
        return self._from_plane_issue(issue, project_id=project_id)

    def assign_task(
        self,
        task_id: str,
        assignee: str,
        actor: str = "mcp-bot",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        del actor
        issue = self._request(
            "PATCH",
            self._issue_path(task_id.strip(), project_id=project_id),
            json_payload={"assignee_names": [assignee.strip()]},
        )
        return self._from_plane_issue(issue, project_id=project_id)

    def add_comment(
        self,
        task_id: str,
        comment: str,
        author: str = "mcp-bot",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        del author
        payload = {"comment_html": comment.strip()}
        self._request("POST", self._comments_path(task_id.strip(), project_id=project_id), json_payload=payload)
        issue = self._request("GET", self._issue_path(task_id.strip(), project_id=project_id))
        return self._from_plane_issue(issue, project_id=project_id)

    def update_task_dates(
        self,
        task_id: str,
        start_date: str | None = None,
        due_date: str | None = None,
        actor: str = "mcp-bot",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        del actor
        payload: dict[str, Any] = {
            "start_date": start_date.strip() if isinstance(start_date, str) and start_date.strip() else None,
            "target_date": due_date.strip() if isinstance(due_date, str) and due_date.strip() else None,
        }
        issue = self._request("PATCH", self._issue_path(task_id.strip(), project_id=project_id), json_payload=payload)
        return self._from_plane_issue(issue, project_id=project_id)

    def list_states(self, project_id: str | None = None) -> list[dict[str, Any]]:
        payload = self._request("GET", self._states_path(project_id=project_id))
        states = self._safe_results(payload)
        return [
            {
                "id": state.get("id"),
                "name": state.get("name", ""),
                "group": self._extract_state_group(state),
            }
            for state in states
            if state.get("id")
        ]

    def list_labels(self, limit: int = 200, project_id: str | None = None) -> list[dict[str, Any]]:
        payload = self._request("GET", self._labels_path(project_id=project_id))
        labels = self._safe_results(payload)
        normalized = [
            {
                "id": label.get("id"),
                "name": label.get("name", ""),
                "color": label.get("color"),
            }
            for label in labels
            if label.get("id")
        ]
        return normalized[: max(1, min(limit, 500))]

    def create_label(self, name: str, color: str | None = None, project_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": name.strip()}
        if isinstance(color, str) and color.strip():
            payload["color"] = color.strip()
        label = self._request("POST", self._labels_path(project_id=project_id), json_payload=payload)
        return {
            "id": label.get("id"),
            "name": label.get("name", ""),
            "color": label.get("color"),
        }

    def _resolve_label_ids(
        self,
        label_ids: list[str] | None = None,
        label_names: list[str] | None = None,
        project_id: str | None = None,
    ) -> list[str]:
        resolved: set[str] = set()
        if label_ids:
            for label_id in label_ids:
                cleaned = str(label_id).strip()
                if cleaned:
                    resolved.add(cleaned)

        if label_names:
            names = {self._normalize(name) for name in label_names if isinstance(name, str) and name.strip()}
            if names:
                labels = self.list_labels(limit=500, project_id=project_id)
                for label in labels:
                    if self._normalize(label.get("name")) in names and label.get("id"):
                        resolved.add(str(label["id"]))
                missing = names - {self._normalize(label.get("name")) for label in labels}
                if missing:
                    raise ValueError(f"Label names not found: {sorted(missing)}")

        return sorted(resolved)

    def set_task_labels(
        self,
        task_id: str,
        label_ids: list[str] | None = None,
        label_names: list[str] | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        resolved_label_ids = self._resolve_label_ids(
            label_ids=label_ids,
            label_names=label_names,
            project_id=project_id,
        )
        issue = self._request(
            "PATCH",
            self._issue_path(task_id.strip(), project_id=project_id),
            json_payload={"label_ids": resolved_label_ids},
        )
        return self._from_plane_issue(issue, project_id=project_id)

    def list_cycles(self, limit: int = 200, project_id: str | None = None) -> list[dict[str, Any]]:
        payload = self._request("GET", self._cycles_path(project_id=project_id))
        cycles = self._safe_results(payload)
        normalized = [
            {
                "id": cycle.get("id"),
                "name": cycle.get("name", ""),
                "start_date": cycle.get("start_date"),
                "end_date": cycle.get("end_date"),
            }
            for cycle in cycles
            if cycle.get("id")
        ]
        return normalized[: max(1, min(limit, 500))]

    def set_task_cycle(
        self,
        task_id: str,
        cycle_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"cycle_id": cycle_id.strip() if isinstance(cycle_id, str) and cycle_id.strip() else None}
        issue = self._request("PATCH", self._issue_path(task_id.strip(), project_id=project_id), json_payload=payload)
        return self._from_plane_issue(issue, project_id=project_id)

    def list_projects(self, limit: int = 200) -> list[dict[str, Any]]:
        payload = self._request("GET", self._projects_path())
        projects = self._safe_results(payload)
        normalized = [
            {
                "id": project.get("id"),
                "name": project.get("name", ""),
                "identifier": project.get("identifier") or project.get("slug"),
            }
            for project in projects
            if project.get("id")
        ]
        return normalized[: max(1, min(limit, 500))]

    def list_members(self, limit: int = 200) -> list[dict[str, Any]]:
        payload = self._request("GET", self._members_path())
        members = self._safe_results(payload)
        normalized: list[dict[str, Any]] = []
        for member in members:
            if not isinstance(member, dict):
                continue
            user = member.get("member") if isinstance(member.get("member"), dict) else member
            if not isinstance(user, dict):
                continue
            normalized.append(
                {
                    "id": user.get("id"),
                    "email": user.get("email"),
                    "display_name": user.get("display_name") or user.get("first_name"),
                }
            )
        return normalized[: max(1, min(limit, 500))]

    def list_assignable_users(self, query: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        members = self.list_members(limit=500)
        if query and query.strip():
            needle = query.strip().lower()
            members = [
                member
                for member in members
                if needle in str(member.get("email", "")).lower()
                or needle in str(member.get("display_name", "")).lower()
            ]
        return members[: max(1, min(limit, 500))]

    def search_tasks(
        self,
        query: str | None = None,
        status: Status | None = None,
        assignee: str | None = None,
        start_date_from: str | None = None,
        start_date_to: str | None = None,
        due_date_from: str | None = None,
        due_date_to: str | None = None,
        limit: int = 50,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        tasks = self.list_tasks(status=status, assignee=assignee, limit=500, project_id=project_id)
        filtered = tasks

        if query and query.strip():
            needle = query.strip().lower()
            filtered = [
                task
                for task in filtered
                if needle in str(task.get("title", "")).lower() or needle in str(task.get("description", "")).lower()
            ]

        def _in_range(value: str | None, min_date: str | None, max_date: str | None) -> bool:
            if not value:
                return False if (min_date or max_date) else True
            try:
                current = date.fromisoformat(value)
            except ValueError:
                return False
            if min_date:
                try:
                    if current < date.fromisoformat(min_date):
                        return False
                except ValueError:
                    pass
            if max_date:
                try:
                    if current > date.fromisoformat(max_date):
                        return False
                except ValueError:
                    pass
            return True

        filtered = [
            task
            for task in filtered
            if _in_range(task.get("start_date"), start_date_from, start_date_to)
            and _in_range(task.get("due_date"), due_date_from, due_date_to)
        ]

        return filtered[: max(1, min(limit, 500))]

    def bulk_update_tasks(
        self,
        task_ids: list[str],
        new_status: Status | None = None,
        assignee: str | None = None,
        start_date: str | None = None,
        due_date: str | None = None,
        label_ids: list[str] | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        updated: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        state_id = self._resolve_state_id(new_status, project_id=project_id) if new_status else None
        resolved_labels = (
            self._resolve_label_ids(label_ids=label_ids, label_names=None, project_id=project_id)
            if label_ids is not None
            else None
        )

        for raw_task_id in task_ids:
            task_id = str(raw_task_id).strip()
            if not task_id:
                continue
            payload: dict[str, Any] = {}
            if state_id:
                payload["state"] = state_id
            if assignee and assignee.strip():
                payload["assignee_names"] = [assignee.strip()]
            if start_date is not None:
                payload["start_date"] = start_date.strip() if isinstance(start_date, str) and start_date.strip() else None
            if due_date is not None:
                payload["target_date"] = due_date.strip() if isinstance(due_date, str) and due_date.strip() else None
            if resolved_labels is not None:
                payload["label_ids"] = resolved_labels
            if not payload:
                errors.append({"task_id": task_id, "error": "No fields to update"})
                continue
            try:
                issue = self._request("PATCH", self._issue_path(task_id, project_id=project_id), json_payload=payload)
                updated.append(self._from_plane_issue(issue, project_id=project_id))
            except Exception as exc:  # noqa: BLE001
                errors.append({"task_id": task_id, "error": str(exc)})

        return {
            "updated": updated,
            "updated_count": len(updated),
            "errors": errors,
            "error_count": len(errors),
        }
