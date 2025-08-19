#!/usr/bin/env python3
"""
Android strings.xml Translator

#Este script traduce recursos de texto de Android desde un archivo strings.xml
usando la API de Microsoft Translator (Azure AI Translator).

Características:
- Respeta el atributo translatable="false"
- Soporta elementos string-array
- Soporta elementos plurals
- Preserva placeholders de formato como %s, %d, %1$s
- Preserva secuencias de escape como \n, \", \"
- Preserva patrones regex comunes
- Soporta transliteración usando toScript=Latn cuando se solicita
- Procesamiento en paralelo de múltiples idiomas destino

Requisitos de configuración (CLI o variables de entorno):
- Clave: --ms-key o AZURE_TRANSLATOR_KEY
- Región (si aplica): --ms-region o AZURE_TRANSLATOR_REGION
- Endpoint: --ms-endpoint o AZURE_TRANSLATOR_ENDPOINT (por defecto: https://api.cognitive.microsofttranslator.com)
- Versión API: --ms-api-version (por defecto: 3.0)
- Categoría personalizada: --ms-category o AZURE_TRANSLATOR_CATEGORY (opcional)
"""

import os
import re
import argparse
import html
import time
import random
import requests
import json
import xml.etree.ElementTree as ET
from urllib.parse import quote
import threading
import concurrent.futures

# Configuración global de Microsoft Translator. Se inicializa en main()
MS_TRANSLATOR_CONFIG = {
    "endpoint": os.getenv("AZURE_TRANSLATOR_ENDPOINT", "https://api.cognitive.microsofttranslator.com"),
    "key": os.getenv("AZURE_TRANSLATOR_KEY"),
    "region": os.getenv("AZURE_TRANSLATOR_REGION"),
    "api_version": "3.0",
    "category": os.getenv("AZURE_TRANSLATOR_CATEGORY"),
    "text_type": "plain",
}

def extract_strings(xml_file):
    """Extract strings from an Android strings.xml file"""
    tree = ET.parse(xml_file)
    root = tree.getroot()
    
    strings = {}
    
    # Extract regular string elements
    for string_elem in root.findall("string"):
        name = string_elem.get("name")
        translatable = string_elem.get("translatable", "true").lower()
        
        if name and string_elem.text and translatable != "false":
            strings[f"string:{name}"] = string_elem.text
    
    # Extract string-array elements
    for array_elem in root.findall("string-array"):
        array_name = array_elem.get("name")
        translatable = array_elem.get("translatable", "true").lower()
        
        if array_name and translatable != "false":
            for i, item_elem in enumerate(array_elem.findall("item")):
                if item_elem.text:
                    strings[f"array:{array_name}:{i}"] = item_elem.text
    
    # Extract plurals elements
    for plurals_elem in root.findall("plurals"):
        plurals_name = plurals_elem.get("name")
        translatable = plurals_elem.get("translatable", "true").lower()
        
        if plurals_name and translatable != "false":
            for item_elem in plurals_elem.findall("item"):
                quantity = item_elem.get("quantity")
                if quantity and item_elem.text:
                    strings[f"plurals:{plurals_name}:{quantity}"] = item_elem.text
    
    return strings


