"""Source-only views of complete documents; never used as their graph or XML."""

import re

from backend.app.document_serialization import build_document_tree
from backend.app.document_validation import DocumentIndex, is_type, semantic_tag, standard
from backend.app.domain import Flow, Issue, Lane, Node, Participant, Process, ProcessIR


def _review_index(ir):
    return DocumentIndex(build_document_tree(ir))


def _scope(element):
    return next(
        (
            item
            for item in (element, *element.iterancestors())
            if standard(item) and semantic_tag(item) in {"process", "choreography", "globalChoreographyTask"}
        ),
        None,
    )


def _identifier(element):
    return element.get("id", "") if element is not None else ""


def _source_task(element):
    return is_type(element, "Task") or semantic_tag(element) in {
        "callActivity",
        "choreographyTask",
        "callChoreography",
    }


def source_projection(ir: ProcessIR) -> ProcessIR:
    from backend.app.audit import _verified

    result = ProcessIR(
        source_text=ir.source_text,
        provenance=ir.provenance,
        questions=ir.questions,
        assumptions=ir.assumptions,
    )
    index = _review_index(ir)
    quotes = {item.element_id: item.exact_quote for item in ir.provenance if _verified(item, ir.source_text)}
    result.processes = [
        Process(id=_identifier(item), name=item.get("name", ""))
        for item in index.elements
        if _scope(item) is item
    ]
    lane_by_node = {}
    for item in index.elements:
        kind = semantic_tag(item)
        if kind == "flowNodeRef":
            node = index.resolve(item.text, item, "FlowNode", report=False)
            if node is not None:
                previous = lane_by_node.get(_identifier(node))
                lane = item.getparent()
                # Specific nested lanes take precedence over their parent lanes.
                if previous is None or len(list(lane.iterancestors())) > len(list(previous.iterancestors())):
                    lane_by_node[_identifier(node)] = lane
        if kind == "lane":
            result.lanes.append(
                Lane(id=_identifier(item), name=item.get("name", ""), process_id=_identifier(_scope(item)))
            )
        if kind == "participant":
            process = index.ref(item, "processRef", "Process")
            if process is not None:
                result.participants.append(
                    Participant(
                        id=_identifier(item), name=item.get("name", ""), process_id=_identifier(process)
                    )
                )

    def add_role(identifier, name, process_id):
        if not any(lane.id == identifier for lane in result.lanes):
            result.lanes.append(Lane(id=identifier, name=name, process_id=process_id))
        return identifier

    declared_lanes = tuple(result.lanes)
    resource_names = {
        entry.get("name", "").strip() for entry in index.elements
        if is_type(entry, "Resource") and entry.get("name", "").strip()
    }

    def conflicts_with_quoted_role(role_name, identifier, process_id):
        quote = quotes.get(identifier, "")
        names = resource_names | {
            role.name.strip() for role in declared_lanes
            if role.process_id == process_id and role.name.strip()
        }
        mentioned = {
            name.casefold() for name in names
            for match in re.finditer(r"(?<!\w)" + re.escape(name) + r"(?!\w)", quote, re.I)
            if re.fullmatch(
                r"\s*(?:(?:затем|далее|тогда|параллельно|после этого|then|next)\s+)?"
                r"(?:(?:в|у)\s+[^,;.!?\s]+(?:\s+[^,;.!?\s]+){0,3}\s+)?",
                re.split(r"[,;.!?\n]", quote[:match.start()])[-1], re.I,
            )
        }
        # Only a leading subject is evidence here: a sole mentioned recipient such
        # as "sends a request to SAP" must not replace the assigned Manager role.
        # Multiple named subjects still need richer interpretation.
        return len(mentioned) == 1 and not any(
            re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", role_name, re.I)
            for name in mentioned
        )

    for item in index.elements:
        local = semantic_tag(item)
        if local in {"startEvent", "endEvent", "exclusiveGateway", "parallelGateway"}:
            kind = local
        elif _source_task(item):
            kind = "task"
        else:
            continue
        identifier, process_id = _identifier(item), _identifier(_scope(item))
        lane = _identifier(lane_by_node.get(identifier)) or None
        if not lane:
            resources = []
            for role in item:
                if not is_type(role, "ResourceRole"):
                    continue
                for reference in role:
                    if semantic_tag(reference) != "resourceRef":
                        continue
                    resource = index.resolve(reference.text, reference, "Resource", report=False)
                    if resource is not None and resource.get("name", "").strip():
                        resources.append(resource.get("name").strip())
            if resources:
                # Source-only handle; this lane is never serialized into BPMN.
                lane = add_role(
                    f"resource_assignment:{identifier}", " / ".join(dict.fromkeys(resources)), process_id
                )
        if not lane:
            for parent in item.iterancestors():
                assigned = lane_by_node.get(_identifier(parent))
                if assigned is not None:
                    if not conflicts_with_quoted_role(assigned.get("name", ""), identifier, process_id):
                        lane = _identifier(assigned)
                    break
        if not lane:
            participant = next(
                (entry for entry in result.participants if entry.process_id == process_id), None
            )
            if participant:
                # An organization is a valid actor, but cannot hide an omitted assignment
                # to a more specific role that is both declared and named in this action's quote.
                if not conflicts_with_quoted_role(participant.name, identifier, process_id):
                    lane = add_role(participant.id, participant.name, process_id)
        if not lane and local in {"choreographyTask", "callChoreography"}:
            initiator = index.ref(item, "initiatingParticipantRef", "Participant")
            if initiator is not None:
                lane = add_role(_identifier(initiator), initiator.get("name", ""), process_id)
        assigned_role = next((role for role in result.lanes if role.id == lane), None)
        if assigned_role and conflicts_with_quoted_role(assigned_role.name, identifier, process_id):
            lane = None
        result.nodes.append(
            Node(id=identifier, type=kind, name=item.get("name", ""), process_id=process_id, lane_id=lane)
        )

    for item in index.elements:
        kind = semantic_tag(item)
        if kind not in {"sequenceFlow", "messageFlow"}:
            continue
        source, target = index.ref(item, "sourceRef"), index.ref(item, "targetRef")
        # Never strip an external namespace and pretend the target is local.
        if source is None or target is None:
            continue
        condition = next((child for child in item if semantic_tag(child) == "conditionExpression"), None)
        result.flows.append(
            Flow(
                id=_identifier(item),
                kind="sequence" if kind == "sequenceFlow" else "message",
                source=_identifier(source),
                target=_identifier(target),
                name=item.get("name", ""),
                condition="".join(condition.itertext()) if condition is not None else None,
                is_default=index.ref(source, "default") is item,
            )
        )
    return result


