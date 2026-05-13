import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch

fig, ax = plt.subplots(1, 1, figsize=(22, 15))
ax.set_xlim(0, 22)
ax.set_ylim(0, 15)
ax.axis('off')
fig.patch.set_facecolor('#EEF2FF')

# ── helpers ───────────────────────────────────────────────────────────────────

def rbox(ax, x, y, w, h, fc, ec, lw=1.8, ls='-', radius=0.3, alpha=1.0, zo=2):
    p = FancyBboxPatch((x, y), w, h,
                       boxstyle=f"round,pad=0,rounding_size={radius}",
                       facecolor=fc, edgecolor=ec, linewidth=lw,
                       linestyle=ls, alpha=alpha, zorder=zo)
    ax.add_patch(p)

def txt(ax, x, y, s, fs=9, fw='normal', ha='center', va='center',
        col='#1a1a2e', zo=5):
    ax.text(x, y, s, fontsize=fs, fontweight=fw, ha=ha, va=va,
            color=col, zorder=zo, fontfamily='DejaVu Sans')

def arrow(ax, x1, y1, x2, y2, col='#444', lw=2.0, bidir=False,
          label='', loff=(0, 0.25)):
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle='->', color=col, lw=lw,
                                connectionstyle='arc3,rad=0.0'), zorder=6)
    if bidir:
        ax.annotate('', xy=(x1, y1), xytext=(x2, y2),
                    arrowprops=dict(arrowstyle='->', color=col, lw=lw,
                                    connectionstyle='arc3,rad=0.0'), zorder=6)
    if label:
        mx = (x1+x2)/2 + loff[0]
        my = (y1+y2)/2 + loff[1]
        ax.text(mx, my, label, fontsize=7.5, ha='center', va='center',
                color=col, fontstyle='italic', zorder=7,
                bbox=dict(boxstyle='round,pad=0.15', fc='white', ec=col,
                          lw=0.6, alpha=0.85))

# ═════════════════════════════════════════════════════════════════════════════
# OUTER CONTAINER
# ═════════════════════════════════════════════════════════════════════════════
rbox(ax, 0.3, 0.4, 21.4, 14.3, '#E8EEFF', '#4A6FD9', lw=3, radius=0.6, zo=1)

# Title bar
rbox(ax, 0.3, 13.85, 21.4, 0.85, '#4A6FD9', '#2c4fad', lw=0, radius=0.5, zo=3)
txt(ax, 11.0, 14.28,
    'CHATBOT REGENERA  —  Arquitectura de Sistema',
    fs=16, fw='bold', col='white')

# Deployment badge
rbox(ax, 0.6, 13.1, 5.5, 0.55, '#2c4fad', '#1a3090', lw=0, radius=0.2, zo=4)
txt(ax, 3.35, 13.375,
    'Despliegue: Windows 11  |  Uvicorn ASGI  |  localhost:8000',
    fs=8.5, fw='bold', col='white')

# ═════════════════════════════════════════════════════════════════════════════
# FRONTEND  (left column)
# ═════════════════════════════════════════════════════════════════════════════
rbox(ax, 0.6, 9.3, 4.2, 3.5, '#DBEAFE', '#1D4ED8', lw=2.2, radius=0.4, zo=2)
txt(ax, 2.7, 12.55, 'FRONTEND', fs=12, fw='bold', col='#1E3A8A')
txt(ax, 2.7, 12.18, 'Navegador (Browser)', fs=9.5, col='#1D4ED8')

for i, (label, sub) in enumerate([
    ('index.html + styles.css', 'Interfaz SPA: Login / Chat / Admin'),
    ('app.js', 'Logica cliente: fetch() / JSON'),
    ('localStorage', 'Sesion: user_id, email, rol'),
]):
    rbox(ax, 0.85, 11.55 - i*0.82, 3.7, 0.65, '#BFDBFE', '#1D4ED8', lw=1, radius=0.2, zo=3)
    txt(ax, 2.7, 11.88 - i*0.82 - 0.14, label, fs=8.5, fw='bold', col='#1E3A8A')
    txt(ax, 2.7, 11.88 - i*0.82 - 0.38, sub, fs=7.5, col='#1D4ED8')

