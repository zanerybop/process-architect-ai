"""Standard-document checks in addition to the official XSD; never execute expressions.

The schema determines XML containment/cardinality. This module checks local typed
references, control-flow scopes, event rules and DI. Advanced execution semantics
remain explicitly inconclusive instead of being projected onto a simpler graph.
"""

import math
from collections import defaultdict

from lxml import etree

from backend.app.bpmn_schema import catalog
from backend.app.domain import Issue

BPMN = "http://www.omg.org/spec/BPMN/20100524/MODEL"
BPMNDI = "http://www.omg.org/spec/BPMN/20100524/DI"
DC = "http://www.omg.org/spec/DD/20100524/DC"
DI = "http://www.omg.org/spec/DD/20100524/DI"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
NS = {"bpmn": BPMN, "bpmndi": BPMNDI, "dc": DC, "di": DI, "xsi": XSI}
SCOPE_TAGS = {"process", "subProcess", "adHocSubProcess", "transaction", "choreography", "subChoreography"}
ITEM_TAGS = {
    "dataInput",
    "dataOutput",
    "dataObject",
    "dataObjectReference",
    "dataStore",
    "dataStoreReference",
    "property",
}
EDGE_TAGS = {
    "sequenceFlow",
    "messageFlow",
    "association",
    "dataInputAssociation",
    "dataOutputAssociation",
    "conversationLink",
}
EXTRA_SHAPES = {
    "participant",
    "lane",
    "dataObjectReference",
    "dataStoreReference",
    "dataObject",
    "dataInput",
    "dataOutput",
    "textAnnotation",
    "group",
}

# QName / IDREF targets that XSD cannot check for type or document membership.
# The XML names (including singular endPointRef/messageFlowRef) follow OMG XSD,
# not the occasionally different plural property names in bpmn-moddle.
ATTR_TARGETS = {
    "attachedToRef": "Activity",
    "processRef": "Process",
    "default": "SequenceFlow",
    "definitionalCollaborationRef": "Collaboration",
    "errorRef": "Error",
    "escalationRef": "Escalation",
    "activityRef": "Activity",
    "messageRef": "Message",
    "signalRef": "Signal",
    "itemSubjectRef": "ItemDefinition",
    "dataStoreRef": "DataStore",
    "dataObjectRef": "DataObject",
    "partitionElementRef": "BaseElement",
    "calledCollaborationRef": "Collaboration",
    "calledChoreographyRef": "Choreography",
    "innerConversationNodeRef": "ConversationNode",
    "outerConversationNodeRef": "ConversationNode",
    "evaluatesToTypeRef": "ItemDefinition",
    "itemRef": "ItemDefinition",
    "correlationPropertyRef": "CorrelationProperty",
    "correlationKeyRef": "CorrelationKey",
    "innerMessageFlowRef": "MessageFlow",
    "outerMessageFlowRef": "MessageFlow",
    "innerParticipantRef": "Participant",
    "outerParticipantRef": "Participant",
    "initiatingParticipantRef": "Participant",
    "categoryValueRef": "CategoryValue",
    "oneBehaviorEventRef": "EventDefinition",
    "noneBehaviorEventRef": "EventDefinition",
    "parameterRef": "ResourceParameter",
    "operationRef": "Operation",
    "inputDataRef": "InputSet",
    "outputDataRef": "OutputSet",
    "calledElement": "CallableElement",
}
TEXT_TARGETS = {
    "incoming": "SequenceFlow",
    "outgoing": "SequenceFlow",
    "flowNodeRef": "FlowNode",
    "eventDefinitionRef": "EventDefinition",
    "participantRef": "Participant",
    "messageFlowRef": "MessageFlow",
    "interfaceRef": "Interface",
    "endPointRef": "EndPoint",
    "supportedInterfaceRef": "Interface",
    "choreographyRef": "Choreography",
    "supports": "Process",
    "inMessageRef": "Message",
    "outMessageRef": "Message",
    "errorRef": "Error",
    "correlationPropertyRef": "CorrelationProperty",
    "categoryValueRef": "CategoryValue",
    "operationRef": "Operation",
    "resourceRef": "Resource",
    "loopDataInputRef": "ItemAwareElement",
    "loopDataOutputRef": "ItemAwareElement",
    "dataInputRefs": "DataInput",
    "optionalInputRefs": "DataInput",
    "whileExecutingInputRefs": "DataInput",
    "dataOutputRefs": "DataOutput",
    "optionalOutputRefs": "DataOutput",
    "whileExecutingOutputRefs": "DataOutput",
    "inputSetRefs": "InputSet",
    "outputSetRefs": "OutputSet",
}


