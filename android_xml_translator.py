#!/usr/bin/env python3
"""
Android strings.xml Translator

This script translates Android string resources from a strings.xml file
to another language using free online translation services.
No API keys or authentication required.

Features:
- Respects translatable="false" attribute
- Handles string-array elements
- Preserves formatting placeholders like %s, %d, %1$s
- Preserves escape sequences like \n, \', \" 
- Preserves regex patterns
- Multiple fallback translation services for reliability
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
    
    return strings


def translate_text(text, source_lang, target_lang):
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
    
    # Find all matches and their positions
    for match in re.finditer(pattern, text):
        placeholders.append(match.group(0))
        placeholder_positions.append((match.start(), match.end()))
    
    # If no special sequences found, translate the whole text normally
    if not placeholders:
        return _perform_translation(text, source_lang, target_lang)
    
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
        translated_combined = _perform_translation(combined_text, source_lang, target_lang)
        
        # Split the translated result back into segments
        translated_texts = translated_combined.split(delimiter)
        
        # If we didn't get the same number of segments back, fall back to translating individually
        if len(translated_texts) != len(text_segments):
            translated_texts = [_perform_translation(segment, source_lang, target_lang) for segment in text_segments]
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
            # Use the original placeholder
            result += segment_value
    
    return result


def _perform_translation(text, source_lang, target_lang):
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
            "dt": "t",
            "q": text
        }
        
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
        
        # Extract translated text from response
        translation = ""
        for sentence in result[0]:
            if sentence[0]:
                translation += sentence[0]
        
        return translation
    
    except requests.exceptions.RequestException as e:
        print(f"Translation error: {e}")
        # Fallback to another service if the first one fails
        return _fallback_translate(text, source_lang, target_lang)


def _fallback_translate(text, source_lang, target_lang):
    """Fallback translation method using DeepL's free website (no API key)"""
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
    
    # Create filename for the translated file
    base_name = os.path.basename(original_file)
    dir_name = os.path.dirname(original_file)
    translated_file = os.path.join(dir_name, f"strings-{target_lang}.xml")
    
    # Write the translated XML
    tree.write(translated_file, encoding='utf-8', xml_declaration=True)
    return translated_file


def main():
    parser = argparse.ArgumentParser(description='Translate Android strings.xml to another language')
    parser.add_argument('input_file', help='Path to the original strings.xml file')
    parser.add_argument('source_lang', help='Source language code (e.g., en, fr, es)')
    parser.add_argument('target_lang', help='Target language code (e.g., fr, es, de)')
    parser.add_argument('--preserve', action='store_true', help='Preserve untranslated strings')
    args = parser.parse_args()
    
    if not os.path.isfile(args.input_file):
        print(f"Error: Input file '{args.input_file}' not found.")
        return
    
    print(f"Extracting strings from {args.input_file}...")
    strings = extract_strings(args.input_file)
    print(f"Found {len(strings)} translatable strings to process.")
    
    translated_strings = {}
    
    print(f"Translating from {args.source_lang} to {args.target_lang}...")
    
    # Counter for progress tracking
    total = len(strings)
    current = 0
    
    for key, text in strings.items():
        current += 1
        
        # Determine string type
        if key.startswith("string:"):
            name = key.split(":", 1)[1]
            print(f"Translating string ({current}/{total}): {name}")
        elif key.startswith("array:"):
            parts = key.split(":", 2)
            array_name = parts[1]
            item_index = parts[2]  
            print(f"Translating array item ({current}/{total}): {array_name}[{item_index}]")
        
        # Translate the text
        translated_text = translate_text(text, args.source_lang, args.target_lang)
        translated_strings[key] = translated_text
    
    # Create translated XML file
    output_file = create_translated_xml(args.input_file, translated_strings, args.target_lang)
    print(f"Translation completed! Translated file saved as: {output_file}")
    
    # Print summary
    string_count = len([k for k in strings.keys() if k.startswith("string:")])
    array_items_count = len([k for k in strings.keys() if k.startswith("array:")])
    array_count = len(set([k.split(":", 2)[1] for k in strings.keys() if k.startswith("array:")]))
    
    print("\nTranslation Summary:")
    print(f"- Regular strings: {string_count}")
    print(f"- String arrays: {array_count} (with {array_items_count} items)")
    print(f"- Total translated elements: {len(strings)}")


if __name__ == "__main__":
    main()