"""Offline official-XSD, graph and DI gates for the exact submitted XML revision."""

import math
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urlparse

from lxml import etree

from backend.app.document_validation import validate_document
from backend.app.domain import Flow, Issue, Lane, Node, Participant, Process, ProcessIR
from backend.app.serialization import NODE_TYPES, NS

MAX_XML_BYTES = 2_000_000
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas" / "bpmn"
SCHEMA_FILES = {"BPMN20.xsd", "BPMNDI.xsd", "Semantic.xsd", "DC.xsd", "DI.xsd"}


class LocalSchemasOnly(etree.Resolver):
    def resolve(self, url, public_id, context):
        parsed = urlparse(url)
        # A drive letter is interpreted as a URI scheme on Windows.
        drive_path = len(parsed.scheme) == 1 and url[1:3] in (":\\", ":/")
        if parsed.scheme and parsed.scheme != "file" and not drive_path:
            raise OSError("Внешние схемы запрещены")
        filename = Path(unquote(parsed.path) if parsed.scheme == "file" else url).name
        if filename not in SCHEMA_FILES:
            raise OSError("Схема не входит в локальный разрешённый набор")
        return self.resolve_filename(str(SCHEMA_DIR / filename), context)


@lru_cache(maxsize=1)
def _schema():
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    parser.resolvers.add(LocalSchemasOnly())
    return etree.XMLSchema(etree.parse(str(SCHEMA_DIR / "BPMN20.xsd"), parser))


def _issue(code, message, ids=()):
    return Issue(code=code, message=message, element_ids=[item for item in ids if item])


def _local(element):
    return etree.QName(element).localname


def _ref(value):
    # QName values from bpmn-js may have a namespace prefix; IDs are local to the document.
    return (value or "").split(":")[-1]