# ═════════════════════════════════════════════════════════════════════════════
# BACKEND  (center)
# ═════════════════════════════════════════════════════════════════════════════
rbox(ax, 5.8, 4.8, 10.6, 8.0, '#DCFCE7', '#16A34A', lw=2.5, radius=0.5, zo=2)
txt(ax, 11.1, 12.55, 'BACKEND  —  FastAPI  (Python)', fs=12, fw='bold', col='#14532D')
txt(ax, 11.1, 12.18, 'main.py  |  Uvicorn ASGI server  |  CORS middleware', fs=9, col='#166534')

# --- Middleware strip
rbox(ax, 6.1, 11.35, 10.0, 0.65, '#BBF7D0', '#15803D', lw=1, ls='--', radius=0.2, zo=3)
txt(ax, 11.1, 11.675,
    'Middleware: CORS (all origins)  |  Request-ID (UUID / x-request-id header)  |  Logging contextual',
    fs=8.5, col='#14532D')

# --- Routes
for i, (name, detail) in enumerate([
    ('routes/auth.py', 'POST /registro   POST /login'),
    ('routes/chat.py', 'POST /messages   GET /conversations/{id}'),
    ('Admin endpoints', 'GET /admin/metrics   POST /knowledge/{id}/validate'),
]):
    rbox(ax, 6.1 + i*3.35, 10.22, 3.15, 0.90, '#A7F3D0', '#059669', lw=1.3, radius=0.25, zo=3)
    txt(ax, 6.1 + i*3.35 + 1.575, 10.82, name, fs=8.5, fw='bold', col='#064E3B')
    txt(ax, 6.1 + i*3.35 + 1.575, 10.52, detail, fs=7.8, col='#065F46')

# --- Query Router
rbox(ax, 6.1, 9.2, 10.0, 0.80, '#FEF9C3', '#CA8A04', lw=1.8, radius=0.25, zo=3)
txt(ax, 11.1, 9.6,
    'query_router.py  ->  knowledge  |  smalltalk  |  invalid  |  out_of_scope',
    fs=9.5, fw='bold', col='#713F12')

# --- Services row
svc_data = [
    ('ai_service.py', 'Llama Gemini API', 'Valida confianza 0-0.95', '#FEF3C7', '#D97706', '#78350F'),
    ('rag_service.py', 'Busqueda semantica', 'Re-rank chunks PDF', '#FEF3C7', '#D97706', '#78350F'),
    ('memory_service.py', 'Cache de Q&A validadas', 'distancia vectorial <= 0.6', '#FEF3C7', '#D97706', '#78350F'),
]
for i, (name, l1, l2, fc, ec, tc) in enumerate(svc_data):
    rbox(ax, 6.1 + i*3.35, 7.8, 3.15, 1.18, fc, ec, lw=1.5, radius=0.25, zo=3)
    txt(ax, 6.1 + i*3.35 + 1.575, 8.64, name, fs=9, fw='bold', col=tc)
    txt(ax, 6.1 + i*3.35 + 1.575, 8.34, l1, fs=8, col='#92400E')
    txt(ax, 6.1 + i*3.35 + 1.575, 8.07, l2, fs=8, col='#92400E')

# --- Database connector
rbox(ax, 6.1, 5.0, 10.0, 2.60, '#FCE7F3', '#DB2777', lw=1.8, radius=0.3, zo=3)
txt(ax, 11.1, 7.35, 'database.py  (pyodbc  |  ODBC Driver 17 for SQL Server)',
    fs=9.5, fw='bold', col='#831843')
txt(ax, 11.1, 7.02,
    'Server: localhost\\SQLEXPRESS   |   Database: ChatBot   |   Windows Auth (Trusted_Connection)',
    fs=8.5, col='#9D174D')

tables = [
    ('Usuarios', 'Id | Nombre | Email | Password | Rol'),
    ('Conversaciones', 'Id | UsuarioId | Titulo | Estado | Fecha'),
    ('Mensajes', 'Id | ConvId | Pregunta | Respuesta | TiempoMs'),
    ('InteraccionesRAG', 'Estado | Tokens | Ruta | Confianza | FechaCreacion'),
    ('ConocimientoValidado', 'InteraccionId | Pregunta | Respuesta | Fuentes'),
]
for i, (tbl, cols) in enumerate(tables):
    rbox(ax, 6.1 + i*2.0, 5.18, 1.9, 1.65, '#FBCFE8', '#EC4899', lw=0.8, radius=0.18, zo=4)
    txt(ax, 6.1 + i*2.0 + 0.95, 6.58, tbl, fs=7.8, fw='bold', col='#831843')
    for j, col in enumerate(cols.split(' | ')):
        txt(ax, 6.1 + i*2.0 + 0.95, 6.3 - j*0.22, col, fs=6.5, col='#9D174D')

