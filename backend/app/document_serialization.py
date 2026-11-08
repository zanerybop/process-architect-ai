"""Lossless schema-document serialization and deterministic hierarchical BPMN DI.

This path does not project the document onto the legacy ProcessIR node subset.
Layout is a presentation operation: semantic XML, references and expressions are
never replaced to make a graph easier to draw. Existing DI is preserved unless
the caller explicitly requests a complete rearrangement.
"""

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass, field, replace
from math import ceil

from lxml import etree

from backend.app.bpmn_schema import NAMESPACES, canonical, catalog, qualified
from backend.app.document_validation import effective_type, in_metadata, semantic_tag
from backend.app.domain import PipelineError, ProcessIR
from backend.app.serialization import _clear_route, _overlap_area, _route_between, _route_segments

NS = NAMESPACES
BPMN = NS["bpmn"]
DIAGRAM = qualified("bpmndi:BPMNDiagram")
CONTAINERS = {"subProcess", "transaction", "adHocSubProcess", "subChoreography", "subConversation"}
DATA_SHAPES = {"dataObject", "dataObjectReference", "dataStoreReference", "dataInput", "dataOutput"}
ARTIFACT_SHAPES = {"textAnnotation", "group"}
EDGE_TYPES = {
    "sequenceFlow",
    "messageFlow",
    "association",
    "dataInputAssociation",
    "dataOutputAssociation",
    "conversationLink",
}


def _local(element):
    return semantic_tag(element)


def _id(element):
    value = element.get("id")
    if not value:
        raise PipelineError("LAYOUT_ID_REQUIRED", "Для графического элемента требуется XML ID.")
    return value


def _ref(value, context=None):
    value = value or ""
    if ":" in value and context is not None:
        prefix = value.split(":", 1)[0]
        allowed = {BPMN, context.getroottree().getroot().get("targetNamespace")}
        if context.nsmap.get(prefix) not in allowed:
            return ""
    return value.split(":")[-1]


def _is(element, base):
    if etree.QName(element).namespace != BPMN:
        return False
    type_name = effective_type(element)
    return bool(type_name and catalog().derived_from(type_name, "bpmn:" + base))


def build_document_tree(ir: ProcessIR) -> etree._Element:
    """Build the complete semantic tree; XSD validation remains a separate gate."""
    if ir.document is None:
        raise PipelineError("DOCUMENT_REQUIRED", "Не найден BPMN document IR.")
    namespaces = dict(NS)
    for prefix, uri in ir.document.namespaces.items():
        if prefix in NS and NS[prefix] != uri:
            raise PipelineError("DOCUMENT_NAMESPACE", "Стандартное пространство имён переопределено.")
        namespaces[prefix] = uri
    records = {item.key: item for item in ir.document.elements}
    if len(records) != len(ir.document.elements):
        raise PipelineError("DOCUMENT_DUPLICATE_KEY", "Ключ элемента документа повторяется.")
    roots = [item for item in records.values() if item.parent is None]
    if len(roots) != 1 or qualified(roots[0].tag, namespaces) != qualified("bpmn:definitions"):
        raise PipelineError("DOCUMENT_ROOT", "Нужен единственный корневой элемент definitions.")
    children = defaultdict(list)
    for item in records.values():
        if item.parent is not None and item.parent not in records:
            raise PipelineError("DOCUMENT_PARENT", "Родитель элемента документа не найден.")
        children[item.parent].append(item)
    visited = set()

    def create(item, parent_type=None, depth=0):
        if item.key in visited or depth > 64:
            raise PipelineError("DOCUMENT_CYCLE", "Циклическое или слишком глубокое вложение XML.")
        visited.add(item.key)
        tag = qualified(item.tag, namespaces)
        attrs = {
            qualified(key, namespaces) if ":" in key or key.startswith("{") else key: value
            for key, value in item.attributes.items()
        }
        element = etree.Element(
            tag,
            attrib=attrs,
            nsmap={key or None: value for key, value in namespaces.items()} if item.parent is None else None,
        )
        element.text, element.tail = item.text, getattr(item, "tail", None)
        type_name = catalog().element_type(canonical(tag), parent_type)
        xsi_type = attrs.get(qualified("xsi:type"))
        if xsi_type:
            type_name = canonical(qualified(xsi_type, namespaces))
        ordered = children[item.key]
        # Mixed content and foreign extensions are ordered text, not XML slots.
        if type_name and not catalog().types.get(type_name, {}).get("mixed"):
            ordered = sorted(
                ordered,
                key=lambda child: catalog().child_rank(
                    type_name, canonical(qualified(child.tag, namespaces))
                ),
            )
        for child in ordered:
            element.append(create(child, type_name, depth + 1))
        return element

    try:
        root = create(roots[0])
    except (ValueError, KeyError, TypeError) as exc:
        raise PipelineError("DOCUMENT_XML", "Некорректное имя, пространство имён или текст XML.") from exc
    if len(visited) != len(records):
        raise PipelineError("DOCUMENT_CYCLE", "Часть документа недостижима от definitions.")
    return root


