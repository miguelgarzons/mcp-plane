from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .storage import TaskRepository
from .types import Priority, Status


class TaskService:
    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _append_history(self, task: dict[str, Any], event: str, actor: str = "mcp-bot") -> None:
        task.setdefault("history", []).append(
            {
                "at": self._utc_now(),
                "actor": actor,
                "event": event,
            }
        )

    def create_task(
        self,
        title: str,
        description: str = "",
        assignee: str | None = None,
        priority: Priority = "medium",
    ) -> dict[str, Any]:
        tasks = self.repository.load_tasks()
        task_id = f"TSK-{uuid4().hex[:8]}"
        now = self._utc_now()

        task = {
            "id": task_id,
            "title": title.strip(),
            "description": description.strip(),
            "status": "todo",
            "priority": priority,
            "assignee": assignee.strip() if assignee else None,
            "created_at": now,
            "updated_at": now,
            "comments": [],
            "history": [],
        }

        self._append_history(task, "task_created")
        tasks.append(task)
        self.repository.save_tasks(tasks)
        return task

    def list_tasks(
        self,
        status: Status | None = None,
        assignee: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        tasks = self.repository.load_tasks()
        filtered = tasks

        if status:
            filtered = [task for task in filtered if task["status"] == status]

        if assignee:
            assignee_lower = assignee.strip().lower()
            filtered = [
                task
                for task in filtered
                if isinstance(task.get("assignee"), str)
                and task["assignee"].strip().lower() == assignee_lower
            ]

        return filtered[: max(1, min(limit, 500))]

    def get_task(self, task_id: str) -> dict[str, Any]:
        tasks = self.repository.load_tasks()
        task = self.repository.find_task(tasks, task_id.strip())
        if not task:
            raise ValueError(f"Task not found: {task_id}")
        return task

    def update_task_status(
        self,
        task_id: str,
        new_status: Status,
        actor: str = "mcp-bot",
    ) -> dict[str, Any]:
        tasks = self.repository.load_tasks()
        task = self.repository.find_task(tasks, task_id.strip())
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        previous = task["status"]
        task["status"] = new_status
        task["updated_at"] = self._utc_now()
        self._append_history(task, f"status_changed:{previous}->{new_status}", actor=actor)
        self.repository.save_tasks(tasks)
        return task

    def assign_task(self, task_id: str, assignee: str, actor: str = "mcp-bot") -> dict[str, Any]:
        tasks = self.repository.load_tasks()
        task = self.repository.find_task(tasks, task_id.strip())
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        previous = task.get("assignee")
        task["assignee"] = assignee.strip()
        task["updated_at"] = self._utc_now()
        self._append_history(task, f"assignee_changed:{previous}->{task['assignee']}", actor=actor)
        self.repository.save_tasks(tasks)
        return task

    def add_comment(self, task_id: str, comment: str, author: str = "mcp-bot") -> dict[str, Any]:
        tasks = self.repository.load_tasks()
        task = self.repository.find_task(tasks, task_id.strip())
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        comment_data = {
            "id": f"CMT-{uuid4().hex[:8]}",
            "author": author,
            "text": comment.strip(),
            "created_at": self._utc_now(),
        }
        task.setdefault("comments", []).append(comment_data)
        task["updated_at"] = self._utc_now()
        self._append_history(task, f"comment_added:{comment_data['id']}", actor=author)
        self.repository.save_tasks(tasks)
        return task
