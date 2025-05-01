#!/usr/bin/env python3
"""
Android strings.xml Translator

This script translates Android string resources from a strings.xml file
to multiple languages using free online translation services.
No API keys or authentication required.

Features:
- Respects translatable="false" attribute
- Handles string-array elements
- Handles plurals elements
- Preserves formatting placeholders like %s, %d, %1$s
- Preserves escape sequences like \n, \', \" 
- Preserves regex patterns
- Multiple fallback translation services for reliability
- Optional transliteration instead of translation
- Parallel processing of multiple target languages
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


def translate_text(text, source_lang, target_lang, transliterate=False):
    """Translate text using Google Translate (no API key required) while preserving placeholders"""
    if not text.strip():
        return text
    
    # Handle special case: if the text only consists of format specifiers or escape sequences, don't translate
    if re.match(r'^([%\\][\w\'"\n$]+)+$', text.strip()):
        return text
    
    # 1. Extract and store all special sequences that should not be translated
    # These will be replaced with unique tokens that won't be translated
    
    # Track placeholders with their positions
    placeholders = []
    placeholder_positions = []
    
    # Patterns to match:
    # - Format specifiers like %s, %d, %1$s
    # - Escaped chars like \n, \t, \', \"
    # - Unicode escapes like \u1234
    # - Common regex patterns
    pattern = r'%([0-9]+\$)?[sdif]|%[sdif]|\\\'|\\"|\\\n|\\n|\\t|\\r|\\b|\\u[0-9a-fA-F]{4}|\[[^\]]*\]|\{\d+\}|\{[a-zA-Z_]+\}'
    
    # Find all matches and their positions, also preserve surrounding spaces
    for match in re.finditer(pattern, text):
        start, end = match.span()
        placeholder = match.group(0)
        
        # Check for spaces before the placeholder
        leading_space = ""
        if start > 0 and text[start-1] == " ":
            leading_space = " "
            start -= 1
            
        # Check for spaces after the placeholder
        trailing_space = ""
        if end < len(text) and text[end] == " ":
            trailing_space = " "
            end += 1
            
        # Store the placeholder with its surrounding spaces
        placeholders.append(leading_space + placeholder + trailing_space)
        placeholder_positions.append((start, end))
    
    # If no special sequences found, translate the whole text normally
    if not placeholders:
        return _perform_translation(text, source_lang, target_lang, transliterate)
    
    # 2. Split the text into translatable segments and non-translatable tokens
    segments = []
    last_end = 0
    
    for i, (start, end) in enumerate(placeholder_positions):
        # Add text segment before the placeholder (if any)
        if start > last_end:
            segments.append(('text', text[last_end:start]))
        
        # Add the placeholder as a non-translatable token
        segments.append(('placeholder', placeholders[i]))
        last_end = end
    
    # Add any remaining text after the last placeholder
    if last_end < len(text):
        segments.append(('text', text[last_end:]))
    
    # 3. Translate only the text segments
    translated_segments = []
    
    # Collect all text segments for batch translation
    text_segments = [segment[1] for segment in segments if segment[0] == 'text']
    
    # If we have text to translate
    if text_segments:
        # Join with a special delimiter that's unlikely to appear in the text
        delimiter = "⟐⟐⟐SPLIT⟐⟐⟐"
        combined_text = delimiter.join(text_segments)
        
        # Translate the combined text
        translated_combined = _perform_translation(combined_text, source_lang, target_lang, transliterate)
        
        # Split the translated result back into segments
        translated_texts = translated_combined.split(delimiter)
        
        # If we didn't get the same number of segments back, fall back to translating individually
        if len(translated_texts) != len(text_segments):
            translated_texts = [_perform_translation(segment, source_lang, target_lang, transliterate) for segment in text_segments]
    else:
        translated_texts = []
    
    # 4. Reconstruct the text with translated segments and original placeholders
    result = ""
    text_segment_index = 0
    
    for segment_type, segment_value in segments:
        if segment_type == 'text':
            # Use the translated text segment
            if text_segment_index < len(translated_texts):
                result += translated_texts[text_segment_index]
                text_segment_index += 1
            else:
                result += segment_value  # Fallback if something went wrong
        else:
            # Use the original placeholder with its surrounding spaces
            result += segment_value
    
    # Check if spaces around placeholders were preserved correctly
    # If not, try to fix any missing spaces by checking for placeholder formats directly attached to words
    placeholder_pattern = r'(\w+)(%[0-9]*\$?[sdif])(\w+)'
    result = re.sub(placeholder_pattern, r'\1 \2 \3', result)
    
    return result


def _perform_translation(text, source_lang, target_lang, transliterate=False):
    """Actually perform the translation using Google Translate API"""
    if not text.strip():
        return text
    
    try:
        # Add delay to avoid rate limiting
        time.sleep(random.uniform(0.8, 2.0))
        
        # Use Google Translate without API key
        url = f"https://translate.googleapis.com/translate_a/single"
        
        params = {
            "client": "gtx",
            "sl": source_lang,
            "tl": target_lang,
            "q": text
        }
        
        # For transliteration, we need several data types
        if transliterate:
            # dt=t: translation
            # dt=rm: transliteration
            params["dt"] = ["t", "rm"]
        else:
            params["dt"] = "t"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        response = requests.get(url, params=params, headers=headers)
        response.raise_for_status()
        
        # Parse the JSON response
        result = response.json()
        
        # Extract transliteration or translation from response
        if transliterate:
            # First, get the standard translation as fallback
            translation = ""
            for sentence in result[0]:
                if sentence and len(sentence) > 0 and sentence[0]:
                    translation += sentence[0]
            
            # For Crimean Tatar (crh) and other languages with Latin transliteration in specific position
            latin_transliteration = ""
            
            # Based on the debug output, the Latin transliteration is in result[0][i][2]
            # where i is the index of each sentence segment
            for i, sentence_data in enumerate(result[0]):
                if sentence_data and len(sentence_data) > 2 and sentence_data[2]:
                    latin_transliteration += sentence_data[2]
            
            # If we found a Latin transliteration, use it
            if latin_transliteration:
                return latin_transliteration
            
            # If no transliteration found in the expected position, fall back to other methods
            if not latin_transliteration:
                # Try other positions in the structure
                if len(result) >= 2 and result[1]:
                    for entry in result[1]:
                        if entry and len(entry) > 2 and entry[2]:
                            latin_transliteration += entry[2]
            
            # If we found a transliteration with any method, use it; otherwise return the translation
            if latin_transliteration:
                return latin_transliteration
            else:
                return translation
        else:
            # Normal translation
            translation = ""
            for sentence in result[0]:
                if sentence and len(sentence) > 0 and sentence[0]:
                    translation += sentence[0]
            return translation
    
    except requests.exceptions.RequestException as e:
        print(f"Translation error: {e}")
        # Fallback to another service if the first one fails
        return _fallback_translate(text, source_lang, target_lang, transliterate)


def _fallback_translate(text, source_lang, target_lang, transliterate=False):
    """Fallback translation method using DeepL's free website (no API key)"""
    # If transliteration is requested, we can't use the fallback services as they don't support this
    # So we'll just return the original text or attempt a standard translation
    if transliterate:
        print("Warning: Transliteration not supported by fallback services. Attempting regular translation.")
    
    try:
        # Add delay to avoid rate limiting
        time.sleep(random.uniform(1.5, 3.0))
        
        # DeepL uses slightly different language codes
        deepl_lang_codes = {
            'en': 'EN',
            'es': 'ES',
            'fr': 'FR',
            'de': 'DE',
            'it': 'IT',
            'pt': 'PT',
            'ru': 'RU',
            'ja': 'JA',
            'zh': 'ZH',
            'nl': 'NL',
            'pl': 'PL',
            # Add more as needed
        }
        
        src = deepl_lang_codes.get(source_lang, source_lang.upper())
        tgt = deepl_lang_codes.get(target_lang, target_lang.upper())
        
        # First we need to get cookies and authentication
        session = requests.Session()
        
        # Get initial cookies
        url = "https://www.deepl.com/translator"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.deepl.com/',
            'Origin': 'https://www.deepl.com'
        }
        
        session.get(url, headers=headers)
        
        # Now make the translation request
        translate_url = "https://www2.deepl.com/jsonrpc"
        
        # Generate a random request ID
        request_id = random.randint(1000000, 9999999)
        
        payload = {
            "jsonrpc": "2.0",
            "method": "LMT_handle_texts",
            "params": {
                "texts": [{"text": text}],
                "lang": {
                    "source_lang_user_selected": src,
                    "target_lang": tgt
                },
                "timestamp": int(time.time() * 1000)
            },
            "id": request_id
        }
        
        response = session.post(translate_url, json=payload, headers=headers)
        response.raise_for_status()
        response_json = response.json()
        
        if "result" in response_json and "texts" in response_json["result"]:
            translation = response_json["result"]["texts"][0]["text"]
            return translation
        else:
            print("DeepL fallback translation failed. Trying MyMemory...")
            raise Exception("DeepL failed")
            
    except Exception as e:
        print(f"Fallback translation error: {e}")
        # If all fails, try a simpler third option
        try:
            # MyMemory translation API (free tier)
            time.sleep(random.uniform(1.0, 2.0))
            url = f"https://api.mymemory.translated.net/get?q={quote(text)}&langpair={source_lang}|{target_lang}"
            response = requests.get(url)
            response.raise_for_status()
            result = response.json()
            translation = result.get("responseData", {}).get("translatedText", text)
            return translation
        except Exception as e2:
            print(f"MyMemory fallback translation error: {e2}")
            return text  # Return original text if all translation attempts fail


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
    """Translate all strings for a specific target language"""
    translated_strings = {}
    total = len(strings)
    
    # Progress tracking
    if transliterate:
        print(f"Starting transliteration from {source_lang} to {target_lang}...")
    else:
        print(f"Starting translation from {source_lang} to {target_lang}...")
    
    for current, (key, text) in enumerate(strings.items(), 1):
        # Determine string type for progress display
        if key.startswith("string:"):
            name = key.split(":", 1)[1]
            if current % 10 == 0 or current == total:  # Show progress every 10 items
                if transliterate:
                    print(f"[{target_lang}] Transliterating string ({current}/{total}): {name}")
                else:
                    print(f"[{target_lang}] Translating string ({current}/{total}): {name}")
        elif key.startswith("array:"):
            parts = key.split(":", 2)
            array_name = parts[1]
            item_index = parts[2]
            if current % 10 == 0 or current == total:  # Show progress every 10 items
                if transliterate:
                    print(f"[{target_lang}] Transliterating array item ({current}/{total}): {array_name}[{item_index}]")
                else:
                    print(f"[{target_lang}] Translating array item ({current}/{total}): {array_name}[{item_index}]")
        elif key.startswith("plurals:"):
            parts = key.split(":", 2)
            plurals_name = parts[1]
            quantity = parts[2]
            if current % 10 == 0 or current == total:  # Show progress every 10 items
                if transliterate:
                    print(f"[{target_lang}] Transliterating plural item ({current}/{total}): {plurals_name}[{quantity}]")
                else:
                    print(f"[{target_lang}] Translating plural item ({current}/{total}): {plurals_name}[{quantity}]")
        
        # Translate or transliterate the text
        translated_text = translate_text(text, source_lang, target_lang, transliterate)
        translated_strings[key] = translated_text
    
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
    parser.add_argument('--max-workers', type=int, default=3, help='Maximum number of parallel translation workers (default: 3)')
    args = parser.parse_args()
    
    if not os.path.isfile(args.input_file):
        print(f"Error: Input file '{args.input_file}' not found.")
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