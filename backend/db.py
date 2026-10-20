# backend/db.py
from pathlib import Path
import os
import sqlite3
from contextlib import contextmanager
import re
from typing import Optional

DEFAULT_DB = "data/palabria.db"

def get_db_path():
    env_db = os.getenv("DB_PATH")
    if env_db:
        p = Path(env_db)
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)
    local_db = Path(DEFAULT_DB)
    local_db.parent.mkdir(parents=True, exist_ok=True)
    return str(local_db)

DB_PATH = get_db_path()

# ── Categorización de errores por fenómeno (data-driven) ───────────────────────
# Añadir un fenómeno = añadir una entrada (metric = métrica guardada por documento).
CATEGORIAS_ERROR = [
    {"id": "tu",         "metric": "frases_con_tu_impersonal", "nombre": "'Tú' impersonal"},
    {"id": "marcadores", "metric": "marcadores_incidencias",   "nombre": "Marcadores discursivos"},
    {"id": "dequeismo",  "metric": "dequeismo_incidencias",    "nombre": "Dequeísmo (preposiciones)"},
    {"id": "gerundio",   "metric": "gerundio_incidencias",     "nombre": "Gerundio de posterioridad"},
]

DDL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  filename TEXT,
  uploaded_at TEXT DEFAULT (datetime('now')),
  text_hash TEXT,
  FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS metrics(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id INTEGER NOT NULL,
  metric_name TEXT NOT NULL,
  metric_value REAL,
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY(document_id) REFERENCES documents(id)
);

