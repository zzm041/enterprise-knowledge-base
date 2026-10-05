import hashlib
import re

from ekb.errors import KBError


def normalize(filename: str, text: str) -> str:
    if not filename.lower().endswith((".txt", ".md")):
        raise KBError("KB.UNSUPPORTED_FORMAT", 422)
    if len(text.encode("utf-8")) > 200_000 or any(
        ord(c) < 32 and c not in "\n\r\t" for c in text
    ):
        raise KBError("KB.INVALID_TEXT", 422)
    result = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not result:
        raise KBError("KB.EMPTY_SOURCE", 422)
    return result


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunks(text: str, size: int = 500, overlap: int = 60) -> list[dict]:
    if size < 1 or overlap < 0 or overlap >= size:
        raise ValueError("Require 0 <= overlap < size")
    result = []
    for paragraph in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", text):
        start = paragraph.start()
        while start < paragraph.end():
            end = min(start + size, paragraph.end())
            result.append({"start": start, "end": end, "text": text[start:end]})
            if end == paragraph.end():
                break
            start = end - overlap
    return result