def associated_data_names(ir: ProcessIR) -> dict[str, set[str]]:
    """Visible names reached by an activity's data associations, using local typed refs."""
    index = _review_index(ir)
    result: dict[str, set[str]] = {}
    for association in index.elements:
        if not is_type(association, "DataAssociation"):
            continue
        owner = association.getparent()
        if not _source_task(owner):
            continue
        for reference in association:
            if semantic_tag(reference) not in {"sourceRef", "targetRef"}:
                continue
            target = index.resolve(reference.text, reference, "ItemAwareElement", report=False)
            if target is None:
                continue
            visible = semantic_tag(target) in {"dataObjectReference", "dataStoreReference"}
            if semantic_tag(target) in {"dataObject", "dataInput", "dataOutput"}:
                visible = semantic_tag(target.getparent()) in {
                    "process",
                    "subProcess",
                    "transaction",
                    "adHocSubProcess",
                }
            if visible and target.get("name", "").strip():
                result.setdefault(_identifier(owner), set()).add(target.get("name"))
    return result


def explicit_document_issues(ir: ProcessIR) -> list[Issue]:
    """Only positive explicit event requests; implicit starts/ends remain valid BPMN."""
    index = _review_index(ir)
    tags = {semantic_tag(element) for element in index.elements}
    issues = []
    patterns = (
        (r"(?:начальн[а-яё]+\s+событи[а-яё]+|startEvent)", "startEvent"),
        (r"(?:конечн[а-яё]+\s+событи[а-яё]+|endEvent)", "endEvent"),
    )
    positive = re.compile(
        r"(?:\bпосле|\bперед|\bдо|\bчерез|\bначинается\s+с|\bзавершается|"
        r"\bдобав(?:ить|ьте)|\bсозда(?:ть|йте)|\bиспольз(?:овать|уйте)|\bтребуется|\bнужно|\bнеобходимо)"
        r"\s+(?:явн[а-яё]+\s+)?$",
        re.I,
    )
    for pattern, required in patterns:
        if required in tags:
            continue
        for match in re.finditer(r"\b" + pattern + r"\b", ir.source_text, re.I):
            start = max(ir.source_text.rfind(char, 0, match.start()) for char in ".!?;\n") + 1
            before = ir.source_text[start : match.start()]
            after = ir.source_text[match.end() : match.end() + 70]
            if not positive.search(before) or re.search(r"\b(?:без|не)\b", before, re.I):
                continue
            if re.match(
                r"\s*(?:не\s+(?:нуж[а-яё]*|требу[а-яё]*|использу[а-яё]*|предусмотр[а-яё]*)|отсутств)",
                after,
                re.I,
            ):
                continue
            issues.append(
                Issue(
                    code="EXPLICIT_EVENT_OMITTED",
                    category="model",
                    message=f"Описание явно требует {required}, но такого события нет в документе.",
                    source_quote=match.group(),
                    suggested_action="Сохраните явно указанное событие в модели.",
                )
            )
            break
    return issues
