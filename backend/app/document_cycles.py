"""A narrow token-persistence proof, not an advanced BPMN execution engine.

OMG BPMN 2.0.2 sections 13.3.1, 13.3.4 and 13.4.1/13.4.3:
https://www.omg.org/spec/BPMN/2.0.2/PDF

After a token enters an unconditional task/AND/OR cycle, every transition that
consumes its last internal token creates another internal token. Ordinary end
events on other branches cannot empty this set. A blocked join cannot empty it
either. We prove entry only along a single-token unconditional prefix. This
deliberately misses more complex reachable cycles instead of guessing OR joins,
conditional execution, cancellation or the behavior of a surrounding MI scope.
"""

import re
from collections import defaultdict

from backend.app.behavior import BehaviorFinding, ProcessBehaviorReport

TASKS = {
    "task",
    "userTask",
    "manualTask",
    "serviceTask",
    "sendTask",
    "receiveTask",
    "businessRuleTask",
    "scriptTask",
}
CYCLE_NODES = TASKS | {"parallelGateway", "inclusiveGateway"}
PLAIN_NODES = CYCLE_NODES | {"startEvent", "endEvent", "exclusiveGateway"}


def forced_cycle_reports(index):
    """Return only proven local failures; no result means no additional proof."""
    from backend.app.document_validation import is_type, local, scope, standard

    if index.issues:  # Duplicate IDs must not accidentally resolve into a witness.
        return []
    semantic = set(index.elements)

    def children(item):
        return [child for child in item if child in semantic]

    def safe_context(container):
        ancestors = [container, *container.iterancestors()]
        process = next((item for item in ancestors if is_type(item, "Process")), None)
        if process is None:
            return False
        # A callable process can be canceled by a caller outside its XML ancestry.
        if any(
            local(item) == "callActivity" and index.ref(item, "calledElement") is process
            for item in index.elements
        ):
            return False
        for owner in ancestors:
            if owner is process.getparent():
                break
            if not standard(owner) or local(owner) not in {"process", "subProcess"}:
                return False
            if owner.get("triggeredByEvent") in {"true", "1"}:
                return False
            if owner.get("isForCompensation") in {"true", "1"}:
                return False
            if owner.get("startQuantity", "1") != "1" or owner.get("completionQuantity", "1") != "1":
                return False
            for child in children(owner):
                if local(child) == "standardLoopCharacteristics":
                    return False
                if local(child) == "multiInstanceLoopCharacteristics":
                    cardinality = [c for c in children(child) if local(c) == "loopCardinality"]
                    if (
                        len(cardinality) != 1
                        or not re.fullmatch(r"[1-9][0-9]*", (cardinality[0].text or "").strip())
                        or len(cardinality[0])
                        or child.get("behavior", "All") != "All"
                        or any(
                            local(c) in {"completionCondition", "complexBehaviorDefinition"}
                            for c in children(child)
                        )
                        or child.get("oneBehaviorEventRef")
                        or child.get("noneBehaviorEventRef")
                    ):
                        return False
        # Conservative context exclusion: even a sibling can cancel an ancestor.
        # The single exception is a noninterrupting message event-subprocess start;
        # its other descendants must still pass these cancellation checks.
        for item in process.iterdescendants():
            if item not in semantic:
                continue
            kind = local(item)
            if kind in {
                "boundaryEvent",
                "adHocSubProcess",
                "transaction",
                "eventDefinitionRef",
                "completionCondition",
                "complexBehaviorDefinition",
            }:
                return False
            if kind == "subProcess" and item.get("triggeredByEvent") in {"true", "1"}:
                starts = [c for c in children(item) if local(c) == "startEvent"]
                if len(starts) != 1 or starts[0].get("isInterrupting", "true") not in {"false", "0"}:
                    return False
            if is_type(item, "EventDefinition"):
                event = item.getparent()
                owner = event.getparent()
                if not (
                    kind == "messageEventDefinition"
                    and local(event) == "startEvent"
                    and event.get("isInterrupting", "true") in {"false", "0"}
                    and local(owner) == "subProcess"
                    and owner.get("triggeredByEvent") in {"true", "1"}
                ):
                    return False
        return True

    reports = []
    for container in index.elements:
        if (
            local(container) not in {"process", "subProcess"}
            or not container.get("id")
            or not safe_context(container)
        ):
            continue
        nodes = [item for item in children(container) if is_type(item, "FlowNode")]
        flows = [item for item in children(container) if is_type(item, "SequenceFlow")]
        if not nodes or len(nodes) > 200 or len(flows) > 1000:
            continue
        if any(
            local(item) not in PLAIN_NODES
            or not item.get("id")
            or item.get("isForCompensation") in {"true", "1"}
            or item.get("startQuantity", "1") != "1"
            or item.get("completionQuantity", "1") != "1"
            or item.get("instantiate") in {"true", "1"}
            or any(
                local(c)
                in {"standardLoopCharacteristics", "multiInstanceLoopCharacteristics", "eventDefinitionRef"}
                or is_type(c, "EventDefinition")
                for c in children(item)
            )
            for item in nodes
        ):
            continue
        starts = [item for item in nodes if local(item) == "startEvent"]
        if len(starts) != 1:
            continue
        incoming, outgoing, unconditional = defaultdict(list), defaultdict(list), []
        valid = True
        for edge in flows:
            source, target = index.ref(edge, "sourceRef"), index.ref(edge, "targetRef")
            if (
                not edge.get("id")
                or source not in nodes
                or target not in nodes
                or scope(source) is not container
                or scope(target) is not container
            ):
                valid = False
                break
            outgoing[source].append((target, edge))
            incoming[target].append((source, edge))
            if not any(local(c) == "conditionExpression" for c in children(edge)):
                if not source.get("default") or index.ref(source, "default") is not edge:
                    unconditional.append(edge)
        if not valid or incoming[starts[0]]:
            continue
        if any(node.get("default") and index.ref(node, "default") is None for node in nodes):
            continue
        graph = {
            node: [
                (target, edge)
                for target, edge in outgoing[node]
                if edge in unconditional and local(target) in CYCLE_NODES
            ]
            for node in nodes
            if local(node) in CYCLE_NODES
        }
        components = _strong_components(graph)
        cyclic = {
            node: component
            for component in components
            for node in component
            if len(component) > 1 or any(target is node for target, _ in graph[node])
        }
        # A sole token follows this prefix. Multi-output nodes and XOR stop the
        # proof before the SCC; graph reachability alone is insufficient evidence.
        prefix, current, seen = [], starts[0], set()
        while current not in seen:
            seen.add(current)
            prefix.append(current)
            kind = local(current)
            if kind == "parallelGateway" and len(incoming[current]) != 1:
                break
            if current in cyclic:
                component = cyclic[current]
                cycle = _cycle_witness(graph, component, current)
                internal = [edge for node in component for target, edge in graph[node] if target in component]
                finding = BehaviorFinding(
                    code="BEHAVIOR_FORCED_CYCLE",
                    message=(
                        f"Область {container.get('id')} при входе в её начальное событие не может "
                        "штатно завершиться: безусловный цикл сохраняет токен или активную работу. "
                        "Обычный конец на соседней ветке не завершает остальные ветви. "
                        "Это локальная проверка области, а не полный анализ внешних событий или MI."
                    ),
                    element_ids=[
                        item.get("id")
                        for item in [
                            container,
                            *sorted(component, key=lambda n: n.get("id")),
                            *sorted(internal, key=lambda e: e.get("id")),
                        ]
                    ],
                    path=[item.get("id") for item in [*prefix[:-1], *cycle]],
                )
                reports.append(
                    ProcessBehaviorReport(
                        process_id=container.get("id") or "anonymous_scope",
                        status="failed",
                        findings=[finding],
                    )
                )
                break
            if kind not in CYCLE_NODES | {"startEvent"} or len(outgoing[current]) != 1:
                break
            target, edge = outgoing[current][0]
            if edge not in unconditional:
                break
            current = target
    return reports


def _strong_components(graph):
    """Bounded Tarjan; XML scopes above the explicit limits are not analyzed."""
    numbers, low, stack, active, found = {}, {}, [], set(), []

    def visit(node):
        numbers[node] = low[node] = len(numbers)
        stack.append(node)
        active.add(node)
        for target, _ in graph[node]:
            if target not in numbers:
                visit(target)
                low[node] = min(low[node], low[target])
            elif target in active:
                low[node] = min(low[node], numbers[target])
        if low[node] == numbers[node]:
            component = set()
            while True:
                item = stack.pop()
                active.remove(item)
                component.add(item)
                if item is node:
                    break
            found.append(component)

    for node in graph:
        if node not in numbers:
            visit(node)
    return found


def _cycle_witness(graph, component, entry):
    """A real directed walk from the entry to a repeated node, no synthetic hops."""
    path, visited, node = [], set(), entry
    while node not in visited:
        visited.add(node)
        path.append(node)
        node = next(target for target, _ in graph[node] if target in component)
    return [*path, node]
