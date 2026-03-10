from __future__ import annotations

import os
import re
from typing import Any

from fastmcp import FastMCP

from .agent_router import PlaneAgentRouter
from .credential_store import CredentialStore
from .natural_language import NaturalTextUpdater
from .plane_service import PlaneTaskService
from .types import Priority, Status


def _env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


def _plane_runtime_config() -> dict[str, str | None]:
    return {
        "base_url": os.getenv("PLANE_BASE_URL", "").strip() or "https://api.plane.so",
        "workspace_slug": _env_required("PLANE_WORKSPACE_SLUG"),
    }


def _db_config() -> dict[str, Any]:
    host = _env_required("DB_HOST")
    database = _env_required("DB_NAME")
    user = _env_required("DB_USER")
    password = _env_required("DB_PASSWORD")
    schema = os.getenv("DB_SCHEMA", "mcp").strip() or "mcp"
    port_raw = os.getenv("DB_PORT", "5432").strip() or "5432"
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValueError("DB_PORT must be a valid integer") from exc
    return {
        "host": host,
        "port": port,
        "database": database,
        "user": user,
        "password": password,
        "schema": schema,
    }


def _create_plane_service(
    base_url: str,
    api_token: str,
    workspace_slug: str,
    project_id: str | None,
) -> PlaneTaskService:
    return PlaneTaskService(
        base_url=base_url,
        api_token=api_token,
        workspace_slug=workspace_slug,
        project_id=project_id,
    )


def _is_valid_email(value: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()))


def _require_user_email(user_id: str | None) -> str:
    cleaned = user_id.strip() if isinstance(user_id, str) else ""
    if not cleaned:
        raise ValueError("user_id is required")
    if not _is_valid_email(cleaned):
        raise ValueError("user_id must be an email address")
    return cleaned


def _require_assignee_email(assignee: str | None) -> str | None:
    if not isinstance(assignee, str) or not assignee.strip():
        return None
    cleaned = assignee.strip()
    if not _is_valid_email(cleaned):
        raise ValueError(
            "Assignee must be a valid email address. Use list_plane_users and pick exact email."
        )
    return cleaned


