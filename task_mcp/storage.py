from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TaskRepository:
    def __init__(self, tasks_file: Path) -> None:
        self.tasks_file = tasks_file

    def _ensure_storage(self) -> None:
        self.tasks_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.tasks_file.exists():
            self.tasks_file.write_text("[]", encoding="utf-8")

    def load_tasks(self) -> list[dict[str, Any]]:
        self._ensure_storage()
        raw = self.tasks_file.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("tasks.json must contain a list")
        return data

    def save_tasks(self, tasks: list[dict[str, Any]]) -> None:
        self._ensure_storage()
        self.tasks_file.write_text(
            json.dumps(tasks, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def find_task(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any] | None:
        for task in tasks:
            if task["id"] == task_id:
                return task
        return None