@dataclass
class Shape:
    element: etree._Element
    box: tuple[float, float, float, float]
    role: str = "node"
    attrs: dict[str, str] = field(default_factory=dict)
    host: str | None = None
    label: tuple[float, float, float, float] | None = None
    di_id: str = ""


@dataclass
class Part:
    width: float = 360
    height: float = 180
    shapes: list[Shape] = field(default_factory=list)
    scopes: dict[str, tuple] = field(default_factory=dict)

    def move(self, x, y):
        def moved(box):
            return (box[0] + x, box[1] + y, box[2], box[3])

        return Part(
            self.width,
            self.height,
            [
                replace(shape, box=moved(shape.box), label=moved(shape.label) if shape.label else None)
                for shape in self.shapes
            ],
            {key: moved(box) for key, box in self.scopes.items()},
        )


def _caption(element, box):
    name = element.get("name", "")
    if not name:
        return None
    x, y, width, height = box
    caption_width = min(160, max(80, len(name) * 6.5 + 12))
    return (
        x + width / 2 - caption_width / 2,
        y + height + 8,
        caption_width,
        max(32, ceil(len(name) / 22) * 17),
    )


def _size(element):
    name, kind = element.get("name", ""), _local(element)
    if _is(element, "tGateway"):
        return 54, 54
    if _is(element, "tEvent"):
        return 38, 38
    if kind in DATA_SHAPES:
        return (66, 70) if kind == "dataStoreReference" else (50, 66)
    if kind == "textAnnotation":
        text = "".join(element.itertext())
        return 200, max(60, ceil(len(text) / 26) * 18 + 20)
    if kind == "group":
        return 240, 140
    if _is(element, "tConversationNode"):
        return 190, 100
    if _is(element, "tChoreographyActivity"):
        return 196, 84 + 30 * max(2, len(element.findall("bpmn:participantRef", NS)))
    return 150, max(76, ceil(len(name) / 21) * 18 + 24)


def _external(element):
    return _is(element, "tEvent") or _is(element, "tGateway") or _local(element) in DATA_SHAPES


def _ranks(nodes, flows, attached):
    """Stable DFS cycle breaking only for ranks; no semantic flow is removed."""
    ids = {_id(node) for node in nodes}
    outgoing = defaultdict(list)
    for flow in flows:
        source, target = _ref(flow.get("sourceRef"), flow), _ref(flow.get("targetRef"), flow)
        source = attached.get(source, source)
        if source in ids and target in ids:
            outgoing[source].append(target)
    state, feedback, postorder = {}, set(), []

    def visit(node_id):
        state[node_id] = 1
        for target in outgoing[node_id]:
            if state.get(target) == 1:
                feedback.add((node_id, target))
            elif target not in state:
                visit(target)
        state[node_id] = 2
        postorder.append(node_id)

    for node in sorted(nodes, key=lambda item: _local(item) != "startEvent"):
        if _id(node) not in state:
            visit(_id(node))
    ranks = dict.fromkeys(ids, 0)
    for source in reversed(postorder):
        for target in outgoing[source]:
            if (source, target) not in feedback:
                ranks[target] = max(ranks[target], ranks[source] + 1)
    return ranks


