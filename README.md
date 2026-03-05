# MCP local para tareas (tipo Plane/Jira)

Servidor MCP en Python con FastMCP para gestionar tareas de desarrollo en modo local.

## Features

- Crear tareas
- Listar tareas (filtros por estado y asignado)
- Ver una tarea por ID
- Cambiar estado (`backlog`, `todo`, `in_progress`, `done`, `cancelled`)
- Asignar tareas
- Definir fecha de inicio y vencimiento (`start_date`, `due_date`)
- Agregar comentarios
- Gestion de labels, ciclos (sprints) y miembros de Plane
- Busqueda avanzada y actualizaciones en lote
- Actualizar tareas con texto natural en espanol
- Modo backend local o Plane API real (por variables de entorno)
- Modo multiusuario para despliegue (credenciales por usuario)

Persistencia local en `data/tasks.json`.

## Arquitectura del proyecto

```text
.
|-- server.py
|-- task_mcp/
|   |-- __init__.py
|   |-- app.py
|   |-- natural_language.py
|   |-- plane_service.py
|   |-- credential_store.py
|   |-- service.py
|   |-- storage.py
|   `-- types.py
|-- requirements.txt
|-- Dockerfile
`-- docker-compose.yml
```

- `task_mcp/storage.py`: acceso a datos y persistencia JSON.
- `task_mcp/service.py`: logica de negocio de tareas.
- `task_mcp/natural_language.py`: parser de comandos naturales.
- `task_mcp/plane_service.py`: integracion con Plane API.
- `task_mcp/credential_store.py`: credenciales cifradas por usuario.
- `task_mcp/app.py`: wiring del servidor FastMCP y exposicion de tools.
- `server.py`: punto de entrada de ejecucion.

## Requisitos

- Python 3.10+

## Ejecutar en local

```bash
pip install -r requirements.txt
python server.py
```

## Configuracion para Plane API

1. Copia `.env.example` a `.env`.
2. Completa estos valores:
   - `MCP_USE_PLANE=true`
   - `PLANE_BASE_URL`
   - `PLANE_API_TOKEN`
   - `PLANE_WORKSPACE_SLUG`
   - `PLANE_PROJECT_ID` (opcional)

Si `MCP_USE_PLANE=false`, el servidor usa almacenamiento local en `data/tasks.json`.

## Multiusuario (cada usuario con su token)

Para despliegue, usa este modo:

1. `MCP_MULTI_TENANT=true`
2. `MCP_CREDENTIALS_KEY=<fernet_key>`
3. Registrar credenciales por usuario con `upsert_user_plane_credentials(...)`
4. Ejecutar tools de tareas con `user_id`

Con este flujo no envias token en cada tool de tareas.

### Modo seguro para clientes con bloqueo de secretos

Si tu cliente bloquea enviar `plane_api_token` en llamadas MCP, puedes evitarlo usando solo variables de entorno del servidor:

1. Configura en el servidor: `PLANE_BASE_URL`, `PLANE_API_TOKEN`, `PLANE_WORKSPACE_SLUG` (`PLANE_PROJECT_ID` opcional)
2. Activa `MCP_MULTI_TENANT_USE_SERVER_PLANE_CREDENTIALS=true` para fallback automatico por usuario
3. Opcional: conecta un usuario sin enviar token con `connect_user_to_server_plane_credentials(user_id)`

### Mini interfaz web para registrar token de Plane

El servidor expone un formulario en `GET /connect-plane` para guardar credenciales cifradas por usuario.

- URL ejemplo en despliegue: `https://tu-servidor.fastmcp.app/connect-plane`
- Requiere `MCP_MULTI_TENANT=true`
- Guarda en `credentials.json` usando cifrado Fernet (`MCP_CREDENTIALS_KEY`)

Seguridad opcional del formulario:

- Define `MCP_CONNECT_UI_KEY=<clave_compartida>`
- Abre la pagina con `?key=<clave_compartida>` (o header `x-connect-key`)

### Nota para despliegues con filesystem de solo lectura

Si tu runtime no permite escribir en la carpeta del proyecto (por ejemplo, algunos despliegues gestionados), define una ruta escribible:

- `MCP_DATA_DIR=/tmp/mcp-plane-data`

En esa ruta se guardan `tasks.json` y `credentials.json`.

## Ejecutar con Docker

Build de imagen:

```bash
docker build -t mcp-tasks-local .
```

Run del contenedor:

```bash
docker run --rm -it -v "${PWD}/data:/app/data" mcp-tasks-local
```

Con Docker Compose:

```bash
docker compose up --build
```

`docker-compose.yml` ya carga variables desde `.env`.

## Herramientas MCP expuestas

- `create_task(..., start_date=None, due_date=None, project_id=None, user_id=None)`
- `list_tasks(..., user_id=None)`
- `get_task(task_id, user_id=None)`
- `update_task_status(..., user_id=None)`
- `update_task_dates(task_id, start_date=None, due_date=None, user_id=None)`
- `assign_task(..., user_id=None)`
- `add_comment(..., user_id=None)`
- `update_from_natural_text(..., user_id=None)`
- `list_plane_states(user_id=None)`
- `list_plane_projects(limit=200, user_id=None)`
- `list_plane_members(limit=200, user_id=None)`
- `list_plane_users(query=None, limit=200, user_id=None)`
- `list_plane_labels(limit=200, user_id=None)`
- `create_plane_label(name, color=None, project_id=None, user_id=None)`
- `set_task_labels(task_id, label_ids=None, label_names=None, project_id=None, user_id=None)`
- `list_plane_cycles(limit=200, project_id=None, user_id=None)`
- `set_task_cycle(task_id, cycle_id=None, project_id=None, user_id=None)`
- `search_tasks(query=None, status=None, assignee=None, start_date_from=None, start_date_to=None, due_date_from=None, due_date_to=None, limit=50, project_id=None, user_id=None)`
- `bulk_update_tasks(task_ids, new_status=None, assignee=None, start_date=None, due_date=None, label_ids=None, project_id=None, user_id=None)`
- `assign_task_to_plane_user(task_id, assignee, project_id=None, actor="mcp-bot", user_id=None)`
- `plane_agent(command, user_id=None, actor="mcp-bot")`

Tools de gestion de credenciales:

- `upsert_user_plane_credentials(user_id, plane_base_url, plane_api_token, plane_workspace_slug, plane_project_id=None)`
- `connect_user_plane_account(user_id, plane_api_token, plane_workspace_slug, plane_base_url=None)`
- `connect_user_plane_quick(user_id, plane_workspace_slug, plane_api_token)`
- `connect_user_to_server_plane_credentials(user_id)`
- `delete_user_plane_credentials(user_id)`
- `list_connected_users()`

## Comandos de texto natural soportados

- `crea tarea: integrar login social`
- `mueve TSK-1a2b3c4d a por hacer`
- `asigna TSK-1a2b3c4d a carla`
- `comenta TSK-1a2b3c4d: revisar bug en auth`

Tambien puedes usar un router unificado:

- `crear issue login social inicio 2026-03-05 fin 2026-03-07`
- `pasar esa tarea a in progress`
- `listar mis issues`
- `comentar esa tarea: revisar con backend`

## Siguiente paso: Plane real

1. Reemplazar `TaskRepository` local por cliente API de Plane.
2. Mantener la misma interfaz de tools MCP.
3. Mapear `task_id` local a IDs reales de Plane.
