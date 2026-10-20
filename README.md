# APP-PALABRIA

## **Aprendizaje y mejora de la escritura académica con ayuda de IA**

APP-PALABRIA es una aplicación educativa diseñada para ayudar a estudiantes a mejorar su escritura en español, especialmente en contextos académicos, científicos y formales.

La aplicación no se limita a corregir errores, sino que está pensada como una herramienta de aprendizaje, que combina:
- Corrección automática de textos.
- Feedback explicativo y pedagógico.
- Métricas que permiten analizar los errores y la evolución del usuario.


## **¿Qué permite hacer la aplicación?**

Con la aplicación de PALABRIA, el estudiante puede:

- Subir documentos PDF o introducir texto plano.
- Obtener una versión corregida del texto.
- Recibir feedback explicativo sobre los cambios realizados, orientado a entender:
  - por qué es incorrecto o inadecuado,
  - cómo reformular el texto de manera más correcta y formal.
- Analizar sus propios textos a través de métricas automáticas.
- Consultar indicadores generales sobre su uso de la aplicación.

Actualmente, la aplicación se centra principalmente en la detección y corrección del uso impersonal del “tú” en textos escritos.


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

Se recomienda seguir las celdas en el orden indicado.


## **Funcionamiento general**

La app de PALABRIA combina dos componentes principales:

- **Backend**  
  Se encarga de:
  - Procesar los textos introducidos por el usuario.
  - Aplicar las correcciones lingüísticas.
  - Generar el feedback explicativo.
  - Calcular y almacenar las métricas.

- **Frontend**  
  Proporciona una interfaz web sencilla e intuitiva que permite:
  - Subir PDFs o introducir texto manualmente.
  - Visualizar el texto corregido.
  - Consultar el feedback y los indicadores generados.


## **Feedback pedagógico**

Uno de los objetivos principales de APP-PALABRIA es favorecer el aprendizaje a partir de la corrección.

Por ello, la aplicación no solo devuelve un texto corregido, sino que genera un feedback explicativo centrado en el uso impersonal del “tú”, que ayuda al estudiante a:
- Reconocer el empleo de la segunda persona con valor impersonal en textos escritos.
- Comprender por qué este uso resulta inadecuado en contextos académicos y formales.
- Entender por qué las construcciones impersonales con “se” son una alternativa más adecuada desde el punto de vista del registro, la objetividad y la claridad.
- Interiorizar esta reformulación para aplicarla de forma autónoma en futuros textos.

El feedback está diseñado con un enfoque pedagógico y reflexivo, de manera que la corrección no sea solo un resultado final, sino un apoyo para el aprendizaje lingüístico.

Además del feedback asociado a cada texto, la aplicación genera un feedback general a partir de las métricas globales del usuario, construido mediante el análisis agregado de su actividad.

Este feedback ofrece una visión sintética e interpretativa del uso de la herramienta, incluyendo:
- Una caracterización del comportamiento global (frecuencia de uso, número de textos analizados, etc.).
- La identificación de patrones recurrentes de error (persistencia del “tú” impersonal).
- Una valoración de la evolución del usuario a lo largo del tiempo.

A partir de estos elementos, el sistema proporciona recomendaciones orientadas a la mejora, permitiendo al estudiante comprender su progreso, detectar tendencias en su escritura y orientar su aprendizaje de forma más autónoma.


## **Métricas e indicadores**

La aplicación genera distintos tipos de métricas con fines educativos y de análisis:

- **Métricas locales por texto**, asociadas a cada documento o entrada procesada (por ejemplo, número de frases, cambios realizados, presencia del fenómeno lingüístico "tú" impersonal).
- **Promedios de métricas locales**, que permiten observar tendencias en los textos del usuario.
- **Indicadores generales de uso**, que reflejan cómo se utiliza la aplicación a lo largo del tiempo (por ejemplo, número de textos analizados, número de inicios de sesión, porcentaje de textos en los que se detecta el uso impersonal del “tú”).

Estas métricas están pensadas para que el estudiante pueda reflexionar sobre su escritura, detectar patrones de error y observar su progreso.


## **Base de datos y privacidad**

- Cada ejecución crea una base de datos (SQLite) asociada al usuario.
- La base de datos se guarda en el Google Drive del propio usuario.
- La base de datos es personal para cada usuario.
- Los datos no se suben a GitHub ni se comparten con terceros.

Esto garantiza la privacidad y el uso individual de la aplicación.


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
