"""Build a local search index over the user's synced document folder
(path set via PAPERWORK_SOURCE_DIR in .env).

Extracts text (pdftotext for PDFs, stdlib zipfile for docx), then classifies
each document with a local Ollama model — content never reaches Claude's API
for this bulk pass, only the resulting index (filename, doc type, one-line
summary, suggested folder) does. Sensitive content (SSNs, passport scans,
mortgage docs) stays on this machine.

Usage:
    python scripts/paperwork_index.py build [--model phi4:14b] [--force]

Incremental by default: files unchanged since the last run (same size+mtime)
are skipped. --force reprocesses everything.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import zipfile

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")

import httpx

ROOT = os.environ.get("CONCIERGE_HOME") or os.getcwd()
ENV_PATH = os.path.join(ROOT, ".env")


def _load_env() -> None:
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_env()

SOURCE_DIR = os.environ.get("PAPERWORK_SOURCE_DIR", "")
INDEX_DIR = os.path.join(ROOT, "data", "paperwork")
TEXT_DIR = os.path.join(INDEX_DIR, "text")
INDEX_PATH = os.path.join(INDEX_DIR, "index.json")

OLLAMA_URL = "http://localhost:11434/api/chat"
DEFAULT_MODEL = "gpt-oss:20b"
REQUEST_TIMEOUT = 120.0

TAXONOMY = [
    "Career", "Finances", "Home", "Automotive", "Family",
    "Projects", "Journal", "Health", "Shopping", "Reading", "Uncategorized",
]

MAX_CHARS_FOR_CLASSIFY = 4000

CLASSIFY_SYSTEM = (
    "You classify personal documents. Given extracted document text, respond "
    "with ONLY a JSON object, no other text: "
    '{"doc_type": "<short type, e.g. \'lease agreement\', \'tax return\', '
    '\'insurance card\'>", "summary": "<one line, <120 chars, include names/'
    'dates/addresses if present>", "suggested_folder": "<one of: '
    + ", ".join(TAXONOMY) + ">\"}"
)


_docling_converter = None


def _get_docling_converter():
    global _docling_converter
    if _docling_converter is None:
        from docling.document_converter import DocumentConverter
        _docling_converter = DocumentConverter()
    return _docling_converter


def extract_pdf(path: str) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", "-layout", path, "-"],
            capture_output=True, timeout=60,
        )
        text = result.stdout.decode("utf-8", errors="replace")
    except (subprocess.SubprocessError, OSError) as e:
        return f"[extraction failed: {e}]"

    if len(text.strip()) >= 20:
        return text

    print(f"  no text layer, trying docling OCR: {os.path.basename(path)}")
    try:
        converter = _get_docling_converter()
        return converter.convert(path).document.export_to_markdown()
    except Exception as e:
        return text or f"[extraction failed: {e}]"


def extract_image(path: str) -> str:
    print(f"  running docling OCR: {os.path.basename(path)}")
    try:
        converter = _get_docling_converter()
        return converter.convert(path).document.export_to_markdown()
    except Exception as e:
        return f"[extraction failed: {e}]"


def extract_docx(path: str) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
        text = re.sub(r"<[^>]+>", " ", xml)
        return re.sub(r"\s+", " ", text).strip()
    except (zipfile.BadZipFile, KeyError, OSError) as e:
        return f"[extraction failed: {e}]"


def extract_text(path: str, ext: str) -> str | None:
    if ext == ".pdf":
        return extract_pdf(path)
    if ext == ".docx":
        return extract_docx(path)
    if ext == ".txt":
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None
    if ext in (".jpg", ".jpeg", ".png"):
        return extract_image(path)
    return None  # zips: no text extraction in phase 1


def classify(text: str, model: str) -> dict:
    prompt = text[:MAX_CHARS_FOR_CLASSIFY]
    try:
        resp = httpx.post(
            OLLAMA_URL,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": CLASSIFY_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "format": "json",
                # gpt-oss ignores think=False and needs an effort level instead.
                "think": "low" if model.startswith("gpt-oss") else False,
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        content = resp.json().get("message", {}).get("content", "{}")
        data = json.loads(content)
        if data.get("suggested_folder") not in TAXONOMY:
            data["suggested_folder"] = "Uncategorized"
        return data
    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as e:
        return {"doc_type": "unknown", "summary": f"[classification failed: {e}]",
                "suggested_folder": "Uncategorized"}


def safe_name(filename: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", filename)


def build(model: str, force: bool) -> None:
    os.makedirs(TEXT_DIR, exist_ok=True)

    existing: dict[str, dict] = {}
    if os.path.exists(INDEX_PATH) and not force:
        with open(INDEX_PATH, encoding="utf-8") as f:
            for rec in json.load(f):
                existing[rec["filename"]] = rec

    entries = sorted(os.listdir(SOURCE_DIR))
    records = []
    total = len(entries)
    for i, filename in enumerate(entries, 1):
        if filename.startswith("."):
            continue
        path = os.path.join(SOURCE_DIR, filename)
        if not os.path.isfile(path):
            continue

        stat = os.stat(path)
        prior = existing.get(filename)
        if prior and prior.get("size") == stat.st_size and prior.get("mtime") == stat.st_mtime:
            failed = prior.get("doc_type") == "unknown" or "failed" in (prior.get("summary") or "")
            text_path = prior.get("text_path")
            if failed and text_path and os.path.exists(text_path):
                print(f"[{i}/{total}] retrying classification: {filename}")
                with open(text_path, encoding="utf-8") as f:
                    text = f.read()
                record = dict(prior)
                record.update(classify(text, model))
                records.append(record)
                with open(INDEX_PATH, "w", encoding="utf-8") as f:
                    json.dump(records, f, indent=2)
                continue
            records.append(prior)
            print(f"[{i}/{total}] skip (unchanged): {filename}")
            with open(INDEX_PATH, "w", encoding="utf-8") as f:
                json.dump(records, f, indent=2)
            continue

        ext = os.path.splitext(filename)[1].lower()
        text = extract_text(path, ext)
        has_content = bool(text and not text.startswith("[extraction failed") and len(text.strip()) >= 20)

        record = {
            "filename": filename,
            "path": path,
            "ext": ext,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "has_text": has_content,
        }

        if has_content:
            text_path = os.path.join(TEXT_DIR, safe_name(filename) + ".txt")
            with open(text_path, "w", encoding="utf-8") as f:
                f.write(text)
            record["text_path"] = text_path
            print(f"[{i}/{total}] classifying: {filename}")
            record.update(classify(text, model))
        else:
            record["doc_type"] = "no text layer (likely scanned image, needs OCR)"
            record["summary"] = ""
            record["suggested_folder"] = "Uncategorized"
            print(f"[{i}/{total}] no usable text extracted: {filename}")

        records.append(record)

        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2)

    print(f"\nDone. {len(records)} files indexed -> {INDEX_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build_p = sub.add_parser("build", help="Build/refresh the document index")
    build_p.add_argument("--model", default=DEFAULT_MODEL, help=f"Ollama model tag (default: {DEFAULT_MODEL})")
    build_p.add_argument("--force", action="store_true", help="Reprocess all files, ignoring cache")
    args = parser.parse_args()

    if args.command == "build":
        if not SOURCE_DIR:
            print("ERROR: PAPERWORK_SOURCE_DIR not set in .env", file=sys.stderr)
            sys.exit(1)
        if not os.path.isdir(SOURCE_DIR):
            print(f"ERROR: source folder not found: {SOURCE_DIR}", file=sys.stderr)
            sys.exit(1)
        build(args.model, args.force)


if __name__ == "__main__":
    main()
