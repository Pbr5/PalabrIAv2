# # backend/utils.py
import pdfplumber
import io
import re
import spacy
from spacy.matcher import PhraseMatcher

nlp = spacy.load("es_core_news_lg")

def extract_text_from_pdf(pdf_bytes):
    pdf_file = io.BytesIO(pdf_bytes)
    with pdfplumber.open(pdf_file) as pdf:
        textos = []
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                textos.append(t)
        return "\r\n".join(textos)

def split_into_sentences(text):
    """Divide el texto en oraciones usando spaCy."""
    doc = nlp(text or "")
    return [s.text.strip() for s in doc.sents]
 
def es_verbo_segunda_persona(verbo):
    """
    Detecta 2ª persona REAL:
    - Verbos finitos con Person=2 (spaCy)
    - Infinitivos o gerundios con clítico -te (prepararte, corregirte, etc.)
    """

    # 1) Verbos FINITOS (reales)
    if "VerbForm=Fin" in verbo.morph:
        if verbo.morph.get("Person") == ["2"]:
            return True

    # 2) Infinitivos/gerundios con clítico -te
    texto = verbo.text.lower()
    if texto.endswith("te"):
        if verbo.morph.get("VerbForm") in (["Inf"], ["Ger"]):
            return True

    return False


def encontrar_verbos_segunda_persona(sent):
    """
    Encuentra verbos reales (VERB/AUX) en 2.ª persona.
    No se basa en terminaciones, solo spaCy + enclíticos.
    """
    verbos_reales = [t for t in sent if t.pos_ in ["VERB", "AUX"]]
    verbos_2p = [v for v in verbos_reales if es_verbo_segunda_persona(v)]
    return verbos_reales, verbos_2p

def posible_tu_impersonal(texto):
    """
    Evalúa exactamente las mismas oraciones que split_into_sentences().
    Marca las frases que contengan verbos en 2ª persona del singular.
    """
    sentences = split_into_sentences(texto or "")
    posibles = []

    for s in sentences:
        doc = nlp(s)
        _, verbos_2p = encontrar_verbos_segunda_persona(doc)

        if verbos_2p:
            posibles.append(s)

    mensaje = (
        "No se detectaron errores de 'tú' impersonal."
        if not posibles else
        f"Se detectaron {len(posibles)} posibles usos del 'tú' impersonal."
    )

    return posibles, mensaje

# ── Marcadores discursivos pareados ────────────────────────────────────────────
# Config data-driven: añadir una familia es añadir un dict.
FAMILIAS_MARCADORES = [
    {
        "id": "un_lado_otro_lado",
        "nombre": "por un lado … por otro lado",
        "apertura": ["por un lado"],
        "cierre":   ["por otro lado"],
    },
    {
        "id": "una_parte_otra_parte",
        "nombre": "por una parte … por otra parte",
        "apertura": ["por una parte"],
        "cierre":   ["por otra parte"],
    },
    {
        "id": "primer_segundo_lugar",
        "nombre": "en primer lugar … en segundo lugar",
        "apertura": ["en primer lugar"],
        "cierre":   ["en segundo lugar"],
    },
]

# PhraseMatcher global, insensible a mayúsculas.
_matcher_marcadores = PhraseMatcher(nlp.vocab, attr="LOWER")
for _fam in FAMILIAS_MARCADORES:
    _matcher_marcadores.add(f"{_fam['id']}__APERTURA", [nlp.make_doc(p) for p in _fam["apertura"]])
    _matcher_marcadores.add(f"{_fam['id']}__CIERRE",   [nlp.make_doc(p) for p in _fam["cierre"]])