def effective_type(element):
    """Resolve polymorphism and local element declarations without fetching schemas."""
    if not isinstance(element.tag, str):
        return None
    override = element.get(f"{{{XSI}}}type")
    if override:
        prefix, name = override.split(":", 1) if ":" in override else (None, override)
        namespace = element.nsmap.get(prefix)
        if namespace == BPMN:
            candidate = "bpmn:" + name
            if candidate in catalog().types:
                return candidate
    parent = element.getparent()
    parent_type = effective_type(parent) if parent is not None else None
    return catalog().element_type(element.tag, parent_type)


def semantic_tag(element):
    """A polymorphic flowElement remains lossless XML but has a concrete semantic kind."""
    if not isinstance(element.tag, str):
        return ""
    name = etree.QName(element).localname
    declaration = catalog().elements.get("bpmn:" + name, {})
    if (
        element.get(f"{{{XSI}}}type")
        and etree.QName(element).namespace == BPMN
        and declaration.get("abstract")
    ):
        value = effective_type(element)
        for tag, declaration in catalog().elements.items():
            if declaration["type"] == value and not declaration["abstract"] and tag.startswith("bpmn:"):
                return tag.split(":", 1)[1]
    return name


def local(element):
    return semantic_tag(element)


def standard(element):
    return isinstance(element.tag, str) and etree.QName(element).namespace == BPMN


def in_metadata(element):
    """BPMN-looking content in documentation/extensions is data, not model structure."""
    for parent in element.iterancestors():
        if not isinstance(parent.tag, str):
            continue
        namespace, tag = etree.QName(parent).namespace, etree.QName(parent).localname
        if namespace not in {BPMN, BPMNDI, DC, DI}:
            return True
        if (namespace == BPMN and tag in {"documentation", "extensionElements"}) or (
            namespace == DI and tag == "extension"
        ):
            return True
    return False


def is_type(element, target):
    if not standard(element):
        return False
    if target == "Element":
        return True
    if target == "ItemAwareElement":
        return local(element) in ITEM_TAGS
    if target == "InteractionNode":
        return (
            local(element) == "participant"
            or is_type(element, "Activity")
            or is_type(element, "Event")
            or is_type(element, "ConversationNode")
        )
    schema = catalog()
    value = effective_type(element)
    return bool(value and schema.derived_from(value, "bpmn:t" + target))


def scope(element):
    return next(
        (parent for parent in element.iterancestors() if standard(parent) and local(parent) in SCOPE_TAGS),
        None,
    )


def _issue(code, message, *elements, severity="error"):
    ids = [element.get("id") for element in elements if element is not None and element.get("id")]
    return Issue(code=code, message=message, element_ids=list(dict.fromkeys(ids)), severity=severity)


class DocumentIndex:
    def __init__(self, root):
        self.root = root
        self.elements = [item for item in root.iter() if standard(item) and not in_metadata(item)]
        self.ids = {}
        self.issues = []
        for item in root.iter():
            if not isinstance(item.tag, str):
                continue
            if in_metadata(item) or etree.QName(item).namespace not in {BPMN, BPMNDI, DC, DI}:
                continue
            identifier = item.get("id")
            if identifier:
                if identifier in self.ids:
                    self.issues.append(_issue("DUPLICATE_XML_ID", "ID документа повторяется.", item))
                self.ids[identifier] = item

    def resolve(self, value, owner, target=None, *, report=True):
        if not value:
            return None
        value = value.strip()
        field = next((name for name, text in owner.attrib.items() if text.strip() == value), local(owner))
        label = f"{field}={value[:120]!r}"
        context = [owner] if owner.get("id") else [owner, owner.getparent()]
        identifier = value
        if ":" in value:
            prefix, identifier = value.split(":", 1)
            namespace = owner.nsmap.get(prefix)
            allowed = {BPMN, self.root.get("targetNamespace")}
            if namespace is None or namespace not in allowed:
                if report:
                    code = (
                        "UNBOUND_REFERENCE_PREFIX" if namespace is None else "EXTERNAL_REFERENCE_UNRESOLVED"
                    )
                    self.issues.append(
                        _issue(
                            code,
                            f"Ссылка {label} не разрешается в документе; внешние ресурсы не загружаются.",
                            *context,
                            severity="error" if namespace is None else "warning",
                        )
                    )
                return None
        found = self.ids.get(identifier)
        if found is None:
            if report:
                self.issues.append(
                    _issue(
                        "UNKNOWN_REFERENCE",
                        f"Ссылка {label} указывает на неизвестный ID.",
                        *context,
                    )
                )
        elif target and not is_type(found, target):
            if report:
                self.issues.append(
                    _issue(
                        "INVALID_REFERENCE_TYPE",
                        f"Цель ссылки {label} должна иметь тип {target}.",
                        *context,
                        found,
                    )
                )
            return None
        return found

    def ref(self, item, attribute, target=None):
        return self.resolve(item.get(attribute), item, target, report=False)


