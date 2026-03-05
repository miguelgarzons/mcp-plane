from __future__ import annotations

import os
import tempfile
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


def _resolve_data_dir() -> Path:
    configured = os.getenv("MCP_DATA_DIR", "").strip()
    project_data_dir = Path(__file__).resolve().parent.parent / "data"
    fallback_data_dir = Path(tempfile.gettempdir()) / "mcp-plane-data"

    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend([project_data_dir, fallback_data_dir])

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except OSError:
            continue

    raise ValueError("No writable data directory found. Set MCP_DATA_DIR to a writable path.")


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

    default_tasks_file = _resolve_data_dir() / "tasks.json"
    repository = TaskRepository(tasks_file or default_tasks_file)
    return TaskService(repository)


def create_app(tasks_file: Path | None = None) -> FastMCP:
    default_service = create_service(tasks_file=tasks_file)
    enable_multi_tenant = _is_truthy(os.getenv("MCP_MULTI_TENANT"))

    data_dir = _resolve_data_dir()
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

    def resolve_plane_service(user_id: str | None = None) -> PlaneTaskService:
        service = resolve_service(user_id=user_id)
        if not isinstance(service, PlaneTaskService):
            raise ValueError("This tool requires Plane mode. Enable MCP_USE_PLANE=true or MCP_MULTI_TENANT=true.")
        return service

    app = FastMCP("plane-local-tasks")

    @app.tool()
    def create_task(
        title: str,
        description: str = "",
        assignee: str | None = None,
        priority: Priority = "medium",
        start_date: str | None = None,
        due_date: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a task in local storage or Plane.

        Dates should use YYYY-MM-DD format.
        """
        service = resolve_service(user_id=user_id)
        return service.create_task(
            title=title,
            description=description,
            assignee=assignee,
            priority=priority,
            start_date=start_date,
            due_date=due_date,
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
    def update_task_dates(
        task_id: str,
        start_date: str | None = None,
        due_date: str | None = None,
        actor: str = "mcp-bot",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Update task start/due dates in local storage or Plane.

        Dates should use YYYY-MM-DD format.
        """
        service = resolve_service(user_id=user_id)
        return service.update_task_dates(task_id=task_id, start_date=start_date, due_date=due_date, actor=actor)

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

    @app.tool()
    def list_plane_states(user_id: str | None = None) -> list[dict[str, Any]]:
        """List available states configured in Plane project."""
        service = resolve_plane_service(user_id=user_id)
        return service.list_states()

    @app.tool()
    def list_plane_members(limit: int = 200, user_id: str | None = None) -> list[dict[str, Any]]:
        """List workspace members that can be assigned."""
        service = resolve_plane_service(user_id=user_id)
        return service.list_members(limit=limit)

    @app.tool()
    def list_plane_labels(limit: int = 200, user_id: str | None = None) -> list[dict[str, Any]]:
        """List labels available in Plane project."""
        service = resolve_plane_service(user_id=user_id)
        return service.list_labels(limit=limit)

    @app.tool()
    def create_plane_label(name: str, color: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        """Create a new label in Plane project."""
        service = resolve_plane_service(user_id=user_id)
        return service.create_label(name=name, color=color)

    @app.tool()
    def set_task_labels(
        task_id: str,
        label_ids: list[str] | None = None,
        label_names: list[str] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Set labels for a task by IDs or names."""
        service = resolve_plane_service(user_id=user_id)
        return service.set_task_labels(task_id=task_id, label_ids=label_ids, label_names=label_names)

    @app.tool()
    def list_plane_cycles(limit: int = 200, user_id: str | None = None) -> list[dict[str, Any]]:
        """List cycles/sprints in Plane project."""
        service = resolve_plane_service(user_id=user_id)
        return service.list_cycles(limit=limit)

    @app.tool()
    def set_task_cycle(task_id: str, cycle_id: str | None = None, user_id: str | None = None) -> dict[str, Any]:
        """Set or clear cycle/sprint for a task."""
        service = resolve_plane_service(user_id=user_id)
        return service.set_task_cycle(task_id=task_id, cycle_id=cycle_id)

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
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search tasks by text, status, assignee and date ranges.

        Dates should use YYYY-MM-DD format.
        """
        service = resolve_service(user_id=user_id)
        if isinstance(service, PlaneTaskService):
            return service.search_tasks(
                query=query,
                status=status,
                assignee=assignee,
                start_date_from=start_date_from,
                start_date_to=start_date_to,
                due_date_from=due_date_from,
                due_date_to=due_date_to,
                limit=limit,
            )
        return service.list_tasks(status=status, assignee=assignee, limit=limit)

    @app.tool()
    def bulk_update_tasks(
        task_ids: list[str],
        new_status: Status | None = None,
        assignee: str | None = None,
        start_date: str | None = None,
        due_date: str | None = None,
        label_ids: list[str] | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Bulk update tasks in Plane (status, assignee, dates, labels)."""
        service = resolve_plane_service(user_id=user_id)
        return service.bulk_update_tasks(
            task_ids=task_ids,
            new_status=new_status,
            assignee=assignee,
            start_date=start_date,
            due_date=due_date,
            label_ids=label_ids,
        )

    return app


mcp = create_app()
