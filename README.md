# Android strings.xml Translator
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

Usage:
```
python3 android_xml_translator.py strings.xml en uk pl ja
```
