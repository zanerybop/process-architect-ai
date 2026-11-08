"""Bounded, data-abstract token analysis of each supported process independently.

Sequence flows are places; nodes are transitions. XOR selects one outgoing flow,
AND consumes one token from *every* incoming flow and produces one on each output.
Tasks merge incoming flows without synchronization. Plain end events consume tokens;
several ends, parallel endings and loops with an exit are all legitimate.

A passed result means every reachable abstract marking can reach completion within
the completely explored finite state space. It is not inevitable termination, a proof
of natural-language conditions, or collaboration/message delivery soundness.
See OMG BPMN 2.0.2, sections 13.3, 13.4 and 13.5.
"""

from collections import defaultdict, deque
from typing import Literal

from pydantic import Field

from backend.app.domain import Flow, Issue, Model, Node, ProcessIR

Status = Literal["passed", "failed", "inconclusive"]
Marking = tuple[int, ...]
SUPPORTED_TYPES = {
    "startEvent",
    "endEvent",
    "task",
    "userTask",
    "serviceTask",
    "sendTask",
    "receiveTask",
    "exclusiveGateway",
    "parallelGateway",
}


class BehaviorLimits(Model):
    max_states: int = Field(default=4096, ge=1, le=100_000)
    max_transitions: int = Field(default=20_000, ge=1, le=1_000_000)
    max_tokens: int = Field(default=32, ge=1, le=256)
    max_steps: int = Field(default=256, ge=1, le=4096)


class BehaviorFinding(Model):
    code: str
    message: str
    element_ids: list[str] = Field(default_factory=list)
    path: list[str] = Field(default_factory=list)
    token_flows: dict[str, int] = Field(default_factory=dict)


class ProcessBehaviorReport(Model):
    process_id: str
    status: Status = "inconclusive"
    explored_states: int = 0
    explored_transitions: int = 0
    maximum_tokens: int = 0
    complete_exploration: bool = False
    findings: list[BehaviorFinding] = Field(default_factory=list)


class BehaviorReport(Model):
    status: Status = "inconclusive"
    scope: Literal["per_process_control_flow"] = "per_process_control_flow"
    collaboration: Literal["not_applicable", "inconclusive"] = "not_applicable"
    conditions: Literal["nondeterministic_not_evaluated"] = "nondeterministic_not_evaluated"
    processes: list[ProcessBehaviorReport] = Field(default_factory=list)
    limits: BehaviorLimits = Field(default_factory=BehaviorLimits)
    limitations: list[str] = Field(
        default_factory=lambda: [
            "Условия XOR считаются возможными альтернативами; их смысл и совместимость не вычисляются.",
            "Проверяется возможность завершения, а не обязательное завершение каждого повторения цикла.",
            "Внешние ответы, сообщения, длительность и выполнение действий не моделируются.",
        ]
    )


def behavior_issues(report: BehaviorReport) -> list[Issue]:
    """Project structured diagnostics onto the existing API Issue contract."""
    issues = []
    for process in report.processes:
        for finding in process.findings:
            witness = " → ".join(finding.path)
            issues.append(
                Issue(
                    code=finding.code,
                    category="model",
                    severity="error" if process.status == "failed" else "warning",
                    message=finding.message,
                    element_ids=finding.element_ids,
                    witness_path=finding.path,
                    suggested_action=(
                        "Проверьте тип слияния и условия достижения всех входящих ветвей. "
                        + (f"Путь проверки: {witness}." if witness else "")
                    )
                    if process.status == "failed"
                    else "Нужна ручная проверка поведения процесса.",
                )
            )
    if report.collaboration == "inconclusive":
        issues.append(
            Issue(
                code="BEHAVIOR_COLLABORATION_NOT_CHECKED",
                category="model",
                severity="warning",
                message=(
                    "Потоки внутри процессов проверены; доставка сообщений "
                    "и взаимные ожидания пулов не проверены."
                ),
            )
        )
    return issues


