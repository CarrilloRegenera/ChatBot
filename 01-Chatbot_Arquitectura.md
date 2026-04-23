# Arquitectura de ChatBot

> **Última actualización:** Abril 2026

## 1. Visión general

ChatBot sigue una **arquitectura web cliente-servidor** con frontend ligero (HTML/CSS/JS) y backend en FastAPI.  
La comunicación entre frontend y backend se realiza por HTTP con payload JSON, y el backend accede a SQL Server mediante `pyodbc`.

```text
┌─────────────────────────────────────────────────────────────────┐
│                         CLIENTE (navegador)                    │
│                    Frontend estático (HTML/CSS/JS)             │
│                  frontend/index.html + app.js + styles.css     │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP/JSON
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                        ChatBot API (FastAPI)                   │
│                         backend/main.py                        │
│                                                                 │
│   routes/auth.py  ──► lógica auth  ──► database.py (pyodbc)    │
│   routes/chat.py  ──► lógica chat  ──► database.py (pyodbc)    │
└────────────────────────────┬────────────────────────────────────┘
                             │ SQL
                             ▼
                    ┌──────────────────────┐
                    │   SQL Server         │
                    │   localhost\SQLEXPRESS│
                    │   DB: ChatBot        │
                    │   Usuarios           │
                    │   Conversaciones     │
                    │   Mensajes           │
                    └──────────────────────┘
```

---

## 2. Estructura del proyecto

El proyecto está organizado en dos carpetas principales:

```text
ChatBot/
├── backend/
│   ├── main.py                    ← Entry point FastAPI
│   ├── database.py                ← Conexión SQL Server por pyodbc
│   ├── models.py                  ← Modelos/esquemas de datos
│   └── routes/
│       ├── auth.py                ← Endpoints de registro/login
│       └── chat.py                ← Endpoints de conversaciones/mensajes
│
└── frontend/
    ├── index.html                 ← Interfaz principal
    ├── app.js                     ← Lógica cliente + llamadas fetch
    └── styles.css                 ← Estilos
```

---

## 3. Flujo de una petición típica

1. El usuario abre `frontend/index.html` en el navegador.
2. `app.js` carga eventos de UI y prepara llamadas al backend.
3. El usuario inicia sesión o envía una acción de chat.
4. El frontend realiza una petición HTTP al endpoint correspondiente.
5. FastAPI enruta la petición a `routes/auth.py` o `routes/chat.py`.
6. La ruta valida/transforma datos usando `models.py` (si aplica).
7. La ruta llama a `database.py` para ejecutar consultas SQL.
8. SQL Server devuelve resultados (usuarios, conversaciones, mensajes).
9. La API responde JSON al frontend.
10. `app.js` actualiza la interfaz con la respuesta.

---

## 4. Autenticación y autorización

### Frontend
- Gestión de formulario de login/registro en `app.js`.
- Llamadas HTTP al backend con `fetch`.
- Almacena estado de sesión en cliente (según implementación actual).

### Backend
- Endpoints de autenticación en `backend/routes/auth.py`.
- Endpoints de chat en `backend/routes/chat.py`.
- Validación de datos a través de modelos en `backend/models.py` (si aplica).

### Endpoints de referencia (según implementación actual)
- `POST /register`
- `POST /login`
- `GET /conversations`
- `POST /messages`

> Si en `main.py` se configuró prefijo de router (por ejemplo `/auth` o `/chat`), las rutas finales incluyen ese prefijo.

---

## 5. Modelo funcional de datos

Las tablas principales del dominio son:
- `Usuarios`
- `Conversaciones`
- `Mensajes`

Relaciones típicas:
- Un `Usuario` puede tener muchas `Conversaciones`.
- Una `Conversación` puede tener muchos `Mensajes`.

---

## 6. Gestión de configuración por entorno

### Backend
| Fichero | Uso |
|---|---|
| `backend/database.py` | Cadena de conexión y acceso SQL Server |
| `backend/.env` (si existe) | Variables sensibles locales |
| Variables de entorno | Configuración para despliegue |

### Frontend
| Fichero | Uso |
|---|---|
| `frontend/app.js` | URL base API y lógica de integración |
| `frontend/index.html` | Carga de scripts y estructura UI |

---

## 7. CORS

Si frontend y backend se ejecutan en dominios/puertos distintos, el backend debe habilitar CORS en FastAPI (`CORSMiddleware`) con los orígenes permitidos (ejemplo: `http://localhost:5500`, `http://127.0.0.1:5500`, etc.).

---

## 8. Persistencia y acceso a datos

| Capa | Componente | Responsabilidad |
|---|---|---|
| API | `routes/auth.py`, `routes/chat.py` | Recibir requests y devolver JSON |
| Acceso a datos | `database.py` | Abrir conexión y ejecutar SQL por `pyodbc` |
| Base de datos | SQL Server (`ChatBot`) | Persistencia de usuarios, conversaciones y mensajes |

---

## 9. Procesos en background

Actualmente no se definen workers dedicados ni jobs programados en la estructura base mostrada.  
Si se añade procesamiento asíncrono futuro, se recomienda separarlo en `backend/tasks/` o servicio independiente.

---

## 10. Auditoría y trazabilidad

Estado actual:
- No hay una capa formal de auditoría centralizada documentada.
- Recomendación: registrar `CreatedDate`, `UpdatedDate`, `CreationUser`, `UpdatedUser` en tablas principales para trazabilidad.

---

## 11. Salud de la API y documentación

Recomendado en FastAPI:
- `GET /health` — estado de API/BD.
- `GET /docs` — Swagger UI automático de FastAPI.
- `GET /openapi.json` — especificación OpenAPI.

Si ya están activos en `main.py`, usar esos endpoints como base de monitorización.

---

## 12. Diagrama de dependencias internas

```text
main.py (FastAPI)
   │
   ├── → routes/auth.py
   ├── → routes/chat.py
   ├── → models.py
   └── → database.py

routes/auth.py → database.py
routes/chat.py → database.py
```

`main.py` actúa como punto de entrada y registro de rutas.  
`database.py` es la capa común de conexión SQL para los módulos de dominio.