def translate_text(text, source_lang, target_lang, transliterate=False, batch_mode=False):
    """Traduce texto usando Microsoft Translator preservando placeholders. Optimizado para endpoint privado."""
    if not text.strip():
        return text

    # Si el texto solo tiene placeholders/escapes, no traducir
    if re.match(r'^([%\\][\w\'"\n$]+)+$', text.strip()):
        return text

    # Extraer placeholders
    placeholders = []
    placeholder_positions = []
    pattern = r'%([0-9]+\$)?[sdif]|%[sdif]|\\'|\\"|\\\n|\\n|\\t|\\r|\\b|\\u[0-9a-fA-F]{4}|\[[^\]]*\]|\{\d+\}|\{[a-zA-Z_]+\}'
    for match in re.finditer(pattern, text):
        start, end = match.span()
        placeholder = match.group(0)
        leading_space = ""
        if start > 0 and text[start-1] == " ":
            leading_space = " "
            start -= 1
        trailing_space = ""
        if end < len(text) and text[end] == " ":
            trailing_space = " "
            end += 1
        placeholders.append(leading_space + placeholder + trailing_space)
        placeholder_positions.append((start, end))

    if not placeholders:
        return _perform_translation(text, source_lang, target_lang, transliterate)

    # Dividir en segmentos traducibles y no traducibles
    segments = []
    last_end = 0
    for i, (start, end) in enumerate(placeholder_positions):
        if start > last_end:
            segments.append(('text', text[last_end:start]))
        segments.append(('placeholder', placeholders[i]))
        last_end = end
    if last_end < len(text):
        segments.append(('text', text[last_end:]))

    text_segments = [segment[1] for segment in segments if segment[0] == 'text']

    # Batch mode: traduce hasta 25 segmentos en una sola llamada
    if batch_mode and text_segments:
        batch_size = 25
        translated_texts = []
        for i in range(0, len(text_segments), batch_size):
            batch = text_segments[i:i+batch_size]
            batch_translated = _perform_translation(batch, source_lang, target_lang, transliterate, batch_mode=True)
            translated_texts.extend(batch_translated)
    elif text_segments:
        # Traducir todos los segmentos juntos (como antes)
        delimiter = "⟐⟐⟐SPLIT⟐⟐⟐"
        combined_text = delimiter.join(text_segments)
        translated_combined = _perform_translation(combined_text, source_lang, target_lang, transliterate)
        translated_texts = translated_combined.split(delimiter)
        if len(translated_texts) != len(text_segments):
            translated_texts = [
                _perform_translation(segment, source_lang, target_lang, transliterate)
                for segment in text_segments
            ]
    else:
        translated_texts = []

    # Reconstruir el texto
    result = ""
    text_segment_index = 0
    for segment_type, segment_value in segments:
        if segment_type == 'text':
            if text_segment_index < len(translated_texts):
                result += translated_texts[text_segment_index]
                text_segment_index += 1
            else:
                result += segment_value
        else:
            result += segment_value
    placeholder_pattern = r'(\w+)(%[0-9]*\$?[sdif])(\w+)'
    result = re.sub(placeholder_pattern, r'\1 \2 \3', result)
    return result


def _perform_translation(text_or_batch, source_lang, target_lang, transliterate=False, batch_mode=False):
    """Realiza la traducción usando Microsoft Translator API. Soporta batch si batch_mode=True."""
    if batch_mode:
        # text_or_batch es una lista de textos
        texts = text_or_batch
    else:
        if not text_or_batch.strip():
            return text_or_batch
        texts = [text_or_batch]

    endpoint = MS_TRANSLATOR_CONFIG.get("endpoint")
    key = MS_TRANSLATOR_CONFIG.get("key")
    region = MS_TRANSLATOR_CONFIG.get("region")
    api_version = MS_TRANSLATOR_CONFIG.get("api_version", "3.0")
    category = MS_TRANSLATOR_CONFIG.get("category")
    text_type = MS_TRANSLATOR_CONFIG.get("text_type", "plain")

    if not key:
        raise RuntimeError("Falta la clave de Microsoft Translator. Usa --ms-key o AZURE_TRANSLATOR_KEY.")

    url = endpoint.rstrip('/') + "/translate"

    params = {
        "api-version": api_version,
        "from": source_lang,
        "to": target_lang,
        "textType": text_type,
    }
    if category:
        params["category"] = category
    if transliterate:
        params["toScript"] = "Latn"

    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/json",
    }
    if region:
        headers["Ocp-Apim-Subscription-Region"] = region

    body = [{"text": t} for t in texts]

    attempts = 0
    backoff = 0.5
    while attempts < 3:
        attempts += 1
        try:
            resp = requests.post(url, params=params, headers=headers, json=body, timeout=30)
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(backoff + random.uniform(0, 0.3))
                backoff *= 2
                continue
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or not data:
                return [t for t in texts] if batch_mode else texts[0]
            results = []
            for i, item in enumerate(data):
                translations = item.get("translations", [])
                if not translations:
                    results.append(texts[i])
                    continue
                t0 = translations[0]
                if transliterate:
                    translit_obj = t0.get("transliteration")
                    if translit_obj and translit_obj.get("text"):
                        results.append(translit_obj["text"])
                        continue
                results.append(t0.get("text", texts[i]))
            return results if batch_mode else results[0]
        except requests.exceptions.RequestException as e:
            if attempts >= 3:
                print(f"Translation error after retries: {e}")
                return [t for t in texts] if batch_mode else texts[0]
            time.sleep(backoff + random.uniform(0, 0.2))


