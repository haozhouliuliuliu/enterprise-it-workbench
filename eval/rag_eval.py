"""固定 RAG 评估：Top-5 召回、拒答、引用正确率、版本状态。"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from workbench.knowledge import KnowledgeStore
from workbench.seed_knowledge import seed_all

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "evidence" / "rag")
    args = parser.parse_args(argv)
    cases = json.loads((ROOT / "eval" / "rag_cases.json").read_text(encoding="utf-8"))
    run_dir = args.output / datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    store = KnowledgeStore(run_dir / "rag.db")
    seeded = seed_all(store, ROOT)
    rows = []
    answerable = [case for case in cases if case["answerable"]]
    unanswerable = [case for case in cases if not case["answerable"]]
    recall_hits = 0
    citation_hits = 0
    version_ok = 0
    refused = 0
    for case in cases:
        hits = store.search(case["question"], top_k=5, include_inactive=not case["current_only"])
        docs = [hit.doc_id for hit in hits]
        row = {
            "id": case["id"],
            "question": case["question"],
            "expected_doc": case.get("expected_doc"),
            "docs": docs,
            "statuses": [hit.status for hit in hits],
            "scores": [hit.score for hit in hits],
            "answerable": case["answerable"],
        }
        if case["answerable"]:
            if case["expected_doc"] in docs:
                recall_hits += 1
                row["recall_at_5"] = True
            else:
                row["recall_at_5"] = False
            top = hits[0] if hits else None
            row["citation_correct"] = bool(top and top.doc_id == case["expected_doc"])
            if row["citation_correct"]:
                citation_hits += 1
            if "expect_status" in case:
                row["version_ok"] = any(
                    hit.doc_id == case["expected_doc"] and hit.status == case["expect_status"] for hit in hits
                )
            else:
                row["version_ok"] = bool(top and top.is_current)
            if row["version_ok"]:
                version_ok += 1
        else:
            row["refused"] = not hits
            if row["refused"]:
                refused += 1
        rows.append(row)

    recall = recall_hits / len(answerable) if answerable else 0
    citation = citation_hits / len(answerable) if answerable else 0
    version_rate = version_ok / len(answerable) if answerable else 0
    refusal_rate = refused / len(unanswerable) if unanswerable else 0
    metrics = {
        "cases": len(cases),
        "answerable": len(answerable),
        "unanswerable": len(unanswerable),
        "recall_at_5": round(recall, 4),
        "citation_accuracy": round(citation, 4),
        "version_status_accuracy": round(version_rate, 4),
        "refusal_accuracy": round(refusal_rate, 4),
        "thresholds": {
            "recall_at_5": 0.85,
            "refusal_accuracy": 0.9,
            "citation_accuracy": 0.9,
            "version_status_accuracy": 1.0,
        },
        "seeded": seeded,
    }
    metrics["passed"] = recall >= 0.85 and citation >= 0.90 and version_rate >= 1.0 and refusal_rate >= 0.9
    (run_dir / "report.json").write_text(
        json.dumps({"metrics": metrics, "cases": rows}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# RAG 固定评估",
        "",
        f"- 用例：{len(cases)}（可回答 {len(answerable)} / 无答案 {len(unanswerable)}）",
        f"- Top-5 召回：{recall:.2%}（要求 ≥85%）",
        f"- 引用正确：{citation:.2%}（要求 ≥90%）",
        f"- 版本状态：{version_rate:.2%}（要求 100%）",
        f"- 无答案拒答：{refused}/{len(unanswerable)}（要求 ≥9/10）",
        "",
        f"结果：{'通过' if metrics['passed'] else '未通过'}",
    ]
    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"证据：{run_dir}")
    return 0 if metrics["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
