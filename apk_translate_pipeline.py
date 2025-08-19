#!/usr/bin/env python3
"""
Pipeline: decompila un APK con apktool, traduce res/values/strings.xml a varios idiomas
usando android_xml_translator.py (Microsoft Translator), recompila y firma el APK.

Requisitos de herramientas (en PATH o con rutas proporcionadas):
- apktool (https://ibotpeaches.github.io/Apktool/)
- apksigner (preferido, de Android SDK build-tools) o jarsigner (fallback)
- zipalign (opcional pero recomendado, de Android SDK build-tools)

Nota sobre directorios de recursos Android:
- Este script genera archivos localizados en res/values-<lang>/strings.xml (o values-<lang>-r<REGION>)
  a partir de res/values/strings.xml. Android detecta localización por directorio, no por nombre de archivo.

Advertencia sobre códigos de idioma:
- Se admite mapeo simple de BCP-47: "es" -> values-es; "pt-BR" -> values-pt-rBR.
  Casos avanzados (script/variante) pueden requerir ajuste manual.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict
from typing import Optional, List


HERE = Path(__file__).resolve().parent
TRANSLATOR_SCRIPT = HERE / "android_xml_translator.py"


def which(cmd: str):
    from shutil import which as _which
    return _which(cmd)


def ensure_tool(cmd_name: str, custom_path: Optional[str] = None, required: bool = True) -> Optional[str]:
    """Devuelve la ruta ejecutable si existe; si required y no se encuentra, aborta."""
    if custom_path:
        p = Path(custom_path)
        if p.exists():
            return str(p)
    found = which(cmd_name)
    if found:
        return found
    if required:
        print(f"Error: No se encontró '{cmd_name}' en PATH. Instálalo o provee --{cmd_name}-path.")
        sys.exit(1)
    return None


def lang_to_values_dir(lang_code: str) -> str:
    """Convierte código BCP-47 básico a directorio values-*
    - es -> values-es
    - pt-BR -> values-pt-rBR
    - zh-Hans -> values-zh (simplificado, se recomienda revisar manualmente)
    """
    if not lang_code:
        return "values"
    parts = lang_code.replace('_', '-').split('-')
    if len(parts) == 1:
        return f"values-{parts[0]}"
    # Solo lenguaje y región
    return f"values-{parts[0]}-r{parts[1].upper()}"


def run(cmd, cwd=None, env=None, check=True):
    print(f"→ Ejecutando: {' '.join(cmd)}" + (f"  (cwd={cwd})" if cwd else ""))
    proc = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(proc.stdout)
    if check and proc.returncode != 0:
        raise RuntimeError(f"Fallo comando: {' '.join(cmd)}")
    return proc


def find_all_locale_strings(decompiled_dir: Path) -> Dict[str, Path]:
    """Encuentra strings.xml en todas las carpetas res/values* y devuelve mapa locale->ruta.
    Locale "base" usará la clave "base".
    """
    res_dir = decompiled_dir / "res"
    if not res_dir.exists():
        raise FileNotFoundError("No existe directorio res/ en el APK decompilado.")
    result: Dict[str, Path] = {}
    for values_dir in sorted(res_dir.glob("values*")):
        sxml = values_dir / "strings.xml"
        if not sxml.exists():
            continue
        if values_dir.name == "values":
            result["base"] = sxml
        else:
            # values-es, values-pt-rBR, etc.
            locale = values_dir.name[len("values-"):]
            result[locale] = sxml
    if not result:
        raise FileNotFoundError("No se encontró ningún strings.xml en res/values*/")
    return result


def translate_from_all_locales(locale_files: Dict[str, Path], source_lang: str, target_langs: List[str], translator_args: List[str]):
    """Para cada locale existente (incluida base), traduce sus strings hacia cada target y fusiona en el destino.
    Escribe directamente en res/values-<target>/strings.xml usando la capacidad de output del traductor.
    """
    # Elegir un archivo base para el esquema (siempre existe alguno en el mapa)
    base_locale = "base" if "base" in locale_files else sorted(locale_files.keys())[0]
    base_file = locale_files[base_locale]
    res_dir = base_file.parent.parent

    for target in target_langs:
        print(f"==> Preparando traducción combinada hacia {target} desde {len(locale_files)} locales...")
        target_dir = res_dir / lang_to_values_dir(target)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / "strings.xml"

        # Inicial: copiar la base como punto de partida
        shutil.copy2(base_file, target_file)

        # Por cada locale origen, ejecutar el traductor apuntando output a target_file
        for src_locale, strings_xml in locale_files.items():
            # Permitir autodetección si se desea: si source_lang == 'auto' estará soportado por el traductor
            run([
                sys.executable,
                str(TRANSLATOR_SCRIPT),
                str(strings_xml),
                source_lang,
                target,
                *translator_args,
                # No hay flag nativa para output en el traductor; se hará en segunda fase usando la API del módulo si se expone.
            ])

            # Mover/merge: el traductor genera strings-<target>.xml junto al origen; fusionar en el target_file
            generated = strings_xml.with_name(f"strings-{target}.xml")
            if not generated.exists():
                raise RuntimeError(f"No se generó el archivo esperado: {generated}")

            # Fusionar contenidos: cargar ambos y sobreescribir/añadir claves del generado en el target_file
            merge_android_strings(str(target_file), str(generated), str(target_file))
            generated.unlink(missing_ok=True)
        print(f"✓ {target}: {target_file}")

def merge_android_strings(base_xml_path: str, add_xml_path: str, out_xml_path: str):
    """Fusiona strings/arrays/plurals de add_xml sobre base_xml (sobreescribe claves existentes y añade faltantes)."""
    import xml.etree.ElementTree as ET
    base_tree = ET.parse(base_xml_path)
    base_root = base_tree.getroot()
    add_tree = ET.parse(add_xml_path)
    add_root = add_tree.getroot()

    # Mapas de acceso rápido
    strings_map = {e.get('name'): e for e in base_root.findall('string')}
    arrays_map = {e.get('name'): e for e in base_root.findall('string-array')}
    plurals_map = {e.get('name'): e for e in base_root.findall('plurals')}

    # Strings
    for e in add_root.findall('string'):
        name = e.get('name')
        if name in strings_map:
            strings_map[name].text = e.text
        else:
            base_root.append(e)

    # Arrays
    for e in add_root.findall('string-array'):
        name = e.get('name')
        if name in arrays_map:
            # reemplazar completo
            base_root.remove(arrays_map[name])
        base_root.append(e)

    # Plurals
    for e in add_root.findall('plurals'):
        name = e.get('name')
        if name in plurals_map:
            base_root.remove(plurals_map[name])
        base_root.append(e)

    base_tree.write(out_xml_path, encoding='utf-8', xml_declaration=True)


def main():
    parser = argparse.ArgumentParser(description="Decompila, traduce y firma APK usando Microsoft Translator")
    parser.add_argument("apk", help="Ruta al APK de entrada")
    parser.add_argument("source_lang", help="Código de idioma origen (p.ej., en)")
    parser.add_argument("target_langs", nargs="+", help="Idiomas destino (p.ej., es fr pt-BR)")

    # Herramientas
    parser.add_argument("--apktool-path", help="Ruta a apktool si no está en PATH")
    parser.add_argument("--apksigner-path", help="Ruta a apksigner si no está en PATH")
    parser.add_argument("--jarsigner-path", help="Ruta a jarsigner si no está en PATH (fallback)")
    parser.add_argument("--zipalign-path", help="Ruta a zipalign si no está en PATH (opcional)")

    # Firmado
    parser.add_argument("--keystore", help="Ruta al keystore (.jks/.keystore)")
    parser.add_argument("--ks-alias", help="Alias de la clave en el keystore")
    parser.add_argument("--ks-pass", help="Password del keystore (storepass)")
    parser.add_argument("--key-pass", help="Password de la clave (keypass)")

    # Opciones del traductor (se reenvían al script)
    parser.add_argument("--config", help="Ruta al config JSON para Microsoft Translator")
    parser.add_argument("--ms-endpoint", help="Endpoint de Microsoft Translator")
    parser.add_argument("--ms-key", help="Clave de Microsoft Translator")
    parser.add_argument("--ms-region", help="Región de Microsoft Translator")
    parser.add_argument("--ms-api-version", help="Versión API (default 3.0)")
    parser.add_argument("--ms-category", help="Categoría personalizada (opcional)")
    parser.add_argument("--ms-text-type", choices=["plain", "html"], help="Tipo de texto (plain/html)")
    parser.add_argument("--max-workers", type=int, help="Trabajadores paralelos del traductor (por idioma)")
    parser.add_argument("--http-timeout", type=float, help="Timeout HTTP del traductor")
    parser.add_argument("--http-pool-maxsize", type=int, help="Pool de conexiones HTTP")
    parser.add_argument("--http-retries", type=int, help="Reintentos HTTP del traductor")

    # Directorios/archivos de salida
    parser.add_argument("--workdir", help="Directorio de trabajo (se creará si no existe)")
    parser.add_argument("--out", help="Ruta del APK firmado de salida (default: <apk>_signed.apk)")

    args = parser.parse_args()

    apk_path = Path(args.apk).resolve()
    if not apk_path.exists():
        print(f"Error: APK no encontrado: {apk_path}")
        sys.exit(1)

    apktool = ensure_tool("apktool", args.apktool_path, required=True)
    apksigner = ensure_tool("apksigner", args.apksigner_path, required=False)
    jarsigner = ensure_tool("jarsigner", args.jarsigner_path, required=False)
    zipalign = ensure_tool("zipalign", args.zipalign_path, required=False)

    if not TRANSLATOR_SCRIPT.exists():
        print(f"Error: No se encontró el traductor en {TRANSLATOR_SCRIPT}")
        sys.exit(1)

    # Validar firmado si se solicita salida firmada
    want_sign = args.keystore and args.ks_alias
    if want_sign and not (apksigner or jarsigner):
        print("Error: Para firmar necesitas apksigner o jarsigner disponible.")
        sys.exit(1)

    # Preparar directorio de trabajo
    if args.workdir:
        workdir = Path(args.workdir).resolve()
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        workdir = Path(tempfile.mkdtemp(prefix="apk_i18n_"))
    print(f"Directorio de trabajo: {workdir}")

    decompiled_dir = workdir / "apk_src"
    if decompiled_dir.exists():
        shutil.rmtree(decompiled_dir)

    # 1) Decompilar
    run([apktool, "d", str(apk_path), "-o", str(decompiled_dir), "-f"])  # -f para forzar overwrite

    # 2) Traducir res/values/strings.xml
    locale_files = find_all_locale_strings(decompiled_dir)
    print(f"Locales encontradas: {', '.join(sorted(locale_files.keys()))}")

    # Construir args a reenviar al traductor
    forward_args = []
    for opt in [
        ("--config", args.config),
        ("--ms-endpoint", args.ms_endpoint),
        ("--ms-key", args.ms_key),
        ("--ms-region", args.ms_region),
        ("--ms-api-version", args.ms_api_version),
        ("--ms-category", args.ms_category),
        ("--ms-text-type", args.ms_text_type),
        ("--max-workers", str(args.max_workers) if args.max_workers is not None else None),
        ("--http-timeout", str(args.http_timeout) if args.http_timeout is not None else None),
        ("--http-pool-maxsize", str(args.http_pool_maxsize) if args.http_pool_maxsize is not None else None),
        ("--http-retries", str(args.http_retries) if args.http_retries is not None else None),
    ]:
        if opt[1]:
            forward_args.extend([opt[0], opt[1]])

    translate_from_all_locales(locale_files, args.source_lang, args.target_langs, forward_args)

    # 3) Recompilar
    unsigned_apk = workdir / "unsigned.apk"
    run([apktool, "b", str(decompiled_dir), "-o", str(unsigned_apk)])

    # 4) Zipalign (opcional pero recomendado antes de firmar)
    aligned_apk = unsigned_apk
    if zipalign:
        aligned_apk = workdir / "aligned.apk"
        run([zipalign, "-f", "-p", "4", str(unsigned_apk), str(aligned_apk)])

    # 5) Firmar (si se proporcionó keystore)
    final_apk = Path(args.out).resolve() if args.out else apk_path.with_name(apk_path.stem + "_signed.apk")
    if want_sign:
        if apksigner:
            cmd = [
                apksigner, "sign",
                "--ks", args.keystore,
                "--ks-key-alias", args.ks_alias,
                "--out", str(final_apk),
            ]
            if args.ks_pass:
                cmd.extend(["--ks-pass", f"pass:{args.ks_pass}"])
            if args.key_pass:
                cmd.extend(["--key-pass", f"pass:{args.key_pass}"])
            cmd.append(str(aligned_apk))
            run(cmd)
        else:
            # jarsigner firma in-place; luego renombramos
            cmd = [
                jarsigner,
                "-keystore", args.keystore,
                "-signedjar", str(final_apk),
            ]
            if args.ks_pass:
                cmd.extend(["-storepass", args.ks_pass])
            if args.key_pass:
                cmd.extend(["-keypass", args.key_pass])
            cmd.extend([str(aligned_apk), args.ks_alias])
            run(cmd)
        print(f"APK firmado: {final_apk}")
    else:
        # Si no se firma, dejamos el APK (posiblemente aligned) como salida
        final_apk = aligned_apk
        print(f"APK generado (no firmado): {final_apk}")

    print("\nProceso completado.")


if __name__ == "__main__":
    main()
