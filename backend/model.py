# backend/model.py
import gc
import re
import time
import threading
from typing import List, Optional

import torch
from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig


# ── Estado global ──────────────────────────────────────────────────────────────
MODEL_LOADED: bool = False
LOAD_PROGRESS: int = 0
LOAD_MESSAGE: str = "Modelo no cargado."

_lock   = threading.Lock()
_thread = None

_tokenizer: Optional[AutoTokenizer]         = None
_model:     Optional[AutoModelForCausalLM]  = None

# ── Feedback jobs asíncronos ───────────────────────────────────────────────────
# doc_id → {"status": "pending"|"done"|"error", "result": str}
_feedback_jobs: dict = {}
_feedback_lock  = threading.Lock()

# ── Modelo ─────────────────────────────────────────────────────────────────────
MODEL_ID = "meta-llama/Llama-3.2-3B-Instruct"

# ── Parámetros de generación ───────────────────────────────────────────────────
MAX_NEW_TOKENS_CORRECTION = 1024  # texto completo — escala dinámicamente
MAX_NEW_TOKENS_FEEDBACK   = 300   # feedback por documento
MAX_NEW_TOKENS_GLOBAL     = 350   # valoración global
MAX_NEW_TOKENS_GERUNDIO   = 150   # corrección frase a frase (gerundio)
DO_SAMPLE          = False
NUM_BEAMS          = 1

# ── Prompts de corrección — optimizados para Llama 3.2 3B Instruct ─────────────
# Texto completo en una sola llamada (como el original); corrige solo el 'tú'.
SYSTEM_PROMPT_CORRECTION = (
    "Eres un corrector gramatical de español escrito formal. "
    "Corrige SOLO los usos impersonales genéricos de la segunda persona del singular ('tú'): "
    "sustitúyelos por construcciones impersonales con 'se'. "
    "Solo usa 'uno' si 'se' es gramaticalmente inadecuado o queda poco natural. "
    "NO corrijas si 'tú' se refiere a una persona concreta con nombre, está entre comillas "
    "o forma parte de discurso directo. "
    "Si una oración ya es impersonal o no tiene un 'tú' genérico (empieza por 'conviene', 'hay', "
    "'es necesario', o ya usa 'se'), cópiala EXACTAMENTE IGUAL: no le añadas 'se' ni la reformules, "
    "y no uses perífrasis como 'se considera que'. "
    "Cuida la concordancia del verbo con 'se' (por ejemplo, 'se revisa cada apartado', no 'se revisan cada apartado'). "
    "No cambies nada más. Devuelve solo el texto corregido."
)

USER_PROMPT_CORRECTION = (
    "Corrige SOLO el 'tú' impersonal genérico del siguiente texto. "
    "Usa 'se' en todos los casos. Solo usa 'uno' si 'se' es gramaticalmente inadecuado o poco natural. "
    "No modifiques nada que esté entre comillas (\", «», ''). "
    "Deja todo lo demás exactamente igual.\n\n"
    "Ejemplo de entrada:\n"
    "Cuando analizas los resultados, puedes cometer errores. "
    "Se observa cómo suben los precios. Laura dijo: \"cuando publicas, tú asumes la responsabilidad.\"\n"
    "Ejemplo de salida:\n"
    "Cuando se analizan los resultados, se pueden cometer errores. "
    "Se observa cómo suben los precios. Laura dijo: \"cuando publicas, tú asumes la responsabilidad.\"\n\n"
    "Texto:\n"
    "{text}\n\n"
    "Texto corregido:"
)

# ── Prompt feedback por documento ──────────────────────────────────────────────
SYSTEM_PROMPT_FEEDBACK = (
    "Eres un tutor de escritura académica en español. "
    "Recibes frases corregidas por uso impersonal del 'tú'. "
    "Escribe un feedback pedagógico en prosa, BREVE: un solo párrafo de 2 a 4 frases, sin listas. "
    "Explica brevemente que el error consiste en usar la segunda persona singular con valor general "
    "y por qué no es adecuado en textos académicos, donde se prefieren construcciones impersonales con 'se'. "
    "Incluye: una explicación clara del fenómeno, una regla fácil de recordar y una frase final de motivación. "
    "Si mencionas ejemplos, usa solo fragmentos reales de las frases recibidas y no inventes casos nuevos. "
    "Tono cercano y profesional, sin ser condescendiente."
)

