# MCP para Plane con tokens por usuario en PostgreSQL

Servidor MCP en Python para operar tareas de Plane.

## Como funciona ahora

- Las credenciales por usuario se guardan en PostgreSQL.
- Solo necesitas enviar `user_id` en las tools de tareas (`user_id` debe ser correo).
- El token de Plane se registra por usuario con `set_user_plane_token(user_id, plane_api_token)`.
- El `workspace_slug` llega dinamico en cada llamada (igual que `project_id`), no se guarda en DB.
- `PLANE_PROJECT_ID` no se usa; el proyecto se resuelve dinamicamente desde Plane.
- Si el usuario tiene varios proyectos, debe enviar `project_id` en la tool para elegirlo dinamicamente.



```bash
PLANE_BASE_URL=https://api.plane.so
PLANE_WORKSPACE_SLUG=fs

DB_HOST=localhost
DB_PORT=5432
DB_NAME=plane
DB_USER=plane_user
DB_PASSWORD=replace_me
DB_SCHEMA=mcp
```

## Tabla creada automaticamente

Al iniciar, el servidor crea (si no existe):

```sql
CREATE SCHEMA IF NOT EXISTS mcp;

CREATE TABLE IF NOT EXISTS mcp.plane_user_credentials (
  user_id TEXT PRIMARY KEY,
  plane_api_token TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## Ejecucion local

```bash
pip install -r requirements.txt
python server.py
```

## Flujo recomendado

1. Registrar token por usuario:
   - `set_user_plane_token(user_id, plane_api_token)`
2. Trabajar tareas enviando `user_id` y `workspace_slug` (y `project_id` cuando aplique).

## Tools de credenciales

- `set_user_plane_token(user_id, plane_api_token)`
- `delete_user_plane_token(user_id)`
- `list_connected_users()`

## Tools de tareas

Todas las tools de tareas aceptan `user_id` para resolver el token desde DB.
Las tools tambien aceptan `workspace_slug` dinamico por llamada.
Las tools que operan sobre issues/proyecto aceptan `project_id` opcional. Si hay multiples proyectos, enviarlo es obligatorio.