def _lane_rows(scope, nodes):
    lanes, paths, rows, assignments = [], {}, [], {}

    def walk(lane, ancestors):
        lane_id = _id(lane)
        lanes.append(lane)
        paths[lane_id] = [*ancestors, lane_id]
        for reference in lane.findall("bpmn:flowNodeRef", NS):
            assignments[_ref(reference.text, reference)] = lane_id
        nested = lane.findall("bpmn:childLaneSet/bpmn:lane", NS)
        if not nested:
            rows.append(lane_id)
        for child in nested:
            walk(child, paths[lane_id])

    for lane in scope.findall("bpmn:laneSet/bpmn:lane", NS):
        walk(lane, [])
    # Position control nodes near their connected work, without adding any
    # semantic lane membership or assigning responsibility for a gateway/event.
    semantic_assignments = dict(assignments)
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for flow in scope.findall("bpmn:sequenceFlow", NS):
        source, target = _ref(flow.get("sourceRef"), flow), _ref(flow.get("targetRef"), flow)
        outgoing[source].append(target)
        incoming[target].append(source)
    for node in nodes:
        node_id = _id(node)
        if node_id in assignments or not (_is(node, "tEvent") or _is(node, "tGateway")):
            continue
        preferred = incoming if len(outgoing[node_id]) > 1 or _local(node) == "endEvent" else outgoing
        fallback = outgoing if preferred is incoming else incoming
        for adjacency in (preferred, fallback):
            queue, seen = deque(adjacency[node_id]), {node_id}
            while queue:
                candidate = queue.popleft()
                if candidate in seen:
                    continue
                seen.add(candidate)
                if candidate in semantic_assignments:
                    assignments[node_id] = semantic_assignments[candidate]
                    break
                queue.extend(adjacency[candidate])
            if node_id in assignments:
                break
    used = {assignments.get(_id(node)) for node in nodes}
    for lane in lanes:
        if _id(lane) in used and _id(lane) not in rows:
            rows.append(_id(lane))
    if None in used or not rows:
        rows.append(None)
    return lanes, paths, rows, assignments


