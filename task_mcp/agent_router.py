from __future__ import annotations

import re
from typing import Any, Callable

from .types import Status


class PlaneAgentRouter:
    def __init__(self, resolve_service: Callable[[str | None], Any]) -> None:
        self.resolve_service = resolve_service
        self._memory: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _status_alias(value: str) -> Status | None:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        mapping: dict[str, Status] = {
            "backlog": "backlog",
            "todo": "todo",
            "to_do": "todo",
            "por_hacer": "todo",
            "in_progress": "in_progress",
            "progreso": "in_progress",
            "ejecutando": "in_progress",
            "doing": "in_progress",
            "done": "done",
            "hecho": "done",
            "completado": "done",
            "cancelled": "cancelled",
            "canceled": "cancelled",
            "cancelado": "cancelled",
            "blocked": "blocked",
            "bloqueado": "blocked",
        }
        return mapping.get(normalized)

    @staticmethod
    def _memory_key(user_id: str | None) -> str:
        return user_id.strip().lower() if isinstance(user_id, str) and user_id.strip() else "__default__"

    def _remember_tasks(self, user_id: str | None, tasks: list[dict[str, Any]]) -> None:
        memory = self._memory.setdefault(self._memory_key(user_id), {})
        memory["last_list_ids"] = [str(task.get("id", "")).strip() for task in tasks if task.get("id")][:50]
        if tasks and tasks[0].get("id"):
            memory["last_task_id"] = str(tasks[0]["id"])
        if tasks and tasks[0].get("external") and isinstance(tasks[0]["external"], dict):
            ext = tasks[0]["external"]
            project_id = ext.get("project_id") or ext.get("project")
            if isinstance(project_id, str) and project_id.strip():
                memory["last_project_id"] = project_id.strip()

    def _remember_task(self, user_id: str | None, task: dict[str, Any]) -> None:
        task_id = str(task.get("id", "")).strip()
        if not task_id:
            return
        memory = self._memory.setdefault(self._memory_key(user_id), {})
        memory["last_task_id"] = task_id
        external = task.get("external")
        if isinstance(external, dict):
            project_id = external.get("project_id") or external.get("project")
            if isinstance(project_id, str) and project_id.strip():
                memory["last_project_id"] = project_id.strip()

    @staticmethod
    def _extract_project_hint(text: str) -> tuple[str | None, str]:
        hint_match = re.search(
            r"(?:en\s+proyecto|proyecto|project)\s*(?:=|:)?\s*([\w\-. ]{2,80})",
            text,
            flags=re.IGNORECASE,
        )
        if not hint_match:
            return None, text
        hint = hint_match.group(1).strip().strip('"')
        cleaned = (text[: hint_match.start()] + " " + text[hint_match.end() :]).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return hint, cleaned

    def _resolve_project_id(self, service: Any, user_id: str | None, project_hint: str | None = None) -> str | None:
        if not hasattr(service, "list_projects"):
            return None

        memory = self._memory.get(self._memory_key(user_id), {})
        projects = service.list_projects(limit=500)
        if not projects:
            raise ValueError("No projects available for this user in Plane")

        if project_hint and project_hint.strip():
            hint = project_hint.strip().lower()
            for project in projects:
                project_id = str(project.get("id", "")).strip()
                name = str(project.get("name", "")).strip().lower()
                identifier = str(project.get("identifier", "")).strip().lower()
                if not project_id:
                    continue
                if hint == project_id.lower() or hint == identifier or hint in name:
                    memory["last_project_id"] = project_id
                    self._memory[self._memory_key(user_id)] = memory
                    return project_id
            raise ValueError(f"Project not found for hint '{project_hint}'")

        last_project_id = str(memory.get("last_project_id", "")).strip()
        if last_project_id:
            return last_project_id

        configured = str(getattr(service, "project_id", "") or "").strip()
        if configured:
            memory["last_project_id"] = configured
            self._memory[self._memory_key(user_id)] = memory
            return configured

        if len(projects) == 1 and projects[0].get("id"):
            selected = str(projects[0]["id"])
            memory["last_project_id"] = selected
            self._memory[self._memory_key(user_id)] = memory
            return selected

        options = [f"{p.get('name', '')} ({p.get('id', '')})" for p in projects[:10]]
        raise ValueError(f"Multiple projects found. Specify one in command with 'en proyecto <nombre|id>'. Options: {options}")

    def _resolve_task_reference(self, service: Any, reference: str, user_id: str | None) -> str:
        ref = reference.strip()
        if not ref:
            raise ValueError("Task reference is required")

        if re.fullmatch(r"[A-Za-z]{2,6}-[A-Za-z0-9\-]{4,64}", ref):
            return ref

        memory = self._memory.get(self._memory_key(user_id), {})
        lowered = ref.lower()
        if lowered in {"esa tarea", "la tarea", "ultima", "última", "last", "last task"}:
            last_task_id = memory.get("last_task_id")
            if not last_task_id:
                raise ValueError("No previous task in context. Use a task id.")
            return str(last_task_id)

        if lowered.startswith("#") and lowered[1:].isdigit():
            index = int(lowered[1:]) - 1
            list_ids = memory.get("last_list_ids") or []
            if 0 <= index < len(list_ids):
                return str(list_ids[index])
            raise ValueError("Task reference index is out of range")

        project_id = self._resolve_project_id(service, user_id) if hasattr(service, "list_projects") else None

        if hasattr(service, "search_tasks"):
            matches = service.search_tasks(query=ref, limit=5, project_id=project_id)
        else:
            matches = [
                task
                for task in service.list_tasks(limit=200)
                if ref.lower() in str(task.get("title", "")).lower()
            ][:5]

        if len(matches) == 1 and matches[0].get("id"):
            return str(matches[0]["id"])
        if len(matches) > 1:
            options = [f"{task.get('id')}: {task.get('title', '')}" for task in matches]
            raise ValueError(f"Ambiguous task reference '{reference}'. Matches: {options}")

        raise ValueError(f"Could not resolve task reference: {reference}")

    @staticmethod
    def _extract_dates(text: str) -> tuple[str | None, str | None, str]:
        start_match = re.search(r"(?:inicio|start)\s*(?:=|:)?\s*(\d{4}-\d{2}-\d{2})", text, flags=re.IGNORECASE)
        due_match = re.search(r"(?:fin|vencimiento|due)\s*(?:=|:)?\s*(\d{4}-\d{2}-\d{2})", text, flags=re.IGNORECASE)
        start_date = start_match.group(1) if start_match else None
        due_date = due_match.group(1) if due_match else None

        cleaned = text
        if start_match:
            cleaned = cleaned.replace(start_match.group(0), " ")
        if due_match:
            cleaned = cleaned.replace(due_match.group(0), " ")
        return start_date, due_date, re.sub(r"\s+", " ", cleaned).strip(" ,")

    def handle(self, command: str, user_id: str | None = None, actor: str = "mcp-bot") -> dict[str, Any]:
        service = self.resolve_service(user_id)
        cleaned = command.strip()
        if not cleaned:
            raise ValueError("command is required")

        create_match = re.search(
            r"(?:crear?|crea)\s+(?:issue|tarea)\s*(?::)?\s*(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if create_match:
            raw = create_match.group(1).strip()
            project_hint, raw = self._extract_project_hint(raw)
            assignee_match = re.search(r"(?:asignad[oa]?\s+a|asignar\s+a)\s+([\w.\-@]+)", raw, flags=re.IGNORECASE)
            assignee = assignee_match.group(1) if assignee_match else None
            if assignee_match:
                raw = raw.replace(assignee_match.group(0), " ")
            start_date, due_date, raw = self._extract_dates(raw)
            title = raw.strip().strip('"')
            if not title:
                raise ValueError("Title is required to create a task")
            project_id = self._resolve_project_id(service, user_id, project_hint=project_hint)
            if hasattr(service, "list_projects"):
                task = service.create_task(
                    title=title,
                    assignee=assignee,
                    start_date=start_date,
                    due_date=due_date,
                    project_id=project_id,
                )
            else:
                task = service.create_task(
                    title=title,
                    assignee=assignee,
                    start_date=start_date,
                    due_date=due_date,
                )
            self._remember_task(user_id, task)
            return {"action": "create_task", "task": task}

        status_match = re.search(
            r"(?:pasar|mover|cambiar(?:\s+estado)?)\s+(.+?)\s+(?:a|to)\s+([a-zA-Z_\-\s]+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if status_match:
            reference = status_match.group(1).strip()
            target_status = self._status_alias(status_match.group(2))
            if not target_status:
                raise ValueError("Unsupported status in command")
            task_id = self._resolve_task_reference(service, reference, user_id)
            project_id = self._resolve_project_id(service, user_id)
            if hasattr(service, "list_projects"):
                task = service.update_task_status(
                    task_id=task_id,
                    new_status=target_status,
                    actor=actor,
                    project_id=project_id,
                )
            else:
                task = service.update_task_status(task_id=task_id, new_status=target_status, actor=actor)
            self._remember_task(user_id, task)
            return {"action": "update_task_status", "task": task}

        assign_match = re.search(
            r"(?:asignar|asigna)\s+(.+?)\s+(?:a|to)\s+([\w.\-@]+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if assign_match:
            reference = assign_match.group(1).strip()
            assignee = assign_match.group(2).strip()
            task_id = self._resolve_task_reference(service, reference, user_id)
            project_id = self._resolve_project_id(service, user_id)
            if hasattr(service, "list_projects"):
                task = service.assign_task(task_id=task_id, assignee=assignee, actor=actor, project_id=project_id)
            else:
                task = service.assign_task(task_id=task_id, assignee=assignee, actor=actor)
            self._remember_task(user_id, task)
            return {"action": "assign_task", "task": task}

        comment_match = re.search(
            r"(?:comentar|comenta|agregar\s+comentario\s+a|agrega\s+comentario\s+a)\s+(.+?)\s*:\s*(.+)$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if comment_match:
            reference = comment_match.group(1).strip()
            comment = comment_match.group(2).strip()
            task_id = self._resolve_task_reference(service, reference, user_id)
            project_id = self._resolve_project_id(service, user_id)
            if hasattr(service, "list_projects"):
                task = service.add_comment(task_id=task_id, comment=comment, author=actor, project_id=project_id)
            else:
                task = service.add_comment(task_id=task_id, comment=comment, author=actor)
            self._remember_task(user_id, task)
            return {"action": "add_comment", "task": task}

        dates_match = re.search(
            r"(?:actualizar|actualiza|poner|set)\s+fechas?\s+(.+?)\s+inicio\s+(\d{4}-\d{2}-\d{2})\s+(?:fin|vencimiento|due)\s+(\d{4}-\d{2}-\d{2})$",
            cleaned,
            flags=re.IGNORECASE,
        )
        if dates_match:
            reference = dates_match.group(1).strip()
            start_date = dates_match.group(2)
            due_date = dates_match.group(3)
            task_id = self._resolve_task_reference(service, reference, user_id)
            project_id = self._resolve_project_id(service, user_id)
            if hasattr(service, "list_projects"):
                task = service.update_task_dates(
                    task_id=task_id,
                    start_date=start_date,
                    due_date=due_date,
                    actor=actor,
                    project_id=project_id,
                )
            else:
                task = service.update_task_dates(task_id=task_id, start_date=start_date, due_date=due_date, actor=actor)
            self._remember_task(user_id, task)
            return {"action": "update_task_dates", "task": task}

        list_intent = re.search(r"\b(listar|lista|muestra|mostrar)\b", cleaned, flags=re.IGNORECASE)
        if list_intent:
            project_hint, cleaned = self._extract_project_hint(cleaned)
            status: Status | None = None
            for candidate in ["backlog", "todo", "in_progress", "done", "cancelled", "blocked"]:
                if candidate.replace("_", " ") in cleaned.lower() or candidate in cleaned.lower():
                    status = candidate  # type: ignore[assignment]
                    break

            assignee: str | None = None
            if re.search(r"\b(mi|mis|my)\b", cleaned, flags=re.IGNORECASE):
                assignee = user_id
            query_match = re.search(r"(?:que\s+contenga|con\s+texto|about)\s+(.+)$", cleaned, flags=re.IGNORECASE)
            query = query_match.group(1).strip() if query_match else None
            project_id = self._resolve_project_id(service, user_id, project_hint=project_hint)

            if hasattr(service, "search_tasks") and query:
                tasks = service.search_tasks(
                    query=query,
                    status=status,
                    assignee=assignee,
                    limit=50,
                    project_id=project_id,
                )
            else:
                if hasattr(service, "list_projects"):
                    tasks = service.list_tasks(status=status, assignee=assignee, limit=50, project_id=project_id)
                else:
                    tasks = service.list_tasks(status=status, assignee=assignee, limit=50)

            self._remember_tasks(user_id, tasks)
            return {"action": "list_tasks", "count": len(tasks), "tasks": tasks}

        get_match = re.search(r"(?:ver|detalle|show|get)\s+(.+)$", cleaned, flags=re.IGNORECASE)
        if get_match:
            reference = get_match.group(1).strip()
            task_id = self._resolve_task_reference(service, reference, user_id)
            project_id = self._resolve_project_id(service, user_id)
            if hasattr(service, "list_projects"):
                task = service.get_task(task_id, project_id=project_id)
            else:
                task = service.get_task(task_id)
            self._remember_task(user_id, task)
            return {"action": "get_task", "task": task}

        raise ValueError(
            "Command not recognized. Use intents like create, move status, assign, comment, list, details, update dates."
        )