USER_PROMPT_FEEDBACK = (
    "Se han detectado {n_errores} frase(s) con posible 'tú' impersonal. "
    "Estas son las correcciones:\n\n"
    "{ejemplos}\n\n"
    "Genera el feedback usando solo esta información."
)

# ── Prompt valoración global ───────────────────────────────────────────────────

SYSTEM_PROMPT_GLOBAL = (
    "Eres un tutor de escritura académica en español. "
    "Tu tarea es dar una valoración global breve, motivadora y constructiva del progreso del estudiante "
    "basándote en sus métricas de uso de la aplicación. "
    "Habla directamente al estudiante usando la segunda persona (tú), no uses 'el estudiante' ni tercera persona. "
    "Mantén un tono positivo y de apoyo incluso cuando los resultados sean bajos. "
    "En escritura académica, el uso impersonal de la segunda persona singular ('tú impersonal') se considera inadecuado, "
    "por lo que un porcentaje alto indica que todavía hay aspectos que mejorar, pero debes comentarlo de forma motivadora, "
    "valorando el esfuerzo y animando a seguir practicando. "
    "Máximo dos párrafos cortos."
)

USER_PROMPT_GLOBAL = (
    "Has procesado {total_docs} documentos en {login_days} días de uso.\n"
    "En el {pct_tu:.1f}% de tus documentos se detectó uso del 'tú' impersonal.\n"
    "En el {pct_sin_cambios:.1f}% de tus documentos no realizaste cambios manuales sobre la corrección del modelo.\n"
    "Tiempo medio de sesión: {avg_session}.\n\n"
    "Escribe una valoración motivadora de mi progreso y dame una recomendación concreta para mejorar, "
    "teniendo en cuenta que el 'tú' impersonal es un error que se debe evitar en textos académicos."
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _set(progress: int, message: str):
    global LOAD_PROGRESS, LOAD_MESSAGE
    with _lock:
        LOAD_PROGRESS = max(0, min(100, int(progress)))
        LOAD_MESSAGE  = message


def _clear_cache():
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def clean_pred(text: str) -> str:
    """
    Limpia la salida de Llama 3.2 3B Instruct para texto completo.
    Elimina prefijos tipo 'Corrected text:', 'Output:', artefactos iniciales.
    """
    text = text.strip()

    # Si el modelo repite "Corrected text:" o "Output:", quedarse con lo que hay después
    for prefix in (r'texto\s+corregido\s*:', r'corrected\s+text\s*:', r'output\s*:', r'texto\s*:'):
        m = re.search(prefix, text, re.IGNORECASE)
        if m:
            text = text[m.end():].strip()
            break

    # Eliminar prefijos residuales al inicio
    text = re.sub(r'^[\s\-–—]+', '', text)
    text = re.sub(
        r'^(corrección|corregido|input|sentence)\s*:\s*',
        '', text, flags=re.IGNORECASE
    )

    return text.strip().strip('"\'')



# ── Carga del modelo ───────────────────────────────────────────────────────────

def _load_impl():
    global MODEL_LOADED, _tokenizer, _model
    MODEL_LOADED = False
    _clear_cache()

    try:
        _set(5, "Inicializando carga…")

        _set(20, "Preparando configuración del modelo…")

        _set(40, "Cargando tokenizer…")
        _tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, use_fast=True)
        if _tokenizer.pad_token is None:
            _tokenizer.pad_token = _tokenizer.eos_token

        _set(75, "Cargando modelo (esto puede tardar)…")
        _model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            device_map="auto",
            torch_dtype=torch.bfloat16,   # bfloat16 completo — más rápido que NF4 en A100
        )
        _model.eval()

        # Asegurar que use_cache está activo (KV-cache → tokens sucesivos más rápidos)
        if hasattr(_model, "config"):
            _model.config.use_cache = True

        _set(95, "Finalizando…")
        time.sleep(0.3)
        MODEL_LOADED = True
        _set(100, "✅ Modelo cargado y listo")

    except Exception as e:
        _set(0, f"❌ Error cargando modelo: {e}")
        MODEL_LOADED = False


