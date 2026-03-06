from __future__ import annotations

from datetime import date
import html
import time
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
        self._min_request_interval_seconds = 0.25
        self._last_request_ts = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "x-api-key": api_token,
            }
        )

    def _request(
        self,
        method: str,
        path: str,
        json_payload: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        attempts = 0
        max_attempts = 5
        while True:
            now = time.monotonic()
            wait = self._min_request_interval_seconds - (now - self._last_request_ts)
            if wait > 0:
                time.sleep(wait)

            response = self.session.request(
                method=method,
                url=url,
                json=json_payload,
                params=query_params,
                timeout=self.timeout_seconds,
            )
            self._last_request_ts = time.monotonic()

            if response.status_code != 429:
                break

            attempts += 1
            if attempts >= max_attempts:
                raise ValueError(f"Plane API error ({response.status_code}): {response.text}")

            retry_after = response.headers.get("Retry-After")
            sleep_seconds = 1.0
            if retry_after:
                try:
                    sleep_seconds = max(0.5, float(retry_after))
                except ValueError:
                    sleep_seconds = 1.0
            else:
                sleep_seconds = float(2**attempts)
            time.sleep(min(sleep_seconds, 8.0))

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
            effective = str(projects[0].get("id", "")).strip()
            if not effective:
                raise ValueError("Could not auto-resolve default project")
            self.project_id = effective
        return effective

    def get_active_project(self) -> dict[str, Any]:
        current_id = self._effective_project_id()
        projects = self.list_projects(limit=500)
        for project in projects:
            if str(project.get("id", "")).strip() == current_id:
                return project
        return {"id": current_id, "name": ""}

    def set_active_project(self, project_name: str) -> dict[str, Any]:
        hint = self._normalize(project_name)
        if not hint:
            raise ValueError("project_name is required")
        projects = self.list_projects(limit=500)
        for project in projects:
            name = self._normalize(project.get("name"))
            identifier = self._normalize(project.get("identifier"))
            project_id = str(project.get("id", "")).strip()
            if not project_id:
                continue
            if hint == name or hint == identifier or hint in name:
                self.project_id = project_id
                return project
        names = [str(project.get("name", "")).strip() for project in projects[:25]]
        raise ValueError(f"Project not found: '{project_name}'. Available projects: {names}")

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
    def _to_description_html(description: str) -> str | None:
        cleaned = description.strip()
        if not cleaned:
            return None
        if "<" in cleaned and ">" in cleaned:
            return cleaned
        escaped = html.escape(cleaned).replace("\n", "<br/>")
        return f"<p>{escaped}</p>"

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

    @staticmethod
    def _has_assignee(task: dict[str, Any], expected: str) -> bool:
        wanted = expected.strip().lower()
        if not wanted:
            return True
        current = str(task.get("assignee", "")).strip().lower()
        if current and wanted in current:
            return True
        external = task.get("external")
        assignees = external.get("assignees") if isinstance(external, dict) else None
        if isinstance(assignees, list):
            for item in assignees:
                if not isinstance(item, dict):
                    continue
                values = [
                    str(item.get("display_name", "")).strip().lower(),
                    str(item.get("email", "")).strip().lower(),
                    str(item.get("id", "")).strip().lower(),
                ]
                if any(wanted == value or wanted in value for value in values if value):
                    return True
        return False

    @staticmethod
    def _has_labels(task: dict[str, Any], label_ids: list[str] | None, label_names: list[str] | None) -> bool:
        actual_ids = {str(label.get("id", "")).strip() for label in task.get("labels", []) if isinstance(label, dict)}
        actual_names = {
            str(label.get("name", "")).strip().lower() for label in task.get("labels", []) if isinstance(label, dict)
        }
        if label_ids:
            wanted_ids = {str(value).strip() for value in label_ids if str(value).strip()}
            if not wanted_ids.issubset(actual_ids):
                return False
        if label_names:
            wanted_names = {str(value).strip().lower() for value in label_names if str(value).strip()}
            if not wanted_names.issubset(actual_names):
                return False
        return True

    def _refresh_task_with_retries(
        self,
        task_id: str,
        project_id: str | None = None,
        retries: int = 4,
        delay_seconds: float = 0.6,
    ) -> dict[str, Any]:
        latest = self.get_task(task_id=task_id, project_id=project_id)
        for _ in range(max(0, retries)):
            time.sleep(delay_seconds)
            latest = self.get_task(task_id=task_id, project_id=project_id)
        return latest

    def _wait_until(
        self,
        task_id: str,
        checker: Any,
        project_id: str | None = None,
        retries: int = 3,
        delay_seconds: float = 1.0,
    ) -> dict[str, Any]:
        latest = self.get_task(task_id=task_id, project_id=project_id)
        if checker(latest):
            return latest
        for _ in range(max(0, retries)):
            time.sleep(delay_seconds)
            latest = self.get_task(task_id=task_id, project_id=project_id)
            if checker(latest):
                return latest
        return latest

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
        label_ids: list[str] | None = None,
        label_names: list[str] | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        state_id = self._resolve_state_id("backlog", project_id=project_id)
        payload = {
            "name": title.strip(),
            "priority": self._map_priority(priority),
            "state": state_id,
        }
        description_html = self._to_description_html(description)
        if description_html:
            payload["description_html"] = description_html
        if isinstance(start_date, str) and start_date.strip():
            payload["start_date"] = start_date.strip()
        if isinstance(due_date, str) and due_date.strip():
            payload["target_date"] = due_date.strip()
        issue = self._request("POST", self._issues_path(project_id=project_id), json_payload=payload)
        created = self._from_plane_issue(issue, project_id=project_id)
        if start_date or due_date:
            created = self.update_task_dates(
                task_id=str(created["id"]),
                start_date=start_date,
                due_date=due_date,
                project_id=project_id,
            )
        if assignee:
            created = self.assign_task(task_id=str(created["id"]), assignee=assignee, project_id=project_id)
        elif created.get("assignee"):
            created = self._from_plane_issue(
                self._request(
                    "PATCH",
                    self._issue_path(str(created["id"]), project_id=project_id),
                    json_payload={"assignee_names": []},
                ),
                project_id=project_id,
            )
        if label_ids or label_names:
            created = self.set_task_labels(
                task_id=str(created["id"]),
                label_ids=label_ids,
                label_names=label_names,
                project_id=project_id,
            )
        return self.get_task(task_id=str(created["id"]), project_id=project_id)

    def list_tasks(
        self,
        status: Status | None = None,
        assignee: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        page_size: int = 50,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        page = self.list_tasks_paginated(
            status=status,
            assignee=assignee,
            limit=limit,
            cursor=cursor,
            page_size=page_size,
            project_id=project_id,
        )
        return page["tasks"]

    def list_tasks_paginated(
        self,
        status: Status | None = None,
        assignee: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        page_size: int = 50,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        safe_page_size = max(1, min(page_size, 100))
        query: dict[str, Any] = {"limit": safe_page_size}
        if isinstance(cursor, str) and cursor.strip():
            query["cursor"] = cursor.strip()

        payload = self._request("GET", self._issues_path(project_id=project_id), query_params=query)
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

        capped = filtered[: max(1, min(limit, 500))]
        next_cursor = payload.get("next_cursor") if isinstance(payload, dict) else None
        prev_cursor = payload.get("prev_cursor") if isinstance(payload, dict) else None
        total_count = payload.get("total_count") if isinstance(payload, dict) else None
        return {
            "tasks": capped,
            "count": len(capped),
            "next_cursor": next_cursor,
            "prev_cursor": prev_cursor,
            "total_count": total_count,
            "page_size": safe_page_size,
        }

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
        task = self._from_plane_issue(issue, project_id=project_id)
        refreshed = self._refresh_task_with_retries(task_id=task_id.strip(), project_id=project_id, retries=2)
        if not self._has_assignee(refreshed, assignee):
            raise ValueError(
                "Assignment was not applied by Plane. Use an exact user from list_plane_users "
                "(preferably email) and try again."
            )
        return refreshed

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
        payload_candidates: list[dict[str, Any]] = [
            {"label_ids": resolved_label_ids},
            {"labels": resolved_label_ids},
        ]

        last_error: str | None = None
        for payload in payload_candidates:
            try:
                self._request(
                    "PATCH",
                    self._issue_path(task_id.strip(), project_id=project_id),
                    json_payload=payload,
                )
                refreshed = self._wait_until(
                    task_id=task_id.strip(),
                    project_id=project_id,
                    retries=2,
                    delay_seconds=1.0,
                    checker=lambda task: self._has_labels(
                        task,
                        label_ids=resolved_label_ids,
                        label_names=label_names,
                    ),
                )
                if self._has_labels(refreshed, label_ids=resolved_label_ids, label_names=label_names):
                    return refreshed
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)

        suffix = f" Last API error: {last_error}" if last_error else ""
        raise ValueError(
            "Labels were not applied by Plane. Check label names/ids with list_plane_labels and try again."
            + suffix
        )

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
