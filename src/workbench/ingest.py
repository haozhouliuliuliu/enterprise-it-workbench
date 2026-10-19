"""文档导入：PDF/DOCX/TXT/Markdown 解析、限制与知识库写入。"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from .knowledge import (
    ALLOWED_SUFFIXES,
    MAX_FILE_BYTES,
    DocumentMeta,
    KnowledgeError,
    KnowledgeStore,
    Section,
)

MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_sections(filename: str, data: bytes) -> tuple[str, list[Section]]:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise KnowledgeError("UNSUPPORTED_TYPE", f"不支持的文件类型：{suffix or '(无扩展名)'}")
    if len(data) > MAX_FILE_BYTES:
        raise KnowledgeError("FILE_TOO_LARGE", f"文件超过 {MAX_FILE_BYTES // 1024}KB")
    if not data:
        raise KnowledgeError("EMPTY_FILE", "文件为空")
    if suffix == ".pdf":
        return "pdf", _pdf_sections(data)
    if suffix == ".docx":
        return "docx", _docx_sections(data)
    return ("markdown" if suffix == ".md" else "txt"), _text_sections(data, markdown=suffix == ".md")


def _pdf_sections(data: bytes) -> list[Section]:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:
        raise KnowledgeError("PARSE_ERROR", "PDF 无法解析") from exc
    sections: list[Section] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            raise KnowledgeError("PARSE_ERROR", f"PDF 第 {index} 页解析失败") from exc
        if text.strip():
            sections.append(Section(text=text, section=f"第 {index} 页", page=index))
    if not sections:
        raise KnowledgeError("EMPTY_DOCUMENT", "PDF 没有可提取文本")
    return sections


def _docx_sections(data: bytes) -> list[Section]:
    try:
        document = Document(io.BytesIO(data))
    except (ValueError, zipfile.BadZipFile, KeyError) as exc:
        raise KnowledgeError("PARSE_ERROR", "DOCX 无法解析") from exc
    sections: list[Section] = []
    current = "正文"
    buffer: list[str] = []

    def flush() -> None:
        text = "\n".join(buffer).strip()
        if text:
            sections.append(Section(text=text, section=current))
        buffer.clear()

    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style = (paragraph.style.name or "").lower()
        if style.startswith("heading") and buffer:
            flush()
            current = text
        elif style.startswith("heading"):
            current = text
        else:
            buffer.append(text)
    flush()
    if not sections:
        raise KnowledgeError("EMPTY_DOCUMENT", "DOCX 没有可提取文本")
    return sections


def _text_sections(data: bytes, *, markdown: bool) -> list[Section]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise KnowledgeError("PARSE_ERROR", "文本必须是 UTF-8 编码") from exc
    if not text.strip():
        raise KnowledgeError("EMPTY_DOCUMENT", "文本为空")
    if not markdown:
        return [Section(text=text, section="正文")]
    sections: list[Section] = []
    title = "概述"
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            body = "\n".join(buffer).strip()
            if body:
                sections.append(Section(text=body, section=title))
            title = line[3:].strip()
            buffer = []
        elif line.startswith("# "):
            continue
        else:
            buffer.append(line)
    body = "\n".join(buffer).strip()
    if body:
        sections.append(Section(text=body, section=title))
    return sections or [Section(text=text, section="正文")]


def import_document(
    store: KnowledgeStore,
    *,
    filename: str,
    data: bytes,
    title: str,
    doc_id: str,
    version: str,
    status: str,
    doc_type: str,
    source: str,
    doc_group: str = "",
    effective_from: str | None = None,
    effective_to: str | None = None,
) -> dict:
    digest = file_hash(data)
    parsed_type, sections = extract_sections(filename, data)
    meta = DocumentMeta(
        doc_id=doc_id,
        title=title or Path(filename).stem,
        version=version,
        status=status,
        doc_type=doc_type or parsed_type,
        source=source,
        filename=filename,
        file_hash=digest,
        doc_group=doc_group or doc_id,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    doc_pk = store.add_document(meta, sections)
    return {
        "doc_pk": doc_pk,
        "doc_id": doc_id,
        "version": version,
        "status": status,
        "file_hash": digest,
        "doc_type": parsed_type,
        "chunks": store.get_document(doc_pk)["chunk_count"],
    }


def import_path(
    store: KnowledgeStore,
    path: Path,
    *,
    doc_id: str,
    title: str,
    version: str,
    status: str,
    doc_type: str,
    source: str,
    doc_group: str = "",
    effective_from: str | None = None,
    effective_to: str | None = None,
) -> dict:
    """仅用于受控的种子导入；API 不接受任意路径。"""
    data = path.read_bytes()
    return import_document(
        store,
        filename=path.name,
        data=data,
        title=title,
        doc_id=doc_id,
        version=version,
        status=status,
        doc_type=doc_type,
        source=source,
        doc_group=doc_group,
        effective_from=effective_from,
        effective_to=effective_to,
    )


def _looks_like_doc_id(text: str) -> str | None:
    match = re.search(r"POL-[A-Z]+-\d+", text)
    return match.group(0) if match else None


def infer_metadata(path: Path) -> dict:
    name = path.stem
    header = path.read_text(encoding="utf-8") if path.suffix.lower() in {".md", ".txt"} else ""
    doc_id = _looks_like_doc_id(header) or _looks_like_doc_id(name) or "DOC-" + file_hash(name.encode())[:8]
    version_match = re.search(r"版本[：:]\s*([^\s　|]+)", header)
    version = version_match.group(1).strip("*") if version_match else "v1.0"
    status = "current"
    if any(token in name for token in ("旧版", "废止")):
        status = "superseded"
    elif any(token in name for token in ("草稿", "未审核")):
        status = "draft"
    return {
        "doc_id": doc_id,
        "title": name,
        "version": version,
        "status": status,
        "doc_type": path.suffix.lstrip("."),
        "source": "seed_data/policies",
        "doc_group": doc_id,
    }