def _analyze_process(process_id: str, nodes: list[Node], flows: list[Flow], limits: BehaviorLimits):
    report = ProcessBehaviorReport(process_id=process_id)
    by_id = {node.id: node for node in nodes}
    starts = [node for node in nodes if node.type == "startEvent"]
    incoming: dict[str, list[int]] = defaultdict(list)
    outgoing: dict[str, list[int]] = defaultdict(list)
    for index, flow in enumerate(flows):
        incoming[flow.target].append(index)
        outgoing[flow.source].append(index)

    def inconclusive(code, message, ids):
        report.findings.append(BehaviorFinding(code=code, message=message, element_ids=ids))
        return report

    if len(nodes) > 80 or len(flows) > 320:
        return inconclusive(
            "BEHAVIOR_PROFILE_LIMIT", "Граф превышает профиль проверки поведения.", [process_id]
        )
    if len(starts) != 1:
        return inconclusive(
            "BEHAVIOR_START_SEMANTICS",
            "Для проверки требуется ровно одно начальное событие процесса.",
            [node.id for node in starts] or [process_id],
        )
    invalid = [node.id for node in nodes if node.type not in SUPPORTED_TYPES]
    invalid.extend(flow.id for flow in flows if flow.source not in by_id or flow.target not in by_id)
    for node in nodes:
        if node.type == "startEvent":
            if incoming[node.id] or not outgoing[node.id]:
                invalid.append(node.id)
        elif node.type == "endEvent":
            if outgoing[node.id] or not incoming[node.id]:
                invalid.append(node.id)
        elif not incoming[node.id] or not outgoing[node.id]:
            invalid.append(node.id)
    if invalid or len(by_id) != len(nodes) or len({flow.id for flow in flows}) != len(flows):
        return inconclusive(
            "BEHAVIOR_INVALID_GRAPH",
            "Сначала исправьте структуру графа или неподдерживаемые узлы.",
            sorted(set(invalid)) or [process_id],
        )
    conditional = [
        flow.id
        for flow in flows
        if (flow.condition is not None or flow.is_default) and by_id[flow.source].type != "exclusiveGateway"
    ]
    if conditional:
        return inconclusive(
            "BEHAVIOR_UNSUPPORTED_CONDITION",
            "Условия вне XOR требуют отдельной семантики исполнения.",
            conditional,
        )
    start = starts[0]
    initial = tuple(1 if index in outgoing[start.id] else 0 for index in range(len(flows)))
    report.maximum_tokens = sum(initial)
    if report.maximum_tokens > limits.max_tokens:
        return inconclusive(
            "BEHAVIOR_TOKEN_LIMIT", "Превышен лимит одновременно проверяемых токенов.", [start.id]
        )
    pending = deque([initial])
    predecessors: dict[Marking, tuple[Marking, str] | None] = {initial: None}
    depths = {initial: 0}
    reverse: dict[Marking, set[Marking]] = defaultdict(set)
    completed: set[Marking] = set()

    def witness(marking):
        path = []
        while predecessors[marking] is not None:
            marking, node_id = predecessors[marking]
            path.append(node_id)
        return [start.id, *reversed(path)]

    def stop(code, message, marking, ids, status="inconclusive", next_node=None):
        report.status = status
        report.explored_states = len(predecessors)
        report.findings.append(
            BehaviorFinding(
                code=code,
                message=message,
                element_ids=ids,
                path=[*witness(marking), *([next_node] if next_node else [])],
                token_flows={flow.id: marking[index] for index, flow in enumerate(flows) if marking[index]},
            )
        )
        return report

    while pending:
        marking = pending.popleft()
        if not any(marking):
            completed.add(marking)
            continue
        enabled = False
        for node in nodes:
            if node.type == "startEvent":
                continue
            inputs = incoming[node.id]
            if node.type == "parallelGateway":
                choices = [inputs] if all(marking[index] for index in inputs) else []
            else:
                choices = [[index] for index in inputs if marking[index]]
            outputs = outgoing[node.id]
            branches = [[index] for index in outputs] if node.type == "exclusiveGateway" else [outputs]
            for consumed in choices:
                for produced in branches:
                    enabled = True
                    if report.explored_transitions >= limits.max_transitions:
                        return stop(
                            "BEHAVIOR_TRANSITION_LIMIT",
                            "Достигнут лимит проверяемых переходов.",
                            marking,
                            [node.id],
                        )
                    report.explored_transitions += 1
                    candidate = list(marking)
                    for index in consumed:
                        candidate[index] -= 1
                    for index in produced:
                        candidate[index] += 1
                    successor = tuple(candidate)
                    report.maximum_tokens = max(report.maximum_tokens, sum(successor))
                    if sum(successor) > limits.max_tokens:
                        return stop(
                            "BEHAVIOR_TOKEN_LIMIT",
                            "Превышен лимит одновременно проверяемых токенов.",
                            marking,
                            [node.id],
                            next_node=node.id,
                        )
                    reverse[successor].add(marking)
                    if successor in predecessors:
                        continue
                    if len(predecessors) >= limits.max_states:
                        return stop(
                            "BEHAVIOR_STATE_LIMIT",
                            "Достигнут лимит состояний; проверка не завершена.",
                            marking,
                            [node.id],
                        )
                    if depths[marking] >= limits.max_steps:
                        return stop(
                            "BEHAVIOR_STEP_LIMIT", "Достигнут лимит длины пути проверки.", marking, [node.id]
                        )
                    predecessors[successor] = (marking, node.id)
                    depths[successor] = depths[marking] + 1
                    pending.append(successor)
        if not enabled:
            blocked = sorted({flows[index].target for index, count in enumerate(marking) if count})
            return stop(
                "BEHAVIOR_DEADLOCK",
                "Возможен тупик: токены остались в процессе, но ни один узел не может продолжить работу.",
                marking,
                blocked,
                status="failed",
            )

    report.complete_exploration = True
    report.explored_states = len(predecessors)
    can_complete = set(completed)
    pending = deque(completed)
    while pending:
        for previous in reverse[pending.popleft()]:
            if previous not in can_complete:
                can_complete.add(previous)
                pending.append(previous)
    trapped = next((marking for marking in predecessors if marking not in can_complete), None)
    if trapped is not None:
        return stop(
            "BEHAVIOR_NO_COMPLETION",
            "Из достигнутого состояния невозможно завершить процесс.",
            trapped,
            sorted({flows[index].target for index, count in enumerate(trapped) if count}),
            status="failed",
        )
    report.status = "passed"
    return report