def ensure_model_loaded(async_load: bool = True):
    global _thread
    if MODEL_LOADED and _model is not None and _tokenizer is not None:
        return
    if _thread and _thread.is_alive():
        return
    if async_load:
        _thread = threading.Thread(target=_load_impl, daemon=True)
        _thread.start()
    else:
        _load_impl()


# ── Inferencia base ────────────────────────────────────────────────────────────

@torch.inference_mode()
def _chat_generate(messages: list, max_new_tokens: int) -> str:
    """Genera texto a partir de una lista de mensajes chat."""
    global _tokenizer, _model
    if not MODEL_LOADED or _tokenizer is None or _model is None:
        return ""

    enc = _tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        return_tensors="pt",
        return_dict=True,
        truncation=True,
        max_length=2048,          # limitar prompt largo → menos tokens de entrada
    )
    input_ids      = enc["input_ids"].to(_model.device)
    attention_mask = enc["attention_mask"].to(_model.device)
    input_length   = input_ids.shape[1]

    output_ids = _model.generate(
        input_ids,
        attention_mask=attention_mask,
        max_new_tokens=max_new_tokens,
        do_sample=DO_SAMPLE,
        num_beams=NUM_BEAMS,
        pad_token_id=_tokenizer.eos_token_id,
        eos_token_id=_tokenizer.eos_token_id,
        use_cache=True,
    )

    generated = output_ids[0][input_length:]
    return _tokenizer.decode(
        generated,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=True,
    ).strip()


# ── Corrección — texto completo en una sola llamada ───────────────────────────

def correct_full_text(text: str) -> str:
    """
    Corrige el texto completo en UNA sola llamada al modelo.
    Igual que el original del ZIP — mucho más rápido que frase a frase.
    El modelo recibe el texto completo y devuelve el texto corregido.
    """
    if not text or not text.strip():
        return ""
    if not MODEL_LOADED:
        raise RuntimeError("El modelo aún no está cargado.")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_CORRECTION},
        {"role": "user",   "content": USER_PROMPT_CORRECTION.format(text=text.strip())},
    ]
    # max_new_tokens proporcional al texto: ~1.2 tokens por palabra estimado
    n_words = len(text.split())
    max_tok = min(max(n_words * 2, 256), 1024)

    raw = _chat_generate(messages, max_new_tokens=max_tok)
    return clean_pred(raw) if raw else text


# ── Corrección de marcadores discursivos (2.ª pasada, guiada por el detector) ───
SYSTEM_PROMPT_MARCADORES = (
    "Eres un corrector de estilo de español académico. "
    "Corrige ÚNICAMENTE los problemas de marcadores discursivos correlativos que se te indican "
    "(pares como 'por un lado … por otro lado' o 'por una parte … por otra parte'). "
    "Si falta la pareja de un conector, AÑADE el conector que falta en el lugar adecuado "
    "(por ejemplo, añade 'por otro lado' antes de la idea que contrasta) SIN renombrar ni cambiar "
    "el conector que ya existe; si un conector se repite "
    "de forma desequilibrada, sustituye las repeticiones sobrantes por conectores equivalentes "
    "('además', 'asimismo', 'por último', 'en cambio', etc.). "
    "No corrijas ningún otro aspecto, no cambies el contenido ni el orden de las ideas, "
    "y no toques nada que esté entre comillas. Devuelve solo el texto corregido."
)

USER_PROMPT_MARCADORES = (
    "Problemas de marcadores discursivos detectados:\n{incidencias}\n\n"
    "Corrige SOLO esos problemas de conectores y deja todo lo demás exactamente igual.\n\n"
    "Texto:\n{text}\n\n"
    "Texto corregido:"
)


def correct_marcadores(text: str, incidencias=None) -> str:
    """
    Segunda pasada de corrección: arregla SOLO los marcadores discursivos señalados
    por el detector, sobre el texto ya corregido del 'tú'. Aditivo: no toca correct_full_text.
    """
    incidencias = incidencias or []
    if not text or not text.strip() or not incidencias:
        return text
    if not MODEL_LOADED:
        return text

    inc_txt = "\n".join("- " + str(i) for i in incidencias[:5])
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_MARCADORES},
        {"role": "user",   "content": USER_PROMPT_MARCADORES.format(incidencias=inc_txt, text=text.strip())},
    ]
    n_words = len(text.split())
    max_tok = min(max(n_words * 2, 256), 1024)
    raw = _chat_generate(messages, max_new_tokens=max_tok)
    return clean_pred(raw) if raw else text


