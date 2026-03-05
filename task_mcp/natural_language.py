from __future__ import annotations

import re
from typing import Any

from .types import Status


class NaturalTextUpdater:
    def __init__(self, service: Any) -> None:
        self.service = service

    @staticmethod
    def _status_alias(value: str) -> Status | None:
        normalized = value.strip().lower()
        mapping: dict[str, Status] = {
            "todo": "todo",
            "to_do": "todo",
            "por_hacer": "todo",
            "in_progress": "in_progress",
            "in-progress": "in_progress",
            "progreso": "in_progress",
            "doing": "in_progress",
            "done": "done",
            "hecho": "done",
            "completado": "done",
            "blocked": "blocked",
            "bloqueado": "blocked",
        }
        return mapping.get(normalized)

    def update(self, text: str, actor: str = "mcp-bot") -> dict[str, Any]:
        cleaned = text.strip()

        create_match = re.search(r"(?:crea|crear)\s+tarea\s*:\s*(.+)$", cleaned, flags=re.IGNORECASE)
        if create_match:
            title = create_match.group(1).strip()
            created = self.service.create_task(title=title)
            return {"action": "create_task", "task": created}

        status_match = re.search(
            r"(?:mueve|cambia(?:r)?\s+estado\s+de)\s+([A-Za-z0-9\-_]{6,64})\s+(?:a|to)\s+([a-zA-Z_\-]+)",
            cleaned,
            flags=re.IGNORECASE,
        )
        if status_match:
            task_id = status_match.group(1)
            target_status = self._status_alias(status_match.group(2))
            if not target_status:
                raise ValueError("Unsupported status in natural command")
            updated = self.service.update_task_status(task_id=task_id, new_status=target_status, actor=actor)
            return {"action": "update_task_status", "task": updated}

        assign_match = re.search(
            r"(?:asigna|asignar)\s+([A-Za-z0-9\-_]{6,64})\s+(?:a|to)\s+([\w.\-@]+)",
            cleaned,
            flags=re.IGNORECASE,
        )
        if assign_match:
            task_id = assign_match.group(1)
            assignee = assign_match.group(2)
            updated = self.service.assign_task(task_id=task_id, assignee=assignee, actor=actor)
            return {"action": "assign_task", "task": updated}

        comment_match = re.search(
            r"(?:comenta|comentar|agrega\s+comentario\s+a)\s+([A-Za-z0-9\-_]{6,64})\s*:\s*(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if comment_match:
            task_id = comment_match.group(1)
            comment = comment_match.group(2).strip()
            updated = self.service.add_comment(task_id=task_id, comment=comment, author=actor)
            return {"action": "add_comment", "task": updated}

        raise ValueError(
            "Natural text not recognized. Use one of: create, move status, assign, add comment."
        )
