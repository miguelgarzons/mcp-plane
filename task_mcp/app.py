from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from fastmcp import FastMCP

from .credential_store import CredentialStore
from .natural_language import NaturalTextUpdater
from .plane_service import PlaneTaskService
from .service import TaskService
from .storage import TaskRepository
from .types import Priority, Status


def _is_truthy(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _create_plane_service(base_url: str, api_token: str, workspace_slug: str, project_id: str) -> PlaneTaskService:
    return PlaneTaskService(
        base_url=base_url,
        api_token=api_token,
        workspace_slug=workspace_slug,
        project_id=project_id,
    )


def _get_credentials_key(required: bool) -> str:
    key = os.getenv("MCP_CREDENTIALS_KEY", "").strip()
    if key:
        return key
    if required:
        raise ValueError("MCP_CREDENTIALS_KEY is required when MCP_MULTI_TENANT=true")
    return Fernet.generate_key().decode("utf-8")


def create_service(tasks_file: Path | None = None) -> TaskService | PlaneTaskService:
    use_plane = _is_truthy(os.getenv("MCP_USE_PLANE"))
    if use_plane:
        base_url = os.getenv("PLANE_BASE_URL", "").strip()
        api_token = os.getenv("PLANE_API_TOKEN", "").strip()
        workspace_slug = os.getenv("PLANE_WORKSPACE_SLUG", "").strip()
        project_id = os.getenv("PLANE_PROJECT_ID", "").strip()

        if not all([base_url, api_token, workspace_slug, project_id]):
            raise ValueError(
                "Missing Plane config. Set PLANE_BASE_URL, PLANE_API_TOKEN, "
                "PLANE_WORKSPACE_SLUG, and PLANE_PROJECT_ID."
            )

        return _create_plane_service(
            base_url=base_url,
            api_token=api_token,
            workspace_slug=workspace_slug,
            project_id=project_id,
        )

    default_tasks_file = Path(__file__).resolve().parent.parent / "data" / "tasks.json"
    repository = TaskRepository(tasks_file or default_tasks_file)
    return TaskService(repository)


def create_app(tasks_file: Path | None = None) -> FastMCP:
    default_service = create_service(tasks_file=tasks_file)
    enable_multi_tenant = _is_truthy(os.getenv("MCP_MULTI_TENANT"))

    data_dir = Path(__file__).resolve().parent.parent / "data"
    credentials_path = data_dir / "credentials.json"
    credentials_store = CredentialStore(credentials_path, encryption_key=_get_credentials_key(required=enable_multi_tenant))

    def resolve_service(user_id: str | None = None) -> TaskService | PlaneTaskService:
        if enable_multi_tenant:
            if not user_id or not user_id.strip():
                raise ValueError("user_id is required when MCP_MULTI_TENANT=true")
            credentials = credentials_store.get_plane_credentials(user_id.strip())
            if not credentials:
                raise ValueError(f"No Plane credentials found for user_id={user_id}")
            return _create_plane_service(
                base_url=credentials["base_url"],
                api_token=credentials["api_token"],
                workspace_slug=credentials["workspace_slug"],
                project_id=credentials["project_id"],
            )
        return default_service

    app = FastMCP("plane-local-tasks")

    @app.tool()
    def create_task(
        title: str,
        description: str = "",
        assignee: str | None = None,
        priority: Priority = "medium",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a task in local storage or Plane."""
        service = resolve_service(user_id=user_id)
        return service.create_task(
            title=title,
            description=description,
            assignee=assignee,
            priority=priority,
        )

    @app.tool()
    def list_tasks(
        status: Status | None = None,
        assignee: str | None = None,
        limit: int = 50,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List tasks from local storage or Plane."""
        service = resolve_service(user_id=user_id)
        return service.list_tasks(status=status, assignee=assignee, limit=limit)

    @app.tool()
    def get_task(
        task_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Get one task by id from local storage or Plane."""
        service = resolve_service(user_id=user_id)
        return service.get_task(task_id)

    @app.tool()
    def update_task_status(
        task_id: str,
        new_status: Status,
        actor: str = "mcp-bot",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Update task status in local storage or Plane."""
        service = resolve_service(user_id=user_id)
        return service.update_task_status(task_id=task_id, new_status=new_status, actor=actor)

    @app.tool()
    def assign_task(
        task_id: str,
        assignee: str,
        actor: str = "mcp-bot",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Assign task in local storage or Plane."""
        service = resolve_service(user_id=user_id)
        return service.assign_task(task_id=task_id, assignee=assignee, actor=actor)

    @app.tool()
    def add_comment(
        task_id: str,
        comment: str,
        author: str = "mcp-bot",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Add a comment to a task in local storage or Plane."""
        service = resolve_service(user_id=user_id)
        return service.add_comment(task_id=task_id, comment=comment, author=author)

    @app.tool()
    def update_from_natural_text(
        text: str,
        actor: str = "mcp-bot",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Update tasks using simple Spanish natural language commands."""
        service = resolve_service(user_id=user_id)
        text_updater = NaturalTextUpdater(service)
        return text_updater.update(text=text, actor=actor)

    @app.tool()
    def upsert_user_plane_credentials(
        user_id: str,
        plane_base_url: str,
        plane_api_token: str,
        plane_workspace_slug: str,
        plane_project_id: str,
    ) -> dict[str, str]:
        """Save/update encrypted Plane credentials for a user."""
        if not enable_multi_tenant:
            raise ValueError("MCP_MULTI_TENANT=false. Enable it to manage per-user credentials.")
        return credentials_store.upsert_plane_credentials(
            user_id=user_id,
            base_url=plane_base_url,
            workspace_slug=plane_workspace_slug,
            project_id=plane_project_id,
            api_token=plane_api_token,
        )

    @app.tool()
    def delete_user_plane_credentials(user_id: str) -> dict[str, Any]:
        """Delete stored Plane credentials for a user."""
        if not enable_multi_tenant:
            raise ValueError("MCP_MULTI_TENANT=false. Enable it to manage per-user credentials.")
        deleted = credentials_store.delete_plane_credentials(user_id)
        return {"user_id": user_id, "deleted": deleted}

    @app.tool()
    def list_connected_users() -> dict[str, Any]:
        """List user IDs with stored Plane credentials."""
        if not enable_multi_tenant:
            raise ValueError("MCP_MULTI_TENANT=false. Enable it to manage per-user credentials.")
        users = credentials_store.list_users()
        return {"users": users, "count": len(users)}

    return app


mcp = create_app()