# Alias para compatibilidad con main.py existente
def correct_text(sentences: List[str], batch_size: int = 4, max_new_tokens: int = 150) -> List[str]:
    text = " ".join(s.strip() for s in sentences if isinstance(s, str) and s.strip())
    return [correct_full_text(text)]



# ── Gerundio de posterioridad (2.ª pasada, guiada por el pre-filtro) ────────────
# Prompt 4 (few-shot + reglas) de la investigación. Solo sobre frases candidatas.
SYSTEM_PROMPT_GERUNDIO = (
    "Eres un corrector de gramática española especializado en lengua escrita formal y en un único fenómeno: "
    "el gerundio de posterioridad.\n\n"
    "TAREA:\n"
    "Corrige SOLO los casos en los que el gerundio expresa una acción posterior a la del verbo principal.\n\n"
    "DEFINICIÓN:\n"
    "Hay gerundio de posterioridad únicamente cuando la acción expresada por el gerundio ocurre después de la acción del verbo principal. "
    "Si el gerundio expresa simultaneidad, modo, medio, causa, condición, descripción o forma parte de una perífrasis, no debe corregirse.\n\n"
    "OBJETIVO:\n"
    "Transforma esos casos en una coordinación con 'y' y un verbo conjugado.\n\n"
    "REGLA PRINCIPAL:\n"
    "Si hay duda sobre si el gerundio expresa posterioridad real, NO corrijas.\n\n"
    "CORREGIR SOLO SI:\n"
    "- el gerundio expresa con claridad una acción posterior;\n"
    "- la acción del gerundio ocurre después de la acción del verbo principal;\n"
    "- la corrección puede hacerse sustituyendo el gerundio por 'y' + verbo conjugado;\n"
    "- el nuevo verbo puede mantenerse en el mismo tiempo y modo que el verbo principal sin reformular libremente la oración.\n\n"
    "CÓMO CORREGIR:\n"
    "- Sustituye solo el segmento en gerundio afectado.\n"
    "- Usa 'y' + verbo conjugado.\n"
    "- Mantén el mismo tiempo verbal y modo que el verbo principal.\n"
    "- Mantén el mismo sujeto.\n"
    "- Ajusta pronombres o concordancia solo si es imprescindible para que la oración sea gramatical.\n"
    "- Conserva el significado original.\n\n"
    "NO CORREGIR NUNCA SI:\n"
    "- el gerundio expresa simultaneidad con el verbo principal;\n"
    "- el gerundio expresa modo o manera: 'habló sonriendo', 'respondió mirando al suelo';\n"
    "- el gerundio expresa medio o instrumento;\n"
    "- el gerundio expresa causa, condición o descripción;\n"
    "- el gerundio forma parte de una perífrasis o construcción aspectual: 'está estudiando', 'sigue trabajando', 'continúa avanzando', 'acabó aceptando', 'terminó cediendo', 'lleva años investigando';\n"
    "- el gerundio aparece dentro de comillas, cita directa o estilo directo;\n"
    "- la frase no contiene gerundio;\n"
    "- la frase ya es correcta y no contiene gerundio de posterioridad;\n"
    "- la modificación exigiría reescribir libremente la oración o alterar partes ajenas al fenómeno.\n\n"
    "RESTRICCIONES DE SALIDA:\n"
    "- Devuelve una sola oración final.\n"
    "- No expliques nada.\n"
    "- No añadas comentarios.\n"
    "- No cambies palabras no relacionadas con el fenómeno.\n"
    "- No cambies léxico, nombres, números, puntuación, mayúsculas, comillas ni tildes, salvo lo imprescindible para la corrección mínima.\n"
    "- No resumas ni reformules.\n"
    "- Conserva exactamente el contenido original excepto los cambios mínimos necesarios para sustituir el gerundio de posterioridad.\n\n"
    "CRITERIO DE CONSERVACIÓN:\n"
    "Si solo una parte de la oración contiene un gerundio de posterioridad, corrige solo esa parte y deja intacto todo lo demás.\n\n"
    "IMPORTANTE:\n"
    "Es mejor dejar una frase sin corregir que hacer una corrección falsa."
)

