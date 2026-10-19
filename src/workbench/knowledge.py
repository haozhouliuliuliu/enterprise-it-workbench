"""持久化知识库：文档元数据、分块、混合检索（向量 + 关键词）。

不依赖外部向量服务：使用确定性 hashing embedding + BM25 风格关键词分数。
索引持久化在 SQLite，重启后可直接查询；重新索引会重算 chunk 向量。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CJK = re.compile(r"[\u4e00-\u9fff]+")
WORD = re.compile(r"[A-Za-z0-9_]+")
DIM = 256
MAX_FILE_BYTES = 2 * 1024 * 1024
ALLOWED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}
VALID_STATUS = {"current", "superseded", "draft", "unpublished"}
STOP_TERMS = set(
    [
        "什么",
        "是什",
        "哪些",
        "多少",
        "如何",
        "怎么",
        "么处",
        "应该",
        "该怎",
        "需要",
        "可以",
        "我们",
        "公司",
        "员工",
        "是否",
        "有哪",
        "一个",
        "怎样",
        "为什",
        "何时",
        "多久",
    ]
)


class KnowledgeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class DocumentMeta:
    doc_id: str
    title: str
    version: str
    status: str
    doc_type: str
    source: str
    filename: str
    file_hash: str
    doc_group: str = ""
    effective_from: str | None = None
    effective_to: str | None = None


@dataclass
class Section:
    text: str
    section: str
    page: int | None = None


@dataclass
class SearchHit:
    evidence_id: str
    doc_pk: int
    doc_id: str
    title: str
    version: str
    status: str
    doc_type: str
    section: str
    page: int | None
    snippet: str
    citation: str
    score: float
    is_current: bool
    effective_from: str | None = None
    effective_to: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    tokens = WORD.findall(lowered)
    for run in CJK.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _hash_bucket(token: str) -> tuple[int, float]:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % DIM, 1.0 if (value >> 63) & 1 else -1.0


def embed(text: str) -> list[float]:
    vector = [0.0] * DIM
    for token in tokenize(text):
        bucket, sign = _hash_bucket(token)
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 6) for value in vector]


def cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def effective(doc_status: str, effective_from: str | None, effective_to: str | None) -> bool:
    if doc_status != "current":
        return False
    today = datetime.now(UTC).date().isoformat()
    if effective_from and effective_from > today:
        return False
    return not (effective_to and effective_to < today)


SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_documents (
    doc_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    mime_type TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    effective_from TEXT,
    effective_to TEXT,
    source TEXT NOT NULL,
    doc_group TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    index_status TEXT NOT NULL DEFAULT 'indexed',
    error TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(doc_id, version)
);

CREATE TABLE IF NOT EXISTS kb_chunks (
    chunk_pk INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_pk INTEGER NOT NULL REFERENCES kb_documents(doc_pk) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL UNIQUE,
    section TEXT NOT NULL,
    page INTEGER,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    embedding TEXT NOT NULL,
    term_counts TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_kb_docs_group ON kb_documents(doc_group, status);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc ON kb_chunks(doc_pk);
"""