def validate_behavior(ir: ProcessIR, limits: BehaviorLimits | None = None) -> BehaviorReport:
    """Analyze local control flow, retaining inconclusive outcomes without silent PASS."""
    report = BehaviorReport(limits=limits or BehaviorLimits())
    if ir.document is not None:
        from backend.app.document_serialization import build_document_tree
        from backend.app.document_validation import validate_document_behavior

        return validate_document_behavior(build_document_tree(ir))
    process_ids = {process.id for process in ir.processes}
    nodes = {node.id: node for node in ir.nodes}
    if any(flow.kind == "message" for flow in ir.flows) or len(process_ids) > 1:
        report.collaboration = "inconclusive"
    invalid = [node.id for node in ir.nodes if node.process_id not in process_ids]
    invalid.extend(
        flow.id
        for flow in ir.flows
        if flow.kind == "sequence"
        and (
            flow.source not in nodes
            or flow.target not in nodes
            or nodes[flow.source].process_id != nodes[flow.target].process_id
        )
    )
    if invalid or len(process_ids) != len(ir.processes):
        report.processes.append(
            ProcessBehaviorReport(
                process_id="",
                findings=[
                    BehaviorFinding(
                        code="BEHAVIOR_INVALID_GRAPH",
                        message="Сначала исправьте структуру графа.",
                        element_ids=sorted(set(invalid)),
                    )
                ],
            )
        )
        return report
    for process_id in sorted(process_ids):
        process_nodes = sorted(
            (node for node in ir.nodes if node.process_id == process_id), key=lambda n: n.id
        )
        process_flows = sorted(
            (
                flow
                for flow in ir.flows
                if flow.kind == "sequence" and nodes[flow.source].process_id == process_id
            ),
            key=lambda f: f.id,
        )
        report.processes.append(_analyze_process(process_id, process_nodes, process_flows, report.limits))
    if any(process.status == "failed" for process in report.processes):
        report.status = "failed"
    elif report.processes and all(process.status == "passed" for process in report.processes):
        report.status = "passed" if report.collaboration == "not_applicable" else "inconclusive"
    return report
