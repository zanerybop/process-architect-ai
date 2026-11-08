"""Bounded, factual repair context from an actual document, never an inferred patch."""

import json

from backend.app.bpmn_schema import catalog
from backend.app.domain import ProcessIR


def repair_context(ir: ProcessIR | None, issues: list[dict]) -> dict | None:
    if ir is None or ir.document is None:
        return None
    entries = {item.key: item for item in ir.document.elements}
    evidence = {item.element_id: item.exact_quote for item in ir.provenance}

    def describe(item):
        return {
            "id": item.key, "tag": item.tag, "parent": item.parent,
            "name": item.attributes.get("name", "")[:200],
        }

    failed = []
    identifiers = dict.fromkeys(key for issue in issues for key in issue.get("element_ids", []))
    for identifier in list(identifiers)[:10]:
        item = entries.get(identifier)
        if item is None:
            continue
        ancestors, parent = [], entries.get(item.parent)
        while parent is not None and len(ancestors) < 8:
            ancestors.append(describe(parent))
            parent = entries.get(parent.parent)
        failed.append({
            **describe(item), "source_quote": evidence.get(identifier, "")[:700], "ancestors": ancestors,
            "children": [describe(child) for child in entries.values() if child.parent == identifier][:10],
        })
    roles = []
    for item in entries.values():
        type_name = item.attributes.get("xsi:type") or catalog().element_type(item.tag) or ""
        if not any(
            catalog().derived_from(type_name, base) for base in ("tLane", "tResource", "tParticipant")
        ):
            continue
        roles.append({**describe(item), "references": [
            {"tag": child.tag, "text": (child.text or "")[:100]}
            for child in entries.values() if child.parent == item.key and child.tag == "bpmn:flowNodeRef"
        ][:12]})
    result = {
        "affected_elements": failed,
        "declared_roles": roles[:20],
        "instructions": (
            "Контекст описывает текущий код, а не правильное решение. Сверь исполнителя с исходной цитатой. "
            "flowNodeRef должен ссылаться на узел в том же контейнере laneSet: для задачи внутри "
            "подпроцесса используй его внутреннюю laneSet или performer/potentialOwner с resourceRef. "
            "Не удаляй явно требуемые элементы, связи, данные или действия ради прохождения проверок. "
            "Технический выбор XML-ID, необязательного name и размещения не требует бизнес-допущения. "
            "Если само действие или исполнитель действительно неизвестны, верни clarify."
        ),
    }
    while len(json.dumps(result, ensure_ascii=False).encode()) > 12_000:
        if len(result["declared_roles"]) > 2:
            result["declared_roles"].pop()
        elif len(result["affected_elements"]) > 1:
            result["affected_elements"].pop()
        elif result["declared_roles"]:
            result["declared_roles"].pop()
        elif result["affected_elements"]:
            item = result["affected_elements"][0]
            if item["children"] or item["ancestors"]:
                item["children"], item["ancestors"] = [], []
            else:
                result["affected_elements"].pop()
        else:
            break
        result["truncated"] = True
    return result
