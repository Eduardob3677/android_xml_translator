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


def find_base_strings_xml(decompiled_dir: Path) -> Path:
    res_dir = decompiled_dir / "res"
    # Preferido: res/values/strings.xml
    candidate = res_dir / "values" / "strings.xml"
    if candidate.exists():
        return candidate
    # Fallback: primera coincidencia en cualquier values*/strings.xml
    for p in sorted(res_dir.glob("values*/strings.xml")):
        return p
    raise FileNotFoundError("No se encontró res/values/strings.xml en el APK decompilado.")


def translate_into_dirs(strings_xml: Path, source_lang: str, target_langs: List[str], translator_args: List[str]):
    """Ejecuta el traductor por cada idioma y mueve salida a values-*/strings.xml"""
    for lang in target_langs:
        print(f"==> Traduciendo a {lang}...")
        # Llamar al script traductor
        run([
            sys.executable,
            str(TRANSLATOR_SCRIPT),
            str(strings_xml),
            source_lang,
            lang,
            *translator_args,
        ])

        # El traductor genera strings-<lang>.xml junto al archivo original
        generated = strings_xml.with_name(f"strings-{lang}.xml")
        if not generated.exists():
            raise RuntimeError(f"No se generó el archivo esperado: {generated}")

        # Mover a res/values-<lang>/strings.xml
        values_dir_name = lang_to_values_dir(lang)
        target_dir = strings_xml.parent.parent / values_dir_name
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / "strings.xml"

        # Sobrescribir si existe
        shutil.move(str(generated), str(target_file))
        print(f"✓ {lang}: {target_file}")


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
    base_strings = find_base_strings_xml(decompiled_dir)
    print(f"Archivo base: {base_strings}")

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

    translate_into_dirs(base_strings, args.source_lang, args.target_langs, forward_args)

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