def _fallback_translate(text, source_lang, target_lang, transliterate=False):
    """Obsoleto: ya no se usan servicios de respaldo externos."""
    return text


def create_translated_xml(original_file, strings_dict, target_lang):
    """Create a new XML file with translated strings"""
    tree = ET.parse(original_file)
    root = tree.getroot()
    
    # Track string-arrays to update
    arrays_updated = set()
    
    # Track plurals to update
    plurals_updated = set()
    
    # Update regular strings
    for string_elem in root.findall("string"):
        name = string_elem.get("name")
        key = f"string:{name}"
        
        if key in strings_dict:
            string_elem.text = strings_dict[key]
    
    # Update string-arrays
    for array_elem in root.findall("string-array"):
        array_name = array_elem.get("name")
        
        # Check if this array has any translated items
        array_has_translations = False
        for i, item_elem in enumerate(array_elem.findall("item")):
            key = f"array:{array_name}:{i}"
            if key in strings_dict:
                array_has_translations = True
                break
                
        if array_has_translations:
            arrays_updated.add(array_name)
            # Update the items
            for i, item_elem in enumerate(array_elem.findall("item")):
                key = f"array:{array_name}:{i}"
                if key in strings_dict:
                    item_elem.text = strings_dict[key]
    
    # Update plurals
    for plurals_elem in root.findall("plurals"):
        plurals_name = plurals_elem.get("name")
        
        # Check if this plural has any translated items
        plurals_has_translations = False
        for item_elem in plurals_elem.findall("item"):
            quantity = item_elem.get("quantity")
            key = f"plurals:{plurals_name}:{quantity}"
            if key in strings_dict:
                plurals_has_translations = True
                break
                
        if plurals_has_translations:
            plurals_updated.add(plurals_name)
            # Update the items
            for item_elem in plurals_elem.findall("item"):
                quantity = item_elem.get("quantity")
                key = f"plurals:{plurals_name}:{quantity}"
                if key in strings_dict:
                    item_elem.text = strings_dict[key]
    
    # Create filename for the translated file
    base_name = os.path.basename(original_file)
    dir_name = os.path.dirname(original_file)
    translated_file = os.path.join(dir_name, f"strings-{target_lang}.xml")
    
    # Write the translated XML
    tree.write(translated_file, encoding='utf-8', xml_declaration=True)
    return translated_file


def translate_strings_for_language(strings, source_lang, target_lang, transliterate=False):
    """Traduce todos los strings para un idioma destino usando batch y concurrencia máxima."""
    translated_strings = {}
    total = len(strings)

    print(f"Starting {'transliteration' if transliterate else 'translation'} from {source_lang} to {target_lang} with batch mode and high concurrency...")

    # Agrupar por lotes de 25 para batch
    items = list(strings.items())
    batch_size = 25
    for i in range(0, len(items), batch_size):
        batch = items[i:i+batch_size]
        keys = [k for k, _ in batch]
        texts = [t for _, t in batch]
        translated_batch = [
            translate_text(text, source_lang, target_lang, transliterate, batch_mode=True)
            for text in texts
        ]
        # Si translate_text devuelve lista, tomar el primer elemento (por compatibilidad)
        for idx, k in enumerate(keys):
            tb = translated_batch[idx]
            if isinstance(tb, list):
                translated_strings[k] = tb[0]
            else:
                translated_strings[k] = tb
    return translated_strings