def _check_references(index):
    for element in index.elements:
        tag = local(element)
        for name, target in ATTR_TARGETS.items():
            if element.get(name):
                index.resolve(element.get(name), element, target)
        if element.get("structureRef") and tag in {"error", "signal", "escalation"}:
            index.resolve(element.get("structureRef"), element, "ItemDefinition")
        if element.get("type") and tag in {"resourceParameter", "correlationProperty"}:
            index.resolve(element.get("type"), element, "ItemDefinition")
        if tag in {"sequenceFlow", "association", "messageFlow", "conversationLink"}:
            target = {
                "sequenceFlow": "FlowNode",
                "association": "BaseElement",
                "messageFlow": "InteractionNode",
                "conversationLink": "InteractionNode",
            }[tag]
            for name in ("sourceRef", "targetRef"):
                index.resolve(element.get(name), element, target)
        parent = element.getparent()
        if tag in TEXT_TARGETS and element.text:
            index.resolve(element.text, element, TEXT_TARGETS[tag])
        elif parent is not None and is_type(parent, "DataAssociation") and tag in {"sourceRef", "targetRef"}:
            index.resolve(element.text, element, "ItemAwareElement")
        elif parent is not None and local(parent) == "linkEventDefinition" and tag in {"source", "target"}:
            index.resolve(element.text, element, "LinkEventDefinition")
        elif parent is not None and local(parent) == "relationship" and tag in {"source", "target"}:
            index.resolve(element.text, element, "Element")


def _check_flows(index):
    incoming, outgoing = defaultdict(list), defaultdict(list)
    for flow in index.elements:
        if local(flow) != "sequenceFlow":
            continue
        source, target = index.ref(flow, "sourceRef", "FlowNode"), index.ref(flow, "targetRef", "FlowNode")
        if source is None or target is None:
            continue
        incoming[target].append(flow)
        outgoing[source].append(flow)
        if scope(source) is not scope(flow) or scope(target) is not scope(flow):
            index.issues.append(
                _issue(
                    "FLOW_SCOPE_MISMATCH", "Sequence flow пересекает область процесса.", flow, source, target
                )
            )
        if local(source) == "endEvent" or local(target) in {"startEvent", "boundaryEvent"}:
            index.issues.append(
                _issue("EVENT_FLOW_INVALID", "Недопустимое направление потока у события.", flow)
            )
        if any(
            item.get("triggeredByEvent") in {"true", "1"} or item.get("isForCompensation") in {"true", "1"}
            for item in (source, target)
        ):
            index.issues.append(
                _issue(
                    "SPECIAL_ACTIVITY_SEQUENCE_FLOW",
                    "Event subprocess и компенсационный handler вне обычного потока.",
                    flow,
                )
            )
        condition = flow.find(f"{{{BPMN}}}conditionExpression")
        if condition is not None and (
            local(source) in {"parallelGateway", "eventBasedGateway"} or is_type(source, "Event")
        ):
            index.issues.append(
                _issue("INVALID_FLOW_CONDITION", "У этого источника условие потока недопустимо.", flow)
            )
    for element in index.elements:
        if local(element) == "startEvent" and not outgoing[element]:
            index.issues.append(
                _issue(
                    "START_WITHOUT_OUTGOING",
                    "У явно заданного начального события должен быть исходящий sequence flow.",
                    element,
                )
            )
        default = index.ref(element, "default", "SequenceFlow")
        if default is not None:
            if default not in outgoing[element] or default.find(f"{{{BPMN}}}conditionExpression") is not None:
                index.issues.append(
                    _issue(
                        "INVALID_DEFAULT_FLOW",
                        "Default должен быть исходящим потоком без условия.",
                        element,
                        default,
                    )
                )
        for direction, expected in (("incoming", incoming[element]), ("outgoing", outgoing[element])):
            for child in element.findall(f"{{{BPMN}}}{direction}"):
                target = index.resolve(child.text, child, report=False)
                if target is not None and target not in expected:
                    index.issues.append(
                        _issue(
                            "INVALID_FLOW_REFERENCE",
                            "incoming/outgoing не соответствует потоку.",
                            element,
                            target,
                        )
                    )
        if local(element) == "eventBasedGateway":
            for flow in outgoing[element]:
                target = index.ref(flow, "targetRef")
                if target is not None and local(target) not in {"intermediateCatchEvent", "receiveTask"}:
                    index.issues.append(
                        _issue(
                            "EVENT_GATEWAY_TARGET",
                            "После event-based gateway нужен catch event или receiveTask.",
                            element,
                            target,
                        )
                    )
    return incoming, outgoing