CREATE TABLE IF NOT EXISTS usage_stats(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  event TEXT NOT NULL,
  value REAL,
  created_at TEXT DEFAULT (datetime('now')),
  FOREIGN KEY(user_id) REFERENCES users(id)
);
"""

def init_db():
    con = sqlite3.connect(DB_PATH)
    try:
        con.executescript(DDL)
        con.commit()
    finally:
        con.close()

@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = ON;")
        yield con
        con.commit()
    finally:
        con.close()

_ALLOWED = re.compile(r"^[A-Za-z0-9_\-\.]{1,32}$")

def sanitize_username(username: str) -> str:
    username = (username or "").strip()
    if not username:
        raise ValueError("username vacío")
    if not _ALLOWED.match(username):
        raise ValueError("username inválido: usa letras, números, _ - . (máx 32)")
    return username

def user_exists(username: str) -> bool:
    username = sanitize_username(username)
    with db() as con:
        row = con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
        return bool(row)

def create_user(username: str) -> int:
    username = sanitize_username(username)
    with db() as con:
        cur = con.execute("INSERT INTO users(username) VALUES(?)", (username,))
        return cur.lastrowid

def get_user_id(username: str) -> Optional[int]:
    username = sanitize_username(username)
    with db() as con:
        row = con.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        return row["id"] if row else None

def ensure_user(username: str) -> int:
    username = sanitize_username(username)
    with db() as con:
        row = con.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row:
            return row["id"]
        cur = con.execute("INSERT INTO users(username) VALUES(?)", (username,))
        return cur.lastrowid

def record_usage(user_id: int, event: str, value: float | None = None):
    with db() as con:
        con.execute(
            "INSERT INTO usage_stats(user_id, event, value) VALUES(?,?,?)",
            (user_id, event, value)
        )

def record_login_ts(user_id: int, epoch_seconds: float):
    record_usage(user_id, "login_ts", float(epoch_seconds))
    record_usage(user_id, "heartbeat", float(epoch_seconds))

def close_open_session(user_id: int, now_epoch: float, idle_grace: float = 1800.0):
    with db() as con:
        row_login = con.execute("""
            SELECT id, value FROM usage_stats
            WHERE user_id=? AND event='login_ts'
            ORDER BY id DESC LIMIT 1
        """, (user_id,)).fetchone()
        if not row_login:
            return
        last_login_id = int(row_login["id"])
        start_ts = float(row_login["value"] or 0.0)

        # Si ya existe session_duration para esta sesión, no crear otra
        row_sd = con.execute("""
            SELECT id FROM usage_stats
            WHERE user_id=? AND event='session_duration' AND id>?
            ORDER BY id DESC LIMIT 1
        """, (user_id, last_login_id)).fetchone()
        if row_sd:
            return

        row_hb = con.execute("""
            SELECT value FROM usage_stats
            WHERE user_id=? AND event='heartbeat' AND id>=?
            ORDER BY id DESC LIMIT 1
        """, (user_id, last_login_id)).fetchone()
        hb_ts = float(row_hb["value"]) if row_hb and row_hb["value"] is not None else None

        end_ts = now_epoch
        if hb_ts is not None:
            if (now_epoch - hb_ts) > idle_grace:
                end_ts = hb_ts  
            else:
                end_ts = now_epoch

        dur = max(0.0, end_ts - start_ts)
        dur = min(dur, 12 * 3600.0)  

        con.execute("INSERT INTO usage_stats(user_id, event, value) VALUES(?,?,?)",
                    (user_id, "session_duration", float(dur)))
        con.execute("INSERT INTO usage_stats(user_id, event, value) VALUES(?,?,NULL)",
                    (user_id, "logout"))

def close_idle_sessions(idle_secs: float = 1800.0):
    """
    Cierra todas las sesiones abiertas cuyo último heartbeat fue hace
    más de idle_secs segundos. Llamar al inicio de cada login.
    """
    import time
    now = time.time()
    with db() as con:
        open_sessions = con.execute("""
            SELECT us.user_id, us.id AS login_id, us.value AS start_ts
            FROM usage_stats us
            WHERE us.event = 'login_ts'
              AND NOT EXISTS (
                SELECT 1 FROM usage_stats sd
                WHERE sd.user_id = us.user_id
                  AND sd.event = 'session_duration'
                  AND sd.id > us.id
              )
        """).fetchall()

        for row in open_sessions:
            uid = row["user_id"]
            login_id = row["login_id"]
            start_ts = float(row["start_ts"] or 0)

            hb = con.execute("""
                SELECT value FROM usage_stats
                WHERE user_id=? AND event='heartbeat' AND id >= ?
                ORDER BY id DESC LIMIT 1
            """, (uid, login_id)).fetchone()

            hb_ts = float(hb["value"]) if hb and hb["value"] else None

            if hb_ts is None:
                continue  

            if (now - hb_ts) > idle_secs:
                dur = max(0.0, min(hb_ts - start_ts, 12 * 3600.0))
                con.execute(
                    "INSERT INTO usage_stats(user_id, event, value) VALUES(?,?,?)",
                    (uid, "session_duration", dur)
                )
                con.execute(
                    "INSERT INTO usage_stats(user_id, event, value) VALUES(?,?,NULL)",
                    (uid, "logout")
                )

def create_document(user_id: int, filename: str | None, text_hash: str | None) -> int:
    with db() as con:
        cur = con.execute(
            "INSERT INTO documents(user_id, filename, text_hash) VALUES(?,?,?)",
            (user_id, filename, text_hash)
        )
        return cur.lastrowid

def insert_metric(document_id: int, name: str, value: float):
    with db() as con:
        con.execute(
            "INSERT INTO metrics(document_id, metric_name, metric_value) VALUES(?,?,?)",
            (document_id, name, float(value))
        )

def get_user_overview(username: str):
    username = sanitize_username(username)
    with db() as con:
        row_docs = con.execute("""
            SELECT COUNT(*) AS docs
            FROM documents d
            JOIN users u ON u.id = d.user_id
            WHERE u.username=?
        """, (username,)).fetchone()
        total_docs = int(row_docs["docs"] if row_docs else 0)

        avg_rows = con.execute("""
            WITH latest AS (
                SELECT m.* FROM metrics m
                JOIN (
                    SELECT document_id, metric_name, MAX(id) AS max_id
                    FROM metrics GROUP BY document_id, metric_name
                ) mx ON mx.max_id = m.id
            )
            SELECT l.metric_name, AVG(l.metric_value) AS avg_value
            FROM latest l
            JOIN documents d ON d.id = l.document_id
            JOIN users u ON u.id = d.user_id
            WHERE u.username=?
            GROUP BY l.metric_name
        """, (username,)).fetchall()

        row_docs_with_tu = con.execute("""
            WITH latest_tu AS (
                SELECT m.document_id, m.metric_value
                FROM metrics m
                JOIN (
                    SELECT document_id, MAX(id) AS max_id
                    FROM metrics
                    WHERE metric_name='frases_con_tu_impersonal'
                    GROUP BY document_id
                ) mx ON mx.max_id = m.id
            )
            SELECT COUNT(DISTINCT d.id) AS docs_with_tu
            FROM documents d
            JOIN users u ON u.id = d.user_id
            LEFT JOIN latest_tu lt ON lt.document_id = d.id
            WHERE u.username=? AND COALESCE(lt.metric_value,0) > 0
        """, (username,)).fetchone()
        docs_with_tu = int(row_docs_with_tu["docs_with_tu"] if row_docs_with_tu else 0)
        docs_with_tu_percent = round((docs_with_tu * 100.0 / total_docs), 1) if total_docs > 0 else 0.0

        # Indicador v2: % de documentos con incidencias de marcadores discursivos pareados
        row_docs_with_marc = con.execute("""
            WITH latest_marc AS (
                SELECT m.document_id, m.metric_value
                FROM metrics m
                JOIN (
                    SELECT document_id, MAX(id) AS max_id
                    FROM metrics
                    WHERE metric_name='marcadores_incidencias'
                    GROUP BY document_id
                ) mx ON mx.max_id = m.id
            )
            SELECT COUNT(DISTINCT d.id) AS docs_with_marc
            FROM documents d
            JOIN users u ON u.id = d.user_id
            LEFT JOIN latest_marc lm ON lm.document_id = d.id
            WHERE u.username=? AND COALESCE(lm.metric_value,0) > 0
        """, (username,)).fetchone()
        docs_with_marc = int(row_docs_with_marc["docs_with_marc"] if row_docs_with_marc else 0)
        docs_with_marcadores_percent = round((docs_with_marc * 100.0 / total_docs), 1) if total_docs > 0 else 0.0

        row_docs_no_changes = con.execute("""
            WITH latest_user_changes AS (
                SELECT m.document_id, m.metric_value
                FROM metrics m
                JOIN (
                    SELECT document_id, MAX(id) AS max_id
                    FROM metrics
                    WHERE metric_name='cambios_realizados_usuario'
                    GROUP BY document_id
                ) mx ON mx.max_id = m.id
            )
            SELECT COUNT(DISTINCT d.id) AS docs_no_changes
            FROM documents d
            JOIN users u ON u.id = d.user_id
            LEFT JOIN latest_user_changes luc ON luc.document_id = d.id
            WHERE u.username=? AND COALESCE(luc.metric_value,0) = 0
        """, (username,)).fetchone()

        docs_no_changes = int(row_docs_no_changes["docs_no_changes"] if row_docs_no_changes else 0)
        docs_no_changes_percent = round((docs_no_changes * 100.0 / total_docs), 1) if total_docs > 0 else 0.0

        usage_rows = con.execute("""
            SELECT event, COUNT(*) AS n, AVG(value) AS avg_val
            FROM usage_stats us
            JOIN users u ON u.id = us.user_id
            WHERE u.username=?
            GROUP BY event
        """, (username,)).fetchall()

        login_days_row = con.execute("""
            SELECT COUNT(DISTINCT date(us.created_at)) AS days
            FROM usage_stats us
            JOIN users u ON u.id = us.user_id
            WHERE u.username=? AND us.event='login'
        """, (username,)).fetchone()

        avg_session_row = con.execute("""
            SELECT AVG(us.value) AS avg_sec
            FROM usage_stats us
            JOIN users u ON u.id = us.user_id
            WHERE u.username=? AND us.event='session_duration'
        """, (username,)).fetchone()

        avg_metrics = {r["metric_name"]: r["avg_value"] for r in avg_rows}
        _avg_prop = avg_metrics.get("cambios_propuestos_modelo", 0) or 0
        _avg_real = avg_metrics.get("cambios_realizados_usuario", 0) or 0
        ratio_cambios_aceptados_percent = (
            min(round((_avg_real / _avg_prop * 100), 1), 100.0) if _avg_prop > 0 else 0.0
        )

        # Indicadores del paper: errores/doc, densidad, time on task, nivel
        _ph_metrics = [c["metric"] for c in CATEGORIAS_ERROR]
        errores_por_documento = round(sum((avg_metrics.get(k, 0) or 0) for k in _ph_metrics), 2)
        _frases_avg = avg_metrics.get("total_frases", 0) or 0
        densidad_errores = round(errores_por_documento / _frases_avg, 2) if _frases_avg > 0 else 0.0
        _sess = next((r for r in usage_rows if r["event"] == "session_duration"), None)
        _tiempo_total = (float(_sess["avg_val"] or 0) * int(_sess["n"])) if _sess else 0.0
        tiempo_medio_doc_seconds = round(_tiempo_total / total_docs, 1) if total_docs > 0 else 0.0
        nivel = "avanzado" if errores_por_documento < 1 else ("intermedio" if errores_por_documento < 3 else "principiante")

        return {
            "docs": total_docs,
            "avg_metrics": avg_metrics,
            "usage": {r["event"]: {"count": r["n"], "avg": r["avg_val"]} for r in usage_rows},
            "login_days": int(login_days_row["days"] if login_days_row else 0),
            "docs_with_tu_percent": docs_with_tu_percent,
            "docs_with_marcadores_percent": docs_with_marcadores_percent,
            "ratio_cambios_aceptados_percent": ratio_cambios_aceptados_percent,
            "docs_no_changes_percent": docs_no_changes_percent,
            "avg_session_seconds": float(avg_session_row["avg_sec"] or 0.0),
            "errores_por_documento": errores_por_documento,
            "densidad_errores": densidad_errores,
            "tiempo_medio_doc_seconds": tiempo_medio_doc_seconds,
            "nivel": nivel,
        }


# ── Evolución de errores en el tiempo (error progress history) ──────────────────
# Serie por documento en orden cronológico; alimenta la gráfica de evolución.
def get_error_timeline(username: str):
    username = sanitize_username(username)
    names = [c["metric"] for c in CATEGORIAS_ERROR]
    q_names = names + ["cambios_propuestos_modelo", "cambios_realizados_usuario"]
    ph = ",".join("?" for _ in q_names)
    with db() as con:
        docs = con.execute("""
            SELECT d.id, d.filename
            FROM documents d JOIN users u ON u.id = d.user_id
            WHERE u.username=? ORDER BY d.id ASC
        """, (username,)).fetchall()
        rows = con.execute(f"""
            WITH latest AS (
                SELECT m.document_id, m.metric_name, m.metric_value
                FROM metrics m
                JOIN (
                    SELECT document_id, metric_name, MAX(id) AS max_id
                    FROM metrics GROUP BY document_id, metric_name
                ) mx ON mx.max_id = m.id
            )
            SELECT l.document_id, l.metric_name, l.metric_value
            FROM latest l
            JOIN documents d ON d.id = l.document_id
            JOIN users u ON u.id = d.user_id
            WHERE u.username=? AND l.metric_name IN ({ph})
        """, (username, *q_names)).fetchall()

    por_doc = {}
    for r in rows:
        por_doc.setdefault(r["document_id"], {})[r["metric_name"]] = r["metric_value"]

    serie = []
    for i, d in enumerate(docs, 1):
        vals = por_doc.get(d["id"], {})
        item = {"indice": i, "doc_id": d["id"], "filename": d["filename"], "total": 0}
        for c in CATEGORIAS_ERROR:
            v = int(vals.get(c["metric"], 0) or 0)
            item[c["id"]] = v
            item["total"] += v
        item["propuestos"] = int(vals.get("cambios_propuestos_modelo", 0) or 0)
        item["realizados"] = int(vals.get("cambios_realizados_usuario", 0) or 0)
        serie.append(item)

    # Tendencia: media de la primera mitad vs la segunda
    tendencia = "insuficiente"
    if len(serie) >= 4:
        tot = [s["total"] for s in serie]
        mid = len(tot) // 2
        a = sum(tot[:mid]) / mid
        b = sum(tot[mid:]) / (len(tot) - mid)
        tendencia = "mejora" if b < a - 0.5 else ("empeora" if b > a + 0.5 else "estable")

    return {"serie": serie, "tendencia": tendencia}


# ── Vista de cohorte (errors by group / comparison with class) ──────────────────
# Agrega incidencias por fenómeno entre TODOS los usuarios.
def get_cohort_overview():
    names = [c["metric"] for c in CATEGORIAS_ERROR]
    ph = ",".join("?" for _ in names)
    with db() as con:
        total_docs = int(con.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"])
        total_users = int(con.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"])
        rows = con.execute(f"""
            WITH latest AS (
                SELECT m.document_id, m.metric_name, m.metric_value
                FROM metrics m
                JOIN (
                    SELECT document_id, metric_name, MAX(id) AS max_id
                    FROM metrics GROUP BY document_id, metric_name
                ) mx ON mx.max_id = m.id
            )
            SELECT l.metric_name,
                   SUM(l.metric_value) AS total,
                   SUM(CASE WHEN l.metric_value > 0 THEN 1 ELSE 0 END) AS docs_con,
                   COUNT(DISTINCT CASE WHEN l.metric_value > 0 THEN d.user_id END) AS usuarios
            FROM latest l
            JOIN documents d ON d.id = l.document_id
            WHERE l.metric_name IN ({ph})
            GROUP BY l.metric_name
        """, (*names,)).fetchall()

    por_metrica = {r["metric_name"]: r for r in rows}
    categorias = []
    for c in CATEGORIAS_ERROR:
        r = por_metrica.get(c["metric"])
        total = float(r["total"]) if r and r["total"] is not None else 0.0
        usuarios = int(r["usuarios"]) if r and r["usuarios"] is not None else 0
        categorias.append({
            "id": c["id"],
            "nombre": c["nombre"],
            "total": int(round(total)),
            "media_por_doc": round(total / total_docs, 2) if total_docs > 0 else 0.0,
            "usuarios_afectados": usuarios,
            "pct_usuarios": round(usuarios * 100.0 / total_users, 1) if total_users > 0 else 0.0,
        })

    return {"total_docs": total_docs, "total_users": total_users, "categorias": categorias}


# ── Distribución de niveles del alumnado ────────────────────────────────────────
def get_cohort_levels():
    names = [c["metric"] for c in CATEGORIAS_ERROR]
    ph = ",".join("?" for _ in names)
    with db() as con:
        rows = con.execute(f"""
            WITH latest AS (
                SELECT m.document_id, m.metric_name, m.metric_value
                FROM metrics m
                JOIN (
                    SELECT document_id, metric_name, MAX(id) AS max_id
                    FROM metrics GROUP BY document_id, metric_name
                ) mx ON mx.max_id = m.id
            ),
            por_doc AS (
                SELECT document_id, SUM(metric_value) AS doc_total
                FROM latest WHERE metric_name IN ({ph}) GROUP BY document_id
            )
            SELECT d.user_id, AVG(COALESCE(pd.doc_total, 0)) AS avg_err
            FROM documents d
            LEFT JOIN por_doc pd ON pd.document_id = d.id
            GROUP BY d.user_id
        """, (*names,)).fetchall()

    dist = {"principiante": 0, "intermedio": 0, "avanzado": 0}
    for r in rows:
        avg = float(r["avg_err"] or 0)
        nivel = "avanzado" if avg < 1 else ("intermedio" if avg < 3 else "principiante")
        dist[nivel] += 1
    return [{"nivel": k, "estudiantes": v} for k, v in dist.items()]


def get_error_categorization(username: str):
    """
    Desglose de incidencias por fenómeno para un usuario, sumando el ÚLTIMO
    valor por documento de cada métrica (mismo criterio latest-per-doc que
    el resto de agregados). Data-driven: recorre CATEGORIAS_ERROR.
    Devuelve {total_incidencias, total_docs, categorias:[{id,nombre,total,docs_con,recurrencia,pct}]}.
    """
    username = sanitize_username(username)
    metric_names = [c['metric'] for c in CATEGORIAS_ERROR]
    placeholders = ','.join('?' for _ in metric_names)
    with db() as con:
        rows = con.execute(f'''
            WITH latest AS (
                SELECT m.document_id, m.metric_name, m.metric_value
                FROM metrics m
                JOIN (
                    SELECT document_id, metric_name, MAX(id) AS max_id
                    FROM metrics GROUP BY document_id, metric_name
                ) mx ON mx.max_id = m.id
            )
            SELECT l.metric_name,
                   SUM(l.metric_value) AS total,
                   SUM(CASE WHEN l.metric_value > 0 THEN 1 ELSE 0 END) AS docs_con
            FROM latest l
            JOIN documents d ON d.id = l.document_id
            JOIN users u ON u.id = d.user_id
            WHERE u.username=? AND l.metric_name IN ({placeholders})
            GROUP BY l.metric_name
        ''', (username, *metric_names)).fetchall()

        total_docs_row = con.execute(
            "SELECT COUNT(*) AS n FROM documents d JOIN users u ON u.id=d.user_id WHERE u.username=?",
            (username,)
        ).fetchone()
    total_docs = int(total_docs_row['n']) if total_docs_row else 0

    por_metrica = {r['metric_name']: r for r in rows}
    categorias = []
    total_global = 0.0
    for c in CATEGORIAS_ERROR:
        r = por_metrica.get(c['metric'])
        total = float(r['total']) if r and r['total'] is not None else 0.0
        docs_con = int(r['docs_con']) if r and r['docs_con'] is not None else 0
        total_global += total
        categorias.append({
            'id': c['id'],
            'nombre': c['nombre'],
            'total': int(round(total)),
            'docs_con': docs_con,
            'recurrencia': round(docs_con / total_docs, 2) if total_docs > 0 else 0.0,
            'pct': 0.0,
        })
    for cat in categorias:
        cat['pct'] = round(cat['total'] * 100.0 / total_global, 1) if total_global > 0 else 0.0

    return {
        'total_incidencias': int(round(total_global)),
        'total_docs': total_docs,
        'categorias': categorias,
    }

def get_user_weekly_activity(username: str):
    """
    Devuelve los últimos 7 días con su tiempo total de conexión (sumado)
    y una categoría de actividad.
    """
    username = sanitize_username(username)
    with db() as con:
        rows = con.execute("""
            SELECT 
                date(us.created_at) AS day,
                SUM(CASE WHEN us.event='session_duration' THEN us.value ELSE 0 END) AS total_seconds
            FROM usage_stats us
            JOIN users u ON u.id = us.user_id
            WHERE u.username=? 
              AND date(us.created_at) >= date('now', '-6 days')
            GROUP BY date(us.created_at)
            ORDER BY day ASC
        """, (username,)).fetchall()

        from datetime import datetime, timedelta
        today = datetime.utcnow().date()
        days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
        data = []
        seconds_by_day = {r["day"]: float(r["total_seconds"] or 0) for r in rows}

        for d in days:
            secs = seconds_by_day.get(d, 0)
            if secs == 0:
                cat = "No inició sesión"
            elif secs <= 300:
                cat = "Hasta 5 min"
            elif secs <= 900:
                cat = "Hasta 15 min"
            elif secs <= 1800:
                cat = "Hasta 30 min"
            else:
                cat = "Más de 30 min"
            data.append({"day": d, "total_seconds": secs, "categoria": cat})
        return data

def get_user_documents(username: str):
    username = sanitize_username(username)
    with db() as con:
        rows = con.execute("""
            SELECT d.id, d.filename, d.uploaded_at
            FROM documents d
            JOIN users u ON u.id = d.user_id
            WHERE u.username=?
            ORDER BY d.id DESC
        """, (username,)).fetchall()
        return [dict(r) for r in rows]

def get_document_metrics(doc_id: int):
    with db() as con:
        rows = con.execute("""
            SELECT metric_name, metric_value, created_at
            FROM metrics
            WHERE document_id=?
            ORDER BY id
        """, (doc_id,)).fetchall()
        return [dict(r) for r in rows]

def delete_document(doc_id: int) -> bool:
    with db() as con:
        con.execute("DELETE FROM metrics WHERE document_id=?", (doc_id,))
        cur = con.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        return cur.rowcount > 0