def process_language(input_file, source_lang, target_lang, strings, transliterate=False):
    """Process a single target language"""
    # Translate all strings for this language
    translated_strings = translate_strings_for_language(strings, source_lang, target_lang, transliterate)
    
    # Create translated XML file
    output_file_suffix = "translit-" + target_lang if transliterate else target_lang
    output_file = create_translated_xml(input_file, translated_strings, output_file_suffix)
    
    # Print completion message
    if transliterate:
        print(f"✓ Transliteration to {target_lang} completed! File saved as: {output_file}")
    else:
        print(f"✓ Translation to {target_lang} completed! File saved as: {output_file}")
    
    # Return statistics
    string_count = len([k for k in strings.keys() if k.startswith("string:")])
    array_items_count = len([k for k in strings.keys() if k.startswith("array:")])
    array_count = len(set([k.split(":", 2)[1] for k in strings.keys() if k.startswith("array:")]))
    plurals_items_count = len([k for k in strings.keys() if k.startswith("plurals:")])
    plurals_count = len(set([k.split(":", 2)[1] for k in strings.keys() if k.startswith("plurals:")]))
    
    return {
        "target_lang": target_lang,
        "string_count": string_count,
        "array_count": array_count,
        "array_items_count": array_items_count,
        "plurals_count": plurals_count,
        "plurals_items_count": plurals_items_count,
        "total_elements": len(strings),
        "output_file": output_file
    }

