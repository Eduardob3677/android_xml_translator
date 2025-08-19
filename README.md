# Android strings.xml Translator

Traductor de recursos `strings.xml` para Android usando Microsoft Translator (Azure AI Translator).

Características:

- Respeta `translatable="false"`
- Soporta `string-array` y `plurals`
- Preserva placeholders (`%s`, `%d`, `%1$s`) y secuencias de escape
- Opción de transliteración (`--transliterate`) a script latino cuando es posible
- Procesamiento paralelo de múltiples idiomas

Requisitos

- Cuenta de Azure y un recurso Translator con una clave de suscripción.

Configuración

- Variables de entorno (recomendado):
	- `AZURE_TRANSLATOR_KEY` (obligatoria)
	- `AZURE_TRANSLATOR_REGION` (requerida si tu recurso no es global)
	- `AZURE_TRANSLATOR_ENDPOINT` (opcional, por defecto `https://api.cognitive.microsofttranslator.com`)
	- `AZURE_TRANSLATOR_API_VERSION` (opcional, por defecto `3.0`)
	- `AZURE_TRANSLATOR_CATEGORY` (opcional, para Custom Translator)
	- `AZURE_TRANSLATOR_TEXT_TYPE` (`plain` o `html`, por defecto `plain`)

o parámetros CLI equivalentes:

- `--ms-key`, `--ms-region`, `--ms-endpoint`, `--ms-api-version`, `--ms-category`, `--ms-text-type`.

Archivo de configuración (opcional)

- Puedes pasar `--config config.json` con las mismas claves: `endpoint`, `key`, `region`, `api_version`, `category`, `text_type`.
- Precedencia de valores: defaults < archivo de configuración < variables de entorno < parámetros de CLI.
- Ejemplo: `config.example.json` incluido en el repo.

Uso

```bash
# Ejemplo básico (con variables de entorno ya exportadas)
python3 android_xml_translator.py app/src/main/res/values/strings.xml en fr es de

# Pasando la clave y región por CLI
python3 android_xml_translator.py app/src/main/res/values/strings.xml en fr es \
	--ms-key "$AZURE_TRANSLATOR_KEY" \
	--ms-region "westeurope"

# Usando archivo de configuración
python3 android_xml_translator.py app/src/main/res/values/strings.xml en fr es \
	--config config.json

Rendimiento (endpoint privado)

- Ajusta concurrencia: `--max-workers 10` (sube/baja según tu cuota).
- Pool de conexiones: `--http-pool-maxsize 100` puede ayudar con muchas llamadas en paralelo.
- Timeout: `--http-timeout 20` para reducir espera si hay colas.
- Retries: `--http-retries 3-5` según tolerancia a reintentos.

# Transliteración (por ejemplo, de uk a latino)
python3 android_xml_translator.py strings.xml uk en --transliterate
```

Salida

- Se generan archivos `strings-<lang>.xml` al lado del archivo original, por ejemplo: `strings-fr.xml`.

Notas

- Para transliteración, el script usa `toScript=Latn` de Microsoft Translator cuando está disponible.
- El script realiza reintentos ante errores 429/5xx con backoff.

## Pipeline APK: decompilar → traducir → recompilar → firmar

Incluye `apk_translate_pipeline.py` para automatizar el flujo completo:

Requisitos:

- apktool en PATH (o usa `--apktool-path`)
- apksigner o jarsigner para firmar (opcional). zipalign recomendado si está disponible.

Ejemplo:

```bash
python3 apk_translate_pipeline.py app.apk en es fr pt-BR \
	--config config.example.json \
	--keystore my.keystore --ks-alias myalias --ks-pass secret
```

Qué hace:

- Decompila el APK
- Traduce `res/values/strings.xml` y coloca resultados en `res/values-<lang>/strings.xml` (o `values-<lang>-r<REGION>`)
- Recompila
- Zipalign (si disponible) y firma (si se provee keystore)

Flags que se reenvían al traductor: `--config`, `--ms-endpoint`, `--ms-key`, `--ms-region`, `--ms-api-version`, `--ms-category`, `--ms-text-type`, `--max-workers`, `--http-timeout`, `--http-pool-maxsize`, `--http-retries`.

Salida por defecto firmada: `<nombre>_signed.apk`. Si no se firma, quedará un APK sin firmar/alineado en el directorio de trabajo.
