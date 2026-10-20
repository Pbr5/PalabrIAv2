# backend/main.py
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import hashlib
import time

import backend.model as model
from backend.metrics import _normalize_for_diff, word_levenshtein_count
from backend.utils import (extract_text_from_pdf, split_into_sentences, posible_tu_impersonal, marcadores_discursivos_pareados, posible_dequeismo, corregir_dequeismo, corregir_marcadores, limpiar_se_impersonal, corregir_se_segunda_persona, candidato_gerundio_posterioridad)
from backend.db import (
    init_db, user_exists, create_user, get_user_id,
    record_usage, create_document, insert_metric,
    get_user_overview, get_user_documents, get_document_metrics,
    sanitize_username, delete_document, record_login_ts,
    close_open_session, close_idle_sessions, get_user_weekly_activity,
    get_error_categorization, get_error_timeline, get_cohort_overview, get_cohort_levels
)

app = FastAPI(title="PALABRIA Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

init_db()


# ── Estado ─────────────────────────────────────────────────────────────────────

@app.get("/status/")
def check_status():
    return {
        "modelo_listo": model.MODEL_LOADED,
        "progress":     model.LOAD_PROGRESS,
        "message":      model.LOAD_MESSAGE,
    }

@app.post("/load/")
def trigger_load():
    model.ensure_model_loaded(async_load=True)
    return {"ok": True}


# ── Usuarios ───────────────────────────────────────────────────────────────────

@app.post("/users/create")
def user_create(username: str = Form(...)):
    try:
        username = sanitize_username(username)
        if user_exists(username):
            raise HTTPException(status_code=409, detail="El usuario ya existe. Elige otro nombre.")
        uid = create_user(username)
        record_usage(uid, "login", None)
        close_idle_sessions(idle_secs=3600)
        record_login_ts(uid, time.time())
        return {"ok": True, "user_id": uid, "username": username}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/users/login")
def user_login(username: str = Form(...)):
    try:
        username = sanitize_username(username)
        uid = get_user_id(username)
        if uid is None:
            raise HTTPException(status_code=404, detail="La cuenta no existe. Crea una nueva.")
        record_usage(uid, "login", None)
        close_idle_sessions(idle_secs=3600)
        record_login_ts(uid, time.time())
        return {"ok": True, "user_id": uid, "username": username}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/users/logout")
def user_logout(username: str = Form(...)):
    try:
        username = sanitize_username(username)
        uid = get_user_id(username)
        if uid is None:
            raise HTTPException(status_code=404, detail="Usuario no válido.")
        close_open_session(uid, time.time())
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/users/heartbeat")
def user_heartbeat(username: str = Form(...)):
    try:
        username = sanitize_username(username)
        uid = get_user_id(username)
        if uid is None:
            raise HTTPException(status_code=404, detail="Usuario no válido.")
        record_usage(uid, "heartbeat", time.time())
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Procesamiento de documentos ────────────────────────────────────────────────

@app.post("/process/")
async def process_pdf(
    file: UploadFile = File(...),
    username: str = Form(...),
):
    username = sanitize_username(username)
    uid = get_user_id(username)
    if uid is None:
        raise HTTPException(status_code=403, detail="Usuario no válido. Inicia sesión con una cuenta existente.")

    content       = await file.read()
    original_text = extract_text_from_pdf(content)

    # Corrección y detección spaCy en paralelo (spaCy es CPU, modelo es GPU)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_correct = ex.submit(model.correct_full_text, original_text)
        fut_errores = ex.submit(posible_tu_impersonal, original_text)
        corrected_text = fut_correct.result()
        corrected_text = limpiar_se_impersonal(corrected_text)
        corrected_text = corregir_se_segunda_persona(corrected_text)
        errores_result = fut_errores.result()

    errores_posibles = errores_result[0] if isinstance(errores_result[0], list) else []

    sentences      = split_into_sentences(original_text)
    total_frases   = len(sentences)
    total_errores  = len(errores_posibles)
    cambios_modelo = word_levenshtein_count(original_text, corrected_text)

    # Feedback y escritura en DB en paralelo
    from concurrent.futures import ThreadPoolExecutor as TPE
    text_hash = hashlib.sha256((original_text or "").encode("utf-8")).hexdigest()
    doc_id    = create_document(uid, file.filename, text_hash)

    # Marcadores discursivos pareados
    marc_incidencias, marc_mensaje, marc_resumen = marcadores_discursivos_pareados(original_text)
    corrected_tu = corrected_text  # corrección solo del 'tú' (para el feedback)
    if marc_incidencias:
        corrected_text = corregir_marcadores(corrected_text)
        cambios_modelo = word_levenshtein_count(original_text, corrected_text)

    deq_incidencias, deq_mensaje, deq_resumen = posible_dequeismo(original_text)
    if deq_incidencias:
        corrected_text = corregir_dequeismo(corrected_text)
        cambios_modelo = word_levenshtein_count(original_text, corrected_text)

    # Gerundio de posterioridad
    ger_candidatos, ger_mensaje, ger_resumen = candidato_gerundio_posterioridad(original_text)
    if ger_candidatos:
        corrected_text = model.corregir_gerundio_posterioridad(corrected_text, ger_candidatos)
        cambios_modelo = word_levenshtein_count(original_text, corrected_text)

    insert_metric(doc_id, "total_frases",              float(total_frases))
    insert_metric(doc_id, "frases_con_tu_impersonal",  float(total_errores))
    insert_metric(doc_id, "cambios_propuestos_modelo", float(cambios_modelo))
    insert_metric(doc_id, "cambios_realizados_usuario", float(cambios_modelo))
    for _m_name, _m_val in marc_resumen.items():
        insert_metric(doc_id, _m_name, float(_m_val))
    insert_metric(doc_id, "dequeismo_incidencias", float(deq_resumen["dequeismo_incidencias"]))
    insert_metric(doc_id, "gerundio_incidencias", float(ger_resumen["gerundio_incidencias"]))
    record_usage(uid, "pdf_uploaded", None)

    # Adaptación: ventana reciente (modo), progreso, foco, nivel y errores superados
    adapt = {"modo": {}, "progreso": {}, "foco": None, "nivel": None, "superado": []}
    try:
        _serie = get_error_timeline(username).get("serie", [])
        _ndocs = len(_serie)
        _recientes = _serie[-5:]
        _actual = {"tu": total_errores, "marcadores": len(marc_incidencias),
                   "dequeismo": len(deq_incidencias), "gerundio": len(ger_candidatos)}
        _nombres = {"tu": "'Tú' impersonal", "marcadores": "Marcadores discursivos",
                    "dequeismo": "Dequeísmo (preposiciones)", "gerundio": "Gerundio de posterioridad"}
        _peor = 0.0
        for _pid in ("tu", "marcadores", "dequeismo", "gerundio"):
            _rec = (sum(1 for s in _recientes if s.get(_pid, 0) > 0) / len(_recientes)) if _recientes else 0.0
            adapt["modo"][_pid] = "detallado" if (_ndocs >= 2 and _rec >= 0.5) else "breve"
            if _ndocs >= 2:
                _prev = [s.get(_pid, 0) for s in _serie[:-1]]
                _media_prev = sum(_prev) / len(_prev) if _prev else 0.0
                _a = _actual.get(_pid, 0)
                if _a < _media_prev - 0.5:
                    adapt["progreso"][_pid] = "mejora"
                elif _a > _media_prev + 0.5:
                    adapt["progreso"][_pid] = "empeora"
            if _rec > _peor:
                _peor, adapt["foco"] = _rec, _nombres[_pid]
            if _ndocs >= 3 and all(s.get(_pid, 0) == 0 for s in _serie[-3:]):
                if sum(1 for s in _serie[:-3] if s.get(_pid, 0) > 0) >= 2:
                    adapt["superado"].append(_nombres[_pid])
        if _ndocs > 0:
            _avg = sum(s.get("total", 0) for s in _serie) / _ndocs
            adapt["nivel"] = "avanzado" if _avg < 1 else ("intermedio" if _avg < 3 else "principiante")
    except Exception:
        adapt = {"modo": {}, "progreso": {}, "foco": None, "nivel": None, "superado": []}

    # Lanzar feedback en background — la respuesta se devuelve sin esperar
    model.schedule_feedback(doc_id, original_text, corrected_tu, n_errores=total_errores, marcadores=marc_incidencias, dequeismo=deq_incidencias, gerundio=ger_candidatos, adapt=adapt)

    return {
        "doc_id":        doc_id,
        "original_text": original_text,
        "corrected":     corrected_text,
        "feedback":      "",  # generándose en background — consultar /feedback_status/{doc_id}
        "errores_posibles": errores_posibles,
        "mensaje_errores": (
            "No se detectaron errores de 'tú' impersonal."
            if not errores_posibles
            else f"Se detectaron {len(errores_posibles)} posibles usos del 'tú' impersonal."
        ),
        "marcadores_incidencias": marc_incidencias,
        "mensaje_marcadores": marc_mensaje,
        "dequeismo_incidencias": deq_incidencias,
        "mensaje_dequeismo": deq_mensaje,
        "gerundio_incidencias": ger_candidatos,
        "mensaje_gerundio": ger_mensaje,
        "metricas": {
            "total_frases":               total_frases,
            "frases_con_tu_impersonal":   total_errores,
            "cambios_propuestos_modelo":  cambios_modelo,
            "cambios_realizados_usuario": cambios_modelo,
            "marcadores_aperturas":          marc_resumen["marcadores_aperturas"],
            "marcadores_cierres":            marc_resumen["marcadores_cierres"],
            "marcadores_incidencias":        marc_resumen["marcadores_incidencias"],
            "marcadores_pares_equilibrados": marc_resumen["marcadores_pares_equilibrados"],
            "dequeismo_incidencias":         deq_resumen["dequeismo_incidencias"],
            "gerundio_incidencias":          ger_resumen["gerundio_incidencias"],
        },
    }

@app.post("/process_text/")
async def process_text(
    username: str = Form(...),
    text:     str = Form(...),
    filename: str = Form(None),
):
    username = sanitize_username(username)
    uid = get_user_id(username)
    if uid is None:
        raise HTTPException(status_code=403, detail="Usuario no válido. Inicia sesión con una cuenta existente.")

    original_text = text or ""

    # Corrección y detección spaCy en paralelo
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_correct = ex.submit(model.correct_full_text, original_text)
        fut_errores = ex.submit(posible_tu_impersonal, original_text)
        corrected_text = fut_correct.result()
        corrected_text = limpiar_se_impersonal(corrected_text)
        corrected_text = corregir_se_segunda_persona(corrected_text)
        errores_result = fut_errores.result()

    errores_posibles = errores_result[0] if isinstance(errores_result[0], list) else []

    sentences      = split_into_sentences(original_text)
    total_frases   = len(sentences)
    total_errores  = len(errores_posibles)
    cambios_modelo = word_levenshtein_count(original_text, corrected_text)

    text_hash = hashlib.sha256((original_text or "").encode("utf-8")).hexdigest()
    doc_id    = create_document(uid, filename or "entrada_texto.txt", text_hash)

    # Marcadores discursivos pareados
    marc_incidencias, marc_mensaje, marc_resumen = marcadores_discursivos_pareados(original_text)
    corrected_tu = corrected_text  # corrección solo del 'tú' (para el feedback)
    if marc_incidencias:
        corrected_text = corregir_marcadores(corrected_text)
        cambios_modelo = word_levenshtein_count(original_text, corrected_text)

    deq_incidencias, deq_mensaje, deq_resumen = posible_dequeismo(original_text)
    if deq_incidencias:
        corrected_text = corregir_dequeismo(corrected_text)
        cambios_modelo = word_levenshtein_count(original_text, corrected_text)

    # Gerundio de posterioridad
    ger_candidatos, ger_mensaje, ger_resumen = candidato_gerundio_posterioridad(original_text)
    if ger_candidatos:
        corrected_text = model.corregir_gerundio_posterioridad(corrected_text, ger_candidatos)
        cambios_modelo = word_levenshtein_count(original_text, corrected_text)

    insert_metric(doc_id, "total_frases",              float(total_frases))
    insert_metric(doc_id, "frases_con_tu_impersonal",  float(total_errores))
    insert_metric(doc_id, "cambios_propuestos_modelo", float(cambios_modelo))
    insert_metric(doc_id, "cambios_realizados_usuario", float(cambios_modelo))
    for _m_name, _m_val in marc_resumen.items():
        insert_metric(doc_id, _m_name, float(_m_val))
    insert_metric(doc_id, "dequeismo_incidencias", float(deq_resumen["dequeismo_incidencias"]))
    insert_metric(doc_id, "gerundio_incidencias", float(ger_resumen["gerundio_incidencias"]))
    record_usage(uid, "text_uploaded", None)

    # Adaptación: ventana reciente (modo), progreso, foco, nivel y errores superados
    adapt = {"modo": {}, "progreso": {}, "foco": None, "nivel": None, "superado": []}
    try:
        _serie = get_error_timeline(username).get("serie", [])
        _ndocs = len(_serie)
        _recientes = _serie[-5:]
        _actual = {"tu": total_errores, "marcadores": len(marc_incidencias),
                   "dequeismo": len(deq_incidencias), "gerundio": len(ger_candidatos)}
        _nombres = {"tu": "'Tú' impersonal", "marcadores": "Marcadores discursivos",
                    "dequeismo": "Dequeísmo (preposiciones)", "gerundio": "Gerundio de posterioridad"}
        _peor = 0.0
        for _pid in ("tu", "marcadores", "dequeismo", "gerundio"):
            _rec = (sum(1 for s in _recientes if s.get(_pid, 0) > 0) / len(_recientes)) if _recientes else 0.0
            adapt["modo"][_pid] = "detallado" if (_ndocs >= 2 and _rec >= 0.5) else "breve"
            if _ndocs >= 2:
                _prev = [s.get(_pid, 0) for s in _serie[:-1]]
                _media_prev = sum(_prev) / len(_prev) if _prev else 0.0
                _a = _actual.get(_pid, 0)
                if _a < _media_prev - 0.5:
                    adapt["progreso"][_pid] = "mejora"
                elif _a > _media_prev + 0.5:
                    adapt["progreso"][_pid] = "empeora"
            if _rec > _peor:
                _peor, adapt["foco"] = _rec, _nombres[_pid]
            if _ndocs >= 3 and all(s.get(_pid, 0) == 0 for s in _serie[-3:]):
                if sum(1 for s in _serie[:-3] if s.get(_pid, 0) > 0) >= 2:
                    adapt["superado"].append(_nombres[_pid])
        if _ndocs > 0:
            _avg = sum(s.get("total", 0) for s in _serie) / _ndocs
            adapt["nivel"] = "avanzado" if _avg < 1 else ("intermedio" if _avg < 3 else "principiante")
    except Exception:
        adapt = {"modo": {}, "progreso": {}, "foco": None, "nivel": None, "superado": []}

    # Lanzar feedback en background — la respuesta se devuelve sin esperar
    model.schedule_feedback(doc_id, original_text, corrected_tu, n_errores=total_errores, marcadores=marc_incidencias, dequeismo=deq_incidencias, gerundio=ger_candidatos, adapt=adapt)

    return {
        "doc_id":        doc_id,
        "original_text": original_text,
        "corrected":     corrected_text,
        "feedback":      "",  # generándose en background — consultar /feedback_status/{doc_id}
        "errores_posibles": errores_posibles,
        "mensaje_errores": (
            "No se detectaron errores de 'tú' impersonal."
            if not errores_posibles
            else f"Se detectaron {len(errores_posibles)} posibles usos del 'tú' impersonal."
        ),
        "marcadores_incidencias": marc_incidencias,
        "mensaje_marcadores": marc_mensaje,
        "dequeismo_incidencias": deq_incidencias,
        "mensaje_dequeismo": deq_mensaje,
        "gerundio_incidencias": ger_candidatos,
        "mensaje_gerundio": ger_mensaje,
        "metricas": {
            "total_frases":               total_frases,
            "frases_con_tu_impersonal":   total_errores,
            "cambios_propuestos_modelo":  cambios_modelo,
            "cambios_realizados_usuario": cambios_modelo,
            "marcadores_aperturas":          marc_resumen["marcadores_aperturas"],
            "marcadores_cierres":            marc_resumen["marcadores_cierres"],
            "marcadores_incidencias":        marc_resumen["marcadores_incidencias"],
            "marcadores_pares_equilibrados": marc_resumen["marcadores_pares_equilibrados"],
            "dequeismo_incidencias":         deq_resumen["dequeismo_incidencias"],
            "gerundio_incidencias":          ger_resumen["gerundio_incidencias"],
        },
    }


# ── Métricas de documentos ─────────────────────────────────────────────────────

@app.post("/documents/{doc_id}/metrics")
def add_document_metric(
    doc_id: int,
    name:   str   = Form(...),
    value:  float = Form(...),
):
    try:
        insert_metric(doc_id, name, float(value))
        return {"ok": True, "document_id": doc_id, "metric_name": name, "metric_value": float(value)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/documents/{doc_id}/user_changes")
def update_user_changes(
    doc_id:  int,
    changes: int = Form(...),
):
    try:
        insert_metric(doc_id, "cambios_realizados_usuario", float(changes))
        return {"ok": True, "document_id": doc_id, "metric_name": "cambios_realizados_usuario", "metric_value": float(changes)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Overview y actividad ───────────────────────────────────────────────────────

@app.get("/users/{username}/overview")
def user_overview(username: str):
    return get_user_overview(username)

@app.get("/users/{username}/documents")
def user_documents(username: str):
    return {"documents": get_user_documents(username)}

@app.get("/documents/{doc_id}/metrics")
def document_metrics(doc_id: int):
    return {"doc_id": doc_id, "metrics": get_document_metrics(doc_id)}

@app.get("/users/{username}/weekly_activity")
def user_weekly_activity(username: str):
    return {"username": username, "activity": get_user_weekly_activity(username)}

@app.get("/users/{username}/error_categorization")
def user_error_categorization(username: str):
    return get_error_categorization(username)

@app.get("/users/{username}/error_timeline")
def user_error_timeline(username: str):
    return get_error_timeline(username)

@app.get("/cohort/overview")
def cohort_overview():
    return get_cohort_overview()

@app.get("/cohort/levels")
def cohort_levels():
    return {"niveles": get_cohort_levels()}

@app.delete("/documents/{doc_id}")
def delete_doc(doc_id: int):
    ok = delete_document(doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    return {"ok": True, "deleted_id": doc_id}


@app.get("/feedback_status/{doc_id}")
def feedback_status(doc_id: int):
    """
    Polling endpoint — el frontend consulta esto cada N segundos
    hasta que status == 'done' o 'error'.
    """
    return model.get_feedback_status(doc_id)


# ── Valoración global ──────────────────────────────────────────────────────────

@app.get("/users/{username}/global_feedback")
def global_feedback(username: str):
    """
    Genera una valoración global del progreso del estudiante bajo demanda.
    Solo se llama cuando el usuario pulsa el botón en el frontend.
    Usa las métricas ya calculadas (sin procesar documentos nuevos).
    """
    try:
        username = sanitize_username(username)
        uid = get_user_id(username)
        if uid is None:
            raise HTTPException(status_code=404, detail="Usuario no válido.")

        if not model.MODEL_LOADED:
            raise HTTPException(status_code=503, detail="El modelo aún no está cargado.")

        ov = get_user_overview(username)

        total_docs          = int(ov.get("docs", 0))
        login_days          = int(ov.get("login_days", 0))
        pct_tu              = float(ov.get("docs_with_tu_percent", 0.0))
        pct_sin_cambios     = float(ov.get("docs_no_changes_percent", 0.0))
        avg_session_seconds = float(ov.get("avg_session_seconds", 0.0))

        if total_docs == 0:
            return {
                "feedback": "Aún no has procesado ningún documento. ¡Sube tu primer texto para recibir una valoración!",
                "stats": {
                    "total_docs": 0,
                    "login_days": login_days,
                    "pct_con_error": 0.0,
                    "evolucion": "insuficiente",
                },
            }

        feedback_text = model.generate_global_feedback(
            total_docs=total_docs,
            login_days=login_days,
            pct_tu=pct_tu,
            pct_sin_cambios=pct_sin_cambios,
            avg_session_seconds=avg_session_seconds,
        )

        # Tendencia simple: si % documentos con 'tú' > 50% → empeora/estable,
        # si < 30% → mejora, si hay pocos datos → insuficiente
        if total_docs < 3:
            evolucion = "insuficiente"
        elif pct_tu < 30.0:
            evolucion = "mejora"
        elif pct_tu > 60.0:
            evolucion = "empeora"
        else:
            evolucion = "estable"

        return {
            "feedback": feedback_text,
            "stats": {
                "total_docs":   total_docs,
                "login_days":   login_days,
                "pct_con_error": pct_tu,
                "evolucion":    evolucion,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))