def _event_definitions(element, index):
    values = [child for child in element if is_type(child, "EventDefinition")]
    for child in element.findall(f"{{{BPMN}}}eventDefinitionRef"):
        found = index.resolve(child.text, child, "EventDefinition", report=False)
        if found is not None:
            values.append(found)
    return values


def _check_events(index):
    allowed = {
        "boundaryEvent": {
            "message",
            "timer",
            "error",
            "escalation",
            "cancel",
            "compensate",
            "conditional",
            "signal",
        },
        "intermediateCatchEvent": {"message", "timer", "conditional", "link", "signal"},
        "intermediateThrowEvent": {"message", "escalation", "link", "compensate", "signal"},
        "endEvent": {"message", "error", "escalation", "cancel", "compensate", "signal", "terminate"},
        "startEvent": {"message", "timer", "conditional", "signal", "error", "escalation", "compensate"},
    }
    for element in index.elements:
        tag = local(element)
        if tag not in allowed:
            continue
        definitions = _event_definitions(element, index)
        types = {local(item).removesuffix("EventDefinition") for item in definitions}
        if types - allowed[tag] or (tag in {"boundaryEvent", "intermediateCatchEvent"} and not types):
            index.issues.append(
                _issue("EVENT_DEFINITION_INVALID", "Тип события не допускает указанный trigger.", element)
            )
        container = scope(element)
        if tag == "startEvent":
            event_scope = container is not None and container.get("triggeredByEvent") in {"true", "1"}
            if types & {"error", "escalation", "compensate"} and not event_scope:
                index.issues.append(
                    _issue(
                        "EVENT_START_SCOPE", "Этот start trigger допустим только в event subprocess.", element
                    )
                )
            if event_scope and not types:
                index.issues.append(
                    _issue("EVENT_START_SCOPE", "Event subprocess требует trigger у start event.", element)
                )
            if "error" in types and element.get("isInterrupting") in {"false", "0"}:
                index.issues.append(
                    _issue("ERROR_MUST_INTERRUPT", "Error start event всегда прерывающий.", element)
                )
        if tag == "boundaryEvent":
            host = index.ref(element, "attachedToRef", "Activity")
            if host is not None:
                if scope(host) is not container:
                    index.issues.append(
                        _issue(
                            "BOUNDARY_SCOPE",
                            "Boundary event и activity должны быть в одной области.",
                            element,
                            host,
                        )
                    )
                if "cancel" in types and local(host) != "transaction":
                    index.issues.append(
                        _issue(
                            "CANCEL_REQUIRES_TRANSACTION",
                            "Cancel boundary event требует transaction.",
                            element,
                            host,
                        )
                    )
            if types & {"error", "cancel"} and element.get("cancelActivity") in {"false", "0"}:
                index.issues.append(
                    _issue(
                        "ERROR_MUST_INTERRUPT", "Error и cancel boundary events всегда прерывающие.", element
                    )
                )
        if (
            tag == "endEvent"
            and "cancel" in types
            and (container is None or local(container) != "transaction")
        ):
            index.issues.append(
                _issue("CANCEL_REQUIRES_TRANSACTION", "Cancel end event допустим в transaction.", element)
            )
        for definition in definitions:
            if local(definition) == "timerEventDefinition":
                timers = [
                    child for child in definition if local(child) in {"timeDate", "timeDuration", "timeCycle"}
                ]
                if len(timers) != 1 or not "".join(timers[0].itertext()).strip():
                    index.issues.append(
                        _issue(
                            "TIMER_DEFINITION_INVALID",
                            "У timer нужен один непустой timeDate/timeDuration/timeCycle.",
                            element,
                        )
                    )


