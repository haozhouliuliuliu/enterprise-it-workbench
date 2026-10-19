"""把虚构制度与样本文件导入知识库（幂等）。"""

from __future__ import annotations

from pathlib import Path

from .ingest import import_path, infer_metadata
from .knowledge import KnowledgeStore

SAMPLE_META = [
    {
        "filename": "IT Onboarding Handbook.pdf",
        "doc_id": "POL-IT-010",
        "title": "IT Onboarding Handbook",
        "version": "v1.0",
        "status": "current",
        "doc_type": "pdf",
        "source": "sample_docs",
        "doc_group": "POL-IT-010",
        "effective_from": "2026-01-01",
    },
    {
        "filename": "供应商设备交付说明（草稿）.docx",
        "doc_id": "POL-IT-011",
        "title": "供应商设备交付说明（草稿）",
        "version": "v0.9",
        "status": "draft",
        "doc_type": "docx",
        "source": "sample_docs",
        "doc_group": "POL-IT-011",
        "effective_from": "2026-09-01",
    },
    {
        "filename": "资产预留与发放操作规范.txt",
        "doc_id": "POL-IT-012",
        "title": "资产预留与发放操作规范",
        "version": "v1.0",
        "status": "current",
        "doc_type": "txt",
        "source": "sample_docs",
        "doc_group": "POL-IT-012",
        "effective_from": "2026-01-01",
    },
]


def seed_all(store: KnowledgeStore, root: Path) -> dict[str, int]:
    imported = 0
    skipped = 0
    for path in sorted((root / "seed_data" / "policies").glob("*.md")):
        meta = infer_metadata(path)
        try:
            import_path(
                store,
                path,
                doc_id=meta["doc_id"],
                title=meta["title"],
                version=meta["version"],
                status=meta["status"],
                doc_type=meta["doc_type"],
                source=meta["source"],
                doc_group=meta["doc_group"],
                effective_from="2024-01-01" if meta["status"] == "superseded" else "2026-01-01",
            )
            imported += 1
        except Exception as exc:
            if getattr(exc, "code", "") in {"DUPLICATE_DOCUMENT", "DUPLICATE_VERSION"}:
                skipped += 1
                continue
            raise
    for meta in SAMPLE_META:
        path = root / "sample_docs" / meta["filename"]
        if not path.is_file():
            continue
        try:
            import_path(store, path, **{key: value for key, value in meta.items() if key != "filename"})
            imported += 1
        except Exception as exc:
            if getattr(exc, "code", "") in {"DUPLICATE_DOCUMENT", "DUPLICATE_VERSION"}:
                skipped += 1
                continue
            raise
    return {"imported": imported, "skipped": skipped, "documents": store.stats()["documents"]}
