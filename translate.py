"""
translate.py — strings.xml Auto Translator
Menggunakan Google Translate public endpoint (tanpa API key)
"""

import xml.etree.ElementTree as ET
import argparse
import requests
import time
import sys
import os

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
CHUNK_SIZE  = 30      # Jumlah string per batch
DELAY       = 0.4     # Detik antar request (hindari rate limit)
MAX_RETRY   = 3       # Percobaan ulang jika gagal
TIMEOUT     = 10      # Timeout request (detik)

# ──────────────────────────────────────────────
# GOOGLE TRANSLATE (public endpoint, tanpa key)
# ──────────────────────────────────────────────
def translate_text(text: str, target: str, source: str = "auto") -> str:
    if not text or not text.strip():
        return text

    url = "https://translate.googleapis.com/translate_a/single"
    params = {
        "client": "gtx",
        "sl": source,
        "tl": target,
        "dt": "t",
        "q": text,
    }

    for attempt in range(1, MAX_RETRY + 1):
        try:
            resp = requests.get(url, params=params, timeout=TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            if data and data[0]:
                parts = [seg[0] for seg in data[0] if seg and seg[0]]
                return "".join(parts)
        except Exception as e:
            print(f"    ⚠ Percobaan {attempt}/{MAX_RETRY} gagal: {e}", flush=True)
            if attempt < MAX_RETRY:
                time.sleep(1.5 * attempt)

    print(f"    ✗ Gagal menerjemahkan, pakai teks asli.", flush=True)
    return text  # fallback ke teks asli


# ──────────────────────────────────────────────
# PARSE strings.xml
# ──────────────────────────────────────────────
def parse_strings(path: str):
    tree = ET.parse(path)
    root = tree.getroot()
    items = []

    for el in root:
        name = el.get("name", "")
        translatable = el.get("translatable", "true").lower() != "false"

        if el.tag == "string":
            items.append({
                "type": "string",
                "name": name,
                "value": el.text or "",
                "translatable": translatable,
            })

        elif el.tag == "string-array":
            children = [i.text or "" for i in el.findall("item")]
            items.append({
                "type": "string-array",
                "name": name,
                "items": children,
                "translatable": translatable,
            })

        elif el.tag == "plurals":
            plural_items = {
                i.get("quantity"): i.text or ""
                for i in el.findall("item")
            }
            items.append({
                "type": "plurals",
                "name": name,
                "items": plural_items,
                "translatable": translatable,
            })

    return items


# ──────────────────────────────────────────────
# FLATTEN → terjemahkan → REBUILD
# ──────────────────────────────────────────────
def flatten(items):
    """Ubah semua string menjadi list (id, text) untuk batch."""
    flat = []
    for s in items:
        if not s["translatable"]:
            continue
        if s["type"] == "string":
            flat.append((s["name"], s["value"]))
        elif s["type"] == "string-array":
            for idx, text in enumerate(s["items"]):
                flat.append((f"{s['name']}[{idx}]", text))
        elif s["type"] == "plurals":
            for qty, text in s["items"].items():
                flat.append((f"{s['name']}[{qty}]", text))
    return flat


def translate_all(flat_items, target_lang):
    """Terjemahkan semua item, tampilkan progress."""
    result = {}
    total = len(flat_items)
    done  = 0

    print(f"\n📦 Total string: {total}", flush=True)

    for i in range(0, total, CHUNK_SIZE):
        chunk = flat_items[i : i + CHUNK_SIZE]
        chunk_no = i // CHUNK_SIZE + 1
        total_chunks = (total + CHUNK_SIZE - 1) // CHUNK_SIZE
        print(f"\n🔄 Batch {chunk_no}/{total_chunks} ({len(chunk)} strings)...", flush=True)

        for key, text in chunk:
            translated = translate_text(text, target=target_lang)
            result[key] = translated
            done += 1
            pct = int(done / total * 100)
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"  [{bar}] {pct:3d}%  {key[:40]}", flush=True)
            time.sleep(DELAY)

    return result


# ──────────────────────────────────────────────
# BUILD OUTPUT XML
# ──────────────────────────────────────────────
def escape_xml(text: str) -> str:
    if not text:
        return text
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    text = text.replace('"', "&quot;")
    return text


def build_xml(items, translated_map, target_lang) -> str:
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<!-- Translated to {target_lang} by github-actions auto-translator -->',
        "<resources>",
    ]

    for s in items:
        if s["type"] == "string":
            val = translated_map.get(s["name"], s["value"]) if s["translatable"] else s["value"]
            attr = '' if s["translatable"] else ' translatable="false"'
            lines.append(f'    <string name="{s["name"]}"{attr}>{escape_xml(val)}</string>')

        elif s["type"] == "string-array":
            lines.append(f'    <string-array name="{s["name"]}">')
            for idx, orig in enumerate(s["items"]):
                key = f"{s['name']}[{idx}]"
                val = translated_map.get(key, orig) if s["translatable"] else orig
                lines.append(f"        <item>{escape_xml(val)}</item>")
            lines.append("    </string-array>")

        elif s["type"] == "plurals":
            lines.append(f'    <plurals name="{s["name"]}">')
            for qty, orig in s["items"].items():
                key = f"{s['name']}[{qty}]"
                val = translated_map.get(key, orig) if s["translatable"] else orig
                lines.append(f'        <item quantity="{qty}">{escape_xml(val)}</item>')
            lines.append("    </plurals>")

    lines.append("</resources>")
    return "\n".join(lines)


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Translate strings.xml via Google Translate")
    parser.add_argument("--input",  required=True,  help="Path ke strings.xml sumber")
    parser.add_argument("--output", required=True,  help="Path output file hasil terjemahan")
    parser.add_argument("--lang",   required=True,  help="Kode bahasa tujuan, contoh: id, ja, fr")
    parser.add_argument("--source", default="auto", help="Kode bahasa sumber (default: auto)")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"❌ File tidak ditemukan: {args.input}")
        sys.exit(1)

    print(f"📂 Input  : {args.input}")
    print(f"🌐 Target : {args.lang}")
    print(f"💾 Output : {args.output}")

    print("\n⏳ Parsing XML...", flush=True)
    items = parse_strings(args.input)
    print(f"✓ Ditemukan {len(items)} elemen string", flush=True)

    flat = flatten(items)
    skipped = len(items) - len([s for s in items if s["translatable"]])
    print(f"✓ Akan diterjemahkan: {len(flat)} | Dilewati (translatable=false): {skipped}", flush=True)

    translated_map = translate_all(flat, args.lang)

    os.makedirs(os.path.dirname(args.output) if os.path.dirname(args.output) else ".", exist_ok=True)
    xml_result = build_xml(items, translated_map, args.lang)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(xml_result)

    print(f"\n✅ Selesai! File disimpan di: {args.output}", flush=True)


if __name__ == "__main__":
    main()
