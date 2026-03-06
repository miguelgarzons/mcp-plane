from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from cryptography.fernet import Fernet
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from .agent_router import PlaneAgentRouter
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


def _get_credentials_key(required: bool) -> str:
    key = os.getenv("MCP_CREDENTIALS_KEY", "").strip()
    if key:
        return key
    if required:
        raise ValueError("MCP_CREDENTIALS_KEY is required when MCP_MULTI_TENANT=true")
    return Fernet.generate_key().decode("utf-8")


def _get_plane_env_credentials(required: bool = False) -> dict[str, str] | None:
    base_url = os.getenv("PLANE_BASE_URL", "").strip()
    api_token = os.getenv("PLANE_API_TOKEN", "").strip()
    workspace_slug = os.getenv("PLANE_WORKSPACE_SLUG", "").strip()
    project_id = os.getenv("PLANE_PROJECT_ID", "").strip()

    if all([base_url, api_token, workspace_slug]):
        return {
            "base_url": base_url,
            "api_token": api_token,
            "workspace_slug": workspace_slug,
            "project_id": project_id,
        }

    if required:
        raise ValueError(
            "Missing Plane config in environment. Set PLANE_BASE_URL, PLANE_API_TOKEN, and PLANE_WORKSPACE_SLUG. "
            "PLANE_PROJECT_ID is optional."
        )
    return None


def _default_plane_base_url() -> str:
    return os.getenv("PLANE_BASE_URL", "").strip() or "https://api.plane.so"


def _is_ui_authorized(request: Request, form_key: str | None = None) -> bool:
    required_key = os.getenv("MCP_CONNECT_UI_KEY", "").strip()
    if not required_key:
        return True

    query_key = str(request.query_params.get("key", "")).strip()
    header_key = str(request.headers.get("x-connect-key", "")).strip()
    form_connect_key = form_key.strip() if isinstance(form_key, str) else ""
    return query_key == required_key or header_key == required_key or form_connect_key == required_key


def _connect_form_html(
    *,
    message: str | None = None,
    error: str | None = None,
    values: dict[str, str] | None = None,
    connect_key: str | None = None,
) -> str:
    values = values or {}
    status_message = ""
    if error:
        status_message = f"<p style='color:#b91c1c;font-weight:600'>{error}</p>"
    elif message:
        status_message = f"<p style='color:#166534;font-weight:600'>{message}</p>"

    def _v(key: str, fallback: str = "") -> str:
        return values.get(key, fallback).replace("\"", "&quot;")

    hidden_key = ""
    if connect_key:
        safe_key = connect_key.replace("\"", "&quot;")
        hidden_key = f"<input type='hidden' name='connect_key' value='{safe_key}' />"

    return f"""
<!doctype html>
<html lang=\"es\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Conectar Plane</title>
  <style>
    body {{ font-family: ui-sans-serif, -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background: #f5f7fb; color: #0f172a; }}
    .wrap {{ max-width: 640px; margin: 32px auto; padding: 24px; background: #fff; border-radius: 14px; box-shadow: 0 10px 30px rgba(2,6,23,.08); }}
    h1 {{ margin: 0 0 8px; font-size: 22px; }}
    p {{ margin: 0 0 16px; color: #334155; }}
    label {{ display:block; margin: 12px 0 6px; font-weight: 600; }}
    input {{ width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid #cbd5e1; border-radius: 10px; }}
    .hint {{ margin-top: 8px; font-size: 13px; color:#475569; }}
    button {{ margin-top: 16px; width: 100%; border: 0; padding: 12px; border-radius: 10px; background: #0f766e; color: white; font-weight: 700; cursor: pointer; }}
  </style>
</head>
<body>
  <main class=\"wrap\">
    <h1>Conectar credenciales de Plane</h1>
    <p>Guarda tus credenciales de forma cifrada para usar el MCP sin enviar tokens por el chat.</p>
    {status_message}
    <form method=\"post\" action=\"/connect-plane\">
      {hidden_key}
      <label for=\"user_id\">Usuario</label>
      <input id=\"user_id\" name=\"user_id\" required placeholder=\"correo@empresa.com\" value=\"{_v('user_id')}\" />

      <label for=\"plane_base_url\">Plane Base URL (opcional)</label>
      <input id=\"plane_base_url\" name=\"plane_base_url\" placeholder=\"https://proyectos.cunapp.pro (si lo dejas vacio usa API por defecto)\" value=\"{_v('plane_base_url')}\" />

      <label for=\"plane_workspace_slug\">Workspace slug</label>
      <input id=\"plane_workspace_slug\" name=\"plane_workspace_slug\" required placeholder=\"fs\" value=\"{_v('plane_workspace_slug')}\" />

      <label for=\"plane_api_token\">API Token</label>
      <input id=\"plane_api_token\" name=\"plane_api_token\" type=\"password\" required placeholder=\"plane_api_xxx\" />

      <button type=\"submit\">Guardar credenciales</button>
      <p class=\"hint\">Formato recomendado de fechas en tools: YYYY-MM-DD</p>
    </form>
  </main>
</body>
</html>
"""


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
        env_credentials = _get_plane_env_credentials(required=True)
        if env_credentials is None:
            raise ValueError("Missing Plane environment credentials")

        return _create_plane_service(
            base_url=env_credentials["base_url"],
            api_token=env_credentials["api_token"],
            workspace_slug=env_credentials["workspace_slug"],
            project_id=env_credentials["project_id"],
        )

    default_tasks_file = _resolve_data_dir() / "tasks.json"
    repository = TaskRepository(tasks_file or default_tasks_file)
    return TaskService(repository)


