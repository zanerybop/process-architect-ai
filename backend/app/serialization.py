"""Deterministic BPMN 2.0 serialization and bounded-profile swimlane layout."""

from collections import defaultdict
from heapq import heappop, heappush
from math import ceil

from lxml import etree

from backend.app.domain import ProcessIR

NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
}
TASK_TYPES = {"task", "userTask", "serviceTask", "sendTask", "receiveTask"}
NODE_TYPES = TASK_TYPES | {"startEvent", "endEvent", "exclusiveGateway", "parallelGateway"}


def _element(parent, tag, **attrs):
    prefix, local = tag.split(":")
    return etree.SubElement(parent, f"{{{NS[prefix]}}}{local}", **{k: str(v) for k, v in attrs.items()})


def _ranks(nodes, flows):
    """Longest DAG ranks after classifying DFS feedback edges; keep every original edge."""
    outgoing = defaultdict(list)
    for flow in flows:
        outgoing[flow.source].append(flow)
    state, feedback, postorder = {}, set(), []

    def visit(node_id):
        state[node_id] = 1
        for flow in outgoing[node_id]:
            if state.get(flow.target) == 1:
                feedback.add(flow.id)
            elif flow.target not in state:
                visit(flow.target)
        state[node_id] = 2
        postorder.append(node_id)

    for node in sorted(nodes, key=lambda n: n.type != "startEvent"):
        if node.id not in state:
            visit(node.id)
    ranks = {node.id: 0 for node in nodes}
    for node_id in reversed(postorder):
        for flow in outgoing[node_id]:
            if flow.id not in feedback:
                ranks[flow.target] = max(ranks[flow.target], ranks[node_id] + 1)
    return ranks, feedback


def _compact_ranks(nodes, flows):
    """Keep causal order but share a column when work hands over to another lane.

    Every ancestor in the *same* lane advances the column, including ancestors
    reached through other lanes. Independent branches may share a column and
    get separate vertical tracks. Feedback edges never alter the process graph.
    """
    dag_ranks, feedback = _ranks(nodes, flows)
    incoming = defaultdict(list)
    for flow in flows:
        if flow.id not in feedback:
            incoming[flow.target].append(flow.source)
    ranks, ancestors = {}, {}
    for node in sorted(nodes, key=lambda item: dag_ranks[item.id]):
        inherited = {}
        for source in incoming[node.id]:
            for lane, rank in ancestors[source].items():
                inherited[lane] = max(inherited.get(lane, -1), rank)
        ranks[node.id] = max(
            [ranks[source] for source in incoming[node.id]]
            + [inherited.get(node.lane_id, -1) + 1]
        )
        inherited[node.lane_id] = ranks[node.id]
        ancestors[node.id] = inherited
    return ranks, feedback


def _node_size(node):
    if node.type in TASK_TYPES:
        return 176, max(76, min(168, ceil(len(node.name) / 22) * 18 + 24))
    return (50, 50) if node.type.endswith("Gateway") else (36, 36)


def _clear_segment(first, second, obstacles):
    """An orthogonal segment may touch a box boundary, never enter its interior."""
    if first[0] == second[0]:
        return not any(
            x < first[0] < x + width
            and min(first[1], second[1]) < y + height
            and max(first[1], second[1]) > y
            for x, y, width, height in obstacles
        )
    if first[1] == second[1]:
        return not any(
            y < first[1] < y + height
            and min(first[0], second[0]) < x + width
            and max(first[0], second[0]) > x
            for x, y, width, height in obstacles
        )
    return False


def _clear_route(points, obstacles):
    return all(_clear_segment(first, second, obstacles) for first, second in zip(points, points[1:]))


def _simplify_route(points):
    result = []
    for point in points:
        if result and result[-1] == point:
            continue
        if len(result) > 1:
            a, b = result[-2:]
            if (a[0] == b[0] == point[0] or a[1] == b[1] == point[1]) and (
                (b[0] - a[0]) * (point[0] - b[0]) + (b[1] - a[1]) * (point[1] - b[1]) > 0
            ):
                result.pop()
        result.append(point)
    return result