USER_PROMPT_GERUNDIO = (
    "Ejemplos:\n\n"
    "Input: El laboratorio completó la primera fase del ensayo, publicando los resultados dos meses después.\n"
    "Output: El laboratorio completó la primera fase del ensayo y publicó los resultados dos meses después.\n\n"
    "Input: La comisaria inaugurará la exposición temporal, trasladándola después a la sede principal del museo.\n"
    "Output: La comisaria inaugurará la exposición temporal y la trasladará después a la sede principal del museo.\n\n"
    "Input: El decano presentó el nuevo plan docente, remitiéndolo al claustro al día siguiente para su valoración.\n"
    "Output: El decano presentó el nuevo plan docente y lo remitió al claustro al día siguiente para su valoración.\n\n"
    "Input: La empresa validó el prototipo final, registrando la patente semanas más tarde.\n"
    "Output: La empresa validó el prototipo final y registró la patente semanas más tarde.\n\n"
    "Input: El bibliotecario ordenará los manuscritos antiguos, digitalizándolos después para el archivo central.\n"
    "Output: El bibliotecario ordenará los manuscritos antiguos y los digitalizará después para el archivo central.\n\n"
    "Input: La arquitecta restauró la fachada principal, reabriendo el edificio meses después al público general.\n"
    "Output: La arquitecta restauró la fachada principal y reabrió el edificio meses después al público general.\n\n"
    "Input: El rector compareció ante la prensa, anunciando la decisión definitiva horas más tarde en un comunicado oficial.\n"
    "Output: El rector compareció ante la prensa y anunció la decisión definitiva horas más tarde en un comunicado oficial.\n\n"
    "Input: El equipo entregó la versión preliminar del informe, incorporando las correcciones la semana siguiente.\n"
    "Output: El equipo entregó la versión preliminar del informe e incorporó las correcciones la semana siguiente.\n\n"
    "Input: La profesora explicó el tema utilizando ejemplos del examen anterior.\n"
    "Output: La profesora explicó el tema utilizando ejemplos del examen anterior.\n\n"
    "Input: El violinista interpretó la pieza mirando al director en los compases finales.\n"
    "Output: El violinista interpretó la pieza mirando al director en los compases finales.\n\n"
    "Input: El departamento sigue revisando las solicitudes pendientes.\n"
    "Output: El departamento sigue revisando las solicitudes pendientes.\n\n"
    "Input: La comisión terminó aceptando la propuesta tras varias rondas de negociación.\n"
    "Output: La comisión terminó aceptando la propuesta tras varias rondas de negociación.\n\n"
    "Input: \"Estoy preparando la defensa\", respondió la doctoranda.\n"
    "Output: \"Estoy preparando la defensa\", respondió la doctoranda.\n\n"
    "Input: El senderista avanzaba observando el paisaje desde la ladera.\n"
    "Output: El senderista avanzaba observando el paisaje desde la ladera.\n\n"
    "Input: En los pasillos del edificio, en el archivo o revisando las cajas antiguas.\n"
    "Output: En los pasillos del edificio, en el archivo o revisando las cajas antiguas.\n\n"
    "Input: {sentence}\n"
    "Output:"
)


def _clean_pred_gerundio(text: str) -> str:
    """clean_pred de la investigación: limpia prefijos y se queda con la 1.ª oración."""
    text = re.sub(r'^[\s\-–—"\']+', '', text)
    text = re.sub(r'^(corrección|corregido|texto corregido|output|input|sentence)\s*:\s*', '', text, flags=re.IGNORECASE)
    text = text.strip().strip('"\'')
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return sentences[0].strip() if sentences else text.strip()


def _cambio_gerundio_localizado(orig: str, nuevo: str) -> bool:
    """Acepta solo cambios pequeños/localizados: evita que el modelo reescriba libremente."""
    from collections import Counter
    o = orig.split()
    n = nuevo.split()
    if not n:
        return False
    if abs(len(n) - len(o)) > max(3, int(len(o) * 0.5)):
        return False
    co, cn = Counter(o), Counter(n)
    compartidas = sum((co & cn).values())
    return compartidas / max(len(o), 1) >= 0.5


