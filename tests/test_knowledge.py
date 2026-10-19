"""知识库：导入、分块、版本、去重、删除、重建与检索。"""

from __future__ import annotations

from pathlib import Path

import pytest

from workbench.ingest import extract_sections, import_document
from workbench.knowledge import KnowledgeError, KnowledgeStore

ROOT = Path(__file__).resolve().parents[1]


def make_store(tmp_path: Path) -> KnowledgeStore:
    return KnowledgeStore(tmp_path / "kb.db")


def md(content: str, filename: str = "policy.md") -> bytes:
    return content.encode("utf-8")


def add(
    store: KnowledgeStore,
    *,
    doc_id: str,
    version: str,
    status: str = "current",
    data: bytes | None = None,
    title: str = "测试制度",
):
    return import_document(
        store,
        filename="policy.md",
        data=data or md(f"# {title}\n\n## 条件\n这是 {doc_id} {version} 的测试条款。"),
        title=title,
        doc_id=doc_id,
        version=version,
        status=status,
        doc_type="md",
        source="test",
        doc_group=doc_id,
    )


def test_markdown_import_and_chunking(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    result = add(store, doc_id="POL-T-001", version="v1.0")
    assert result["chunks"] >= 1
    documents = store.list_documents()
    assert documents[0]["doc_id"] == "POL-T-001"
    assert documents[0]["index_status"] == "indexed"


def test_duplicate_file_is_rejected(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    data = md("# 重复文件\n\n## 条件\n同一份文件。")
    add(store, doc_id="POL-T-001", version="v1.0", data=data)
    with pytest.raises(KnowledgeError) as excinfo:
        add(store, doc_id="POL-T-002", version="v1.0", data=data)
    assert excinfo.value.code == "DUPLICATE_DOCUMENT"


def test_duplicate_version_with_different_content_is_rejected(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add(store, doc_id="POL-T-001", version="v1.0")
    with pytest.raises(KnowledgeError) as excinfo:
        add(
            store,
            doc_id="POL-T-001",
            version="v1.0",
            data=md("# 另一个标题\n\n## 条件\n不同内容。"),
        )
    assert excinfo.value.code == "DUPLICATE_VERSION"


def test_new_current_version_supersedes_old(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add(store, doc_id="POL-T-001", version="v1.0")
    add(
        store,
        doc_id="POL-T-001",
        version="v2.0",
        data=md("# 测试制度\n\n## 条件\n新版条款。"),
    )
    documents = store.list_documents()
    statuses = {(item["version"], item["status"]) for item in documents}
    assert ("v1.0", "superseded") in statuses
    assert ("v2.0", "current") in statuses


def test_draft_is_not_current_and_excluded_by_current_only(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add(
        store,
        doc_id="POL-T-002",
        version="v0.1",
        status="draft",
        data=md("# 草稿\n\n## 显示器审批\n显示器由 IT 负责人审批。"),
    )
    hits = store.search("显示器审批", include_inactive=False)
    assert hits == []
    hits_all = store.search("显示器审批", include_inactive=True)
    assert hits_all and hits_all[0].status == "draft"
    assert hits_all[0].is_current is False


def test_effective_dates_block_future_current_document(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    import_document(
        store,
        filename="future.md",
        data=md("# 未来制度\n\n## 生效\n未来条款。"),
        title="未来制度",
        doc_id="POL-T-003",
        version="v1.0",
        status="current",
        doc_type="md",
        source="test",
        doc_group="POL-T-003",
        effective_from="2999-01-01",
    )
    hits = store.search("未来条款", include_inactive=True)
    assert hits
    assert hits[0].is_current is False


def test_delete_document_removes_chunks(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    result = add(store, doc_id="POL-T-001", version="v1.0")
    assert store.delete_document(result["doc_pk"]) is True
    assert store.list_documents() == []
    assert store.stats()["chunks"] == 0


def test_persistence_across_store_instances(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add(store, doc_id="POL-T-001", version="v1.0", data=md("# 持久化\n\n## 条件\n持久化条款。"))
    reopened = KnowledgeStore(tmp_path / "kb.db")
    hits = reopened.search("持久化条款")
    assert hits
    assert hits[0].doc_id == "POL-T-001"


def test_reindex_rebuilds_embeddings(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add(store, doc_id="POL-T-001", version="v1.0")
    count = store.reindex()
    assert count >= 1
    assert store.search("测试条款")


def test_txt_extract_and_import(tmp_path: Path) -> None:
    doc_type, sections = extract_sections("a.txt", "资产预留规则\n预留三天。".encode())
    assert doc_type == "txt" and sections[0].section == "正文"


def test_markdown_extract_sections() -> None:
    _, sections = extract_sections("a.md", md("# 标题\n\n## 第一节\n内容一\n\n## 第二节\n内容二"))
    assert [item.section for item in sections] == ["第一节", "第二节"]


def test_pdf_and_docx_samples_are_parseable() -> None:
    pdf = next((ROOT / "sample_docs").glob("*.pdf"))
    docx = next((ROOT / "sample_docs").glob("*.docx"))
    pdf_type, pdf_sections = extract_sections(pdf.name, pdf.read_bytes())
    docx_type, docx_sections = extract_sections(docx.name, docx.read_bytes())
    assert pdf_type == "pdf" and pdf_sections
    assert docx_type == "docx" and docx_sections


def test_unsupported_and_corrupt_files_are_rejected() -> None:
    with pytest.raises(KnowledgeError) as unsupported:
        extract_sections("a.exe", b"MZ")
    assert unsupported.value.code == "UNSUPPORTED_TYPE"
    with pytest.raises(KnowledgeError) as corrupt:
        extract_sections("a.pdf", b"not a pdf")
    assert corrupt.value.code == "PARSE_ERROR"
    with pytest.raises(KnowledgeError) as encoding:
        extract_sections("a.txt", b"\xff\xfe\x00")
    assert encoding.value.code == "PARSE_ERROR"


def test_file_size_limit() -> None:
    from workbench.knowledge import MAX_FILE_BYTES

    with pytest.raises(KnowledgeError) as excinfo:
        extract_sections("a.txt", b"x" * (MAX_FILE_BYTES + 1))
    assert excinfo.value.code == "FILE_TOO_LARGE"


def test_no_answer_returns_empty(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    add(store, doc_id="POL-T-001", version="v1.0")
    assert store.search("食堂午餐补贴标准") == []


def test_seed_policies_include_current_and_inactive(tmp_path: Path) -> None:
    from workbench.seed_knowledge import seed_all

    store = make_store(tmp_path)
    seed_all(store, ROOT)
    statuses = {item["status"] for item in store.list_documents()}
    assert "current" in statuses
    assert "superseded" in statuses or "draft" in statuses
    assert store.stats()["documents"] >= 10