# ═════════════════════════════════════════════════════════════════════════════
# SQL SERVER  (bottom-left)
# ═════════════════════════════════════════════════════════════════════════════
rbox(ax, 0.6, 0.7, 4.7, 3.9, '#FFF7ED', '#EA580C', lw=2.2, radius=0.4, zo=2)
txt(ax, 3.0, 4.35, 'SQL Server Express', fs=11, fw='bold', col='#7C2D12')
txt(ax, 3.0, 4.0, 'Base de Datos Relacional', fs=9, col='#9A3412')

for i, (tbl, detail) in enumerate([
    ('Usuarios', 'Autenticacion y roles'),
    ('Conversaciones', 'Historial por usuario'),
    ('Mensajes', 'Preguntas + Respuestas'),
    ('InteraccionesRAG', 'Auditoria | pendiente/validada'),
    ('ConocimientoValidado', 'Memoria aprobada por admin'),
]):
    rbox(ax, 0.85, 3.6 - i*0.56, 4.2, 0.46, '#FDBA74', '#EA580C', lw=0.9, radius=0.15, zo=3)
    txt(ax, 2.95, 3.83 - i*0.56, tbl, fs=8, fw='bold', col='#7C2D12')
    txt(ax, 2.95, 3.62 - i*0.56, detail, fs=7, col='#9A3412')

# ═════════════════════════════════════════════════════════════════════════════
# CHROMADB  (bottom-center)
# ═════════════════════════════════════════════════════════════════════════════
rbox(ax, 6.3, 0.7, 5.0, 3.9, '#F5F3FF', '#7C3AED', lw=2.2, radius=0.4, zo=2)
txt(ax, 8.8, 4.35, 'ChromaDB', fs=11, fw='bold', col='#4C1D95')
txt(ax, 8.8, 4.0, 'Base de Datos Vectorial (local)', fs=9, col='#5B21B6')

chroma_items = [
    ('Collection: reglamentos', 'Chunks de PDFs   |   embeddings semanticos'),
    ('Collection: memoria_validada', 'Q&A validadas admin   |   distancia <= 0.6'),
    ('Re-ranking', 'sentence-transformers  |  MiniLM-L12-v2'),
    ('Documentos fuente', 'PDFs cargados con load_docs.py'),
]
for i, (label, detail) in enumerate(chroma_items):
    rbox(ax, 6.55, 3.55 - i*0.68, 4.5, 0.55, '#DDD6FE', '#7C3AED', lw=1, radius=0.18, zo=3)
    txt(ax, 8.8, 3.83 - i*0.68 - 0.06, label, fs=8, fw='bold', col='#4C1D95')
    txt(ax, 8.8, 3.83 - i*0.68 - 0.28, detail, fs=7.5, col='#5B21B6')

# ═════════════════════════════════════════════════════════════════════════════
# GOOGLE GEMINI  (right column top)
# ═════════════════════════════════════════════════════════════════════════════
rbox(ax, 17.0, 8.8, 4.4, 4.5, '#ECFDF5', '#059669', lw=2.2, radius=0.4, zo=2)
txt(ax, 19.2, 13.05, 'Google Gemini API', fs=11, fw='bold', col='#064E3B')
txt(ax, 19.2, 12.68, 'Servicio externo (cloud)', fs=9, col='#065F46')

gemini_items = [
    ('Modelo', 'gemini-2.5-flash-lite'),
    ('Autenticacion', 'GEMINI_API_KEY  (.env)'),
    ('Llamada', 'generate_content()  |  3 reintentos'),
    ('Respuesta', 'Texto completo  (sin streaming)'),
    ('Metricas', 'prompt_tokens + completion_tokens'),
]
for i, (k, v) in enumerate(gemini_items):
    rbox(ax, 17.2, 12.15 - i*0.68, 4.0, 0.55, '#A7F3D0', '#059669', lw=1, radius=0.18, zo=3)
    txt(ax, 19.2, 12.43 - i*0.68 - 0.06, k, fs=8, fw='bold', col='#064E3B')
    txt(ax, 19.2, 12.43 - i*0.68 - 0.28, v, fs=7.5, col='#065F46')

