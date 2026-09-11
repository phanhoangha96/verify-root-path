"""Extract file text the same way as eoffice-solr TikaAnalysis.extractContentUsingParser."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

# Java BodyContentHandler default write limit (Tika 1.x–3.x).
DEFAULT_WRITE_LIMIT = 100000


class TikaExtractError(Exception):
    pass


def clip_content(text: str, write_limit: int) -> str:
    if write_limit > 0 and len(text) > write_limit:
        return text[:write_limit]
    return text


def extract_content(
    data: bytes,
    filename: str = "",
    *,
    tika_app_jar: str = "",
    write_limit: int = DEFAULT_WRITE_LIMIT,
) -> str:
    if not data:
        return ""
    jar = (tika_app_jar or os.getenv("TIKA_APP_JAR") or "").strip()
    if jar:
        text = _extract_with_jar(data, filename, jar)
    else:
        text = _extract_with_tika_python(data, filename)
    return clip_content(text, write_limit)


def _extract_with_jar(data: bytes, filename: str, jar: str) -> str:
    jar_path = Path(jar)
    if not jar_path.is_file():
        raise TikaExtractError(f"TIKA_APP_JAR not found: {jar}")
    suffix = Path(filename).suffix if filename else ""
    tmp = tempfile.NamedTemporaryFile(prefix="solr-file-", suffix=suffix, delete=False)
    try:
        tmp.write(data)
        tmp.close()
        proc = subprocess.run(
            ["java", "-jar", str(jar_path), "-t", tmp.name],
            check=False,
            capture_output=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout).decode("utf-8", errors="replace")[:500]
            raise TikaExtractError(f"tika-app exit {proc.returncode}: {err}")
        return proc.stdout.decode("utf-8", errors="replace")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _extract_with_tika_python(data: bytes, filename: str) -> str:
    try:
        from tika import parser
    except ImportError as ex:
        raise TikaExtractError(
            "Apache Tika is required. pip install tika (needs Java) or set TIKA_APP_JAR"
        ) from ex
    parsed = parser.from_buffer(data, xmlContent=False, requestOptions={"timeout": 120})
    if not isinstance(parsed, dict):
        raise TikaExtractError(f"unexpected tika response for {filename or 'buffer'}")
    content = parsed.get("content")
    return content if isinstance(content, str) else ""