def _scope(scope, id_map):
    """Bottom-up layout; expanded descendants reserve their true outer sizes."""
    children = [
        child for child in scope if isinstance(child.tag, str) and etree.QName(child).namespace == BPMN
    ]
    boundaries = [child for child in children if _local(child) == "boundaryEvent"]
    nodes = [
        child
        for child in children
        if (_is(child, "tFlowNode") or _is(child, "tConversationNode")) and child not in boundaries
    ]
    extras = [child for child in children if _local(child) in DATA_SHAPES | ARTIFACT_SHAPES]
    # Activity-local parameters are represented by their owning activity; drawing
    # them as independent data objects produces misleading unconnected shapes.
    # Process I/O remains a visible part of the process-level interface.
    if _is(scope, "tProcess"):
        extras.extend(scope.findall("bpmn:ioSpecification/bpmn:dataInput", NS))
        extras.extend(scope.findall("bpmn:ioSpecification/bpmn:dataOutput", NS))
    # A referenced dataObject usually has no separate drawing, but a connection
    # may address its semantic ID directly. Such an endpoint needs its own DI;
    # substituting a reference's ID would change which XML element is connected.
    direct_endpoints = set()
    for edge in scope.iter():
        if (
            not isinstance(edge.tag, str)
            or etree.QName(edge).namespace != BPMN
            or in_metadata(edge)
            or _local(edge) not in EDGE_TYPES
        ):
            continue
        for name in ("sourceRef", "targetRef"):
            if edge.get(name):
                direct_endpoints.add(_ref(edge.get(name), edge))
            for reference in edge.findall(f"bpmn:{name}", NS):
                direct_endpoints.add(_ref(reference.text, reference))
    referenced_data = {_ref(item.get("dataObjectRef"), item) for item in extras}
    extras = [
        item for item in extras
        if not (
            _local(item) == "dataObject"
            and _id(item) in referenced_data
            and _id(item) not in direct_endpoints
        )
    ]
    attached = {_id(event): _ref(event.get("attachedToRef"), event) for event in boundaries}
    hosts = defaultdict(list)
    for event in boundaries:
        hosts[attached[_id(event)]].append(event)
    flows = [child for child in children if _local(child) == "sequenceFlow"]
    ranks = _ranks(nodes, flows, attached)
    lanes, lane_paths, rows, assignments = _lane_rows(scope, nodes)
    nested, sizes, footprints = {}, {}, {}
    for node in nodes:
        node_id = _id(node)
        if _local(node) in CONTAINERS:
            nested[node_id] = _scope(node, id_map)
            sizes[node_id] = (nested[node_id].width, nested[node_id].height)
        else:
            sizes[node_id] = _size(node)
        width, height = sizes[node_id]
        sizes[node_id] = (max(width, len(hosts[node_id]) * 60 + 30), height)
        caption = _caption(node, (0, 0, width, height)) if _external(node) else None
        footprints[node_id] = (
            max(70, caption[3] + 12 if caption else 0)
            if hosts[node_id]
            else (caption[3] + 12 if caption else 0)
        )
    column_width = defaultdict(float)
    for node in nodes:
        width, height = sizes[_id(node)]
        caption = _caption(node, (0, 0, width, height)) if _external(node) else None
        column_width[ranks[_id(node)]] = max(
            column_width[ranks[_id(node)]], width, caption[2] if caption else 0
        )
    depth = max((len(value) for value in lane_paths.values()), default=0)
    columns, cursor = {}, 40 + 30 * depth
    for rank in range(max(ranks.values(), default=0) + 1):
        columns[rank], cursor = cursor, cursor + column_width[rank] + 60
    part = Part(width=max(360, cursor - 20))
    row_bounds, y = {}, 56
    for row in rows:
        group = [node for node in nodes if assignments.get(_id(node)) == row]
        column_totals = defaultdict(float)
        for node in group:
            node_id = _id(node)
            rank = ranks[node_id]
            if column_totals[rank]:
                column_totals[rank] += 52
            column_totals[rank] += sizes[node_id][1] + footprints[node_id]
        row_height = max(140, max(column_totals.values(), default=0) + 60)
        column_y = {rank: y + (row_height - total) / 2 for rank, total in column_totals.items()}
        for node in group:
            node_id, rank = _id(node), ranks[_id(node)]
            width, height = sizes[node_id]
            x, ny = columns[rank] + (column_width[rank] - width) / 2, column_y[rank]
            box = (x, ny, width, height)
            attrs = {"isExpanded": "true"} if node_id in nested else {}
            if _local(node) == "exclusiveGateway":
                attrs["isMarkerVisible"] = "true"
            role = "container" if node_id in nested else "node"
            part.shapes.append(
                Shape(node, box, role, attrs, label=_caption(node, box) if _external(node) else None)
            )
            if node_id in nested:
                inner = nested[node_id].move(x, ny)
                part.shapes.extend(inner.shapes)
                part.scopes.update(inner.scopes)
            if _is(node, "tChoreographyActivity"):
                participants = [
                    _ref(reference.text, reference) for reference in node.findall("bpmn:participantRef", NS)
                ]
                initiator = _ref(node.get("initiatingParticipantRef"), node)
                participants.sort(key=lambda item: item != initiator)
                for index, participant_id in enumerate(participants):
                    participant = id_map.get(participant_id)
                    if participant is None:
                        continue
                    by = ny if index == 0 else ny + height - (len(participants) - index) * 30
                    kind = "top" if index == 0 else "bottom" if index == len(participants) - 1 else "middle"
                    kind += "_initiating" if participant_id == initiator else "_non_initiating"
                    part.shapes.append(
                        Shape(
                            participant,
                            (x, by, width, 30),
                            "band",
                            {"participantBandKind": kind},
                            host=node_id,
                        )
                    )
            for index, event in enumerate(hosts[node_id]):
                bx = x + (index + 1) * width / (len(hosts[node_id]) + 1) - 19
                boundary_box = (bx, ny + height - 19, 38, 38)
                part.shapes.append(
                    Shape(event, boundary_box, "boundary", host=node_id, label=_caption(event, boundary_box))
                )
            column_y[rank] = ny + height + footprints[node_id] + 52
        bottom = y + row_height
        row_bounds[row] = (y, bottom)
        y = bottom
    for lane in lanes:
        lane_id = _id(lane)
        covered = [
            bounds for row, bounds in row_bounds.items() if row is not None and lane_id in lane_paths[row]
        ]
        if covered:
            top, bottom = min(item[0] for item in covered), max(item[1] for item in covered)
            x = 30 + (len(lane_paths[lane_id]) - 1) * 30
            part.shapes.insert(
                0, Shape(lane, (x, top, part.width - x - 20, bottom - top), "lane", {"isHorizontal": "true"})
            )
    if extras:
        x, band_height = 70, 0
        for item in extras:
            width, height = _size(item)
            if x + width > part.width - 30:
                x, y, band_height = 70, y + band_height + 66, 0
            box = (x, y + 44, width, height)
            label = _caption(item, box) if _external(item) else None
            part.shapes.append(Shape(item, box, "group" if _local(item) == "group" else "data", label=label))
            band_height = max(band_height, height + (label[3] + 8 if label else 0))
            x += max(170, width) + 60
        y += band_height + 84
    part.height = max(220, y + 36)
    part.scopes[_id(scope)] = (0, 0, part.width, part.height)
    return part


