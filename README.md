# APP-PALABRIA

## **Aprendizaje y mejora de la escritura académica con ayuda de IA**

APP-PALABRIA es una aplicación educativa diseñada para ayudar a estudiantes a mejorar su escritura en español, especialmente en contextos académicos, científicos y formales.

La aplicación no se limita a corregir errores, sino que está pensada como una herramienta de aprendizaje, que combina:
- Corrección automática de textos.
- Feedback explicativo y pedagógico, **adaptado al nivel del estudiante**.
- Ejercicios de práctica **con su solución**.
- Métricas y **gráficas** que permiten analizar los errores y la evolución del usuario.

> **Versión 2 (v2).** Esta versión amplía la aplicación con nuevos fenómenos gramaticales, un feedback que se adapta al estudiante, ejercicios de práctica generados por IA y un panel de analíticas con varias gráficas. Las novedades se detallan en la sección [Novedades de la versión 2](#novedades-de-la-versión-2).


## **¿Qué permite hacer la aplicación?**

Con la aplicación de PALABRIA, el estudiante puede:

- Subir documentos PDF o introducir texto plano.
- Obtener una versión corregida del texto.
- Recibir feedback explicativo y **adaptado a su nivel** sobre los cambios realizados, orientado a entender:
  - por qué es incorrecto o inadecuado,
  - cómo reformular el texto de manera más correcta y formal.
- Practicar con un **ejercicio** sobre el error que más se le repite, con una **solución** que puede consultar cuando quiera.
- Analizar sus propios textos a través de métricas automáticas.
- Consultar un panel de indicadores y **gráficas** sobre su progreso y sobre el conjunto del alumnado.

En la v1 la aplicación se centraba únicamente en el uso impersonal del “tú”. En la **v2** detecta y corrige **cuatro fenómenos** (ver abajo).


## **Novedades de la versión 2**

### 1. Nuevos fenómenos gramaticales
Además del “tú” impersonal (v1), la v2 detecta y corrige:

- **Marcadores discursivos pareados** — pares correlativos incompletos o desequilibrados (p. ej. “por un lado … por otro lado”). Corrección determinista: si falta la pareja de un conector suelto se elimina, y si un conector se repite se varía. Nunca inventa contenido.
- **Dequeísmo (preposiciones)** — uso indebido de “de que” tras verbos de opinión o declaración (p. ej. “pienso de que” → “pienso que”). Detección con spaCy y corrección determinista.
- **Gerundio de posterioridad** — gerundio que expresa una acción posterior a la del verbo principal (p. ej. “firma el acta, entregándola después” → “firma el acta y la entrega después”). Un pre-filtro con spaCy localiza las frases candidatas y, solo sobre esas, el modelo decide y corrige, con un guardarraíl que evita reescrituras o invenciones.

También se añadió una **limpieza determinista** de la sobre-corrección del “tú” (p. ej. normalizar “se citas” → “se cita”).

### 2. Feedback adaptativo
El feedback sigue siendo **breve por defecto** y solo se alarga cuando hace falta. La adaptación se calcula a partir del historial del estudiante (es determinista; el modelo solo redacta el párrafo del “tú”):

- **Tips detallados** cuando un fenómeno es recurrente en las últimas entregas del estudiante.
- **Progreso autorreferido**: compara con sus textos anteriores (“vas mejor que antes” / “ha aparecido más”).
- **Refuerzo**: felicita cuando el estudiante deja de cometer un error que antes se repetía.
- **Cierre personalizado** con el nivel (principiante / intermedio / avanzado) y el fenómeno a reforzar.

### 3. Ejercicios de práctica con solución (IA)
Sobre el fenómeno que más se le repite al estudiante, la aplicación propone una práctica:
- una **explicación** y un **ejemplo** fijos (correctos, no generados por IA),
- un **ejercicio** (una frase con el error) generado por el modelo, adaptado al nivel,
- y su **solución**, que se muestra al pulsar **«Ver solución»**, para que el estudiante lo intente primero.

### 4. Métricas y gráficas nuevas
Nuevos indicadores (errores por documento, densidad de errores, tiempo por documento, nivel del estudiante) y un panel con **varias gráficas** (ver [Métricas e indicadores](#métricas-e-indicadores)).

### 5. Diseño “data-driven”
La lista de fenómenos está centralizada: **añadir un fenómeno nuevo en el futuro es prácticamente una sola línea de configuración**, y entra automáticamente en la categorización, la evolución temporal y el feedback.


## **Fenómenos detectados**

| Fenómeno | Tipo de corrección |
|---|---|
| “Tú” impersonal | Modelo (Llama 3.2 3B) + limpieza determinista |
| Marcadores discursivos pareados | Determinista (spaCy) |
| Dequeísmo (preposiciones) | Determinista (spaCy) |
| Gerundio de posterioridad | Pre-filtro spaCy + modelo, con guardarraíl |


## **Ejecución de la aplicación**

La aplicación se puede ejecutar mediante Google Colab utilizando el notebook disponible.

📓 **Notebook principal**:  
`APP_PALABRIA.ipynb`

En este notebook se explican paso a paso las acciones necesarias para:
1. Preparar el entorno de ejecución.
2. Instalar las dependencias.
3. Arrancar el backend.
4. Arrancar la interfaz web (frontend).
5. Acceder a la aplicación desde el navegador.

Se recomienda seguir las celdas en el orden indicado. La v2 no cambia la forma de lanzar la aplicación.


## **Funcionamiento general**

La app de PALABRIA combina dos componentes principales:

- **Backend**  
  Se encarga de:
  - Procesar los textos introducidos por el usuario.
  - Aplicar las correcciones lingüísticas (deterministas y, para el “tú” y el gerundio, con el modelo).
  - Generar el feedback explicativo y adaptativo, y los ejercicios de práctica.
  - Calcular y almacenar las métricas.

- **Frontend**  
  Proporciona una interfaz web sencilla e intuitiva que permite:
  - Subir PDFs o introducir texto manualmente.
  - Visualizar el texto corregido.
  - Consultar el feedback, el ejercicio con su solución y el panel de métricas y gráficas.


## **Feedback pedagógico**

Uno de los objetivos principales de APP-PALABRIA es favorecer el aprendizaje a partir de la corrección.

Por ello, la aplicación no solo devuelve un texto corregido, sino que genera un feedback explicativo que ayuda al estudiante a:
- Reconocer el error en su propio texto.
- Comprender por qué resulta inadecuado en contextos académicos y formales.
- Entender cómo reformularlo de manera más adecuada.
- Interiorizar la reformulación para aplicarla de forma autónoma en futuros textos.

En la v2 este feedback se **adapta al estudiante**: es breve cuando va bien y ofrece tips detallados cuando un error se le repite, reconoce su progreso respecto a textos anteriores y le indica en qué fenómeno centrarse. Además, propone un **ejercicio de práctica con su solución** sobre ese fenómeno.

Además del feedback asociado a cada texto, la aplicación genera un **feedback global** a partir de las métricas agregadas del usuario, con una visión de su comportamiento, sus patrones de error y su evolución.


## **Métricas e indicadores**

La aplicación calcula métricas con fines educativos y de análisis. Todas se obtienen de datos que ya se guardan por documento (no se añade recogida nueva de información).

### Indicadores numéricos
- **Métricas locales por texto** (número de frases, cambios realizados, incidencias de cada fenómeno).
- **Promedios históricos por métrica** (medias por documento del usuario).
- **Indicadores generales de uso** (documentos procesados, inicios de sesión, tiempo de sesión, % de documentos con cada tipo de error).
- **Indicadores de aprendizaje (v2)**: errores por documento, densidad de errores (errores por frase), tiempo medio por documento y nivel del estudiante.

### Gráficas del panel (v2)
**Por estudiante:**
1. **Categorización de errores por fenómeno** — cuántas incidencias de cada fenómeno y su peso.
2. **Evolución de errores en el tiempo** — una línea por fenómeno a lo largo de los documentos, con la tendencia general.
3. **Historial de cambios** — cambios propuestos por el modelo frente a los que conserva el estudiante.

**Comparativa y conjunto del alumnado:**
4. **Tú frente a la clase** — la media del estudiante frente a la media del alumnado, por fenómeno.
5. **Dónde falla el alumnado** — porcentaje de estudiantes que comete cada error.
6. **Distribución de niveles del alumnado** — cuántos estudiantes hay en cada nivel.

**Actividad:**
7. **Actividad semanal** — minutos de uso por día en los últimos 7 días.

> **Nota:** las gráficas de conjunto (4, 5 y 6) agregan datos de todos los usuarios de una misma base de datos. En el modelo actual, en el que cada usuario tiene su propia base de datos (ver abajo), reflejan únicamente al propio usuario; cobran pleno sentido en un despliegue con base de datos compartida.


## **Base de datos y privacidad**

- Cada ejecución crea una base de datos (SQLite) asociada al usuario.
- La base de datos se guarda en el Google Drive del propio usuario.
- La base de datos es personal para cada usuario.
- Los datos no se suben a GitHub ni se comparten con terceros.

Esto garantiza la privacidad y el uso individual de la aplicación. (Las analíticas de conjunto de la v2 requerirían una base de datos compartida, lo que corresponde a un despliegue centralizado y queda fuera de este modelo.)


## **Ejemplo**

**Texto original:**  
*Cuando analizas los resultados, puedes cometer errores de interpretación.*

**Texto corregido:**  
*Cuando se analizan los resultados, se pueden cometer errores de interpretación.*

**Feedback generado:**  
*La frase original utiliza la segunda persona del singular con valor impersonal, un recurso más propio del lenguaje oral o divulgativo. En textos académicos se recomienda emplear construcciones impersonales con “se”, ya que permiten expresar generalidad sin dirigirse directamente al lector y contribuyen a un estilo más objetivo y adecuado al registro formal.*


## **Acknowledgements**

Financiado por la Comunidad de Madrid a través del convenio-subvención para el fomento y la promoción de la investigación y la transferencia de tecnología en la Universidad Carlos III de Madrid (PALABRIA-CM-UC3M).

<p align="center">
  <img width="200" alt="Logotipo Comunidad de Madrid" src="https://github.com/user-attachments/assets/9adce597-da0a-48b3-84da-32b3ee5fb2f3" />
</p>
