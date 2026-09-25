from __future__ import annotations

import json
import re

from thoth.adapters.parsers.common import safe_zip


def sniff_media_type(raw: bytes) -> str | None:
    prefix = raw.lstrip()[:200].lower()
    if re.match(
        rb"(?:<!doctype\s+html\b|<(?:html|head|body|div|article|section|main|table|p|h[1-6])(?:\s|>))",
        prefix,
    ):
        return "text/html"
    if raw.startswith(b"%PDF-"):
        return "application/pdf"
    if raw.startswith(b"PK\x03\x04"):
        with safe_zip(raw) as archive:
            names = set(archive.namelist())
            if "word/document.xml" in names:
                return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if "xl/workbook.xml" in names:
                return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if {"META-INF/container.xml", "Contents/header.xml"}.issubset(names):
                return "application/hwp+zip"
        return "application/zip"
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    try:
        json.loads(decoded)
    except json.JSONDecodeError:
        return "text/plain"
    return "application/json"
