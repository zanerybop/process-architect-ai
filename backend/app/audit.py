"""Small, deterministic review rules for a generated IR and its source evidence.

An exact quote only proves that these characters occur at the recorded location.
It does not prove that a task paraphrases the quote correctly, or that the BPMN is
sound. Imported XML is validated independently and does not require source links.
"""

import re

from backend.app.builder import TASK_TYPES
from backend.app.domain import Issue, ProcessIR, Provenance

CLARIFICATION_CODES = frozenset(
    {
        "UNASSIGNED_ROLE",
        "ACTION_WITHOUT_SOURCE",
        "SOURCE_UNCERTAINTY",
        "UNACCEPTED_ASSUMPTION",
    }
)
UNASSIGNED_LANE_NAMES = frozenset(
    {
        "роль не указана",
        "не указан",
        "не указана",
        "исполнитель не указан",
    }
)

# Deliberately narrow markers of a missing description, not inference about what
# a refusal or a loop ought to do. All reported quotations come from regex spans.
UNCERTAINTY_PATTERNS = (
    re.compile(
        r"\bчто\s+(?:делать|нужно\s+делать|происходит|произойд[её]т)\b"
        r"[^.!?\n;]{0,160}?\b(?:не\s+(?:определено|описано|согласовано|известно)|неясно)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:услови[ея]|критери[йи])\s+(?:выхода|завершения|прекращения|остановки)\b"
        r"[^.!?\n;]{0,160}?\bне\s+(?:определ[её]н[оы]?|согласован[оы]?|описан[оы]?|задан[оы]?)\b",
        re.IGNORECASE,
    ),
)


def _verified(provenance: Provenance, source: str) -> bool:
    start, end, quote = provenance.source_start, provenance.source_end, provenance.exact_quote
    return (
        provenance.origin == "explicit"
        and bool(quote)
        and type(start) is int
        and type(end) is int
        and 0 <= start < end <= len(source)
        and source[start:end] == quote
    )


def audit(ir: ProcessIR) -> list[Issue]:
    """Return warnings/questions without mutating, completing or simplifying IR."""
    if ir.document is not None:
        from backend.app.document_review import source_projection

        return audit(source_projection(ir))
    issues: list[Issue] = []
    evidence: dict[str, Provenance] = {}
    element_ids = {
        element.id for element in [*ir.processes, *ir.participants, *ir.lanes, *ir.nodes, *ir.flows]
    }
    for entry in ir.provenance:
        if entry.element_id in element_ids and _verified(entry, ir.source_text):
            evidence.setdefault(entry.element_id, entry)

    def quote_for(element_id: str) -> str | None:
        entry = evidence.get(element_id)
        return entry.exact_quote if entry else None

    lanes = {lane.id: lane for lane in ir.lanes}
    for node in ir.nodes:
        if node.type not in TASK_TYPES:
            continue
        lane = lanes.get(node.lane_id)
        if (
            lane is None
            or lane.process_id != node.process_id
            or lane.name.strip().casefold() in UNASSIGNED_LANE_NAMES
            or not lane.name.strip()
        ):
            issues.append(
                Issue(
                    code="UNASSIGNED_ROLE",
                    category="model",
                    severity="warning",
                    element_ids=[node.id],
                    message="Для действия не указан подтверждённый исполнитель.",
                    source_quote=quote_for(node.id),
                    suggested_action=(
                        "Уточните, кто выполняет действие; нейтральная дорожка требует подтверждения."
                    ),
                )
            )
        if node.id not in evidence:
            issues.append(
                Issue(
                    code="ACTION_WITHOUT_SOURCE",
                    category="question",
                    severity="warning",
                    element_ids=[node.id],
                    message="Для действия нет проверенной точной цитаты исходного описания.",
                    suggested_action=(
                        "Укажите основание действия в описании или подтвердите его после уточнения."
                    ),
                )
            )

    for node in ir.nodes:
        if node.type != "exclusiveGateway":
            continue
        branches = [flow for flow in ir.flows if flow.kind == "sequence" and flow.source == node.id]
        if len(branches) < 2:
            continue
        for flow in branches:
            if flow.is_default or flow.name.strip() or (flow.condition or "").strip():
                continue
            issues.append(
                Issue(
                    code="XOR_BRANCH_UNLABELED",
                    category="model",
                    severity="warning",
                    element_ids=[flow.id],
                    message="У альтернативы XOR не описано условие выбора.",
                    source_quote=quote_for(flow.id) or quote_for(node.id),
                    suggested_action="Уточните подпись или условие альтернативы по исходному описанию.",
                )
            )

    spans: set[tuple[int, int]] = set()
    for pattern in UNCERTAINTY_PATTERNS:
        for match in pattern.finditer(ir.source_text):
            start, end = match.span()
            preceding = ir.source_text[
                max(ir.source_text.rfind(char, 0, start) for char in ".!?\n;") + 1 : start
            ]
            # A described conditional business rule is not an unanswered analyst question.
            if re.search(r"\b(?:если|когда)\b", preceding, flags=re.IGNORECASE):
                continue
            if (start, end) in spans:
                continue
            spans.add((start, end))
            linked = [
                element_id
                for element_id, entry in evidence.items()
                if entry.source_start < end and entry.source_end > start
            ]
            issues.append(
                Issue(
                    code="SOURCE_UNCERTAINTY",
                    category="question",
                    severity="warning",
                    element_ids=linked,
                    message=(
                        "В описании прямо отмечено, что действие или условие завершения ещё не определено."
                    ),
                    source_quote=ir.source_text[start:end],
                    suggested_action=(
                        "Уточните этот фрагмент; система не выбирает исход или условие за аналитика."
                    ),
                )
            )

    for assumption in ir.assumptions:
        if assumption.strip():
            issues.append(
                Issue(
                    code="UNACCEPTED_ASSUMPTION",
                    category="model",
                    severity="warning",
                    message=f"Требует подтверждения допущение модели: {assumption}",
                    suggested_action="Уточните описание до генерации схемы с этим допущением.",
                )
            )
    return issues