def marcadores_discursivos_pareados(texto):
    """
    Detecta el uso incompleto o desequilibrado de marcadores discursivos
    pareados (p. ej. 'por un lado' … 'por otro lado').

    Devuelve (incidencias, mensaje, resumen):
      - incidencias: list[str] legibles  (análogo a errores_posibles)
      - mensaje:     str resumen          (análogo a mensaje_errores)
      - resumen:     dict de agregados numéricos (para insert_metric)
    """
    doc = nlp(texto or "")
    matches = _matcher_marcadores(doc)

    conteo = {fam["id"]: {"apertura": 0, "cierre": 0} for fam in FAMILIAS_MARCADORES}
    for match_id, start, end in matches:
        label = nlp.vocab.strings[match_id]      # p.ej. "un_lado_otro_lado__APERTURA"
        fam_id, rol = label.rsplit("__", 1)
        conteo[fam_id]["apertura" if rol == "APERTURA" else "cierre"] += 1

    incidencias = []
    total_aperturas = 0
    total_cierres = 0
    pares_equilibrados = 0

    fam_por_id = {f["id"]: f for f in FAMILIAS_MARCADORES}
    for fam_id, c in conteo.items():
        fam = fam_por_id[fam_id]
        a, ci = c["apertura"], c["cierre"]
        total_aperturas += a
        total_cierres += ci
        ap_txt = fam["apertura"][0]
        ci_txt = fam["cierre"][0]

        if a == 0 and ci == 0:
            continue
        if a > 0 and ci == 0:
            incidencias.append(
                f"Aparece «{ap_txt}» ({a}×) pero no su pareja de cierre «{ci_txt}»."
            )
        elif ci > 0 and a == 0:
            incidencias.append(
                f"Aparece «{ci_txt}» ({ci}×) sin la apertura «{ap_txt}» que lo introduzca."
            )
        elif a != ci:
            incidencias.append(
                f"Desequilibrio en «{fam['nombre']}»: «{ap_txt}» ({a}×) frente a «{ci_txt}» ({ci}×)."
            )
        else:  # a == ci > 0  → pareja bien usada
            pares_equilibrados += a

    if not incidencias:
        mensaje = "No se detectaron problemas en el uso de marcadores discursivos pareados."
    else:
        mensaje = (
            f"Se detectaron {len(incidencias)} posible(s) incidencia(s) "
            "en el uso de marcadores discursivos pareados."
        )

    resumen = {
        "marcadores_aperturas": total_aperturas,
        "marcadores_cierres": total_cierres,
        "marcadores_incidencias": len(incidencias),
        "marcadores_pares_equilibrados": pares_equilibrados,
    }
    return incidencias, mensaje, resumen


# ── Dequeísmo (preposiciones) ──────────────────────────────────────────────────
# "de que" indebido tras verbos de completiva directa; corrección determinista.
VERBOS_DEQUEISMO = {
    "pensar", "creer", "opinar", "considerar", "decir", "comentar", "suponer",
    "imaginar", "temer", "sospechar", "resultar", "recordar", "afirmar",
    "asegurar", "reconocer", "mencionar", "señalar", "indicar", "contar",
}


def _marcar_dequeismo(doc):
    """Devuelve [(verbo_tok, de_tok)] de los 'de que' que son dequeísmo."""
    marcas = []
    for i, tok in enumerate(doc):
        if tok.lower_ == "de" and i + 1 < len(doc) and doc[i + 1].lower_ == "que":
            for j in range(i - 1, max(i - 4, -1), -1):
                t = doc[j]
                if t.is_punct:
                    break
                if t.pos_ in ("VERB", "AUX"):
                    if t.lemma_ in VERBOS_DEQUEISMO:
                        marcas.append((t, tok))
                    break
    return marcas


def posible_dequeismo(texto):
    """
    Detecta posibles casos de dequeísmo. Mismo estilo que los otros detectores:
    devuelve (incidencias, mensaje, resumen).
    """
    doc = nlp(texto or "")
    marcas = _marcar_dequeismo(doc)
    incidencias = [
        f"«{v.text} de que» → posible dequeísmo (sería «{v.text} que»)."
        for v, _de in marcas
    ]
    n = len(incidencias)
    mensaje = (
        "No se detectaron casos de dequeísmo."
        if n == 0
        else f"Se detectaron {n} posible(s) caso(s) de dequeísmo."
    )
    return incidencias, mensaje, {"dequeismo_incidencias": n}


def corregir_dequeismo(texto):
    """
    Corrección DETERMINISTA: elimina el 'de' indebido (y su espacio) de cada
    dequeísmo detectado. No usa el modelo. Devuelve el texto corregido.
    """
    texto = texto or ""
    doc = nlp(texto)
    marcas = _marcar_dequeismo(doc)
    if not marcas:
        return texto
    spans = []
    for _v, de in marcas:
        start = de.idx
        end = de.idx + len(de.text)
        if end < len(texto) and texto[end] == " ":
            end += 1  # quitar también el espacio tras "de"
        spans.append((start, end))
    res = texto
    for start, end in sorted(spans, reverse=True):
        res = res[:start] + res[end:]
    return res


# ── Corrección determinista de marcadores ───────────────────────────────────────
# Conector suelto → se quita; conector repetido → se varía. No añade contenido.
_VARIEDAD_CIERRE = ["además", "asimismo", "por último", "en cambio"]


