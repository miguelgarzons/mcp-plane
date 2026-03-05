# MCP local para tareas (tipo Plane/Jira)

Servidor MCP en Python con FastMCP para gestionar tareas de desarrollo en modo local.

## Features

- Crear tareas
- Listar tareas (filtros por estado y asignado)
- Ver una tarea por ID
- Cambiar estado (`todo`, `in_progress`, `done`, `blocked`)
- Asignar tareas
- Agregar comentarios
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
   - `PLANE_PROJECT_ID`

Si `MCP_USE_PLANE=false`, el servidor usa almacenamiento local en `data/tasks.json`.

## Multiusuario (cada usuario con su token)

Para despliegue, usa este modo:

1. `MCP_MULTI_TENANT=true`
2. `MCP_CREDENTIALS_KEY=<fernet_key>`
3. Registrar credenciales por usuario con `upsert_user_plane_credentials(...)`
4. Ejecutar tools de tareas con `user_id`

Con este flujo no envias token en cada tool de tareas.

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

- `create_task(..., user_id=None)`
- `list_tasks(..., user_id=None)`
- `get_task(task_id, user_id=None)`
- `update_task_status(..., user_id=None)`
- `assign_task(..., user_id=None)`
- `add_comment(..., user_id=None)`
- `update_from_natural_text(..., user_id=None)`

Tools de gestion de credenciales:

- `upsert_user_plane_credentials(user_id, plane_base_url, plane_api_token, plane_workspace_slug, plane_project_id)`
- `delete_user_plane_credentials(user_id)`
- `list_connected_users()`

## Comandos de texto natural soportados

- `crea tarea: integrar login social`
- `mueve TSK-1a2b3c4d a done`
- `asigna TSK-1a2b3c4d a carla`
- `comenta TSK-1a2b3c4d: revisar bug en auth`

## Siguiente paso: Plane real

1. Reemplazar `TaskRepository` local por cliente API de Plane.
2. Mantener la misma interfaz de tools MCP.
3. Mapear `task_id` local a IDs reales de Plane.