def _plane_parts(root):
    id_map = {
        item.get("id"): item
        for item in root.iter()
        if isinstance(item.tag, str) and item.get("id") and not in_metadata(item)
    }
    processes = [item for item in root if isinstance(item.tag, str) and _is(item, "tProcess")]
    consumed, planes = set(), []
    for container in root:
        if not isinstance(container.tag, str) or not _is(container, "tCollaboration"):
            continue
        if _is(container, "tChoreography"):
            planes.append((container, _scope(container, id_map).move(80, 80)))
            continue
        part, y = Part(width=520), 80
        for participant in container.findall("bpmn:participant", NS):
            process_id = _ref(participant.get("processRef"), participant)
            process = id_map.get(process_id)
            if process is not None and _is(process, "tProcess"):
                inner = _scope(process, id_map)
                moved = inner.move(110, y)
                part.shapes.append(
                    Shape(
                        participant, (80, y, inner.width + 30, inner.height), "pool", {"isHorizontal": "true"}
                    )
                )
                part.shapes.extend(moved.shapes)
                part.scopes.update(moved.scopes)
                consumed.add(process_id)
                width, height = inner.width + 30, inner.height
            else:
                width, height = 520, 150
                part.shapes.append(
                    Shape(participant, (80, y, width, height), "pool", {"isHorizontal": "true"})
                )
            part.width = max(part.width, 80 + width)
            y += height + 180
        # Conversation nodes and collaboration artifacts share the same plane.
        if any(
            _is(item, "tConversationNode") or _local(item) in ARTIFACT_SHAPES
            for item in container
            if isinstance(item.tag, str)
        ):
            conversation = _scope(container, id_map).move(80, y)
            part.shapes.extend(conversation.shapes)
            part.scopes.update(conversation.scopes)
            part.width = max(part.width, 80 + conversation.width)
            y += conversation.height + 80
        part.height = max(300, y)
        part.scopes[_id(container)] = (40, 40, part.width + 80, part.height)
        planes.append((container, part))
    for process in processes:
        if _id(process) not in consumed:
            planes.append((process, _scope(process, id_map).move(80, 80)))
    return planes, id_map


def _edge_endpoints(edge, shapes_by_id, id_map):
    """Resolve semantic I/O refs to their visible owner when they lack a shape."""

    def visible(reference):
        value, context = reference if isinstance(reference, tuple) else (reference, edge)
        candidate = id_map.get(_ref(value, context))
        while candidate is not None:
            candidate_id = candidate.get("id")
            if candidate_id in shapes_by_id:
                return shapes_by_id[candidate_id]
            candidate = candidate.getparent()
        return None

    kind = _local(edge)
    if kind in {"dataInputAssociation", "dataOutputAssociation"}:
        sources = [(item.text, item) for item in edge.findall("bpmn:sourceRef", NS)]
        target = edge.find("bpmn:targetRef", NS)
        targets = [(target.text, target)] if target is not None else []
        # The activity/event is the visual endpoint of its input/output parameter.
        owner = edge.getparent()
        if kind == "dataInputAssociation" and owner is not None and owner.get("id") in shapes_by_id:
            targets = [owner.get("id")]
        if kind == "dataOutputAssociation" and owner is not None and owner.get("id") in shapes_by_id:
            sources = [owner.get("id")]
    else:
        sources, targets = [edge.get("sourceRef")], [edge.get("targetRef")]
    return [
        (source, target)
        for source in map(visible, sources)
        for target in map(visible, targets)
        if source is not None and target is not None
    ]