def corregir_marcadores(texto):
    texto = texto or ""
    doc = nlp(texto)
    por_fam = {f["id"]: {"APERTURA": [], "CIERRE": []} for f in FAMILIAS_MARCADORES}
    for mid, s, e in _matcher_marcadores(doc):
        fam_id, rol = nlp.vocab.strings[mid].rsplit("__", 1)
        por_fam[fam_id][rol].append(doc[s:e])

    acciones = []  # (start_char, end_char_conector, reemplazo|None)  None = quitar
    for fam_id, roles in por_fam.items():
        aps = sorted(roles["APERTURA"], key=lambda x: x.start_char)
        cis = sorted(roles["CIERRE"], key=lambda x: x.start_char)
        n_par = min(len(aps), len(cis))
        for sp in aps[n_par:]:                       # aperturas sueltas → quitar
            acciones.append((sp.start_char, sp.end_char, None))
        vi = 0
        for sp in cis[n_par:]:                        # cierres sobrantes → variar
            acciones.append((sp.start_char, sp.end_char, _VARIEDAD_CIERRE[vi % len(_VARIEDAD_CIERRE)]))
            vi += 1

    if not acciones:
        return texto

    res = texto
    for s, e, repl in sorted(acciones, key=lambda x: x[0], reverse=True):
        era_mayus = s < len(res) and res[s].isupper()
        if repl is None:
            j = e
            while j < len(res) and res[j] in ", ":     # quitar también coma/espacios
                j += 1
            res = res[:s] + res[j:]
            if era_mayus and s < len(res):             # recapitalizar la frase
                res = res[:s] + res[s].upper() + res[s + 1:]
        else:
            if era_mayus:
                repl = repl[0].upper() + repl[1:]
            res = res[:s] + repl + res[e:]
    return res


# ── Limpieza de sobre-corrección del 'tú' ───────────────────────────────────────
# Revierte 'se' añadido a verbos ya impersonales ("se conviene", "se hay que").
_COG_IMPERSONAL = r"considera|estima|aprecia|entiende|cree|piensa|opina|reconoce|señala"
_IMP_IMPERSONAL = (r"conviene|hay que|es necesario|es preciso|es posible|es importante|"
                   r"es fundamental|es recomendable|es conveniente|es útil|hace falta|basta con")
_PARES_SE_IMPERSONAL = [
    # "se considera que conviene" -> "conviene"
    (rf"se\s+(?:{_COG_IMPERSONAL})\s+que\s+({_IMP_IMPERSONAL})\b", r"\1"),
    (r"se\s+hay\s+que\b", "hay que"),
    (r"se\s+hay\b", "hay"),
    (r"se\s+conviene\s+en\b", "conviene"),
    (r"se\s+conviene\b", "conviene"),
    (r"se\s+hace\s+falta\b", "hace falta"),
    (r"se\s+es\s+(necesario|preciso|posible|importante|fundamental|recomendable|conveniente|útil)\b", r"es \1"),
    (r"se\s+basta\s+con\b", "basta con"),
]


def limpiar_se_impersonal(texto):
    """Revierte 'se' añadido indebidamente a verbos impersonales. Determinista."""
    texto = texto or ""
    for pat, rep in _PARES_SE_IMPERSONAL:
        def _f(m, rep=rep):
            r = m.expand(rep) if "\\" in rep else rep
            if m.group(0)[:1].isupper():
                r = r[:1].upper() + r[1:]
            return r
        texto = re.sub(r"\b" + pat, _f, texto, flags=re.IGNORECASE)
    return texto


# ── Normalización de "se" + 2.ª persona ─────────────────────────────────────────
# "se citas" → "se cita": pasa el verbo a 3.ª persona (pretérito y dudosos intactos).
_IRREG_2A_3A = {
    "eres": "es", "vas": "va", "estás": "está", "has": "ha",
}


def _a_tercera_persona_sing(tok):
    """Devuelve la 3.ª persona del singular de un verbo en 2.ª persona, o None si
    no es seguro transformarlo (pretérito u otros casos dudosos)."""
    low = tok.text.lower()
    if low in _IRREG_2A_3A:
        nuevo = _IRREG_2A_3A[low]
    elif low.endswith("ste") or low.endswith("steis"):
        return None  # pretérito: la regla de quitar "-s" no aplica
    elif low.endswith("s") and len(low) > 2:
        nuevo = low[:-1]
    else:
        return None
    if tok.text[:1].isupper():
        nuevo = nuevo[:1].upper() + nuevo[1:]
    return nuevo


