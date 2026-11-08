"""Run the separately frozen challenge through the real authenticated API.

The historical runner supplies transport, artifact capture and denominators.
Only this process injects the independent oracle and additional source paths;
the old corpus, module and historical output remain unchanged.
"""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from scripts import document_benchmark as transport
from scripts.document_challenge_oracle import evaluate_properties

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    "examples/document_challenge.json",
    "scripts/document_challenge.py",
    "scripts/document_challenge_oracle.py",
    "tests/test_document_challenge_oracle.py",
]
DEFAULT_FREEZE = ROOT / "artifacts/full-bpmn/challenge/corpus-freeze.json"


def hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-only", action="store_true")
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--url", default="http://127.0.0.1:8088")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error("repeats must be 1..20")
    corpus = json.loads((ROOT / FILES[0]).read_text(encoding="utf-8"))
    if args.freeze_only:
        if args.freeze.exists():
            raise ValueError("Freeze already exists; never overwrite an established challenge")
        transport._write_json(
            args.freeze,
            {
                "created_at": datetime.now(UTC).isoformat(),
                "version": corpus["version"],
                "case_count": len(corpus["cases"]),
                "model_requests_before_freeze": 0,
                "files": hashes(),
                "blinding": (
                    "Корпус и частные ожидания не передавались разработчикам новой версии до этой фиксации. "
                    "Файлы находятся в общем workspace; это организационная независимость, "
                    "не криптографически слепой внешний тест."
                ),
            },
        )
        print(json.dumps({"status": "FROZEN", "path": str(args.freeze), "files": hashes()}))
        return 0
    frozen = json.loads(args.freeze.read_text(encoding="utf-8"))
    if frozen["files"] != hashes():
        raise ValueError("Challenge changed after freeze; do not run altered expectations")
    original_source_files = transport.source_files
    transport.source_files = lambda: sorted(set(original_source_files()) | {ROOT / name for name in FILES})
    transport.evaluate_properties = evaluate_properties
    original_summarize = transport.summarize

    def summarize(records, cases, repeats):
        summary = original_summarize(records, cases, repeats)
        summary["corpus_version"] = corpus["version"]
        summary["corpus_freeze"] = frozen
        summary["limitations"] = [
            "Конкретные свойства XML и критерии сокращений зафиксированы до live-прогона.",
            "Корпус авторский, создан отдельным агентом; это не внешний слепой тест.",
            "Проверки namespace, ссылок, ролей, IO и областей не заменяют полную экспертную оценку.",
            "Все запланированные запросы остаются в знаменателе, включая ошибки и не выполненные.",
            "Импорт и визуальное качество проверяются отдельно для всех полученных XML.",
            f"Повторов на описание: {repeats}; это малый набор без статистической гарантии.",
        ]
        return summary

    transport.summarize = summarize
    output = args.output or ROOT / "artifacts/full-bpmn/challenge" / datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    summary = transport.run(corpus["cases"], args.repeats, output, args.url)
    return 0 if summary["candidate_success"]["numerator"] == summary["planned_runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