def _ports(source, target):
    x, y, width, height = source.box
    tx, ty, tw, th = target.box
    if source.role == "band" and target.role == "band":
        return (x + width, y + height / 2), (tx + tw, ty + th / 2)
    if source.role == "boundary":
        start = (x + width / 2, y + height)
    elif tx >= x + width:
        start = (x + width, y + height / 2)
    elif ty >= y + height:
        start = (x + width / 2, y + height)
    else:
        start = (x, y + height / 2)
    if ty >= y + height and tx < x + width:
        end = (tx + tw / 2, ty)
    elif tx >= x + width:
        end = (tx, ty + th / 2)
    else:
        end = (tx + tw, ty + th / 2)
    return start, end


def _data_ports(source, target):
    """Keep data arrows off sequence-flow ports and dock perpendicularly."""
    x, y, width, height = source.box
    tx, ty, tw, th = target.box
    # Distinct input/output attachment points also avoid a shared data trunk at
    # an activity with both an input and an output. Parameters remain semantic.
    sx = x + width * (2 / 3 if _is(source.element, "tActivity") else 1 / 2)
    ex = tx + tw * (1 / 3 if _is(target.element, "tActivity") else 1 / 2)
    clearance = 16
    if y + height <= ty:
        start, end = (sx, y + height), (ex, ty)
        return start, end, (sx, start[1] + clearance), (ex, end[1] - clearance)
    if ty + th <= y:
        start, end = (sx, y), (ex, ty + th)
        return start, end, (sx, start[1] - clearance), (ex, end[1] + clearance)
    # Horizontally arranged data is less common. Use a separate vertical offset
    # on the facing edge instead of the sequence-flow centre port.
    sy, ey = y + height * 2 / 3, ty + th * 2 / 3
    if x + width <= tx:
        start, end = (x + width, sy), (tx, ey)
        return start, end, (start[0] + clearance, sy), (end[0] - clearance, ey)
    if tx + tw <= x:
        start, end = (x, sy), (tx + tw, ey)
        return start, end, (start[0] - clearance, sy), (end[0] + clearance, ey)
    # Overlapping/container endpoints need the ordinary bounded router.
    start, end = _ports(source, target)
    return start, end, start, end