def _read_graph(root):
    issues, processes, participants, lanes, nodes, flows = [], [], [], [], [], []
    id_map = {}
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        element_id = element.get("id")
        if element_id:
            if element_id in id_map:
                issues.append(_issue("DUPLICATE_XML_ID", "ID XML повторяется.", [element_id]))
            id_map[element_id] = element
        references = [
            element.get(key) for key in ("sourceRef", "targetRef", "processRef", "default", "bpmnElement")
        ]
        if _local(element) in {"incoming", "outgoing", "flowNodeRef"}:
            references.append(element.text)
        for reference in references:
            if reference and ":" in reference:
                prefix = reference.split(":", 1)[0]
                if element.nsmap.get(prefix) not in {root.get("targetNamespace"), NS["bpmn"]}:
                    issues.append(
                        _issue(
                            "EXTERNAL_ELEMENT_REFERENCE",
                            "Ссылка указывает за пределы пространства имён модели.",
                            [element_id],
                        )
                    )
    allowed_process = NODE_TYPES | {"laneSet", "sequenceFlow", "documentation", "extensionElements"}
    allowed_root = {"process", "collaboration", "documentation", "extensionElements"}
    for element in root:
        if isinstance(element.tag, str) and etree.QName(element).namespace == NS["bpmn"]:
            if _local(element) not in allowed_root:
                issues.append(
                    _issue("UNSUPPORTED_XML_ELEMENT", "Конструкция XML вне профиля MVP.", [element.get("id")])
                )
    process_nodes = {}
    node_lanes = {}
    for process in root.findall("bpmn:process", NS):
        process_id = process.get("id", "")
        processes.append(Process(id=process_id, name=process.get("name", "")))
        for element in process:
            if isinstance(element.tag, str) and _local(element) not in allowed_process:
                issues.append(
                    _issue(
                        "UNSUPPORTED_XML_ELEMENT",
                        "Конструкция процесса вне профиля MVP.",
                        [element.get("id")],
                    )
                )
        for lane in process.findall("bpmn:laneSet/bpmn:lane", NS):
            lane_id = lane.get("id", "")
            lanes.append(Lane(id=lane_id, process_id=process_id, name=lane.get("name", "")))
            if lane.find("bpmn:childLaneSet", NS) is not None:
                issues.append(
                    _issue("UNSUPPORTED_XML_ELEMENT", "Вложенные дорожки вне профиля MVP.", [lane_id])
                )
            for reference in lane.findall("bpmn:flowNodeRef", NS):
                node_id = _ref(reference.text)
                if node_id in node_lanes:
                    issues.append(
                        _issue(
                            "MULTIPLE_LANE_ASSIGNMENT",
                            "Узел указан более чем в одной дорожке.",
                            [node_id, lane_id],
                        )
                    )
                node_lanes[node_id] = lane_id
                target = id_map.get(node_id)
                if target is None or target.getparent() is not process or _local(target) not in NODE_TYPES:
                    issues.append(
                        _issue(
                            "INVALID_LANE_REFERENCE",
                            "Дорожка ссылается на чужой или неизвестный узел.",
                            [lane_id, node_id],
                        )
                    )
        for element in process:
            if not isinstance(element.tag, str) or _local(element) not in NODE_TYPES:
                continue
            node_id = element.get("id", "")
            process_nodes[node_id] = process_id
            nodes.append(
                Node(
                    id=node_id,
                    type=_local(element),
                    name=element.get("name", ""),
                    process_id=process_id,
                    lane_id=node_lanes.get(node_id),
                )
            )
            allowed_node_children = {"incoming", "outgoing", "documentation", "extensionElements"}
            if any(
                isinstance(child.tag, str) and _local(child) not in allowed_node_children for child in element
            ) or element.get("isForCompensation") in {"true", "1"}:
                issues.append(
                    _issue(
                        "UNSUPPORTED_XML_ELEMENT", "Специальная семантика узла вне профиля MVP.", [node_id]
                    )
                )
    node_ids = {node.id for node in nodes}
    for process in root.findall("bpmn:process", NS):
        process_id = process.get("id", "")
        for element in process.findall("bpmn:sequenceFlow", NS):
            flow_id, source, target = (
                element.get("id", ""),
                _ref(element.get("sourceRef")),
                _ref(element.get("targetRef")),
            )
            condition = element.find("bpmn:conditionExpression", NS)
            source_element = id_map.get(source)
            default = _ref(source_element.get("default")) if source_element is not None else ""
            flows.append(
                Flow(
                    id=flow_id,
                    source=source,
                    target=target,
                    name=element.get("name", ""),
                    condition="".join(condition.itertext()) if condition is not None else None,
                    is_default=default == flow_id,
                )
            )
            if source in node_ids and process_nodes[source] != process_id:
                issues.append(
                    _issue("FLOW_PROCESS_MISMATCH", "Поток расположен в другом процессе.", [flow_id])
                )
        for element in process:
            if not isinstance(element.tag, str) or _local(element) not in NODE_TYPES:
                continue
            node_id = element.get("id", "")
            default = _ref(element.get("default"))
            if default:
                target = id_map.get(default)
                if (
                    target is None
                    or _local(target) != "sequenceFlow"
                    or _ref(target.get("sourceRef")) != node_id
                ):
                    issues.append(
                        _issue(
                            "INVALID_DEFAULT_FLOW",
                            "Default должен ссылаться на исходящий поток.",
                            [node_id, default],
                        )
                    )
            for direction in ("incoming", "outgoing"):
                for reference in element.findall(f"bpmn:{direction}", NS):
                    flow_id = _ref(reference.text)
                    target = id_map.get(flow_id)
                    endpoint = "targetRef" if direction == "incoming" else "sourceRef"
                    if (
                        target is None
                        or _local(target) != "sequenceFlow"
                        or _ref(target.get(endpoint)) != node_id
                    ):
                        issues.append(
                            _issue(
                                "INVALID_FLOW_REFERENCE",
                                "incoming/outgoing не соответствует потоку.",
                                [node_id, flow_id],
                            )
                        )
    for collaboration in root.findall("bpmn:collaboration", NS):
        for element in collaboration.findall("bpmn:participant", NS):
            participants.append(
                Participant(
                    id=element.get("id", ""),
                    name=element.get("name", ""),
                    process_id=_ref(element.get("processRef")),
                )
            )
        for element in collaboration.findall("bpmn:messageFlow", NS):
            flows.append(
                Flow(
                    id=element.get("id", ""),
                    kind="message",
                    name=element.get("name", ""),
                    source=_ref(element.get("sourceRef")),
                    target=_ref(element.get("targetRef")),
                )
            )
    return ProcessIR(
        processes=processes, participants=participants, lanes=lanes, nodes=nodes, flows=flows
    ), issues