def _route_between(start, end, obstacles, extent):
    """Shortest deterministic orthogonal detour, with a penalty for extra bends.

    The visibility grid is bounded by the process and by at most two coordinates
    per obstacle. Fast one/two-bend candidates cover ordinary flows; A* handles
    crowded columns without drawing through an unrelated task or its caption.
    """
    if start == end:
        return [start]
    direct = [start, end]
    if _clear_route(direct, obstacles):
        return direct
    left, top, right, bottom = extent
    xs = sorted({start[0], end[0], left, right} | {
        value for x, _, width, _ in obstacles for value in (x - 10, x + width + 10)
        if left <= value <= right
    })
    ys = sorted({start[1], end[1], top, bottom} | {
        value for _, y, _, height in obstacles for value in (y - 10, y + height + 10)
        if top <= value <= bottom
    })
    candidates = [[start, (start[0], end[1]), end], [start, (end[0], start[1]), end]]
    candidates.extend([start, (x, start[1]), (x, end[1]), end] for x in xs)
    candidates.extend([start, (start[0], y), (end[0], y), end] for y in ys)

    def length(points):
        return sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(points, points[1:]))

    for points in sorted(candidates, key=length):
        if _clear_route(points, obstacles):
            return _simplify_route(points)
    origin = (xs.index(start[0]), ys.index(start[1]), 0)
    destination = (xs.index(end[0]), ys.index(end[1]))
    pending, cost, previous, clear_cache = [(0, 0, origin)], {origin: 0}, {}, {}
    while pending:
        _, distance, state = heappop(pending)
        if distance != cost.get(state):
            continue
        x_index, y_index, direction = state
        if state[:2] == destination:
            route = []
            while state in previous:
                route.append((xs[state[0]], ys[state[1]]))
                state = previous[state]
            return _simplify_route([start] + list(reversed(route)))
        for dx, dy, axis in ((1, 0, 1), (0, 1, 2), (-1, 0, 1), (0, -1, 2)):
            nx, ny = x_index + dx, y_index + dy
            if not (0 <= nx < len(xs) and 0 <= ny < len(ys)):
                continue
            key = tuple(sorted(((x_index, y_index), (nx, ny))))
            if key not in clear_cache:
                clear_cache[key] = _clear_segment(
                    (xs[x_index], ys[y_index]), (xs[nx], ys[ny]), obstacles
                )
            if not clear_cache[key]:
                continue
            next_state = (nx, ny, axis)
            next_cost = distance + abs(xs[nx] - xs[x_index]) + abs(ys[ny] - ys[y_index])
            next_cost += 28 if direction and direction != axis else 0
            if next_cost >= cost.get(next_state, float("inf")):
                continue
            cost[next_state], previous[next_state] = next_cost, (x_index, y_index, direction)
            heuristic = abs(xs[nx] - end[0]) + abs(ys[ny] - end[1])
            heappush(pending, (next_cost + heuristic, next_cost, next_state))
    # This is a deterministic compiler failure, never a request to the LLM to
    # remove an inconvenient branch or an unsafe line through another node.
    from backend.app.domain import PipelineError

    raise PipelineError("LAYOUT_ROUTE_BLOCKED", "Не удалось проложить связь без пересечения элементов.")