def _check_lanes_and_messages(index):
    lane_assignments = defaultdict(list)
    participants = defaultdict(list)
    for element in index.elements:
        if local(element) == "participant":
            process = index.ref(element, "processRef", "Process")
            if process is not None:
                participants[process].append(element)
        if local(element) == "lane":
            for child in element.findall(f"{{{BPMN}}}flowNodeRef"):
                node = index.resolve(child.text, child, "FlowNode", report=False)
                if node is not None:
                    if scope(node) is not scope(element):
                        index.issues.append(
                            _issue(
                                "INVALID_LANE_REFERENCE",
                                "Дорожка ссылается на узел другой области.",
                                element,
                                node,
                            )
                        )
                    lane_assignments[node].append(element)
                elif child.text and ":" not in child.text:
                    index.issues.append(
                        _issue("INVALID_LANE_REFERENCE", "Дорожка ссылается на неизвестный узел.", element)
                    )
    def assigned_lane(node):
        # An invalid cross-scope flowNodeRef remains an error and cannot establish
        # a role. A lane on an enclosing subprocess covers its nested activities.
        return any(scope(node) is scope(lane) for lane in lane_assignments.get(node, []))

    def assigned_resource(node):
        for role in node:
            if not is_type(role, "ResourceRole"):
                continue
            for reference in role.findall(f"{{{BPMN}}}resourceRef"):
                resource = index.resolve(reference.text, reference, "Resource", report=False)
                if resource is not None and resource.get("name", "").strip():
                    return True
        return False

    for item in index.elements:
        if is_type(item, "Task") and not (
            assigned_lane(item)
            or assigned_resource(item)
            or any(
                is_type(parent, "SubProcess") and assigned_lane(parent)
                for parent in item.iterancestors()
            )
        ):
            index.issues.append(
                _issue(
                    "ROLE_UNSPECIFIED",
                    "У задачи не назначен именованный ресурс или дорожка исполнителя.",
                    item,
                    severity="warning",
                )
            )
    for node, lanes in lane_assignments.items():
        for first in lanes:
            if any(
                first is not second
                and first not in second.iterancestors()
                and second not in first.iterancestors()
                and first.getparent() is second.getparent()
                for second in lanes
            ):
                index.issues.append(
                    _issue("MULTIPLE_LANE_ASSIGNMENT", "Узел назначен двум соседним дорожкам.", node)
                )
                break

    def participant_for(node, collaboration):
        if local(node) == "participant":
            return node if node.getparent() is collaboration else None
        process = next((item for item in node.iterancestors() if local(item) == "process"), None)
        values = [item for item in participants.get(process, []) if item.getparent() is collaboration]
        return values[0] if len(values) == 1 else None

    for flow in index.elements:
        if local(flow) != "messageFlow":
            continue
        source, target = index.ref(flow, "sourceRef"), index.ref(flow, "targetRef")
        if source is None or target is None:
            continue
        first, second = participant_for(source, flow.getparent()), participant_for(target, flow.getparent())
        if first is None or second is None or first is second:
            index.issues.append(
                _issue(
                    "MESSAGE_FLOW_PARTICIPANTS",
                    "Message flow должен соединять разные пулы collaboration.",
                    flow,
                )
            )
        if local(source) == "startEvent" or local(target) == "endEvent":
            index.issues.append(
                _issue("MESSAGE_ENDPOINT_TYPE", "Недопустимое направление сообщения у события.", flow)
            )


def _check_special(index):
    for element in index.elements:
        tag = local(element)
        if is_type(element, "ChoreographyActivity") or tag == "globalChoreographyTask":
            refs = [
                index.resolve(child.text, child, "Participant", report=False)
                for child in element.findall(f"{{{BPMN}}}participantRef")
            ]
            initiator = index.ref(element, "initiatingParticipantRef", "Participant")
            if initiator is not None and initiator not in refs:
                index.issues.append(
                    _issue(
                        "CHOREOGRAPHY_INITIATOR",
                        "Инициатор должен входить в participantRef.",
                        element,
                        initiator,
                    )
                )
            if tag == "choreographyTask" and len([item for item in refs if item is not None]) != 2:
                index.issues.append(
                    _issue(
                        "CHOREOGRAPHY_TASK_PARTICIPANTS",
                        "ChoreographyTask связывает двух участников.",
                        element,
                    )
                )
        if tag == "association":
            source, target = index.ref(element, "sourceRef"), index.ref(element, "targetRef")
            if (
                source is not None
                and local(source) == "boundaryEvent"
                and any(
                    local(item) == "compensateEventDefinition" for item in _event_definitions(source, index)
                )
            ):
                if (
                    target is None
                    or not is_type(target, "Activity")
                    or target.get("isForCompensation") not in {"true", "1"}
                ):
                    index.issues.append(
                        _issue(
                            "COMPENSATION_HANDLER_INVALID",
                            "Компенсационная association должна вести к handler activity.",
                            element,
                        )
                    )
                elif scope(source) is not scope(target):
                    index.issues.append(
                        _issue(
                            "COMPENSATION_SCOPE",
                            "Компенсационный handler находится в другой области.",
                            element,
                        )
                    )


def _finite_bounds(element):
    try:
        numbers = [float(element.get(key)) for key in ("x", "y", "width", "height")]
        return (
            all(math.isfinite(value) and abs(value) <= 1_000_000 for value in numbers)
            and numbers[2] > 0
            and numbers[3] > 0
        )
    except (AttributeError, TypeError, ValueError):
        return False