def _edge_layout(root, part, id_map):
    by_id = {}
    for shape in part.shapes:
        if shape.role != "band":
            by_id.setdefault(_id(shape.element), shape)
    bands = {}
    for shape in part.shapes:
        if shape.role == "band":
            bands[(shape.host, _id(shape.element))] = shape
    message_hosts = defaultdict(list)
    for shape in part.shapes:
        if _is(shape.element, "tChoreographyActivity"):
            for reference in shape.element.findall("bpmn:messageFlowRef", NS):
                message_hosts[_ref(reference.text, reference)].append(_id(shape.element))
    routes = []
    for edge in root.iter():
        if (
            not isinstance(edge.tag, str)
            or in_metadata(edge)
            or etree.QName(edge).namespace != BPMN
            or _local(edge) not in EDGE_TYPES
        ):
            continue
        endpoints = _edge_endpoints(edge, by_id, id_map)
        if _local(edge) == "messageFlow":
            endpoints.extend(
                (
                    bands[(host, _ref(edge.get("sourceRef"), edge))],
                    bands[(host, _ref(edge.get("targetRef"), edge))],
                )
                for host in message_hosts.get(edge.get("id"), [])
                if (host, _ref(edge.get("sourceRef"), edge)) in bands
                and (host, _ref(edge.get("targetRef"), edge)) in bands
            )
        for source, target in endpoints:
            excluded = set()
            # A boundary event docks on the host's bottom edge and its port is
            # outside. Its outgoing route must not turn back through the host.
            excluded.update(item.host for item in (source, target) if item.host and item.role != "boundary")
            for shape in (source, target):
                host = id_map.get(shape.host)
                if host is not None:
                    excluded.update(item.get("id") for item in host.iterancestors())
            for endpoint in (source.element, target.element):
                excluded.update(item.get("id") for item in endpoint.iterancestors())
                ancestor_ids = {endpoint.get("id"), *(item.get("id") for item in endpoint.iterancestors())}
                excluded.update(
                    _id(shape.element)
                    for shape in part.shapes
                    if shape.role == "pool"
                    and _ref(shape.element.get("processRef"), shape.element) in ancestor_ids
                )
            obstacles = [
                shape.box
                if shape is source or shape is target
                else (shape.box[0] - 10, shape.box[1] - 10, shape.box[2] + 20, shape.box[3] + 20)
                for shape in part.shapes
                if shape.role not in {"lane", "band", "group"} and _id(shape.element) not in excluded
            ]
            obstacles.extend(shape.box for shape in (source, target) if shape.role == "band")
            obstacles.extend(
                shape.label for shape in part.shapes if shape.label and _id(shape.element) not in excluded
            )
            extent = (
                40,
                40,
                max(part.width + 160, *(s.box[0] + s.box[2] + 70 for s in part.shapes)),
                max(part.height + 160, *(s.box[1] + s.box[3] + 70 for s in part.shapes)),
            )
            for ancestor in edge.iterancestors():
                bounds = part.scopes.get(ancestor.get("id"))
                if bounds:
                    extent = (
                        bounds[0] + 8,
                        bounds[1] + 8,
                        bounds[0] + bounds[2] - 8,
                        bounds[1] + bounds[3] - 8,
                    )
                    break
            if _local(edge) in {"dataInputAssociation", "dataOutputAssociation"}:
                start, end, source_stub, target_stub = _data_ports(source, target)
                routed = _route_between(source_stub, target_stub, obstacles, extent)
                points = [start, *routed, end]
                points = [point for i, point in enumerate(points) if not i or point != points[i - 1]]
            else:
                start, end = _ports(source, target)
                points = _route_between(start, end, obstacles, extent)
            assert _clear_route(points, obstacles)
            routes.append((edge, source, target, points))
    labels, obstacles = (
        {},
        [
            shape.box
            for shape in part.shapes
            if shape.role not in {"pool", "lane", "band", "group", "container"}
        ],
    )
    obstacles.extend(
        (shape.box[0] + 20, shape.box[1] + 6, shape.box[2] - 40, 24)
        for shape in part.shapes
        if shape.role == "container"
    )
    obstacles.extend(shape.label for shape in part.shapes if shape.label)
    for shape in part.shapes:
        if shape.role in {"pool", "lane"}:
            x, y, width, height = shape.box
            obstacles.extend([(x, y - 1, width, 2), (x, y + height - 1, width, 2)])
    # Labels must not sit over either their own line or a neighbouring branch.
    for edge, _, _, points in routes:
        for first, second in _route_segments(points):
            obstacles.append(
                (
                    min(first[0], second[0]) - 1,
                    min(first[1], second[1]) - 1,
                    abs(second[0] - first[0]) + 2,
                    abs(second[1] - first[1]) + 2,
                )
            )
        # bpmn-js draws a messageRef envelope at half the route length, with
        # the referenced message's caption below it, independently of BPMNLabel.
        # Reserve that display area without removing either semantic name.
        message = id_map.get(_ref(edge.get("messageRef"), edge)) if _local(edge) == "messageFlow" else None
        if message is not None:
            lengths = [abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(points, points[1:])]
            remaining = sum(lengths) / 2
            for first, second, length in zip(points, points[1:], lengths):
                if length and remaining <= length:
                    fraction = remaining / length
                    mx = first[0] + (second[0] - first[0]) * fraction
                    my = first[1] + (second[1] - first[1]) * fraction
                    obstacles.append((mx - 14, my - 10, 28, 20))
                    if message.get("name"):
                        obstacles.append(
                            (mx - 60, my + 12, 120, max(32, ceil(len(message.get("name")) / 14) * 16 + 10))
                        )
                    break
                remaining -= length
    for index, (edge, _, _, points) in enumerate(routes):
        name = edge.get("name", "")
        if not name:
            continue
        width = min(160, max(64, len(name) * 6.5 + 12))
        height = max(30, ceil(len(name) / 22) * 17)
        candidates = []
        # Near-target segments distinguish branches that share an initial trunk.
        segments = sorted(
            _route_segments(points),
            key=lambda segment: abs(segment[1][0] - points[-1][0]) + abs(segment[1][1] - points[-1][1]),
        )
        for gap in (10, 20, 32, 50, 80):
            for first, second in segments:
                for fraction in (0.5, 0.25, 0.75, 0.1, 0.9):
                    x = first[0] + (second[0] - first[0]) * fraction
                    y = first[1] + (second[1] - first[1]) * fraction
                    positions = (
                        ((x - width / 2, y - height - gap), (x - width / 2, y + gap))
                        if first[1] == second[1]
                        else ((x + gap, y - height / 2), (x - width - gap, y - height / 2))
                    )
                    candidates.extend((px, py, width, height) for px, py in positions)
        # Short vertical links between adjacent choreography bands may have no
        # room at either side. Keep a fallback near the route's outer endpoints.
        if points and _local(edge) == "messageFlow":
            left, right = min(p[0] for p in points), max(p[0] for p in points)
            top, bottom = min(p[1] for p in points), max(p[1] for p in points)
            for gap in (10, 20, 32, 50):
                for x in ((left + right - width) / 2, right + gap, left - width - gap):
                    candidates.extend(
                        [(x, top - height - gap, width, height), (x, bottom + gap, width, height)]
                    )
        if candidates:
            label = min(
                candidates, key=lambda box: sum(_overlap_area(box, other, padding=6) for other in obstacles)
            )
            labels[index] = label
            obstacles.append(label)
    return routes, labels