def _geometry(ir):
    bounds, routes, labels = {}, {}, {}
    nodes_by_id = {node.id: node for node in ir.nodes}
    pools_by_process = {pool.process_id: pool.id for pool in ir.participants}
    pool_ids = {pool.id for pool in ir.participants}
    y_offset = 80
    process_layouts = []
    message_offsets, message_gap = {}, 24
    for flow in ir.flows:
        if flow.kind == "message":
            message_gap += max(34, ceil(len(flow.name) / 20) * 16 + 8)
            message_offsets[flow.id] = message_gap
    for process in ir.processes:
        nodes = [node for node in ir.nodes if node.process_id == process.id]
        flows = [
            flow
            for flow in ir.flows
            if flow.kind == "sequence" and nodes_by_id[flow.source].process_id == process.id
        ]
        lanes = [lane for lane in ir.lanes if lane.process_id == process.id]
        participants = [pool for pool in ir.participants if pool.process_id == process.id]
        ranks, feedback = _compact_ranks(nodes, flows)
        groups = [lane.id for lane in lanes]
        if any(node.lane_id is None for node in nodes) or not groups:
            groups.append(None)
        # Named events/gateways reserve their caption width, tasks their actual
        # body width. Empty event columns no longer cost a full task column.
        column_widths = defaultdict(lambda: 50)
        for node in nodes:
            footprint = max(_node_size(node)[0], 140 if node.name else 0)
            column_widths[ranks[node.id]] = max(column_widths[ranks[node.id]], footprint)
        columns, cursor = {}, 154
        for rank in range(max(ranks.values(), default=0) + 1):
            columns[rank] = cursor + column_widths[rank] / 2
            cursor += column_widths[rank] + 64
        width = max(520, cursor - 80)
        group_top = y_offset
        for group in groups:
            members = [node for node in nodes if node.lane_id == group]
            slots = defaultdict(int)
            positions = {}
            for node in members:
                positions[node.id] = slots[ranks[node.id]]
                slots[ranks[node.id]] += 1
            track_above, track_below = defaultdict(int), defaultdict(int)
            for node in members:
                _, node_height = _node_size(node)
                caption = 8 + max(30, ceil(len(node.name) / 20) * 16) if (
                    node.type not in TASK_TYPES and node.name
                ) else 0
                slot = positions[node.id]
                track_above[slot] = max(track_above[slot], node_height / 2)
                track_below[slot] = max(track_below[slot], node_height / 2 + caption)
            track_tops, track_heights, group_height = {}, {}, 12
            for slot in range(max(slots.values(), default=1)):
                track_tops[slot] = group_height
                track_heights[slot] = max(150, track_above[slot] + track_below[slot] + 64)
                group_height += track_heights[slot]
            group_height += 12
            if group is not None:
                bounds[group] = (110, group_top, width - 30, group_height)
            for node in members:
                node_width, node_height = _node_size(node)
                center_x = columns[ranks[node.id]]
                # A shared center line keeps horizontal connections straight;
                # the lower half of each track reserves space for captions.
                slot = positions[node.id]
                center_y = group_top + track_tops[slot] + 32 + track_above[slot]
                bounds[node.id] = (
                    center_x - node_width / 2,
                    center_y - node_height / 2,
                    node_width,
                    node_height,
                )
            group_top += group_height
        # Feedback routes run in a reserved band inside the pool, below the lanes.
        return_band = 22 + len(feedback) * 22
        pool_bottom = group_top + return_band
        for pool in participants:
            bounds[pool.id] = (80, y_offset, width, pool_bottom - y_offset)
        process_layouts.append((nodes, flows, feedback, group_top, pool_bottom, width))
        y_offset = pool_bottom + max(140, message_gap + 24)

    obstacles = [bounds[node.id] for node in ir.nodes]
    obstacles.extend(
        _node_label_bounds(node, bounds[node.id])
        for node in ir.nodes if node.type not in TASK_TYPES and node.name
    )
    for nodes, flows, feedback, group_top, pool_bottom, width in process_layouts:
        process_top = min(bounds[node.id][1] for node in nodes) - 36
        extent = (142, process_top, 80 + width - 12, pool_bottom - 8)
        feedback_index = 0
        for flow in flows:
            sx, sy, sw, sh = bounds[flow.source]
            tx, ty, tw, th = bounds[flow.target]
            start, end = (sx + sw, sy + sh / 2), (tx, ty + th / 2)
            if flow.id in feedback:
                route_y = group_top + 14 + feedback_index * 22
                feedback_index += 1
                proposed = [
                    start,
                    (sx + sw + 32, start[1]),
                    (sx + sw + 32, route_y),
                    (tx - 32, route_y),
                    (tx - 32, end[1]),
                    end,
                ]
                if _clear_route(proposed, obstacles):
                    routes[flow.id] = proposed
                else:
                    outbound = _route_between(start, (start[0] + 20, route_y), obstacles, extent)
                    inbound = _route_between((end[0] - 20, route_y), end, obstacles, extent)
                    routes[flow.id] = _simplify_route(outbound + inbound)
            else:
                # Handover between tasks in the same column reads vertically;
                # otherwise preserve the familiar left-to-right sequence ports.
                if sx + sw / 2 == tx + tw / 2 and (
                    nodes_by_id[flow.source].type in TASK_TYPES
                    and nodes_by_id[flow.target].type in TASK_TYPES
                ):
                    downward = ty > sy
                    start = (sx + sw / 2, sy + sh if downward else sy)
                    end = (tx + tw / 2, ty if downward else ty + th)
                routes[flow.id] = _route_between(start, end, obstacles, extent)
    for flow in (flow for flow in ir.flows if flow.kind == "message"):

        def pool_for(endpoint):
            return endpoint if endpoint in pool_ids else pools_by_process[nodes_by_id[endpoint].process_id]

        source_pool, target_pool = bounds[pool_for(flow.source)], bounds[pool_for(flow.target)]
        upper, lower = sorted([source_pool, target_pool], key=lambda box: box[1])
        gap_y = upper[1] + upper[3] + message_offsets[flow.id]

        def to_gap(endpoint, pool):
            x, y, width, height = bounds[endpoint]
            direction = 1 if pool[1] == upper[1] else -1
            anchor_y = y + height if direction == 1 else y
            center_x = x + width / 2
            extent = (142, upper[1] - 12, max(upper[0] + upper[2], lower[0] + lower[2]) - 12,
                      lower[1] + lower[3] + 12)
            anchor = (center_x, anchor_y)
            if endpoint in pool_ids:
                return [anchor, (center_x, gap_y)]
            # Top/bottom ports and obstacle-aware routing work even when another
            # role occupies the same compacted column between this task and the gap.
            return _route_between(anchor, (center_x, gap_y), obstacles, extent)

        source_path, target_path = to_gap(flow.source, source_pool), to_gap(flow.target, target_pool)
        routes[flow.id] = source_path + list(reversed(target_path))
        labels[flow.id] = ((source_path[-1][0] + target_path[-1][0]) / 2, gap_y)
    return bounds, routes, labels