def create_app(tasks_file: Path | None = None) -> FastMCP:
    default_service = create_service(tasks_file=tasks_file)
    enable_multi_tenant = _is_truthy(os.getenv("MCP_MULTI_TENANT"))
    server_plane_credentials = _get_plane_env_credentials(required=False)

    data_dir = _resolve_data_dir()
    credentials_path = data_dir / "credentials.json"
    credentials_store = CredentialStore(credentials_path, encryption_key=_get_credentials_key(required=enable_multi_tenant))

    def resolve_service(user_id: str | None = None) -> TaskService | PlaneTaskService:
        if enable_multi_tenant:
            if not user_id or not user_id.strip():
                raise ValueError("user_id is required when MCP_MULTI_TENANT=true")
            cleaned_user_id = user_id.strip()
            credentials = credentials_store.get_plane_credentials(cleaned_user_id)
            if not credentials and server_plane_credentials:
                credentials_store.upsert_plane_credentials(
                    user_id=cleaned_user_id,
                    base_url=server_plane_credentials["base_url"],
                    workspace_slug=server_plane_credentials["workspace_slug"],
                    project_id=server_plane_credentials.get("project_id"),
                    api_token=server_plane_credentials["api_token"],
                )
                credentials = credentials_store.get_plane_credentials(cleaned_user_id)
            if not credentials:
                raise ValueError(
                    f"No Plane credentials found for user_id={user_id}. "
                    "First connect with connect_user_plane_quick(user_id, plane_workspace_slug, plane_api_token)."
                )
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

    agent_router = PlaneAgentRouter(resolve_service=resolve_service)

    app = FastMCP("plane-local-tasks")

    @app.custom_route("/connect-plane", methods=["GET"])
    async def connect_plane_get(request: Request) -> HTMLResponse:
        if not _is_ui_authorized(request):
            return HTMLResponse("Unauthorized", status_code=401)

        params = dict(request.query_params)
        return HTMLResponse(
            _connect_form_html(
                message=str(params.get("message", "")).strip() or None,
                error=str(params.get("error", "")).strip() or None,
                connect_key=str(params.get("key", "")).strip() or None,
                values={
                    "user_id": str(params.get("user_id", "")),
                    "plane_base_url": str(params.get("plane_base_url", "")),
                    "plane_workspace_slug": str(params.get("plane_workspace_slug", "")),
                },
            )
        )

    @app.custom_route("/connect-plane", methods=["POST"])
    async def connect_plane_post(request: Request) -> RedirectResponse:
        form = await request.form()
        connect_key = str(form.get("connect_key", "")).strip()
        if not _is_ui_authorized(request, form_key=connect_key):
            return RedirectResponse("/connect-plane?error=Unauthorized", status_code=303)

        if not enable_multi_tenant:
            return RedirectResponse(
                "/connect-plane?error=MCP_MULTI_TENANT%3Dfalse.+Enable+it+to+store+per-user+credentials.",
                status_code=303,
            )

        user_id = str(form.get("user_id", "")).strip()
        base_url = str(form.get("plane_base_url", "")).strip()
        workspace_slug = str(form.get("plane_workspace_slug", "")).strip()
        api_token = str(form.get("plane_api_token", "")).strip()
        resolved_base_url = base_url or _default_plane_base_url()

        if not all([user_id, workspace_slug, api_token]):
            query = urlencode(
                {
                    "error": "Missing required fields: user_id, workspace_slug, plane_api_token",
                    "user_id": user_id,
                    "plane_base_url": base_url,
                    "plane_workspace_slug": workspace_slug,
                }
            )
            return RedirectResponse(f"/connect-plane?{query}", status_code=303)

        credentials_store.upsert_plane_credentials(
            user_id=user_id,
            base_url=resolved_base_url,
            workspace_slug=workspace_slug,
            project_id=None,
            api_token=api_token,
        )

        query = urlencode(
            {
                "message": "Credenciales guardadas correctamente",
                "user_id": user_id,
                "plane_base_url": resolved_base_url,
                "plane_workspace_slug": workspace_slug,
            }
        )
        return RedirectResponse(f"/connect-plane?{query}", status_code=303)

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
        if isinstance(service, PlaneTaskService):
            return service.create_task(
                title=title,
                description=description,
                assignee=assignee,
                priority=priority,
                start_date=start_date,
                due_date=due_date,
            )
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
        if isinstance(service, PlaneTaskService):
            return service.list_tasks(status=status, assignee=assignee, limit=limit)
        return service.list_tasks(status=status, assignee=assignee, limit=limit)

    @app.tool()
    def get_task(
        task_id: str,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Get one task by id from local storage or Plane."""
        service = resolve_service(user_id=user_id)
        if isinstance(service, PlaneTaskService):
            return service.get_task(task_id)
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
        if isinstance(service, PlaneTaskService):
            return service.update_task_status(task_id=task_id, new_status=new_status, actor=actor)
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
        if isinstance(service, PlaneTaskService):
            return service.update_task_dates(
                task_id=task_id,
                start_date=start_date,
                due_date=due_date,
                actor=actor,
            )
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
        if isinstance(service, PlaneTaskService):
            return service.assign_task(task_id=task_id, assignee=assignee, actor=actor)
        return service.assign_task(task_id=task_id, assignee=assignee, actor=actor)

    @app.tool()
    def assign_task_to_plane_user(
        task_id: str,
        assignee: str,
        actor: str = "mcp-bot",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Assign a task manually to a selected Plane user (email/name/id)."""
        service = resolve_service(user_id=user_id)
        if isinstance(service, PlaneTaskService):
            return service.assign_task(task_id=task_id, assignee=assignee, actor=actor)
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
        if isinstance(service, PlaneTaskService):
            return service.add_comment(task_id=task_id, comment=comment, author=author)
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
    def connect_user_plane_quick(
        user_id: str,
        plane_workspace_slug: str,
        plane_api_token: str,
    ) -> dict[str, str]:
        """Quick first-time connection using only workspace slug + API token.

        Base URL is inferred from PLANE_BASE_URL env or defaults to https://api.plane.so.
        """
        if not enable_multi_tenant:
            raise ValueError("MCP_MULTI_TENANT=false. Enable it to manage per-user credentials.")
        return credentials_store.upsert_plane_credentials(
            user_id=user_id,
            base_url=_default_plane_base_url(),
            workspace_slug=plane_workspace_slug,
            project_id=None,
            api_token=plane_api_token,
        )

    @app.tool()
    def connect_user_to_server_plane_credentials(user_id: str) -> dict[str, str]:
        """Connect a user to Plane credentials already configured in server env (no token in tool args)."""
        if not enable_multi_tenant:
            raise ValueError("MCP_MULTI_TENANT=false. Enable it to manage per-user credentials.")
        env_credentials = _get_plane_env_credentials(required=True)
        if env_credentials is None:
            raise ValueError("Missing Plane environment credentials")
        return credentials_store.upsert_plane_credentials(
            user_id=user_id,
            base_url=env_credentials["base_url"],
            workspace_slug=env_credentials["workspace_slug"],
            project_id=env_credentials["project_id"],
            api_token=env_credentials["api_token"],
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
    def list_plane_projects(limit: int = 200, user_id: str | None = None) -> list[dict[str, Any]]:
        """List projects available in Plane workspace for current user."""
        service = resolve_plane_service(user_id=user_id)
        return service.list_projects(limit=limit)

    @app.tool()
    def list_plane_members(limit: int = 200, user_id: str | None = None) -> list[dict[str, Any]]:
        """List workspace members that can be assigned."""
        service = resolve_plane_service(user_id=user_id)
        return service.list_members(limit=limit)

    @app.tool()
    def list_plane_users(
        query: str | None = None,
        limit: int = 200,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List/filter Plane users for manual task assignment."""
        service = resolve_plane_service(user_id=user_id)
        return service.list_assignable_users(query=query, limit=limit)

    @app.tool()
    def list_plane_labels(
        limit: int = 200,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List labels available in Plane project."""
        service = resolve_plane_service(user_id=user_id)
        return service.list_labels(limit=limit)

    @app.tool()
    def create_plane_label(
        name: str,
        color: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
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
        return service.set_task_labels(
            task_id=task_id,
            label_ids=label_ids,
            label_names=label_names,
        )

    @app.tool()
    def list_plane_cycles(
        limit: int = 200,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List cycles/sprints in Plane project."""
        service = resolve_plane_service(user_id=user_id)
        return service.list_cycles(limit=limit)

    @app.tool()
    def set_task_cycle(
        task_id: str,
        cycle_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
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

    @app.tool()
    def plane_agent(command: str, user_id: str | None = None, actor: str = "mcp-bot") -> dict[str, Any]:
        """Natural-language agent router for task operations.

        Supports intents: create, move status, assign, comment, list, details, update dates.
        """
        return agent_router.handle(command=command, user_id=user_id, actor=actor)

    return app


mcp = create_app()