def corregir_gerundio_posterioridad(text: str, candidatos=None) -> str:
    """
    Corrige las frases candidatas con el Prompt 4. Descarta la respuesta si está
    vacía, es idéntica o el cambio no es localizado (no reescribe ni inventa).
    """
    candidatos = candidatos or []
    if not text or not text.strip() or not candidatos:
        return text
    if not MODEL_LOADED:
        return text
    res = text
    for frase in candidatos:
        if frase not in res:
            continue
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_GERUNDIO},
            {"role": "user",   "content": USER_PROMPT_GERUNDIO.format(sentence=frase)},
        ]
        raw = _chat_generate(messages, max_new_tokens=MAX_NEW_TOKENS_GERUNDIO)
        corr = _clean_pred_gerundio(raw) if raw else ''
        if not corr or corr.strip() == frase.strip():
            continue
        if not _cambio_gerundio_localizado(frase, corr):
            continue
        res = res.replace(frase, corr, 1)
    return res


def _nota_gerundio(gerundio, adapt=None) -> str:
    """Nota sobre el gerundio: breve o tips, más progreso."""
    n = len(gerundio or [])
    if n == 0:
        return ''
    if _es_detallado(adapt, "gerundio"):
        nota = _TIPS_DETALLADOS["gerundio"]
    else:
        nota = (
        f"Sobre el gerundio de posterioridad: se {'ha' if n == 1 else 'han'} revisado "
        f"{n} frase(s) en las que el gerundio podría expresar una acción posterior a la "
            "del verbo principal (p. ej. «firma el acta, entregándola después» → «firma el "
            "acta y la entrega después»); en registro formal conviene usar «y» + verbo "
            "conjugado. Revisa la corrección propuesta."
        )
    prog = _linea_progreso(adapt, "gerundio")
    return nota + ("\n" + prog if prog else "")


# ── Feedback adaptativo ────────────────────────────────────────────────────────
# Nota breve por defecto; tips detallados si el fenómeno es recurrente (perfil).
_TIPS_DETALLADOS = {
    "tu": (
        "Este error te aparece a menudo, así que van unos consejos concretos para el «tú» impersonal:\n"
        "- Cambia el «tú» genérico por «se» + verbo en 3.ª persona: «si analizas» → «si se analiza».\n"
        "- Si con «se» suena raro, usa «uno»: «uno debe revisar cada dato».\n"
        "- Mantén el «tú» solo si te diriges a alguien concreto o va entre comillas.\n"
        "- Truco: sustituye «tú» por «cualquiera»; si la frase sigue teniendo sentido, es «tú» impersonal."
    ),
    "marcadores": (
        "Los marcadores discursivos se te resisten, así que repasa estas pautas:\n"
        "- Si abres un par («por un lado»), ciérralo siempre con su pareja («por otro lado»).\n"
        "- No mezcles familias: «por un lado… por otra parte» no casa.\n"
        "- Si solo tienes una idea, no uses el marcador de apertura; ve directo.\n"
        "- Varía los conectores para no repetir: «además», «asimismo», «por último»."
    ),
    "dequeismo": (
        "El dequeísmo te aparece a menudo; estas claves ayudan:\n"
        "- Los verbos de opinión o declaración NO llevan «de»: «pienso que», «creo que», «opino que».\n"
        "- Truco: pregunta «¿qué?», no «¿de qué?». «Pienso… ¿qué?» → sin «de».\n"
        "- Cuidado con el caso contrario: «me acuerdo DE que», «estoy seguro DE que» sí llevan «de»."
    ),
    "gerundio": (
        "El gerundio de posterioridad se te repite; ten en cuenta:\n"
        "- Si la segunda acción ocurre DESPUÉS, usa «y» + verbo: «firmó el acta, entregándola» → «firmó el acta y la entregó».\n"
        "- Es correcto cuando es simultáneo o de modo: «habló sonriendo», «trabaja usando datos».\n"
        "- Truco: si puedes intercalar «y después» entre las dos acciones, suele ser gerundio de posterioridad."
    ),
}