class KnowledgeStore:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        connection = self._connect()
        try:
            connection.executescript(SCHEMA)
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    # ---------------------------------------------------------------- 写入

    def add_document(self, meta: DocumentMeta, sections: list[Section]) -> int:
        if meta.status not in VALID_STATUS:
            raise KnowledgeError("INVALID_STATUS", f"不支持的状态：{meta.status}")
        if not sections:
            raise KnowledgeError("EMPTY_DOCUMENT", "文档没有可索引内容")
        with self._lock:
            connection = self._connect()
            try:
                existing = connection.execute(
                    "SELECT doc_pk FROM kb_documents WHERE file_hash = ?", (meta.file_hash,)
                ).fetchone()
                if existing:
                    raise KnowledgeError("DUPLICATE_DOCUMENT", "相同文件已导入")
                duplicate = connection.execute(
                    "SELECT doc_pk FROM kb_documents WHERE doc_id = ? AND version = ?",
                    (meta.doc_id, meta.version),
                ).fetchone()
                if duplicate:
                    raise KnowledgeError("DUPLICATE_VERSION", "相同文档版本已存在")
                if meta.status == "current" and meta.doc_group:
                    connection.execute(
                        "UPDATE kb_documents SET status = 'superseded', updated_at = ? "
                        "WHERE doc_group = ? AND status = 'current'",
                        (_now(), meta.doc_group),
                    )
                cursor = connection.execute(
                    "INSERT INTO kb_documents (doc_id, title, filename, file_hash, mime_type, doc_type, version, "
                    "status, effective_from, effective_to, source, doc_group, imported_at, updated_at, index_status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'indexed')",
                    (
                        meta.doc_id,
                        meta.title,
                        meta.filename,
                        meta.file_hash,
                        "application/octet-stream",
                        meta.doc_type,
                        meta.version,
                        meta.status,
                        meta.effective_from,
                        meta.effective_to,
                        meta.source,
                        meta.doc_group or meta.doc_id,
                        _now(),
                        _now(),
                    ),
                )
                doc_pk = int(cursor.lastrowid)
                self._insert_chunks(connection, doc_pk, meta.doc_id, meta.version, sections)
                connection.commit()
                return doc_pk
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    @staticmethod
    def _insert_chunks(
        connection: sqlite3.Connection, doc_pk: int, doc_id: str, version: str, sections: list[Section]
    ) -> None:
        index = 0
        for section in sections:
            for content in _split_text(section.text):
                digest = hashlib.sha256(f"{doc_id}|{version}|{section.section}|{index}|{content}".encode()).hexdigest()
                evidence_id = f"EV-KB-{digest[:12]}"
                tokens = tokenize(content)
                counts: dict[str, int] = {}
                for token in tokens:
                    counts[token] = counts.get(token, 0) + 1
                connection.execute(
                    "INSERT INTO kb_chunks (doc_pk, evidence_id, section, page, chunk_index, content, embedding, term_counts) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        doc_pk,
                        evidence_id,
                        section.section,
                        section.page,
                        index,
                        content,
                        json.dumps(embed(content)),
                        json.dumps(counts),
                    ),
                )
                index += 1
        connection.execute(
            "UPDATE kb_documents SET chunk_count = ? WHERE doc_pk = ?",
            (index, doc_pk),
        )

    # ---------------------------------------------------------------- 管理

    def list_documents(self) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT * FROM kb_documents ORDER BY doc_pk DESC").fetchall()
        finally:
            connection.close()
        return [dict(row) for row in rows]

    def get_document(self, doc_pk: int) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM kb_documents WHERE doc_pk = ?", (doc_pk,)).fetchone()
        finally:
            connection.close()
        return dict(row) if row else None

    def delete_document(self, doc_pk: int) -> bool:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("DELETE FROM kb_chunks WHERE doc_pk = ?", (doc_pk,))
                cursor = connection.execute("DELETE FROM kb_documents WHERE doc_pk = ?", (doc_pk,))
                connection.commit()
                return cursor.rowcount > 0
            finally:
                connection.close()

    def reindex(self) -> int:
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute("SELECT chunk_pk, content FROM kb_chunks").fetchall()
                for row in rows:
                    tokens = tokenize(row["content"])
                    counts: dict[str, int] = {}
                    for token in tokens:
                        counts[token] = counts.get(token, 0) + 1
                    connection.execute(
                        "UPDATE kb_chunks SET embedding = ?, term_counts = ? WHERE chunk_pk = ?",
                        (json.dumps(embed(row["content"])), json.dumps(counts), row["chunk_pk"]),
                    )
                connection.commit()
                return len(rows)
            finally:
                connection.close()

    # ---------------------------------------------------------------- 检索

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        include_inactive: bool = True,
        doc_group: str | None = None,
    ) -> list[SearchHit]:
        query = query.strip()
        if not query:
            return []
        connection = self._connect()
        try:
            sql = (
                "SELECT c.*, d.doc_id, d.title, d.version, d.status, d.doc_type, d.effective_from, "
                "d.effective_to, d.doc_group FROM kb_chunks c JOIN kb_documents d ON d.doc_pk = c.doc_pk"
            )
            params: list[Any] = []
            if doc_group:
                sql += " WHERE d.doc_group = ?"
                params.append(doc_group)
            rows = connection.execute(sql, params).fetchall()
        finally:
            connection.close()
        if not rows:
            return []

        documents = []
        for row in rows:
            documents.append(
                {
                    "row": row,
                    "terms": json.loads(row["term_counts"]),
                    "embedding": json.loads(row["embedding"]),
                    "length": sum(json.loads(row["term_counts"]).values()),
                }
            )
        query_terms = tokenize(query)
        if not query_terms:
            return []
        total = len(documents)
        avg_len = sum(item["length"] for item in documents) / total or 1.0
        df: dict[str, int] = {}
        for item in documents:
            for term in set(query_terms):
                if term in item["terms"]:
                    df[term] = df.get(term, 0) + 1
        idf = {term: math.log(1 + (total - count + 0.5) / (count + 0.5)) for term, count in df.items()}
        query_vector = embed(query)
        raw: list[tuple[float, float, Any]] = []
        for item in documents:
            row = item["row"]
            bm25 = 0.0
            for term in query_terms:
                frequency = item["terms"].get(term, 0)
                if not frequency:
                    continue
                denominator = frequency + 1.2 * (1 - 0.75 + 0.75 * item["length"] / avg_len)
                bm25 += idf.get(term, 0.0) * (frequency * 2.2) / denominator
            vector_score = max(0.0, cosine(query_vector, item["embedding"]))
            raw.append((bm25, vector_score, row))
        max_bm25 = max((item[0] for item in raw), default=0.0) or 1.0
        query_unique = set(query_terms)
        content_terms = {
            term for term in query_unique if term not in STOP_TERMS and (len(term) >= 2 or term.isascii())
        } or query_unique
        idf_all = {term: idf.get(term, math.log(1 + (total + 0.5) / 0.5)) for term in content_terms}
        idf_total = sum(idf_all.values()) or 1.0
        scored: list[tuple[float, SearchHit]] = []
        for bm25, vector_score, row in raw:
            hybrid = 0.55 * (bm25 / max_bm25) + 0.45 * vector_score
            if row["status"] != "current":
                hybrid *= 0.92
            is_current = effective(row["status"], row["effective_from"], row["effective_to"])
            if is_current:
                hybrid += 0.03
            if not include_inactive and not is_current:
                continue
            item_terms = json.loads(row["term_counts"])
            matched = {term for term in content_terms if term in item_terms}
            coverage = sum(idf_all[term] for term in matched) / idf_total
            section_tokens = set(tokenize(row["section"]))
            title_tokens = set(tokenize(row["title"]))
            section_overlap = len(content_terms & section_tokens)
            title_overlap = len(content_terms & title_tokens)
            if coverage < 0.12 and section_overlap < 2 and not (len(matched) >= 3 and title_overlap >= 1):
                continue
            hybrid += 0.25 * min(1.0, section_overlap / 3)
            hybrid += 0.08 * min(1.0, title_overlap / 3)
            snippet = row["content"][:400]
            citation = f"{row['title']}（{row['version']}，{row['status']}）· {row['section']}"
            hit = SearchHit(
                evidence_id=row["evidence_id"],
                doc_pk=row["doc_pk"],
                doc_id=row["doc_id"],
                title=row["title"],
                version=row["version"],
                status=row["status"],
                doc_type=row["doc_type"],
                section=row["section"],
                page=row["page"],
                snippet=snippet,
                citation=citation,
                score=round(hybrid, 4),
                is_current=is_current,
                effective_from=row["effective_from"],
                effective_to=row["effective_to"],
            )
            scored.append((hybrid, hit))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        threshold = 0.18
        results = [hit for score, hit in scored[: max(1, top_k)] if score >= threshold]
        return results

    def document_metadata(self, evidence_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not evidence_ids:
            return {}
        placeholders = ",".join("?" * len(evidence_ids))
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT c.evidence_id, d.doc_id, d.title, d.version, d.status, "
                f"d.effective_from, d.effective_to, d.doc_type FROM kb_chunks c "
                f"JOIN kb_documents d ON d.doc_pk = c.doc_pk "
                f"WHERE c.evidence_id IN ({placeholders})",
                evidence_ids,
            ).fetchall()
        finally:
            connection.close()
        return {
            row["evidence_id"]: {
                "doc_id": row["doc_id"],
                "title": row["title"],
                "version": row["version"],
                "status": row["status"],
                "effective_from": row["effective_from"],
                "effective_to": row["effective_to"],
                "doc_type": row["doc_type"],
                "is_current": effective(row["status"], row["effective_from"], row["effective_to"]),
            }
            for row in rows
        }

    def find_evidence(self, evidence_ids: list[str]) -> set[str]:
        if not evidence_ids:
            return set()
        connection = self._connect()
        try:
            rows = connection.execute(
                f"SELECT evidence_id FROM kb_chunks WHERE evidence_id IN ({','.join('?' * len(evidence_ids))})",
                evidence_ids,
            ).fetchall()
        finally:
            connection.close()
        return {row["evidence_id"] for row in rows}

    def stats(self) -> dict[str, int]:
        connection = self._connect()
        try:
            docs = connection.execute("SELECT COUNT(*) FROM kb_documents").fetchone()[0]
            chunks = connection.execute("SELECT COUNT(*) FROM kb_chunks").fetchone()[0]
        finally:
            connection.close()
        return {"documents": int(docs), "chunks": int(chunks)}


def _split_text(text: str, max_chars: int = 700, overlap: int = 80) -> list[str]:
    normalized = " ".join(text.split())
    if len(normalized) <= max_chars:
        return [normalized] if normalized else []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + max_chars)
        chunks.append(normalized[start:end])
        if end >= len(normalized):
            break
        start = max(end - overlap, start + 1)
    return chunks