# Clíticos que pueden seguir a "se" ("se le", "se les"…): NO son el patrón de error.
_CLITICOS_TRAS_SE = {"me", "te", "se", "le", "les", "lo", "la", "los", "las", "nos", "os"}


def corregir_se_segunda_persona(texto):
    """Corrige el patrón agramatical 'se' + verbo en 2.ª persona del singular
    ('se citas' → 'se cita'). Determinista; no toca el resto del texto."""
    texto = texto or ""
    doc = nlp(texto)
    cambios = []
    for i in range(len(doc) - 1):
        if doc[i].lower_ != "se":
            continue
        v = doc[i + 1]
        low = v.text.lower()
        if low in _CLITICOS_TRAS_SE:
            continue  # "se le", "se les"… no es el patrón de mal-corrección

        # spaCy lo etiqueta como verbo en 2.ª persona.
        es_2p_verbo = (
            v.pos_ in ("VERB", "AUX")
            and v.morph.get("Person") == ["2"]
            and v.morph.get("Number") == ["Sing"]
            and "Fin" in v.morph.get("VerbForm")
        )
        # Respaldo: forma ambigua ("citas") que spaCy marca como NOUN; tras "se" es verbo.
        es_2p_probable = (
            v.pos_ == "NOUN"
            and (low.endswith("as") or low.endswith("es"))
            and not low.endswith("ste")
            and len(low) > 3
        )
        if es_2p_verbo or es_2p_probable:
            nuevo = _a_tercera_persona_sing(v)
            if nuevo and nuevo != v.text:
                cambios.append((v.idx, v.idx + len(v.text), nuevo))
    if not cambios:
        return texto
    res = texto
    for s, e, nuevo in sorted(cambios, key=lambda x: x[0], reverse=True):
        res = res[:s] + nuevo + res[e:]
    return res


# ── Gerundio de posterioridad (pre-filtro) ─────────────────────────────────────
# Pre-filtro con spaCy: marca frases candidatas; la corrección la hace el modelo.
_PERIFRASIS_GER = {
    "estar", "seguir", "continuar", "ir", "venir", "andar",
    "llevar", "acabar", "terminar", "quedar", "quedarse",
    "permanecer", "proseguir",
}
# Sufijo simple (-ando/-iendo) y variante con enclíticos (entregándola).
_RE_GER_SIMPLE = re.compile(r"(?:ando|iendo|yendo)$", re.IGNORECASE)
_RE_GER_CLITIC = re.compile(
    r"(?:ándo|iéndo|yéndo)(?:se|me|te|lo|la|le|los|las|les|nos|os)+$",
    re.IGNORECASE,
)


def _es_gerundio_tok(tok):
    # 1) Señal fuerte: morfología de spaCy.
    if tok.pos_ in ("VERB", "AUX") and "Ger" in tok.morph.get("VerbForm"):
        return True
    low = tok.text.lower()
    # 2) Gerundio con enclítico (entregándola).
    if _RE_GER_CLITIC.search(low):
        return True
    # 3) Sufijo simple SOLO si es verbo (evita "cuando", "bando", "comando"…).
    if tok.pos_ in ("VERB", "AUX") and _RE_GER_SIMPLE.search(low) and len(low) > 5:
        return True
    return False


def _es_finito_tok(tok):
    return tok.pos_ in ("VERB", "AUX") and "Fin" in tok.morph.get("VerbForm")


def candidato_gerundio_posterioridad(texto):
    """
    Devuelve (candidatos, mensaje, resumen). Una frase es candidata si tiene un
    gerundio tras coma, después de un verbo finito y sin perífrasis.
    """
    doc = nlp(texto or "")
    candidatos = []
    for sent in doc.sents:
        toks = list(sent)
        for i, tok in enumerate(toks):
            if not _es_gerundio_tok(tok):
                continue
            prev = toks[:i]
            if not any(t.text == "," for t in prev):
                continue
            if not any(_es_finito_tok(t) for t in prev):
                continue
            ventana = toks[max(0, i - 3):i]
            if any(t.lemma_.lower() in _PERIFRASIS_GER for t in ventana):
                continue
            candidatos.append(sent.text.strip())
            break

    n = len(candidatos)
    mensaje = (
        "No se detectaron casos de gerundio de posterioridad."
        if n == 0
        else f"Se detectaron {n} posible(s) caso(s) de gerundio de posterioridad."
    )
    return candidatos, mensaje, {"gerundio_incidencias": n}