def _es_detallado(adapt, phenom_id) -> bool:
    """True si el fenómeno es recurrente para el estudiante."""
    return (adapt or {}).get("modo", {}).get(phenom_id) == "detallado"


def _linea_progreso(adapt, phenom_id) -> str:
    """Progreso autorreferido frente a los textos anteriores."""
    tend = (adapt or {}).get("progreso", {}).get(phenom_id)
    if tend == "mejora":
        return "Vas mejor en esto que en tus textos anteriores. ¡Sigue así!"
    if tend == "empeora":
        return "Ha aparecido más que en tus textos anteriores; repásalo con calma."
    return ""


def _cierre_adaptativo(adapt) -> str:
    """Cierre personalizado: nivel y fenómeno a reforzar."""
    adapt = adapt or {}
    partes = []
    if adapt.get("nivel"):
        partes.append(f"Nivel actual: {adapt['nivel']}")
    if adapt.get("foco"):
        partes.append(f"tu foco ahora es {adapt['foco']}")
    return " · ".join(partes) if partes else ""


def _mensaje_superado(adapt) -> str:
    """Refuerzo: errores recurrentes que el estudiante ha dejado de cometer."""
    sup = (adapt or {}).get("superado", [])
    if not sup:
        return ""
    if len(sup) == 1:
        return f"Has dejado de cometer {sup[0]}, que antes se repetía en tus textos. ¡Buen trabajo!"
    return "Has dejado de cometer errores que antes se repetían (" + ", ".join(sup) + "). ¡Buen trabajo!"

# ── Feedback por documento ─────────────────────────────────────────────────────

def _nota_marcadores(marcadores, adapt=None) -> str:
    """Nota sobre los marcadores: breve o tips, más progreso."""
    n = len(marcadores or [])
    if n == 0:
        return ""
    if _es_detallado(adapt, "marcadores"):
        nota = _TIPS_DETALLADOS["marcadores"]
    else:
        nota = (
            f"En cuanto a los marcadores discursivos, se {'ha' if n == 1 else 'han'} detectado "
            f"{n} problema{'' if n == 1 else 's'} en conectores correlativos (pares como "
            "«por un lado … por otro lado»). Recuerda cerrar el par cuando abres uno y no repetir "
            "el mismo conector; la salida ya incluye la corrección."
        )
    prog = _linea_progreso(adapt, "marcadores")
    return nota + ("\n" + prog if prog else "")


def _nota_dequeismo(dequeismo, adapt=None) -> str:
    """Nota sobre el dequeísmo: breve o tips, más progreso."""
    n = len(dequeismo or [])
    if n == 0:
        return ""
    if _es_detallado(adapt, "dequeismo"):
        nota = _TIPS_DETALLADOS["dequeismo"]
    else:
        nota = (
            f"Sobre las preposiciones: se {'ha' if n == 1 else 'han'} detectado {n} caso(s) de "
            "dequeísmo (uso indebido de 'de que', p. ej. 'pienso de que' → 'pienso que'); "
            "la salida ya lo corrige."
        )
    prog = _linea_progreso(adapt, "dequeismo")
    return nota + ("\n" + prog if prog else "")