# ═════════════════════════════════════════════════════════════════════════════
# AUTH / ROLES  (right column bottom)
# ═════════════════════════════════════════════════════════════════════════════
rbox(ax, 17.0, 0.7, 4.4, 7.8, '#EFF6FF', '#2563EB', lw=2.2, radius=0.4, zo=2)
txt(ax, 19.2, 8.22, 'Autenticacion & Roles', fs=11, fw='bold', col='#1E3A8A')

auth_items = [
    ('Login / Registro', 'POST /login  |  POST /registro'),
    ('Sesion cliente', 'localStorage: user_id + rol'),
    ('Rol admin', 'Metricas + validacion RAG'),
    ('Rol usuario', 'Conversaciones propias'),
    ('Sin JWT (pendiente)', 'Mejora de seguridad recomendada'),
    ('Panel Admin', 'Revisar interacciones pendientes'),
    ('observability.py', 'Request-ID  |  router/rag/llm/db ms'),
]
for i, (k, v) in enumerate(auth_items):
    fc = '#BFDBFE' if 'pendiente' not in k else '#FEE2E2'
    ec = '#2563EB' if 'pendiente' not in k else '#DC2626'
    tc = '#1E3A8A' if 'pendiente' not in k else '#7F1D1D'
    rbox(ax, 17.2, 7.5 - i*0.96, 4.0, 0.80, fc, ec, lw=1, radius=0.2, zo=3)
    txt(ax, 19.2, 7.5 - i*0.96 + 0.52, k, fs=8, fw='bold', col=tc)
    txt(ax, 19.2, 7.5 - i*0.96 + 0.26, v, fs=7.5, col=ec)

# ═════════════════════════════════════════════════════════════════════════════
# ARROWS
# ═════════════════════════════════════════════════════════════════════════════
# Frontend <-> Backend
arrow(ax, 4.8, 11.1, 5.8, 11.1, col='#1D4ED8', lw=2.2, bidir=True,
      label='HTTP / JSON\nfetch()', loff=(0, 0.35))

# Backend -> SQL Server
arrow(ax, 7.5, 4.8, 4.0, 4.6, col='#DB2777', lw=1.8,
      label='pyodbc\nSQL queries', loff=(0, 0.35))

# SQL Server -> Backend (validaciones)
arrow(ax, 5.3, 3.2, 6.1, 5.0, col='#EA580C', lw=1.5,
      label='validaciones\naprobadas', loff=(-0.8, 0))

# Backend -> ChromaDB
arrow(ax, 10.0, 4.8, 9.8, 4.6, col='#7C3AED', lw=1.8,
      label='vector search\n/ store', loff=(0, 0.35))

# ChromaDB -> Backend
arrow(ax, 10.5, 4.6, 10.7, 4.8, col='#7C3AED', lw=1.4)

# Backend <-> Gemini
arrow(ax, 16.4, 10.5, 17.0, 10.5, col='#059669', lw=2.2, bidir=True,
      label='HTTPS\nGemini API', loff=(0, 0.35))

# Backend -> Auth section
arrow(ax, 16.4, 9.0, 17.0, 9.0, col='#2563EB', lw=1.5,
      label='auth routes\n+ roles', loff=(0, 0.35))

# ═════════════════════════════════════════════════════════════════════════════
# FLOW LEGEND
# ═════════════════════════════════════════════════════════════════════════════
rbox(ax, 0.6, 0.43, 16.0, 0.55, '#E0E7FF', '#6366F1', lw=1.2, radius=0.2, zo=3)
txt(ax, 8.6, 0.705,
    'Flujo pregunta:  Frontend -> POST /messages -> Query Router -> '
    '[Memory Hit directo]  o  [RAG ChromaDB + Gemini -> guardar pendiente -> Admin valida -> Memoria]',
    fs=7.8, col='#312E81')

plt.tight_layout(pad=0.1)
plt.savefig('arquitectura_chatbot.png', dpi=160, bbox_inches='tight',
            facecolor='#EEF2FF')
print("OK: arquitectura_chatbot.png")