def validate_document_di(root, index=None):
    """Validate every view; duplicate participant bands are legitimate in choreography."""
    index = index or DocumentIndex(root)
    issues = []
    planes = root.findall("bpmndi:BPMNDiagram/bpmndi:BPMNPlane", NS)
    if not planes:
        return []
    for plane in planes:
        plane_root = index.ref(plane, "bpmnElement")
        if plane_root is None or not (
            local(plane_root) in SCOPE_TAGS or is_type(plane_root, "Collaboration")
        ):
            issues.append(
                _issue(
                    "INVALID_DI_PLANE",
                    "DI plane должен ссылаться на процесс, collaboration или вложенную область.",
                    plane,
                )
            )
        represented = set()
        collapsed = set()
        for shape in plane.findall("bpmndi:BPMNShape", NS):
            semantic = index.ref(shape, "bpmnElement")
            if (
                semantic is not None
                and local(semantic) in {"subProcess", "adHocSubProcess", "transaction", "subChoreography"}
                and shape.get("isExpanded") not in {"true", "1"}
            ):
                collapsed.add(semantic)
        for item in plane:
            if local(item) not in {"BPMNShape", "BPMNEdge"}:
                continue
            semantic = index.ref(item, "bpmnElement")
            band = item.get("choreographyActivityShape")
            if semantic is None:
                issues.append(_issue("INVALID_DI_REFERENCE", "DI ссылается на неизвестный элемент.", item))
                continue
            represented.add(semantic)
            if local(item) == "BPMNShape":
                if not (
                    is_type(semantic, "FlowNode")
                    or is_type(semantic, "ConversationNode")
                    or local(semantic) in EXTRA_SHAPES
                ):
                    issues.append(
                        _issue(
                            "INVALID_DI_REFERENCE",
                            "BPMNShape ссылается на элемент без фигуры.",
                            item,
                            semantic,
                        )
                    )
                if band:
                    activity_shape = index.ref(item, "choreographyActivityShape")
                    activity = (
                        index.ref(activity_shape, "bpmnElement") if activity_shape is not None else None
                    )
                    if (
                        local(semantic) != "participant"
                        or activity_shape is None
                        or local(activity_shape) != "BPMNShape"
                        or activity is None
                        or not is_type(activity, "ChoreographyActivity")
                    ):
                        issues.append(
                            _issue(
                                "INVALID_CHOREOGRAPHY_BAND",
                                "Некорректная ссылка полосы участника choreography.",
                                item,
                            )
                        )
                if not _finite_bounds(item.find("dc:Bounds", NS)):
                    issues.append(
                        _issue("INVALID_DI_BOUNDS", "Некорректные координаты или размеры DI.", item)
                    )
            else:
                if local(semantic) not in EDGE_TAGS:
                    issues.append(
                        _issue(
                            "INVALID_DI_REFERENCE", "BPMNEdge ссылается на элемент без связи.", item, semantic
                        )
                    )
                try:
                    points = [
                        (float(point.get("x")), float(point.get("y")))
                        for point in item.findall("di:waypoint", NS)
                    ]
                    if len(points) < 2 or not all(
                        math.isfinite(v) and abs(v) <= 1_000_000 for point in points for v in point
                    ):
                        raise ValueError
                except (TypeError, ValueError):
                    issues.append(
                        _issue("INVALID_DI_WAYPOINTS", "У связи нужны хотя бы две корректные точки DI.", item)
                    )
                for endpoint in ("sourceElement", "targetElement"):
                    if item.get(endpoint) and index.ref(item, endpoint) is None:
                        issues.append(
                            _issue(
                                "INVALID_DI_REFERENCE", "DI endpoint указывает на неизвестную фигуру.", item
                            )
                        )
        if plane_root is not None:
            candidates = list(plane_root.iterdescendants())
            if is_type(plane_root, "Collaboration"):
                for participant in plane_root.findall("bpmn:participant", NS):
                    process = index.ref(participant, "processRef", "Process")
                    if process is not None:
                        candidates.extend(process.iterdescendants())
            expected = [
                item
                for item in candidates
                if standard(item)
                and (
                    is_type(item, "FlowNode")
                    or is_type(item, "ConversationNode")
                    or local(item) in (EXTRA_SHAPES - {"dataObject", "dataInput", "dataOutput"}) | EDGE_TAGS
                )
                and not any(parent in collapsed for parent in item.iterancestors())
            ]
            for item in expected:
                if local(item) == "messageFlow" and (
                    is_type(plane_root, "Choreography")
                    or any(
                        local(reference) == "messageFlowRef"
                        and index.resolve(reference.text, reference, report=False) is item
                        and (
                            is_type(reference.getparent(), "ConversationNode")
                            or is_type(reference.getparent(), "ChoreographyActivity")
                        )
                        for reference in candidates
                    )
                ):
                    # These messages are represented by conversation/choreography
                    # interactions, not necessarily by a standalone BPMNEdge.
                    continue
                if item not in represented:
                    issues.append(_issue("MISSING_DI", "Для видимого элемента отсутствует DI.", item))
    return issues