def _node_label_bounds(node, bounds):
    x, y, width, height = bounds
    return (x + width / 2 - 70, y + height + 8, 140, max(30, ceil(len(node.name) / 20) * 16))


def _overlap_area(first, second, padding=0):
    x, y, width, height = first
    ox, oy, ow, oh = second
    return max(0, min(x + width, ox + ow + padding) - max(x, ox - padding)) * max(
        0, min(y + height, oy + oh + padding) - max(y, oy - padding)
    )


def _route_segments(points):
    """Join collinear pieces for label placement without changing edge waypoints."""
    turns = []
    for point in points:
        if turns and point == turns[-1]:
            continue
        if len(turns) >= 2:
            first, second = turns[-2:]
            if (first[0] == second[0] == point[0] or first[1] == second[1] == point[1]) and (
                (second[0] - first[0]) * (point[0] - second[0])
                + (second[1] - first[1]) * (point[1] - second[1]) > 0
            ):
                turns.pop()
        turns.append(point)
    return sorted(
        zip(turns, turns[1:]),
        key=lambda segment: -sum(abs(a - b) for a, b in zip(*segment)),
    )


def _flow_label_bounds(ir, bounds, routes, message_anchors):
    """Place labels beside their own segments, avoiding shapes and earlier labels.

    A shared first bend is not a branch label anchor. Prefer a long segment and
    search both sides locally; never move nodes/routes or enlarge the pool.
    """
    boxes = {}
    obstacles = [bounds[node.id] for node in ir.nodes]
    obstacles.extend(
        _node_label_bounds(node, bounds[node.id])
        for node in ir.nodes if node.type not in TASK_TYPES and node.name
    )
    nodes = {node.id: node for node in ir.nodes}
    pools = {pool.process_id: bounds[pool.id] for pool in ir.participants}
    # Preserve the existing message labels in the space between pools, and treat
    # them as obstacles when placing sequence labels near a pool boundary.
    for flow in ir.flows:
        if flow.name and flow.id in message_anchors:
            x, y = message_anchors[flow.id]
            height = max(26, ceil(len(flow.name) / 20) * 16)
            boxes[flow.id] = (x - 75, y - height - 6, 150, height)
            obstacles.append(boxes[flow.id])
    for flow in ir.flows:
        if not flow.name or flow.kind != "sequence":
            continue
        width, height = 150, max(26, ceil(len(flow.name) / 20) * 16)
        pool = pools.get(nodes[flow.source].process_id)
        container = (pool[0] + 38, pool[1] + 8, pool[2] - 46, pool[3] - 16) if pool else None
        other_edges = [
            (min(first[0], second[0]) - 1, min(first[1], second[1]) - 1,
             abs(second[0] - first[0]) + 2, abs(second[1] - first[1]) + 2)
            for other_id, points in routes.items() if other_id != flow.id
            for first, second in zip(points, points[1:]) if first != second
        ]
        segments = _route_segments(routes[flow.id])
        candidates = []
        for gap in (8, 20, 36, 56, 80):
            for first, second in segments:
                for fraction in (0.5, 0.25, 0.75, 0.1, 0.9, 0, 1):
                    x = first[0] + (second[0] - first[0]) * fraction
                    y = first[1] + (second[1] - first[1]) * fraction
                    if first[1] == second[1]:
                        positions = ((x - width / 2, y - height - gap), (x - width / 2, y + gap))
                    else:
                        positions = ((x + gap, y - height / 2), (x - width - gap, y - height / 2))
                    candidates.extend((px, py, width, height) for px, py in positions)

        def penalty(box):
            overlap = sum(_overlap_area(box, obstacle, padding=6) for obstacle in obstacles)
            overlap += sum(_overlap_area(box, obstacle, padding=3) for obstacle in other_edges)
            outside = width * height - _overlap_area(box, container) if container else 0
            return overlap + outside * 4

        # Stable order breaks ties. In an exceptionally crowded graph retain the
        # least obstructed nearby label rather than expanding or rewriting it.
        best, best_penalty = candidates[0], float("inf")
        for box in candidates:
            score = penalty(box)
            if score < best_penalty:
                best, best_penalty = box, score
            if score == 0:
                break
        boxes[flow.id] = best
        obstacles.append(best)
    return boxes


