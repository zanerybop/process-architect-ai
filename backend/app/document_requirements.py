"""Preserve explicitly requested XML constructs; not a general natural-language oracle."""

import re
from functools import lru_cache

from backend.app.bpmn_schema import catalog
from backend.app.domain import Issue, ProcessIR


@lru_cache(maxsize=1)
def _element_names() -> frozenset[str]:
    schema = catalog()
    names = set(schema.elements)
    for definition in schema.types.values():
        names.update(child["tag"] for child in definition.get("children", []))
    return frozenset(
        name.removeprefix("bpmn:") for name in names
        if name.startswith("bpmn:") and any(letter.isupper() for letter in name)
    )


def explicit_requirements(source: str) -> list[dict]:
    """Exact case-sensitive BPMN element names, excluding explicit negation/discussion.

    Ordinary business text has no obligation to name XML elements. This narrow
    gate protects users who deliberately request inputSet, potentialOwner, etc.
    It does not infer a business action or choose its BPMN representation.
    """
    requirements = {}
    for match in re.finditer(r"(?<![\w:])[a-z][A-Za-z]+(?!\w)", source):
        tag = match.group()
        if tag not in _element_names():
            continue
        start = max(source.rfind(char, 0, match.start()) for char in ".!?;\n") + 1
        stops = [pos for char in ".!?;\n" if (pos := source.find(char, match.end())) >= 0]
        end = min(stops, default=len(source))
        before, after = source[start:match.start()], source[match.end():end]
        # Negation applies within a list/clause. A new positive imperative resets it.
        clause = re.split(
            r"\b(?:но|а|зато)\b|[,;]\s*(?=использ|добав|созда|нуж|треб)", before, flags=re.I
        )[-1]
        for imperative in re.finditer(
            r"\b(?:используйте|добавьте|создайте|покажите|нужен|нужна|нужно|нужны|требуется|требуются)\b",
            clause, re.I,
        ):
            if not re.search(r"\bне\s*$", clause[:imperative.start()], re.I):
                clause = clause[imperative.start():]
                break
        if re.search(
            r"\b(?:без|вместо|не\s+(?:нуж[а-яё]*|треб[а-яё]*|использ[а-яё]*|добав[а-яё]*|"
            r"созда[а-яё]*|долж[а-яё]*|предусмотр[а-яё]*))\b|\bне\s*$", clause, re.I
        ):
            continue
        if re.match(
            r"(?:\s*(?:,|и)\s*[a-z][A-Za-z]*)*\s*"
            r"(?:не\s+(?:нуж|треб|использ|предусмотр)|отсутств)", after, re.I
        ):
            continue
        if re.search(r"\b(?:обсужд|упомина|документаци|справк|пример\s+кода)", before, re.I):
            continue
        requirements.setdefault(tag, {"tag": tag, "source_quote": source[start:end].strip()})
    return list(requirements.values())


def required_construct_issues(ir: ProcessIR) -> list[Issue]:
    if ir.document is None:
        return []
    from backend.app.document_review import _review_index
    from backend.app.document_validation import semantic_tag

    tags = {semantic_tag(item) for item in _review_index(ir).elements}
    return [
        Issue(
            code="EXPLICIT_CONSTRUCT_OMITTED", category="model",
            message=f"Потеряна явно указанная в описании конструкция BPMN: {item['tag']}.",
            source_quote=item["source_quote"],
            suggested_action=(
                f"Сохраните {item['tag']} в нужной области документа с корректными ссылками. "
                "Исправление синтаксиса не должно удалять заданные конструкции."
            ),
        )
        for item in explicit_requirements(ir.source_text)
        if item["tag"] not in tags and item["tag"] not in {"startEvent", "endEvent"}
    ]