def create_app() -> FastMCP:
    plane_config = _plane_runtime_config()
    credentials_store = CredentialStore(**_db_config())
    service_cache: dict[str, dict[str, Any]] = {}

    def resolve_service(user_id: str | None) -> PlaneTaskService:
        cleaned_user_id = _require_user_email(user_id)
        credentials = credentials_store.get_plane_credentials(cleaned_user_id)
        if not credentials:
            raise ValueError(
                f"No Plane token found for user_id={cleaned_user_id}. "
                "First call set_user_plane_token(user_id, plane_api_token)."
            )
        cached = service_cache.get(cleaned_user_id)
        if cached and cached.get("token") == credentials["api_token"]:
            cached_service = cached.get("service")
            if isinstance(cached_service, PlaneTaskService):
                return cached_service

        service = _create_plane_service(
            base_url=str(plane_config["base_url"]),
            api_token=credentials["api_token"],
            workspace_slug=str(plane_config["workspace_slug"]),
            project_id=None,
        )
        service_cache[cleaned_user_id] = {
            "token": credentials["api_token"],
            "service": service,
        }
        return service

    agent_router = PlaneAgentRouter(resolve_service=resolve_service)
    app = FastMCP("plane-local-tasks")

    @app.tool()
    def set_user_plane_token(user_id: str, plane_api_token: str) -> dict[str, str]:
        """Store or update Plane API token for a user in PostgreSQL."""
        cleaned_user_id = _require_user_email(user_id)
        saved = credentials_store.upsert_plane_credentials(
            user_id=cleaned_user_id,
            api_token=plane_api_token,
        )
        service_cache.pop(cleaned_user_id, None)
        return saved

    @app.tool()
    def delete_user_plane_token(user_id: str) -> dict[str, Any]:
        """Delete stored Plane API token for a user."""
        cleaned_user_id = _require_user_email(user_id)
        deleted = credentials_store.delete_plane_credentials(cleaned_user_id)
        service_cache.pop(cleaned_user_id, None)
        return {"user_id": cleaned_user_id, "deleted": deleted}

    @app.tool()
    def list_connected_users() -> dict[str, Any]:
        """List all users that have a stored Plane token."""
        users = credentials_store.list_users()
        return {"users": users, "count": len(users)}

    @app.tool()
    def create_task(
        title: str,
        description: str = "",
        assignee: str | None = None,
        assign_to: str | None = None,
        label_ids: list[str] | None = None,
        label_names: list[str] | None = None,
        priority: Priority = "medium",
        start_date: str | None = None,
        due_date: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a task in Plane. Dates should use YYYY-MM-DD format."""
        service = resolve_service(user_id)
        chosen_assignee = (
            assign_to.strip()
            if isinstance(assign_to, str) and assign_to.strip()
            else assignee
        )
        chosen_assignee = _require_assignee_email(chosen_assignee)
        return service.create_task(
            title=title,
            description=description,
            assignee=chosen_assignee,
            priority=priority,
            start_date=start_date,
            due_date=due_date,
            label_ids=label_ids,
            label_names=label_names,
            project_id=project_id,
        )

    @app.tool()
    def list_tasks(
        status: Status | None = None,
        assignee: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        page_size: int = 20,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List tasks from Plane."""
        service = resolve_service(user_id)
        return service.list_tasks(
            status=status,
            assignee=assignee,
            limit=limit,
            cursor=cursor,
            page_size=page_size,
            project_id=project_id,
        )

    @app.tool()
    def list_tasks_paginated(
        status: Status | None = None,
        assignee: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        page_size: int = 20,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """List tasks with cursor pagination metadata."""
        service = resolve_service(user_id)
        return service.list_tasks_paginated(
            status=status,
            assignee=assignee,
            limit=limit,
            cursor=cursor,
            page_size=page_size,
            project_id=project_id,
        )

    @app.tool()
    def get_task(
        task_id: str,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Get one task by id from Plane."""
        service = resolve_service(user_id)
        return service.get_task(task_id, project_id=project_id)

    @app.tool()
    def update_task_status(
        task_id: str,
        new_status: Status,
        actor: str = "mcp-bot",
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Update task status in Plane."""
        service = resolve_service(user_id)
        return service.update_task_status(
            task_id=task_id,
            new_status=new_status,
            actor=actor,
            project_id=project_id,
        )

    @app.tool()
    def update_task_dates(
        task_id: str,
        start_date: str | None = None,
        due_date: str | None = None,
        actor: str = "mcp-bot",
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Update task start/due dates in Plane. Dates should use YYYY-MM-DD format."""
        service = resolve_service(user_id)
        return service.update_task_dates(
            task_id=task_id,
            start_date=start_date,
            due_date=due_date,
            actor=actor,
            project_id=project_id,
        )

    @app.tool()
    def assign_task(
        task_id: str,
        assignee: str,
        actor: str = "mcp-bot",
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Assign a task in Plane."""
        service = resolve_service(user_id)
        assignee = _require_assignee_email(assignee) or ""
        return service.assign_task(
            task_id=task_id,
            assignee=assignee,
            actor=actor,
            project_id=project_id,
        )

    @app.tool()
    def assign_task_to_plane_user(
        task_id: str,
        assignee: str,
        actor: str = "mcp-bot",
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Assign a task manually to a selected Plane user (email only)."""
        service = resolve_service(user_id)
        assignee = _require_assignee_email(assignee) or ""
        return service.assign_task(
            task_id=task_id,
            assignee=assignee,
            actor=actor,
            project_id=project_id,
        )

    @app.tool()
    def add_comment(
        task_id: str,
        comment: str,
        author: str = "mcp-bot",
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a comment to a task in Plane."""
        service = resolve_service(user_id)
        return service.add_comment(
            task_id=task_id,
            comment=comment,
            author=author,
            project_id=project_id,
        )

    @app.tool()
    def list_task_comments(
        task_id: str,
        limit: int = 100,
        cursor: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """List comments of a task with pagination metadata."""
        service = resolve_service(user_id)
        return service.list_task_comments(
            task_id=task_id,
            limit=limit,
            cursor=cursor,
            project_id=project_id,
        )

    @app.tool()
    def delete_task(
        task_id: str,
        actor: str = "mcp-bot",
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Delete a task in Plane."""
        service = resolve_service(user_id)
        return service.delete_task(task_id=task_id, actor=actor, project_id=project_id)

    @app.tool()
    def update_from_natural_text(
        text: str,
        actor: str = "mcp-bot",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Update tasks using simple Spanish natural language commands."""
        service = resolve_service(user_id)
        text_updater = NaturalTextUpdater(service)
        return text_updater.update(text=text, actor=actor)

    @app.tool()
    def list_plane_states(
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List available states configured in Plane project."""
        service = resolve_service(user_id)
        return service.list_states(project_id=project_id)

    @app.tool()
    def list_plane_projects(
        limit: int = 200, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List projects available in Plane workspace for current user."""
        service = resolve_service(user_id)
        return service.list_projects(limit=limit)

    @app.tool()
    def list_plane_members(
        limit: int = 200, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List workspace members that can be assigned."""
        service = resolve_service(user_id)
        return service.list_members(limit=limit)

    @app.tool()
    def list_plane_users(
        query: str | None = None,
        limit: int = 200,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List/filter workspace Plane users (not restricted by project membership)."""
        service = resolve_service(user_id)
        return service.list_assignable_users(query=query, limit=limit)

    @app.tool()
    def list_project_users(
        query: str | None = None,
        limit: int = 200,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """List users that belong to a project and can be assigned."""
        service = resolve_service(user_id)
        result = service.list_project_users(limit=500, project_id=project_id)
        users = result.get("users") if isinstance(result, dict) else []
        if isinstance(users, list) and query and query.strip():
            needle = query.strip().lower()
            users = [
                user
                for user in users
                if needle in str(user.get("email", "")).lower()
                or needle in str(user.get("display_name", "")).lower()
            ]
        safe_limit = max(1, min(limit, 500))
        if isinstance(users, list):
            result["users"] = users[:safe_limit]
            result["count"] = len(result["users"])
        return result

    @app.tool()
    def list_plane_labels(
        limit: int = 200,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List labels available in Plane project."""
        service = resolve_service(user_id)
        return service.list_labels(limit=limit, project_id=project_id)

    @app.tool()
    def create_plane_label(
        name: str,
        color: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a new label in Plane project."""
        service = resolve_service(user_id)
        return service.create_label(name=name, color=color, project_id=project_id)

    @app.tool()
    def set_task_labels(
        task_id: str,
        label_ids: list[str] | None = None,
        label_names: list[str] | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Set labels for a task by IDs or names."""
        service = resolve_service(user_id)
        return service.set_task_labels(
            task_id=task_id,
            label_ids=label_ids,
            label_names=label_names,
            project_id=project_id,
        )

    @app.tool()
    def list_plane_cycles(
        limit: int = 200,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List cycles/sprints in Plane project."""
        service = resolve_service(user_id)
        return service.list_cycles(limit=limit, project_id=project_id)

    @app.tool()
    def set_task_cycle(
        task_id: str,
        cycle_id: str | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Set or clear cycle/sprint for a task."""
        service = resolve_service(user_id)
        return service.set_task_cycle(
            task_id=task_id,
            cycle_id=cycle_id,
            project_id=project_id,
        )

    @app.tool()
    def search_tasks(
        query: str | None = None,
        status: Status | None = None,
        assignee: str | None = None,
        start_date_from: str | None = None,
        start_date_to: str | None = None,
        due_date_from: str | None = None,
        due_date_to: str | None = None,
        limit: int = 50,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search tasks by text, status, assignee and date ranges."""
        service = resolve_service(user_id)
        return service.search_tasks(
            query=query,
            status=status,
            assignee=assignee,
            start_date_from=start_date_from,
            start_date_to=start_date_to,
            due_date_from=due_date_from,
            due_date_to=due_date_to,
            limit=limit,
            project_id=project_id,
        )

    @app.tool()
    def bulk_update_tasks(
        task_ids: list[str],
        new_status: Status | None = None,
        assignee: str | None = None,
        start_date: str | None = None,
        due_date: str | None = None,
        label_ids: list[str] | None = None,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Bulk update tasks in Plane (status, assignee, dates, labels)."""
        service = resolve_service(user_id)
        return service.bulk_update_tasks(
            task_ids=task_ids,
            new_status=new_status,
            assignee=assignee,
            start_date=start_date,
            due_date=due_date,
            label_ids=label_ids,
            project_id=project_id,
        )

    @app.tool()
    def report_task_labels(
        status: Status | None = None,
        assignee: str | None = None,
        limit: int = 500,
        page_size: int = 100,
        include_unlabeled: bool = True,
        project_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Generate label usage report across tasks."""
        service = resolve_service(user_id)
        return service.report_task_labels(
            status=status,
            assignee=assignee,
            limit=limit,
            page_size=page_size,
            include_unlabeled=include_unlabeled,
            project_id=project_id,
        )

    @app.tool()
    def plane_agent(
        command: str, user_id: str | None = None, actor: str = "mcp-bot"
    ) -> dict[str, Any]:
        """Natural-language agent router for task operations."""
        return agent_router.handle(command=command, user_id=user_id, actor=actor)

    return app


mcp = create_app()