def serialize(ir: ProcessIR) -> str:
    if ir.document is not None:
        from backend.app.document_serialization import serialize_document

        return serialize_document(ir)
    """Compile a validated IR without changing its semantics or its in-memory values."""
    from backend.app.domain import PipelineError
    from backend.app.graph import validate_graph

    errors = [issue for issue in validate_graph(ir) if issue.severity == "error"]
    if errors:
        raise PipelineError(errors[0].code, errors[0].message, errors[0].element_ids)
    root = etree.Element(
        f"{{{NS['bpmn']}}}definitions",
        nsmap=NS,
        id="PA_Definitions",
        targetNamespace="https://process-architect.local/bpmn",
        exporter="Process Architect AI",
        exporterVersion="0.1.0",
    )
    node_map = {node.id: node for node in ir.nodes}
    for process in ir.processes:
        process_element = _element(
            root, "bpmn:process", id=process.id, name=process.name, isExecutable="false"
        )
        lanes = [lane for lane in ir.lanes if lane.process_id == process.id]
        if lanes:
            lane_set = _element(process_element, "bpmn:laneSet", id=f"PA_LaneSet_{process.id}")
            for lane in lanes:
                lane_element = _element(lane_set, "bpmn:lane", id=lane.id, name=lane.name)
                for node in ir.nodes:
                    if node.lane_id == lane.id:
                        _element(lane_element, "bpmn:flowNodeRef").text = node.id
        for node in (node for node in ir.nodes if node.process_id == process.id):
            incoming = [flow for flow in ir.flows if flow.kind == "sequence" and flow.target == node.id]
            outgoing = [flow for flow in ir.flows if flow.kind == "sequence" and flow.source == node.id]
            attrs = {"id": node.id, "name": node.name}
            if node.type.endswith("Gateway"):
                attrs["gatewayDirection"] = (
                    "Mixed"
                    if len(incoming) > 1 and len(outgoing) > 1
                    else "Diverging"
                    if len(outgoing) > 1
                    else "Converging"
                    if len(incoming) > 1
                    else "Unspecified"
                )
            for flow in outgoing:
                if flow.is_default:
                    attrs["default"] = flow.id
            node_element = _element(process_element, f"bpmn:{node.type}", **attrs)
            for flow in incoming:
                _element(node_element, "bpmn:incoming").text = flow.id
            for flow in outgoing:
                _element(node_element, "bpmn:outgoing").text = flow.id
        for flow in ir.flows:
            if flow.kind != "sequence" or node_map[flow.source].process_id != process.id:
                continue
            element = _element(
                process_element,
                "bpmn:sequenceFlow",
                id=flow.id,
                name=flow.name,
                sourceRef=flow.source,
                targetRef=flow.target,
            )
            if flow.condition is not None and not flow.is_default:
                condition = _element(element, "bpmn:conditionExpression")
                condition.set(f"{{{NS['xsi']}}}type", "bpmn:tFormalExpression")
                condition.text = flow.condition
    if ir.participants:
        collaboration = _element(root, "bpmn:collaboration", id="PA_Collaboration")
        for participant in ir.participants:
            _element(
                collaboration,
                "bpmn:participant",
                id=participant.id,
                name=participant.name,
                processRef=participant.process_id,
            )
        for flow in (flow for flow in ir.flows if flow.kind == "message"):
            _element(
                collaboration,
                "bpmn:messageFlow",
                id=flow.id,
                name=flow.name,
                sourceRef=flow.source,
                targetRef=flow.target,
            )
    diagram = _element(root, "bpmndi:BPMNDiagram", id="PA_Diagram")
    plane = _element(
        diagram,
        "bpmndi:BPMNPlane",
        id="PA_Plane",
        bpmnElement="PA_Collaboration" if ir.participants else ir.processes[0].id,
    )
    bounds, routes, labels = _geometry(ir)
    flow_labels = _flow_label_bounds(ir, bounds, routes, labels)
    # Container DI comes first so bpmn-js can establish the parent hierarchy.
    for item in [*ir.participants, *ir.lanes, *ir.nodes]:
        attrs = {"id": f"PA_Shape_{item.id}", "bpmnElement": item.id}
        if item in ir.participants or item in ir.lanes:
            attrs["isHorizontal"] = "true"
        shape = _element(plane, "bpmndi:BPMNShape", **attrs)
        x, y, width, height = bounds[item.id]
        _element(shape, "dc:Bounds", x=x, y=y, width=width, height=height)
        if item.id in node_map and node_map[item.id].type not in TASK_TYPES and item.name:
            label = _element(shape, "bpmndi:BPMNLabel")
            lx, ly, lw, lh = _node_label_bounds(item, bounds[item.id])
            _element(label, "dc:Bounds", x=lx, y=ly, width=lw, height=lh)
    for flow in ir.flows:
        edge = _element(plane, "bpmndi:BPMNEdge", id=f"PA_Edge_{flow.id}", bpmnElement=flow.id)
        points = []
        for point in routes[flow.id]:
            if not points or point != points[-1]:
                points.append(point)
        for x, y in points:
            _element(edge, "di:waypoint", x=x, y=y)
        if flow.name:
            label = _element(edge, "bpmndi:BPMNLabel")
            x, y, width, height = flow_labels[flow.id]
            _element(label, "dc:Bounds", x=x, y=y, width=width, height=height)
    return etree.tostring(root, encoding="utf-8", xml_declaration=True, pretty_print=True).decode("utf-8")