def arrange_document(root: etree._Element, *, preserve_existing=True) -> etree._Element:
    """Return a copy with DI for every display root. Explicit False replaces DI only."""
    result = deepcopy(root)
    diagrams = result.findall("bpmndi:BPMNDiagram", NS)
    if diagrams and preserve_existing:
        return result
    for diagram in diagrams:
        result.remove(diagram)
    planes, id_map = _plane_parts(result)
    occupied = {item.get("id") for item in result.iter() if isinstance(item.tag, str)}
    serial = 0

    def identifier(prefix):
        nonlocal serial
        while True:
            serial += 1
            candidate = f"DI_{prefix}_{serial}"
            if candidate not in occupied:
                occupied.add(candidate)
                return candidate

    def add(parent, tag, **attrs):
        return etree.SubElement(parent, qualified(tag), **{key: str(value) for key, value in attrs.items()})

    def bounds(parent, box):
        add(parent, "dc:Bounds", **dict(zip(("x", "y", "width", "height"), box)))

    for semantic_root, part in planes:
        diagram = add(result, "bpmndi:BPMNDiagram", id=identifier("Diagram"))
        plane = add(diagram, "bpmndi:BPMNPlane", id=identifier("Plane"), bpmnElement=_id(semantic_root))
        # Parent containers precede children; participant bands follow their host.
        for shape in part.shapes:
            shape.di_id = identifier("Shape")
        hosts = {_id(shape.element): shape.di_id for shape in part.shapes if shape.role != "band"}
        for shape in part.shapes:
            attrs = dict(shape.attrs)
            if shape.role == "band":
                attrs["choreographyActivityShape"] = hosts[shape.host]
            node = add(plane, "bpmndi:BPMNShape", id=shape.di_id, bpmnElement=_id(shape.element), **attrs)
            bounds(node, shape.box)
            if shape.label:
                bounds(add(node, "bpmndi:BPMNLabel"), shape.label)
        routes, labels = _edge_layout(result, part, id_map)
        for index, (edge, source, target, points) in enumerate(routes):
            node = add(
                plane,
                "bpmndi:BPMNEdge",
                id=identifier("Edge"),
                bpmnElement=_id(edge),
                sourceElement=source.di_id,
                targetElement=target.di_id,
            )
            for x, y in points:
                add(node, "di:waypoint", x=x, y=y)
            if index in labels:
                bounds(add(node, "bpmndi:BPMNLabel"), labels[index])
    # definitions has relationships after diagrams; keep the official XSD order.
    # Comments and processing instructions have no XSD slot. Keep their exact
    # positions while ordering only element children (imported XML may contain both).
    ordered = iter(
        sorted(
            (item for item in result if isinstance(item.tag, str)),
            key=lambda item: catalog().child_rank("bpmn:tDefinitions", canonical(item.tag)),
        )
    )
    result[:] = [next(ordered) if isinstance(item.tag, str) else item for item in result]
    return result


def serialize_document(ir: ProcessIR) -> str:
    root = arrange_document(build_document_tree(ir))
    return etree.tostring(root, encoding="utf-8", xml_declaration=True, pretty_print=True).decode("utf-8")