def generate_feedback(original: str, corrected: str, n_errores: int = 0, marcadores=None, dequeismo=None, gerundio=None, adapt=None) -> str:
    """Genera feedback pedagógico de forma síncrona (uso interno y global_feedback)."""
    if not MODEL_LOADED or not original.strip():
        return ""

    marcadores = marcadores or []
    dequeismo = dequeismo or []
    gerundio = gerundio or []
    adapt = adapt or {}

    cierre = _cierre_adaptativo(adapt)
    superado = _mensaje_superado(adapt)

    if n_errores == 0:
        extras = [n for n in (_nota_marcadores(marcadores, adapt), _nota_dequeismo(dequeismo, adapt), _nota_gerundio(gerundio, adapt)) if n]
        if extras:
            fb = "No se ha detectado 'tú' impersonal en este texto.\n\n" + "\n\n".join(extras)
        else:
            fb = (
                "¡Excelente trabajo! No se ha detectado ningún uso del 'tú' impersonal en este texto. "
                "Las construcciones impersonales están bien empleadas y el registro académico es el adecuado. "
                "Sigue así."
            )
        if superado:
            fb = fb.rstrip() + "\n\n" + superado
        if cierre:
            fb = fb.rstrip() + "\n\n" + cierre
        return fb

    from backend.utils import split_into_sentences
    orig_sents = split_into_sentences(original)
    corr_sents = split_into_sentences(corrected)

    ejemplos = []
    for o, c in zip(orig_sents, corr_sents):
        if o.strip() != c.strip():
            ejemplos.append(f"• Original:  {o.strip()}\n  Corregida: {c.strip()}")
        if len(ejemplos) >= 3:
            break

    ejemplos_txt = "\n\n".join(ejemplos) if ejemplos else "(No se pudieron extraer ejemplos concretos.)"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_FEEDBACK},
        {"role": "user",   "content": USER_PROMPT_FEEDBACK.format(
            n_errores=n_errores,
            ejemplos=ejemplos_txt,
        )},
    ]
    fb = _chat_generate(messages, max_new_tokens=MAX_NEW_TOKENS_FEEDBACK)
    if _es_detallado(adapt, "tu"):
        fb = (fb or "").rstrip() + "\n\n" + _TIPS_DETALLADOS["tu"]
    _prog_tu = _linea_progreso(adapt, "tu")
    if _prog_tu:
        fb = (fb or "").rstrip() + "\n\n" + _prog_tu
    for _n in (_nota_marcadores(marcadores, adapt), _nota_dequeismo(dequeismo, adapt), _nota_gerundio(gerundio, adapt)):
        if _n:
            fb = (fb or "").rstrip() + "\n\n" + _n
    if superado:
        fb = (fb or "").rstrip() + "\n\n" + superado
    if cierre:
        fb = (fb or "").rstrip() + "\n\n" + cierre
    return fb


def schedule_feedback(doc_id: int, original: str, corrected: str, n_errores: int = 0, marcadores=None, dequeismo=None, gerundio=None, adapt=None):
    """
    Lanza la generación de feedback en un hilo background.
    La corrección ya se devolvió al usuario; este hilo genera el feedback
    sin bloquear la respuesta HTTP.
    El resultado queda en _feedback_jobs[doc_id] para consultarlo por polling.
    """
    with _feedback_lock:
        _feedback_jobs[doc_id] = {"status": "pending", "result": ""}

    def _run():
        try:
            result = generate_feedback(original, corrected, n_errores, marcadores, dequeismo, gerundio, adapt)
            with _feedback_lock:
                _feedback_jobs[doc_id] = {"status": "done", "result": result}
        except Exception as e:
            with _feedback_lock:
                _feedback_jobs[doc_id] = {"status": "error", "result": str(e)}

    threading.Thread(target=_run, daemon=True).start()


def get_feedback_status(doc_id: int) -> dict:
    """Devuelve el estado del job de feedback para un doc_id."""
    with _feedback_lock:
        return dict(_feedback_jobs.get(doc_id, {"status": "not_found", "result": ""}))


# ── Valoración global ──────────────────────────────────────────────────────────

def generate_global_feedback(
    total_docs: int,
    login_days: int,
    pct_tu: float,
    pct_sin_cambios: float,
    avg_session_seconds: float,
) -> str:
    """
    Genera una valoración global del progreso del estudiante
    a partir de sus métricas agregadas.
    Solo se llama bajo demanda (botón en el frontend).
    """
    if not MODEL_LOADED:
        return ""

    def _fmt_time(secs: float) -> str:
        s = int(round(secs))
        h, rem = divmod(s, 3600)
        m, ss  = divmod(rem, 60)
        if h > 0:
            return f"{h} h {m} min"
        if m > 0:
            return f"{m} min {ss} s"
        return f"{ss} s"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_GLOBAL},
        {"role": "user",   "content": USER_PROMPT_GLOBAL.format(
            total_docs=total_docs,
            login_days=login_days,
            pct_tu=pct_tu,
            pct_sin_cambios=pct_sin_cambios,
            avg_session=_fmt_time(avg_session_seconds),
        )},
    ]
    return _chat_generate(messages, max_new_tokens=MAX_NEW_TOKENS_GLOBAL)