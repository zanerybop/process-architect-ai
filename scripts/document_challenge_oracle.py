"""Independent XML predicates for the frozen generalisation challenge.

Selectors use business names, schema types and references, never generated IDs.
Foreign namespaces and metadata cannot satisfy a BPMN assertion. This module is
deliberately separate from the historical document benchmark oracle.
"""

import math
import re

from lxml import etree

from backend.app.bpmn_schema import NAMESPACES, catalog

BPMN = NAMESPACES["bpmn"]
XSI = NAMESPACES["xsi"]
SCOPES = {"process", "subProcess", "transaction", "adHocSubProcess", "choreography", "subChoreography"}
METADATA = {"extensionElements", "documentation"}
ROLE_TYPES = {"resourceRole", "performer", "humanPerformer", "potentialOwner"}


def matches(pattern, value):
    return bool(re.search(pattern, (value or "").replace("ё", "е"), re.IGNORECASE))


def semantic(element):
    for node in [element, *element.iterancestors()]:
        if not isinstance(node.tag, str) or etree.QName(node).namespace != BPMN:
            return False
        if etree.QName(node).localname in METADATA:
            return False
    return True


def tag(element):
    if element is None or not semantic(element):
        return ""
    name = etree.QName(element).localname
    explicit = element.get(f"{{{XSI}}}type")
    if explicit and name in {
        "flowElement",
        "rootElement",
        "eventDefinition",
        "artifact",
        "resourceRole",
        "loopCharacteristics",
        "conversationNode",
        "task",
        "subProcess",
    }:
        prefix, separator, local = explicit.partition(":")
        uri = element.nsmap.get(prefix if separator else None)
        local = local if separator else prefix
        declared = catalog().elements.get("bpmn:" + name, {}).get("type")
        actual = "bpmn:" + local
        if uri == BPMN and declared and catalog().derived_from(actual, declared):
            return local[1:2].lower() + local[2:] if local.startswith("t") else name
    return name


