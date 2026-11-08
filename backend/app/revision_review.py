"""Review current editor XML against its immutable generated IR, without rewriting DI."""

from lxml import etree

from backend.app.document_validation import requires_document_validation, semantic_snapshot
from backend.app.domain import ProcessIR
from backend.app.semantic_review import review_source
from backend.app.xml_validation import _read_graph, validate_xml


def _elements(ir):
    return {
        element.id: element.model_dump()
        for element in [*ir.processes, *ir.participants, *ir.lanes, *ir.nodes, *ir.flows]
    }


def review_revision(xml: str, original: dict | None = None) -> dict:
    validation = validate_xml(xml)
    validation["semantic_diff"] = {"status": "unavailable", "changes": [], "geometry_only": False}
    validation["semantic_review"] = None
    if any(validation["checks"].get(key) != "passed" for key in ("xsd", "graph")):
        return validation
    if not original or not original.get("ir"):
        return validation
    baseline = ProcessIR.model_validate(original["ir"])
    root = etree.fromstring(
        xml.encode(),
        etree.XMLParser(
            resolve_entities=False,
            no_network=True,
            load_dtd=False,
            huge_tree=False,
        ),
    )
    if baseline.document is not None or requires_document_validation(root):
        if baseline.document is not None:
            from backend.app.document_serialization import build_document_tree

            before_root = build_document_tree(baseline)
        else:
            from backend.app.serialization import serialize

            before_root = etree.fromstring(
                serialize(baseline).encode(),
                etree.XMLParser(
                    resolve_entities=False,
                    no_network=True,
                    load_dtd=False,
                ),
            )
        before, after = semantic_snapshot(before_root), semantic_snapshot(root)

        def elements(snapshot, path="document"):
            identifier = snapshot["attributes"].get("id") or path
            result = {identifier: {key: value for key, value in snapshot.items() if key != "children"}}
            for index, child in enumerate(snapshot["children"]):
                result.update(elements(child, f"{identifier}/{child['tag']}[{index}]"))
            return result

        old_items, new_items = elements(before), elements(after)
        changes = []
        for identifier in sorted(old_items.keys() | new_items.keys()):
            old, new = old_items.get(identifier), new_items.get(identifier)
            if old == new:
                continue
            changes.append(
                {
                    "kind": "added" if old is None else "removed" if new is None else "changed",
                    "element_id": identifier,
                    "name": (new or old)["attributes"].get("name", identifier),
                    "fields": sorted(
                        key
                        for key in (old or {}).keys() | (new or {}).keys()
                        if (old or {}).get(key) != (new or {}).get(key)
                    ),
                    "before": old,
                    "after": new,
                }
            )
        # Parent movement must be visible even if the element retains its id/attributes.
        if before != after and not changes:
            changes.append(
                {
                    "kind": "changed",
                    "element_id": "document",
                    "name": "Структура документа",
                    "fields": ["containment"],
                    "before": None,
                    "after": None,
                }
            )
        validation["semantic_diff"] = {
            "status": "changed" if before != after else "unchanged",
            "changes": changes,
            "geometry_only": before == after,
            "baseline": "original_generation",
            "human_review_required": before != after,
        }
        validation["semantic_review"] = {
            "version": "standard-document-review-v1",
            "status": "needs_review",
            "human_review_required": True,
            "scope": "Полный документ сравнивается без DI; привязки новых элементов к тексту не доказаны.",
            "coverage": [],
            "issues": [],
            "linked_fragments": 0,
            "total_fragments": 0,
        }
        return validation
    current, _ = _read_graph(root)
    before, after = _elements(baseline), _elements(current)
    changes = []
    for element_id in sorted(before.keys() | after.keys()):
        old, new = before.get(element_id), after.get(element_id)
        if old == new:
            continue
        kind = "added" if old is None else "removed" if new is None else "changed"
        fields = [
            key
            for key in (old or {}).keys() | (new or {}).keys()
            if (old or {}).get(key) != (new or {}).get(key)
        ]
        changes.append(
            {
                "kind": kind,
                "element_id": element_id,
                "name": (new or old).get("name", element_id),
                "fields": sorted(fields),
                "before": old,
                "after": new,
            }
        )
    validation["semantic_diff"] = {
        "status": "changed" if changes else "unchanged",
        "changes": changes,
        "geometry_only": not changes,
        "baseline": "original_generation",
        "human_review_required": bool(changes),
    }
    current.source_text = baseline.source_text
    current.provenance = [item for item in baseline.provenance if item.element_id in after]
    current.assumptions = baseline.assumptions
    validation["semantic_review"] = review_source(current)
    return validation