def validate_document_structure(root):
    """Public structural checks before serialization/DI, suitable for bounded repair."""
    index = DocumentIndex(root)
    _check_references(index)
    _check_flows(index)
    _check_events(index)
    _check_lanes_and_messages(index)
    _check_special(index)
    return index.issues


def validate_document_behavior(root):
    """Analyze only complete supported control-flow scopes; never erase event/loop semantics."""
    from backend.app.behavior import (
        SUPPORTED_TYPES,
        BehaviorFinding,
        BehaviorReport,
        ProcessBehaviorReport,
        validate_behavior,
    )
    from backend.app.document_cycles import forced_cycle_reports
    from backend.app.domain import Flow, Node, Process, ProcessIR

    index = DocumentIndex(root)
    processes = [item for item in root if is_type(item, "Process")]
    communication = len(processes) > 1 or any(
        local(item) in {"messageFlow", "conversation", "choreography"} for item in index.elements
    )
    report = BehaviorReport(
        status="inconclusive", collaboration="inconclusive" if communication else "not_applicable"
    )
    report.limitations.append(
        "Расширенные области не упрощаются до обычных задач; проверены только явно перечисленные процессы."
    )
    for process in processes:
        identifier = process.get("id") or "anonymous_process"
        nodes = [item for item in process if is_type(item, "FlowNode")]
        flows = [item for item in process if is_type(item, "SequenceFlow")]
        unsupported = [
            item
            for item in nodes
            if local(item) not in SUPPORTED_TYPES
            or not item.get("id")
            or item.get("isForCompensation") in {"true", "1"}
            or item.get("startQuantity", "1") != "1"
            or item.get("completionQuantity", "1") != "1"
            or any(
                local(child)
                in {"standardLoopCharacteristics", "multiInstanceLoopCharacteristics", "eventDefinitionRef"}
                or is_type(child, "EventDefinition")
                for child in item
            )
        ]
        if unsupported or any(
            not flow.get("id") or index.ref(flow, "sourceRef") is None or index.ref(flow, "targetRef") is None
            for flow in flows
        ):
            report.processes.append(
                ProcessBehaviorReport(
                    process_id=identifier,
                    findings=[
                        BehaviorFinding(
                            code="BEHAVIOR_ADVANCED_SCOPE",
                            message="Область содержит семантику вне token-flow анализатора.",
                            element_ids=[item.get("id") for item in unsupported if item.get("id")],
                        )
                    ],
                )
            )
            continue
        ir = ProcessIR(
            processes=[Process(id=identifier, name=process.get("name", ""))],
            nodes=[
                Node(id=item.get("id"), type=local(item), process_id=identifier, name=item.get("name", ""))
                for item in nodes
            ],
            flows=[
                Flow(
                    id=item.get("id"),
                    source=index.ref(item, "sourceRef").get("id"),
                    target=index.ref(item, "targetRef").get("id"),
                    name=item.get("name", ""),
                    condition="".join(item.find("bpmn:conditionExpression", NS).itertext())
                    if item.find("bpmn:conditionExpression", NS) is not None
                    else None,
                    is_default=index.ref(index.ref(item, "sourceRef"), "default") is item,
                )
                for item in flows
            ],
        )
        local_report = validate_behavior(ir)
        report.processes.extend(local_report.processes)
    for cycle_report in forced_cycle_reports(index):
        previous = next(
            (item for item in report.processes if item.process_id == cycle_report.process_id), None
        )
        if previous is None:
            report.processes.append(cycle_report)
        else:
            previous.status = "failed"
            previous.findings.extend(cycle_report.findings)
    report.limitations.append(
        "Безусловные циклы проверяются отдельно и консервативно, в том числе во вложенных обычных областях: "
        "диагноз относится к запуску указанной области. Не найденный цикл не означает доказанного завершения."
    )
    if any(process.status == "failed" for process in report.processes):
        report.status = "failed"
    elif (
        report.processes
        and not communication
        and not requires_document_validation(root, include_di=False)
        and all(process.status == "passed" for process in report.processes)
    ):
        report.status = "passed"
    return report