class ChallengeOracle:
    def __init__(self, xml):
        self.root = etree.fromstring(
            xml.encode(), etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        )
        if self.root.getroottree().docinfo.doctype or tag(self.root) != "definitions":
            raise ValueError("A BPMN definitions document without DOCTYPE is required")
        self.elements = [node for node in self.root.iter() if semantic(node)]
        self.by_id = {node.get("id"): node for node in self.elements if node.get("id")}
        if len(self.by_id) != sum(bool(node.get("id")) for node in self.elements):
            raise ValueError("Duplicate semantic IDs")
        self.flows = self.select({"tags": ["sequenceFlow"]})

    def ref(self, context, value):
        if not value:
            return None
        prefix, separator, local = value.strip().partition(":")
        if separator and context.nsmap.get(prefix) != self.root.get("targetNamespace"):
            return None
        return self.by_id.get(local if separator else prefix)

    def scope(self, element):
        return next((p for p in element.iterancestors() if tag(p) in SCOPES), None)

    def attribute(self, element, key):
        value = element.get(key)
        entry = catalog().elements.get("bpmn:" + tag(element), {})
        attributes = catalog().types.get(entry.get("type"), {}).get("attributes", [])
        schema = next((a for a in attributes if a["name"] == key), {})
        if value is None:
            value = schema.get("default")
        if schema.get("type") == "xsd:boolean":
            value = {"1": "true", "0": "false"}.get(value, value)
        return value

    def accepts(self, element, selector):
        if element is None or element not in self.elements:
            return False
        if selector.get("tags") and tag(element) not in selector["tags"]:
            return False
        if selector.get("pattern") and not matches(selector["pattern"], element.get("name")):
            return False
        if selector.get("text") and not matches(selector["text"], element.text):
            return False
        if any(self.attribute(element, key) != value for key, value in selector.get("attrs", {}).items()):
            return False
        if "in" in selector and not any(self.accepts(p, selector["in"]) for p in element.iterancestors()):
            return False
        if "parent" in selector and not self.accepts(element.getparent(), selector["parent"]):
            return False
        return all(any(self.accepts(c, query) for c in element) for query in selector.get("children", []))

    def select(self, selector):
        return [e for e in self.elements if self.accepts(e, selector)]

    def outgoing(self, element):
        return [f for f in self.flows if self.ref(f, f.get("sourceRef")) is element]

    def reaches(self, seeds, excluded=()):
        pending, seen, excluded = list(seeds), set(), set(excluded)
        while pending:
            node = pending.pop()
            if node in seen or node in excluded:
                continue
            seen.add(node)
            for flow in self.outgoing(node):
                target = self.ref(flow, flow.get("targetRef"))
                if target is not None and self.scope(node) is self.scope(target) is self.scope(flow):
                    pending.append(target)
        return seen

    def role_names(self, node, kind):
        candidates = [node, *[p for p in node.iterancestors() if tag(p) in SCOPES]]
        names = []
        if kind in {"lane", "either"}:
            for current in candidates:
                lanes = [
                    lane
                    for lane in self.select({"tags": ["lane"]})
                    if any(tag(c) == "flowNodeRef" and self.ref(c, c.text) is current for c in lane)
                ]
                if lanes:
                    names.extend(lane.get("name", "") for lane in lanes)
                    break
        if kind in {"resource", "either"}:
            for current in candidates:
                roles = [c for c in current if tag(c) in ROLE_TYPES]
                if roles:
                    for role in roles:
                        for child in role:
                            target = self.ref(child, child.text) if tag(child) == "resourceRef" else None
                            if tag(target) == "resource":
                                names.append(target.get("name", ""))
                    break
        return " ".join(names)

    def condition(self, flow, pattern):
        return any(tag(c) == "conditionExpression" and matches(pattern, c.text) for c in flow)

    def data_name(self, node, pattern):
        if tag(node) not in {"dataObject", "dataObjectReference", "dataStore", "dataStoreReference"}:
            return False
        target = self.ref(node, node.get("dataObjectRef") or node.get("dataStoreRef"))
        return matches(pattern, node.get("name")) or (
            target is not None and matches(pattern, target.get("name"))
        )

    def check(self, prop):
        kind, matched, details = prop["kind"], [], {}
        if kind == "count":
            matched = self.select(prop["select"])
            passed = prop.get("min", 1) <= len(matched) <= prop.get("max", math.inf)
            details["count"] = len(matched)
        elif kind == "role":
            matched = [
                e
                for e in self.select(prop["action"])
                if matches(prop["role"], self.role_names(e, prop.get("role_kind", "either")))
            ]
            passed = bool(matched)
        elif kind == "reference":
            pairs = []
            for node in self.select(prop["from"]):
                contexts = [c for c in node if tag(c) == prop["child"]] if prop.get("child") else [node]
                for context in contexts:
                    target = self.ref(
                        context, context.text if prop.get("child") else context.get(prop["attribute"])
                    )
                    if self.accepts(target, prop["to"]):
                        matched.extend([node, target])
                        pairs.append((node, target))
            passed = len({n for n, _ in pairs}) >= prop.get("min", 1)
            if prop.get("same_target"):
                passed = passed and len({t for _, t in pairs}) == 1
        elif kind == "mapping":
            for owner in self.select(prop["owner"]):
                for mapping in [c for c in owner if tag(c) == prop["tag"]]:
                    first = [self.ref(c, c.text) for c in mapping if tag(c) == prop["first_child"]]
                    second = [self.ref(c, c.text) for c in mapping if tag(c) == prop["second_child"]]
                    if any(self.accepts(e, prop["first"]) for e in first) and any(
                        self.accepts(e, prop["second"]) for e in second
                    ):
                        matched.append(mapping)
            passed = bool(matched)
        elif kind == "link":
            for edge in self.select(prop["edge"]):
                source, target = self.ref(edge, edge.get("sourceRef")), self.ref(edge, edge.get("targetRef"))
                if (self.accepts(source, prop["from"]) and self.accepts(target, prop["to"])) or (
                    prop.get("undirected")
                    and self.accepts(target, prop["from"])
                    and self.accepts(source, prop["to"])
                ):
                    matched.append(edge)
            passed = bool(matched)
        elif kind == "path":
            destinations = set(self.select(prop["to"]))
            forbidden = set(self.select(prop["not_to"])) if prop.get("not_to") else set()
            for source in self.select(prop["from"]):
                reached = self.reaches([source])
                if reached & destinations and not reached & forbidden:
                    matched.append(source)
            passed = bool(matched)
        elif kind == "sequence_scope":
            invalid = []
            for flow in self.flows:
                source, target = self.ref(flow, flow.get("sourceRef")), self.ref(flow, flow.get("targetRef"))
                if (
                    source is None
                    or target is None
                    or not (self.scope(source) is self.scope(target) is self.scope(flow))
                ):
                    invalid.append(flow.get("id"))
            details["invalid_flows"] = invalid
            passed = bool(self.flows) and not invalid
        elif kind == "order":
            before, after = self.select(prop["before"]), self.select(prop["after"])
            passed = bool(before and after)
            for node in after:
                scope = self.scope(node)
                seeds = [e for e in self.select({"tags": ["startEvent"]}) if self.scope(e) is scope]
                eligible = [e for e in before if self.scope(e) is scope]
                passed = (
                    passed
                    and bool(seeds and eligible)
                    and node in self.reaches(eligible)
                    and node not in self.reaches(seeds, eligible)
                )
            matched = before + after
        elif kind == "branch":
            destinations = self.select(prop["to"])
            for gateway in self.select(prop.get("gateway", {"tags": ["exclusiveGateway"]})):
                for flow in self.outgoing(gateway):
                    target = self.ref(flow, flow.get("targetRef"))
                    if target is None or not self.condition(flow, prop["condition"]):
                        continue
                    if set(destinations) & self.reaches([target]):
                        forbidden = self.select(prop["not_to"]) if prop.get("not_to") else []
                        if not set(forbidden) & self.reaches([target], [gateway]):
                            matched.append(flow)
            passed = bool(matched)
        elif kind == "io":
            for owner in self.select(prop["action"]):
                direction = prop["direction"]
                association_tag = "dataInputAssociation" if direction == "input" else "dataOutputAssociation"
                for association in [c for c in owner if tag(c) == association_tag]:
                    sources = [self.ref(c, c.text) for c in association if tag(c) == "sourceRef"]
                    targets = [self.ref(c, c.text) for c in association if tag(c) == "targetRef"]
                    ports = targets if direction == "input" else sources
                    data = sources if direction == "input" else targets
                    port_tag = "dataInput" if direction == "input" else "dataOutput"
                    valid_ports = [
                        p
                        for p in ports
                        if tag(p) == port_tag
                        and p.getparent().getparent() is owner
                        and tag(p.getparent()) == "ioSpecification"
                    ]
                    if valid_ports and any(d is not None and self.data_name(d, prop["data"]) for d in data):
                        matched.append(association)
            passed = bool(matched)
        elif kind == "boundary":
            for event in self.select({"tags": ["boundaryEvent"]}):
                host = self.ref(event, event.get("attachedToRef"))
                definitions = [c for c in event if self.accepts(c, prop["definition"])]
                interrupting = event.get("cancelActivity", "true") in {"true", "1"}
                if (
                    not self.accepts(host, prop["host"])
                    or not definitions
                    or interrupting != prop["interrupting"]
                    or self.scope(host) is not self.scope(event)
                ):
                    continue
                handlers = self.select(prop["handler"])
                if not set(handlers) & self.reaches([event]):
                    continue
                if prop.get("raised_inside"):
                    attribute = prop["raised_inside"]["reference"]
                    raised = self.select(prop["raised_inside"]["select"])
                    if not any(
                        host in r.iterancestors()
                        and self.ref(r, r.get(attribute)) is self.ref(d, d.get(attribute))
                        and self.ref(d, d.get(attribute)) is not None
                        for r in raised
                        for d in definitions
                    ):
                        continue
                matched.extend([event, host, *handlers])
            passed = bool(matched)
        elif kind == "event_subprocess":
            for scope in self.select({"tags": ["subProcess"], "attrs": {"triggeredByEvent": "true"}}):
                if prop.get("in") and not any(self.accepts(p, prop["in"]) for p in scope.iterancestors()):
                    continue
                for start in [c for c in scope if tag(c) == "startEvent"]:
                    if (start.get("isInterrupting", "true") in {"true", "1"}) != prop["interrupting"]:
                        continue
                    if not any(self.accepts(c, prop["definition"]) for c in start):
                        continue
                    actions = [e for e in self.select(prop["action"]) if self.scope(e) is scope]
                    if set(actions) & self.reaches([start]) and not any(
                        self.ref(f, f.get(side)) is scope
                        for f in self.flows
                        for side in ("sourceRef", "targetRef")
                    ):
                        matched.extend([scope, start, *actions])
            passed = bool(matched)
        elif kind == "compensation":
            for event in self.select(
                {"tags": ["boundaryEvent"], "children": [{"tags": ["compensateEventDefinition"]}]}
            ):
                host = self.ref(event, event.get("attachedToRef"))
                if not self.accepts(host, prop["host"]):
                    continue
                for association in self.select(
                    {"tags": ["association"], "attrs": {"associationDirection": "One"}}
                ):
                    handler = self.ref(association, association.get("targetRef"))
                    if self.ref(association, association.get("sourceRef")) is not event or not self.accepts(
                        handler, prop["handler"]
                    ):
                        continue
                    if handler.get("isForCompensation") not in {"true", "1"} or not (
                        self.scope(event) is self.scope(host) is self.scope(handler)
                    ):
                        continue
                    if not any(
                        self.ref(f, f.get(side)) is handler
                        for f in self.flows
                        for side in ("sourceRef", "targetRef")
                    ):
                        matched.extend([host, event, handler, association])
            passed = bool(matched)
        elif kind == "choreography":
            for task in self.select(prop["task"]):
                initiator = self.ref(task, task.get("initiatingParticipantRef"))
                refs = [self.ref(c, c.text) for c in task if tag(c) == "participantRef"]
                if (
                    not self.accepts(initiator, prop["initiator"])
                    or initiator not in refs
                    or None in refs
                    or len(set(refs)) < 2
                ):
                    continue
                for link in [c for c in task if tag(c) == "messageFlowRef"]:
                    flow = self.ref(link, link.text)
                    if tag(flow) != "messageFlow" or self.ref(flow, flow.get("sourceRef")) is not initiator:
                        continue
                    receiver = self.ref(flow, flow.get("targetRef"))
                    message = self.ref(flow, flow.get("messageRef"))
                    if (
                        receiver in refs
                        and self.accepts(receiver, prop["receiver"])
                        and self.accepts(message, prop["message"])
                    ):
                        matched.extend([task, flow, message])
            passed = bool(matched)
        else:
            raise ValueError(f"Unknown challenge predicate: {kind}")
        return {
            "id": prop["id"],
            "kind": kind,
            "passed": bool(passed),
            "matched_ids": sorted({e.get("id") for e in matched if e.get("id")}),
            **details,
        }


def evaluate_properties(case, response):
    try:
        oracle = ChallengeOracle(response["bpmn_xml"]) if response.get("bpmn_xml") else None
    except (ValueError, etree.XMLSyntaxError):
        oracle = None
    return [
        oracle.check(prop)
        if oracle
        else {"id": prop["id"], "kind": prop["kind"], "passed": False, "reason": "XML unavailable or invalid"}
        for prop in case["properties"]
    ]