def _check_di(root, ir):
    issues = []
    expected_shapes = {item.id for item in [*ir.participants, *ir.lanes, *ir.nodes]}
    expected_edges = {flow.id for flow in ir.flows}
    seen_shapes, seen_edges = set(), set()
    planes = root.findall("bpmndi:BPMNDiagram/bpmndi:BPMNPlane", NS)
    if len(planes) != 1:
        issues.append(_issue("INVALID_DI_PLANE", "Нужна одна диаграмма с одной плоскостью DI."))
    allowed_planes = {element.get("id") for element in root.findall("bpmn:collaboration", NS)}
    if not ir.participants:
        allowed_planes = {process.id for process in ir.processes}
    for plane in planes:
        if _ref(plane.get("bpmnElement")) not in allowed_planes:
            issues.append(_issue("INVALID_DI_PLANE", "DI не ссылается на процесс или collaboration."))
        for shape in plane.findall("bpmndi:BPMNShape", NS):
            element_id = _ref(shape.get("bpmnElement"))
            if element_id in seen_shapes or element_id not in expected_shapes:
                issues.append(
                    _issue(
                        "INVALID_DI_REFERENCE",
                        "DI shape повторяется или ссылается на неизвестный элемент.",
                        [element_id],
                    )
                )
            seen_shapes.add(element_id)
            bounds = shape.find("dc:Bounds", NS)
            try:
                values = [float(bounds.get(key)) for key in ("x", "y", "width", "height")]
                if not all(math.isfinite(value) and abs(value) <= 1_000_000 for value in values):
                    raise ValueError
                if values[2] <= 0 or values[3] <= 0:
                    raise ValueError
            except (AttributeError, TypeError, ValueError):
                issues.append(
                    _issue(
                        "INVALID_DI_BOUNDS", "DI bounds отсутствуют или имеют неверные размеры.", [element_id]
                    )
                )
        for edge in plane.findall("bpmndi:BPMNEdge", NS):
            element_id = _ref(edge.get("bpmnElement"))
            if element_id in seen_edges or element_id not in expected_edges:
                issues.append(
                    _issue(
                        "INVALID_DI_REFERENCE",
                        "DI edge повторяется или ссылается на неизвестный поток.",
                        [element_id],
                    )
                )
            seen_edges.add(element_id)
            points = edge.findall("di:waypoint", NS)
            try:
                coordinates = [(float(point.get("x")), float(point.get("y"))) for point in points]
                if len(coordinates) < 2 or not all(
                    math.isfinite(v) and abs(v) <= 1_000_000 for point in coordinates for v in point
                ):
                    raise ValueError
            except (TypeError, ValueError):
                issues.append(
                    _issue(
                        "INVALID_DI_WAYPOINTS", "У потока нужны хотя бы две конечные точки DI.", [element_id]
                    )
                )
    missing = (expected_shapes - seen_shapes) | (expected_edges - seen_edges)
    if missing:
        issues.append(_issue("MISSING_DI", "Для части элементов отсутствует DI.", sorted(missing)))
    return issues


def validate_xml(xml: str) -> dict:
    """Validate the current XML only; never regenerate coordinates or repair semantics."""
    checks = {
        "xsd": "not_run",
        "graph": "not_run",
        "behavior": "not_run",
        "layout": "not_run",
        "import": "not_run",
    }
    issues = []
    behavior = None

    def result():
        return {
            "checks": checks,
            "issues": [issue.model_dump() for issue in issues[:50]],
            "behavior": behavior,
        }

    if not isinstance(xml, str) or len(xml) > MAX_XML_BYTES:
        issues.append(_issue("XML_TOO_LARGE", "XML превышает допустимый размер."))
        checks["xsd"] = "failed"
        return result()
    try:
        data = xml.encode("utf-8")
    except UnicodeError:
        issues.append(_issue("INVALID_XML", "Некорректная кодировка XML."))
        checks["xsd"] = "failed"
        return result()
    if len(data) > MAX_XML_BYTES:
        issues.append(_issue("XML_TOO_LARGE", "XML превышает допустимый размер."))
        checks["xsd"] = "failed"
        return result()
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
        issues.append(_issue("XML_DTD_FORBIDDEN", "DTD и объявления сущностей запрещены."))
        checks["xsd"] = "failed"
        return result()
    try:
        root = etree.fromstring(
            data,
            etree.XMLParser(
                resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False, remove_comments=True
            ),
        )
    except etree.XMLSyntaxError:
        issues.append(_issue("INVALID_XML", "XML повреждён или превышает ограничения parser."))
        checks["xsd"] = "failed"
        return result()
    if root.tag != f"{{{NS['bpmn']}}}definitions":
        issues.append(_issue("INVALID_BPMN_ROOT", "Корневой элемент должен быть BPMN definitions."))
        checks["xsd"] = "failed"
        return result()
    if any(
        element.get(f"{{{NS['xsi']}}}{name}") is not None
        for element in root.iter()
        if isinstance(element.tag, str)
        for name in ("schemaLocation", "noNamespaceSchemaLocation")
    ):
        issues.append(
            _issue("EXTERNAL_XML_REFERENCE", "Внешние схемы и BPMN imports во входном XML запрещены.")
        )
        checks["xsd"] = "failed"
        return result()
    try:
        schema = _schema()
        checks["xsd"] = "passed" if schema.validate(root) else "failed"
        if checks["xsd"] == "failed":
            for error in list(schema.error_log)[:10]:
                issues.append(_issue("XSD_INVALID", f"XML не соответствует BPMN XSD: {error.message[:400]}"))
    except (OSError, etree.XMLSchemaError, etree.XMLSyntaxError):
        issues.append(_issue("XSD_UNAVAILABLE", "Локальный официальный комплект XSD недоступен."))
    document_report = validate_document(root)
    checks.update(document_report["checks"])
    issues.extend(Issue.model_validate(item) for item in document_report["issues"])
    behavior = document_report["behavior"]
    report = result()
    report["reference_resolution"] = document_report["reference_resolution"]
    report["validation_profile"] = "standard_document"
    return report
