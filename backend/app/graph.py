"""Structural validation of the bounded BPMN profile; no graph rewriting."""

from collections import defaultdict

from backend.app.builder import (
    MAX_FLOWS,
    MAX_LANES,
    MAX_NODES,
    MAX_PARTICIPANTS,
    MAX_PROCESSES,
    MAX_TASKS,
    TASK_TYPES,
    valid_id,
)
from backend.app.domain import Flow, Issue, ProcessIR


def _reachable(seeds: list[str], adjacency: dict[str, list[str]]) -> set[str]:
    seen = set(seeds)
    pending = list(seeds)
    while pending:
        current = pending.pop()
        for target in adjacency.get(current, []):
            if target not in seen:
                seen.add(target)
                pending.append(target)
    return seen


def validate_graph(ir: ProcessIR) -> list[Issue]:
    """Report technical violations separately from model warnings.

    Start/end reachability checks do not prove token-flow soundness or eliminate
    all possible deadlocks. In particular, several alternative end events are valid.
    """
    if ir.document is not None:
        from lxml import etree

        from backend.app.document_serialization import build_document_tree
        from backend.app.document_validation import validate_document_structure
        from backend.app.xml_validation import _schema

        try:
            root = build_document_tree(ir)
            schema = _schema()
            issues = []
            if not schema.validate(root):
                for error in list(schema.error_log)[:10]:
                    selected = root.xpath(error.path, namespaces={
                        prefix: uri for prefix, uri in root.nsmap.items() if prefix
                    }) if error.path else []
                    owner = selected[0] if selected and isinstance(selected[0], etree._Element) else None
                    while owner is not None and not owner.get("id"):
                        owner = owner.getparent()
                    issues.append(Issue(
                        code="XSD_INVALID", message=f"BPMN XSD: {error.message[:1200]}",
                        element_ids=[owner.get("id")] if owner is not None else [],
                    ))
            return issues + validate_document_structure(root)
        except (ValueError, KeyError, TypeError, etree.XMLSyntaxError) as exc:
            return [Issue(code="INVALID_DOCUMENT", message=f"Некорректный документ: {str(exc)[:300]}")]
    issues: list[Issue] = []

    def error(code: str, message: str, *ids: str) -> None:
        issues.append(Issue(code=code, message=message, element_ids=list(ids)))

    def warning(code: str, message: str, *ids: str) -> None:
        issues.append(
            Issue(
                code=code,
                message=message,
                element_ids=list(ids),
                category="model",
                severity="warning",
            )
        )

    all_entities = [*ir.processes, *ir.participants, *ir.lanes, *ir.nodes, *ir.flows]
    ids: set[str] = set()
    for entity in all_entities:
        if not valid_id(entity.id):
            error("INVALID_ID", "Некорректный или зарезервированный ID.", entity.id)
        if entity.id in ids:
            error("DUPLICATE_ID", "Идентификатор повторяется.", entity.id)
        ids.add(entity.id)
    limits = [
        (ir.processes, MAX_PROCESSES, "процессы"),
        (ir.participants, MAX_PARTICIPANTS, "пулы"),
        (ir.lanes, MAX_LANES, "дорожки"),
        (ir.nodes, MAX_NODES, "узлы"),
        ([node for node in ir.nodes if node.type in TASK_TYPES], MAX_TASKS, "действия"),
        (ir.flows, MAX_FLOWS, "связи"),
    ]
    for items, limit, label in limits:
        if len(items) > limit:
            error("PROFILE_LIMIT", f"Превышен лимит профиля: {label} — {limit}.")
    if not ir.processes:
        error("PROCESS_REQUIRED", "В модели должен быть хотя бы один процесс.")

    processes = {process.id: process for process in ir.processes}
    participants = {participant.id: participant for participant in ir.participants}
    lanes = {lane.id: lane for lane in ir.lanes}
    nodes = {node.id: node for node in ir.nodes}
    process_pools: dict[str, list[str]] = defaultdict(list)
    for participant in ir.participants:
        if participant.process_id not in processes:
            error("UNKNOWN_PROCESS", "Пул ссылается на неизвестный процесс.", participant.id)
        process_pools[participant.process_id].append(participant.id)
    for process_id, pool_ids in process_pools.items():
        if len(pool_ids) > 1:
            error(
                "PROCESS_MULTIPLE_POOLS", "В профиле процесс принадлежит одному пулу.", process_id, *pool_ids
            )
    if len(processes) > 1:
        for process_id in processes:
            if process_id not in process_pools:
                error(
                    "PROCESS_POOL_REQUIRED",
                    "Для нескольких процессов нужны явные отдельные пулы.",
                    process_id,
                )
    for lane in ir.lanes:
        if lane.process_id not in processes:
            error("UNKNOWN_PROCESS", "Дорожка ссылается на неизвестный процесс.", lane.id)
    for node in ir.nodes:
        if node.process_id not in processes:
            error("UNKNOWN_PROCESS", "Узел ссылается на неизвестный процесс.", node.id)
        if node.lane_id is not None:
            lane = lanes.get(node.lane_id)
            if lane is None:
                error("UNKNOWN_LANE", "Указана неизвестная дорожка.", node.id, node.lane_id)
            elif lane.process_id != node.process_id:
                error(
                    "LANE_PROCESS_MISMATCH", "Узел и дорожка относятся к разным процессам.", node.id, lane.id
                )
        elif node.type in TASK_TYPES:
            warning("ROLE_UNSPECIFIED", "Для действия не указан исполнитель; требуется уточнение.", node.id)

    incoming: dict[str, list[Flow]] = defaultdict(list)
    outgoing: dict[str, list[Flow]] = defaultdict(list)
    adjacency: dict[str, list[str]] = defaultdict(list)
    reverse: dict[str, list[str]] = defaultdict(list)

    def endpoint_pool(endpoint: str) -> str | None:
        if endpoint in participants:
            return endpoint
        node = nodes.get(endpoint)
        pools = process_pools.get(node.process_id, []) if node else []
        return pools[0] if len(pools) == 1 else None

    for flow in ir.flows:
        if flow.kind == "message":
            for endpoint in (flow.source, flow.target):
                if endpoint not in nodes and endpoint not in participants:
                    error(
                        "UNKNOWN_FLOW_ENDPOINT",
                        "Message flow ссылается на неизвестный узел или пул.",
                        flow.id,
                    )
            # Match the editor's InteractionNode and event-direction rules.
            # Within this profile an end event throws, a start event catches;
            # gateways cannot send/receive messages. Participants and all five
            # supported activity types are interaction nodes in either direction.
            source_node, target_node = nodes.get(flow.source), nodes.get(flow.target)
            if source_node and source_node.type not in TASK_TYPES | {"endEvent"}:
                error(
                    "MESSAGE_ENDPOINT_TYPE",
                    "Источник сообщения: пул, задача или конечное событие; шлюз/начало недопустимы.",
                    flow.id,
                    source_node.id,
                )
            if target_node and target_node.type not in TASK_TYPES | {"startEvent"}:
                error(
                    "MESSAGE_ENDPOINT_TYPE",
                    "Получатель сообщения: пул, задача или начальное событие; шлюз/конец недопустимы.",
                    flow.id,
                    target_node.id,
                )
            source_pool, target_pool = endpoint_pool(flow.source), endpoint_pool(flow.target)
            if source_pool is None or target_pool is None:
                error(
                    "MESSAGE_POOL_REQUIRED",
                    "Концы message flow должны принадлежать определённым пулам.",
                    flow.id,
                )
            elif source_pool == target_pool:
                error("MESSAGE_SAME_POOL", "Message flow соединяет только разные пулы.", flow.id)
            if flow.condition is not None or flow.is_default:
                error("MESSAGE_CONDITION", "У message flow нет условия или признака default.", flow.id)
            continue
        source, target = nodes.get(flow.source), nodes.get(flow.target)
        if source is None or target is None:
            error("UNKNOWN_FLOW_ENDPOINT", "Sequence flow ссылается на неизвестный flow node.", flow.id)
            continue
        if source.process_id != target.process_id:
            error("CROSS_PROCESS_SEQUENCE", "Sequence flow не может пересекать процессы или пулы.", flow.id)
            continue
        incoming[target.id].append(flow)
        outgoing[source.id].append(flow)
        adjacency[source.id].append(target.id)
        reverse[target.id].append(source.id)
        if flow.is_default:
            if source.type != "exclusiveGateway":
                error("DEFAULT_SOURCE", "В профиле default flow задаётся у XOR-шлюза.", flow.id, source.id)
            if flow.condition is not None:
                error("DEFAULT_CONDITION", "Default flow не должен иметь conditionExpression.", flow.id)
        if source.type == "parallelGateway" and (flow.condition is not None or flow.is_default):
            error(
                "PARALLEL_CONDITION", "AND-шлюз не выбирает ветку по условию или default.", flow.id, source.id
            )

    for node in ir.nodes:
        entering, leaving = incoming[node.id], outgoing[node.id]
        if node.type == "startEvent":
            if entering:
                error("START_INCOMING", "Начальное событие не принимает sequence flow.", node.id)
            if not leaving:
                error("NODE_NO_OUTGOING", "У начального события нет продолжения.", node.id)
        elif node.type == "endEvent":
            if leaving:
                error("END_OUTGOING", "Конечное событие не имеет исходящего sequence flow.", node.id)
            if not entering:
                error("NODE_NO_INCOMING", "Конечное событие не связано с процессом.", node.id)
        else:
            if not entering:
                error("NODE_NO_INCOMING", "У узла нет входящего sequence flow.", node.id)
            if not leaving:
                error("NODE_NO_OUTGOING", "У узла нет исходящего sequence flow.", node.id)
        if node.type in {"exclusiveGateway", "parallelGateway"}:
            if len(entering) < 2 and len(leaving) < 2:
                warning("GATEWAY_REDUNDANT", "Шлюз не выполняет разделение или объединение потоков.", node.id)
        if node.type == "exclusiveGateway":
            defaults = [flow for flow in leaving if flow.is_default]
            if len(defaults) > 1:
                error("MULTIPLE_DEFAULTS", "У XOR-шлюза может быть только один default flow.", node.id)
            if len(leaving) > 1:
                for flow in leaving:
                    if not flow.is_default and not flow.condition and not flow.name.strip():
                        warning(
                            "XOR_BRANCH_UNLABELED", "У альтернативы XOR не описано условие выбора.", flow.id
                        )

    for process in ir.processes:
        process_nodes = [node for node in ir.nodes if node.process_id == process.id]
        starts = [node.id for node in process_nodes if node.type == "startEvent"]
        ends = [node.id for node in process_nodes if node.type == "endEvent"]
        if not starts:
            error("START_REQUIRED", "В профиле каждый процесс имеет начальное событие.", process.id)
        if not ends:
            error("END_REQUIRED", "В профиле каждый процесс имеет конечное событие.", process.id)
        reached, can_end = _reachable(starts, adjacency), _reachable(ends, reverse)
        for node in process_nodes:
            if node.id not in reached:
                error("UNREACHABLE_NODE", "Узел недостижим из начального события.", node.id)
            if node.id not in can_end:
                error("NO_PATH_TO_END", "Из узла нет пути к конечному событию.", node.id)

    for provenance in ir.provenance:
        if provenance.element_id not in ids:
            error(
                "PROVENANCE_ELEMENT_UNKNOWN",
                "Цитата привязана к неизвестному элементу.",
                provenance.element_id,
            )
        if provenance.origin == "explicit":
            quote = provenance.exact_quote
            start, end = provenance.source_start, provenance.source_end
            if not quote or quote not in ir.source_text:
                error(
                    "SOURCE_QUOTE_NOT_FOUND", "Цитата отсутствует в исходном описании.", provenance.element_id
                )
            elif (
                start is None
                or end is None
                or start < 0
                or end <= start
                or ir.source_text[start:end] != quote
            ):
                error(
                    "SOURCE_OFFSET_INVALID",
                    "Смещение цитаты не соответствует исходному тексту.",
                    provenance.element_id,
                )
    return issues