def validate_document(root):
    """Return independent structural/DI checks; no syntax is silently simplified."""
    index = DocumentIndex(root)
    index.issues = validate_document_structure(root)
    layout_issues = validate_document_di(root, index)
    graph_failed = any(item.severity == "error" for item in index.issues)
    from backend.app.behavior import behavior_issues

    behavior = validate_document_behavior(root)
    warnings = []
    if requires_document_validation(root, include_di=False):
        warnings.append(
            _issue(
                "BEHAVIOR_ADVANCED_NOT_CHECKED",
                "Стандартная структура проверена; поведение расширенных конструкций не доказано.",
                severity="warning",
            )
        )
    has_di = bool(root.findall("bpmndi:BPMNDiagram/bpmndi:BPMNPlane", NS))
    return {
        "checks": {
            "graph": "failed" if graph_failed else "passed",
            "behavior": behavior.status,
            "layout": ("failed" if layout_issues else "passed") if has_di else "not_run",
        },
        "issues": [
            item.model_dump()
            for item in [*index.issues, *layout_issues, *behavior_issues(behavior), *warnings]
        ],
        "behavior": behavior.model_dump(),
        "reference_resolution": (
            "failed"
            if any(
                item.code in {"UNKNOWN_REFERENCE", "INVALID_REFERENCE_TYPE", "UNBOUND_REFERENCE_PREFIX"}
                for item in index.issues
            )
            else "inconclusive"
            if any(item.code == "EXTERNAL_REFERENCE_UNRESOLVED" for item in index.issues)
            else "passed"
        ),
    }


def requires_document_validation(root, *, include_di=True):
    """Use the legacy validator only when it can represent all semantic information."""
    plain_tags = {
        "definitions",
        "process",
        "collaboration",
        "participant",
        "laneSet",
        "lane",
        "flowNodeRef",
        "startEvent",
        "endEvent",
        "task",
        "userTask",
        "serviceTask",
        "sendTask",
        "receiveTask",
        "exclusiveGateway",
        "parallelGateway",
        "sequenceFlow",
        "messageFlow",
        "conditionExpression",
        "incoming",
        "outgoing",
        "documentation",
        "extensionElements",
    }
    plain_attrs = {
        "id",
        "name",
        "isExecutable",
        "processRef",
        "sourceRef",
        "targetRef",
        "default",
        "gatewayDirection",
        "targetNamespace",
        "exporter",
        "exporterVersion",
        "expressionLanguage",
        "typeLanguage",
    }
    for element in root.iter():
        if not standard(element):
            if isinstance(element.tag, str) and etree.QName(element).namespace not in {BPMNDI, DC, DI}:
                return True
            continue
        if local(element) not in plain_tags:
            return True
        if any(not key.startswith("{") and key not in plain_attrs for key in element.attrib):
            return True
        if (
            element.getparent() is not None
            and local(element.getparent()) == "extensionElements"
            and len(element)
        ):
            return True
    return include_di and len(root.findall("bpmndi:BPMNDiagram", NS)) != 1


def semantic_snapshot(root):
    """Include all BPMN attributes, hierarchy and text; omit DI and presentation whitespace."""

    def qname(value, element):
        if ":" in value:
            prefix, name = value.split(":", 1)
            namespace = element.nsmap.get(prefix)
            if namespace in {BPMN, root.get("targetNamespace")}:
                return name
            return f"{{{namespace}}}{name}" if namespace else value
        return value

    def visit(element):
        if not isinstance(element.tag, str) or etree.QName(element).namespace in {BPMNDI, DC, DI}:
            return None
        children = [value for child in element if (value := visit(child)) is not None]
        # Flow-element/root-element XML order is not execution order. bpmn-js may
        # reorder those records while preserving all actual sequence-flow links.
        if local(element) in SCOPE_TAGS | {"definitions", "collaboration", "laneSet", "childLaneSet"}:
            children.sort(key=lambda item: (item["tag"], item["attributes"].get("id", "")))
        text = element.text or ""
        if not text.strip():
            text = ""
        if local(element) in set(TEXT_TARGETS) | {"sourceRef", "targetRef"}:
            text = qname(text.strip(), element)
        attributes = dict(element.attrib)
        if local(element) == "definitions":
            attributes.pop("exporter", None)
            attributes.pop("exporterVersion", None)
        element_type = catalog().element_type(element.tag)
        declarations = catalog().types.get(element_type, {}).get("attributes", [])
        for declaration in declarations:
            key = declaration["name"]
            if key in attributes and declaration["type"] == "xsd:boolean":
                attributes[key] = "true" if attributes[key] in {"true", "1"} else "false"
            if declaration.get("default") is not None and attributes.get(key) == declaration["default"]:
                attributes.pop(key, None)
        for key in list(attributes):
            if key in ATTR_TARGETS or key in {"sourceRef", "targetRef", "structureRef", f"{{{XSI}}}type"}:
                attributes[key] = qname(attributes[key], element)
        return {
            "tag": element.tag,
            "attributes": dict(sorted(attributes.items())),
            "text": text,
            "tail": element.tail if element.tail and element.tail.strip() else "",
            "children": children,
        }

    return visit(root)