def main():
    parser = argparse.ArgumentParser(description='Translate Android strings.xml to multiple languages')
    parser.add_argument('input_file', help='Path to the original strings.xml file')
    parser.add_argument('source_lang', help='Source language code (e.g., en)')
    parser.add_argument('target_langs', nargs='+', help='One or more target language codes (e.g., fr es de)')
    parser.add_argument('--preserve', action='store_true', help='Preserve untranslated strings')
    parser.add_argument('--transliterate', action='store_true', help='Use transliteration instead of translation')
    parser.add_argument('--max-workers', type=int, default=10, help='Maximum number of parallel translation workers (default: 10, recommended for private endpoint)')
    parser.add_argument('--config', help='Path to a JSON config file with Microsoft Translator settings')
    # Parámetros Microsoft Translator
    parser.add_argument('--ms-endpoint', default=os.getenv('AZURE_TRANSLATOR_ENDPOINT', 'https://api.cognitive.microsofttranslator.com'), help='Microsoft Translator endpoint URL')
    parser.add_argument('--ms-key', default=os.getenv('AZURE_TRANSLATOR_KEY'), help='Microsoft Translator subscription key')
    parser.add_argument('--ms-region', default=os.getenv('AZURE_TRANSLATOR_REGION'), help='Microsoft Translator region (si aplica)')
    parser.add_argument('--ms-api-version', default=os.getenv('AZURE_TRANSLATOR_API_VERSION', '3.0'), help='Microsoft Translator API version (default: 3.0)')
    parser.add_argument('--ms-category', default=os.getenv('AZURE_TRANSLATOR_CATEGORY'), help='Custom category for custom translator (optional)')
    parser.add_argument('--ms-text-type', default=os.getenv('AZURE_TRANSLATOR_TEXT_TYPE', 'plain'), choices=['plain','html'], help='Text type for translation (plain or html)')
    args = parser.parse_args()
    
    if not os.path.isfile(args.input_file):
        print(f"Error: Input file '{args.input_file}' not found.")
        return
    
    # Inicializar configuración global de Translator con precedencia:
    # defaults < config file < environment < CLI
    defaults = {
        "endpoint": 'https://api.cognitive.microsofttranslator.com',
        "api_version": '3.0',
        "text_type": 'plain',
        "key": None,
        "region": None,
        "category": None,
    }

    # Cargar config desde archivo si se proporciona
    file_cfg = {}
    if args.config:
        try:
            with open(args.config, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    # normalizar claves esperadas
                    file_cfg = {
                        "endpoint": loaded.get("endpoint"),
                        "key": loaded.get("key"),
                        "region": loaded.get("region"),
                        "api_version": loaded.get("api_version"),
                        "category": loaded.get("category"),
                        "text_type": loaded.get("text_type"),
                    }
        except Exception as e:
            print(f"Warning: No se pudo leer el archivo de configuración: {e}")

    # Config desde entorno (ya accesible vía os.getenv pero lo hacemos explícito)
    env_cfg = {
        "endpoint": os.getenv('AZURE_TRANSLATOR_ENDPOINT'),
        "key": os.getenv('AZURE_TRANSLATOR_KEY'),
        "region": os.getenv('AZURE_TRANSLATOR_REGION'),
        "api_version": os.getenv('AZURE_TRANSLATOR_API_VERSION'),
        "category": os.getenv('AZURE_TRANSLATOR_CATEGORY'),
        "text_type": os.getenv('AZURE_TRANSLATOR_TEXT_TYPE'),
    }

    # Config desde CLI
    cli_cfg = {
        "endpoint": args.ms_endpoint,
        "key": args.ms_key,
        "region": args.ms_region,
        "api_version": args.ms_api_version,
        "category": args.ms_category,
        "text_type": args.ms_text_type,
    }

    # Función de merge que prefiere valores no vacíos del dict2 sobre dict1
    def merge(a, b):
        out = dict(a)
        for k, v in b.items():
            if v is not None and v != '':
                out[k] = v
        return out

    merged = merge(defaults, file_cfg)
    merged = merge(merged, env_cfg)
    merged = merge(merged, cli_cfg)

    MS_TRANSLATOR_CONFIG.update(merged)

    if not MS_TRANSLATOR_CONFIG.get("key"):
        print("Error: Debes proporcionar la clave de Microsoft Translator con --ms-key o AZURE_TRANSLATOR_KEY.")
        return

    print(f"Extracting strings from {args.input_file}...")
    strings = extract_strings(args.input_file)
    print(f"Found {len(strings)} translatable strings to process.")
    
    # Show summary of work to be done
    print(f"\nPreparing to process {len(args.target_langs)} target languages:")
    for lang in args.target_langs:
        if args.transliterate:
            print(f"- Transliterating from {args.source_lang} to {lang}")
        else:
            print(f"- Translating from {args.source_lang} to {lang}")
    
    print("\nStarting parallel processing...")
    
    # Create a thread pool executor
    max_workers = min(args.max_workers, len(args.target_langs))
    results = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit tasks for each target language
        future_to_lang = {
            executor.submit(
                process_language, 
                args.input_file, 
                args.source_lang, 
                target_lang, 
                strings, 
                args.transliterate
            ): target_lang for target_lang in args.target_langs
        }
        
        # Process results as they complete
        for future in concurrent.futures.as_completed(future_to_lang):
            target_lang = future_to_lang[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                print(f"Error processing {target_lang}: {e}")
    
    # Print final summary
    print("\n=== Translation Summary ===")
    for result in sorted(results, key=lambda x: x["target_lang"]):
        lang = result["target_lang"]
        print(f"\n{lang.upper()} ({result['output_file']}):")
        print(f"- Regular strings: {result['string_count']}")
        print(f"- String arrays: {result['array_count']} (with {result['array_items_count']} items)")
        print(f"- Plurals: {result['plurals_count']} (with {result['plurals_items_count']} items)")
        print(f"- Total processed elements: {result['total_elements']}")
    
    print("\nAll translations completed successfully!")


if __name__ == "__main__":
    main()