"""Literal Python API for the complete BPMN schema, with bounded data only."""

import re

from backend.app.bpmn_schema import NAMESPACES, canonical, catalog, qualified
from backend.app.domain import BpmnDocument, BpmnElement, PipelineError, ProcessIR, Provenance

MAX_DOCUMENT_ELEMENTS = 2500
MAX_DOCUMENT_DEPTH = 48
MAX_DOCUMENT_ATTRIBUTES = 64
MAX_DOCUMENT_TEXT = 20000
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,99}\Z")


def _fail(code, message, key=None):
    raise PipelineError(code, message, [key] if key else [])


def _string(value, *, limit=MAX_DOCUMENT_TEXT):
    from backend.app.builder import valid_xml_text

    if not isinstance(value, str) or len(value) > limit or not valid_xml_text(value):
        _fail("INVALID_ARGUMENT", f"Нужна допустимая XML-строка длиной не более {limit} символов.")


class DocumentBuilder:
    def __init__(self, ir: ProcessIR):
        self.ir = ir
        self.elements: dict[str, BpmnElement] = {}
        self.types: dict[str, str | None] = {}
        self.depth: dict[str, int] = {}

    def document(self, *, id="definitions", name="", target_namespace="urn:process-architect:document"):
        if self.ir.document is not None or any((self.ir.nodes, self.ir.processes, self.ir.flows)):
            _fail(
                "DOCUMENT_MODE",
                "Документ объявляется один раз, до элементов; API двух версий не смешивается.",
            )
        _string(name, limit=1000)
        _string(target_namespace, limit=2000)
        if not target_namespace:
            _fail("INVALID_ARGUMENT", "target_namespace не может быть пустым.")
        self.ir.schema_version = "2.0"
        self.ir.document = BpmnDocument(namespaces=dict(NAMESPACES))
        self.element(id=id, tag="definitions", name=name)
        self.attribute(element=id, name="targetNamespace", value=target_namespace)
        self.attribute(element=id, name="exporter", value="Process Architect AI")
        self.attribute(element=id, name="exporterVersion", value="2")

    def namespace(self, *, prefix: str, uri: str):
        self._document()
        if not isinstance(prefix, str) or not _NAME.fullmatch(prefix) or prefix.lower().startswith("xml"):
            _fail("INVALID_NAMESPACE", "Недопустимый префикс пространства имён.")
        _string(uri, limit=2000)
        if not uri or prefix in NAMESPACES or prefix in self.ir.document.namespaces:
            _fail("INVALID_NAMESPACE", "Пространство имён пусто или префикс уже занят.")
        self.ir.document.namespaces[prefix] = uri

    def _document(self):
        if self.ir.document is None:
            _fail("DOCUMENT_REQUIRED", "Сначала вызовите bpmn.document().")

    def _get(self, key):
        if not isinstance(key, str) or key not in self.elements:
            _fail("UNKNOWN_ELEMENT", "Элемент должен быть объявлен до его свойств и дочерних элементов.")
        return self.elements[key]

    def _name(self, value):
        _string(value, limit=200)
        bits = value.split(":")
        if len(bits) > 2 or any(not _NAME.fullmatch(part) for part in bits):
            _fail("SCHEMA_ELEMENT", "Нужен QName элемента или атрибута из схемы.")
        try:
            return canonical(qualified(value, self.ir.document.namespaces))
        except KeyError:
            _fail("INVALID_NAMESPACE", "Префикс пространства имён не объявлен.")

    def element(
        self,
        *,
        id: str,
        tag: str,
        parent: str | None = None,
        name: str | None = None,
        text: str | None = None,
        type: str | None = None,
        source_quote: str | None = None,
        **attributes: str | bool | None,
    ):
        self._document()
        if not isinstance(id, str) or not _NAME.fullmatch(id) or id.startswith("PA_"):
            _fail("INVALID_ID", "Нужен уникальный XML-safe идентификатор без префикса PA_.")
        if id in self.elements:
            _fail("DUPLICATE_ID", "Идентификатор уже объявлен.", id)
        if len(self.elements) >= MAX_DOCUMENT_ELEMENTS:
            _fail("DOCUMENT_LIMIT", "Превышен ресурсный лимит элементов документа.")
        tag = self._name(tag)
        if parent is None:
            if self.elements or tag != "bpmn:definitions":
                _fail("SCHEMA_PARENT", "Единственный корень документа — definitions.", id)
            base_type = "bpmn:tDefinitions"
            depth = 0
        else:
            self._get(parent)
            parent_type = self.types[parent]
            base_type = catalog().element_type(tag, parent_type)
            wildcard = catalog().types.get(parent_type, {}).get("any_child", False) or self.elements[
                parent
            ].tag.startswith("{")
            # Foreign XML is inert extension data, only inside the schema's wildcard.
            foreign = tag.startswith("{")
            if base_type is None and not (wildcard and foreign):
                message = catalog().construction_message(
                    "element", self.elements[parent].tag, parent_type, tag, context_id=parent
                )
                _fail("SCHEMA_PARENT", message, id)
            depth = self.depth[parent] + 1
        if depth > MAX_DOCUMENT_DEPTH:
            _fail("DOCUMENT_LIMIT", "Превышена допустимая глубина документа.")
        if type is not None:
            actual_type = self._name(type)
            if base_type and not catalog().derived_from(actual_type, base_type):
                _fail("SCHEMA_TYPE", "xsi:type должен наследовать тип элемента.", id)
        else:
            actual_type = base_type
        metadata = catalog().types.get(actual_type, {})
        if metadata.get("abstract"):
            _fail("SCHEMA_TYPE", "Абстрактный тип требует конкретного xsi:type.", id)
        attrs = {"id": id} if any(item["name"] == "id" for item in metadata.get("attributes", [])) else {}
        if type is not None:
            attrs["xsi:type"] = actual_type
        entry = BpmnElement(key=id, tag=tag, parent=parent, attributes=attrs)
        self.ir.document.elements.append(entry)
        self.elements[id], self.types[id], self.depth[id] = entry, actual_type, depth
        if name is not None:
            self.attribute(element=id, name="name", value=name)
        if text is not None:
            self.content(element=id, text=text)
        for attr_name, value in attributes.items():
            self.attribute(element=id, name=attr_name, value=value)
        if source_quote is not None:
            _string(source_quote, limit=4000)
            offset = self.ir.source_text.find(source_quote)
            if not source_quote or offset < 0:
                _fail("SOURCE_QUOTE_NOT_FOUND", "Цитата отсутствует в исходном описании.", id)
            self.ir.provenance.append(
                Provenance(
                    element_id=id,
                    exact_quote=source_quote,
                    source_start=offset,
                    source_end=offset + len(source_quote),
                    origin="explicit",
                )
            )

    def attribute(self, *, element: str, name: str, value: str | bool | None):
        self._document()
        entry = self._get(element)
        if not isinstance(name, str):
            _fail("SCHEMA_ATTRIBUTE", "Имя атрибута должно быть строкой.")
        metadata = catalog().types.get(self.types[element], {})
        if ":" in name:
            name = self._name(name)
            if name in {"xsi:schemaLocation", "xsi:noNamespaceSchemaLocation"}:
                _fail("EXTERNAL_XML_REFERENCE", "XML не задаёт внешние схемы проверки.")
            if name == "xsi:type":
                _fail("SCHEMA_TYPE", "Указывайте type в bpmn.element до добавления дочерних элементов.")
            if not (metadata.get("any_attribute", False) or entry.tag.startswith("{")):
                message = catalog().construction_message(
                    "attribute", entry.tag, self.types[element], name, context_id=element
                )
                _fail("SCHEMA_ATTRIBUTE", message, element)
        elif not _NAME.fullmatch(name) or not any(
            item["name"] == name for item in metadata.get("attributes", [])
        ):
            # Wildcard extension elements can carry arbitrary foreign data attributes.
            if not entry.tag.startswith("{") or not _NAME.fullmatch(name):
                message = catalog().construction_message(
                    "attribute", entry.tag, self.types[element], name, context_id=element
                )
                _fail("SCHEMA_ATTRIBUTE", message, element)
        if name == "id" and value != element:
            _fail(
                "INVALID_ID", "XML ID задаётся идентификатором bpmn.element и не переопределяется.", element
            )
        if value is None:
            entry.attributes.pop(name, None)
            return
        if isinstance(value, bool):
            value = "true" if value else "false"
        attribute_type = next(
            (item["type"] for item in metadata.get("attributes", []) if item["name"] == name), None
        )
        if attribute_type == "xsd:boolean" and value in {"True", "False"}:
            value = value.lower()
        _string(value)
        if name not in entry.attributes and len(entry.attributes) >= MAX_DOCUMENT_ATTRIBUTES:
            _fail("DOCUMENT_LIMIT", "Превышен лимит атрибутов элемента.")
        entry.attributes[name] = value

    def content(self, *, element: str, text: str, tail: str | None = None):
        entry = self._get(element)
        _string(text)
        entry.text = text
        if tail is not None:
            _string(tail)
            entry.tail = tail

    def build(self):
        if self.ir.document is not None and any(
            (self.ir.processes, self.ir.nodes, self.ir.flows, self.ir.lanes, self.ir.participants)
        ):
            _fail("DOCUMENT_MODE", "API документа и прежний плоский API нельзя смешивать.")
        snapshot = self.ir.model_copy(deep=True)
        if snapshot.document is None:
            return snapshot
        entries = {entry.key: entry for entry in snapshot.document.elements}
        parents_with_children = {entry.parent for entry in entries.values()}
        for condition in entries.values():
            if (
                condition.tag != "bpmn:conditionExpression"
                or not (condition.text or "").strip()
                or condition.key in parents_with_children
            ):
                continue
            flow = entries.get(condition.parent)
            flow_type = self.types.get(condition.parent)
            if (
                flow is None or not flow_type
                or not catalog().derived_from(flow_type, "bpmn:tSequenceFlow")
                or flow.attributes.get("name", "").strip()
            ):
                continue
            ancestor = flow
            while ancestor is not None:
                if (
                    ancestor.tag in {"bpmn:extensionElements", "bpmn:documentation"}
                    or ancestor.tag.startswith("{")
                ):
                    break
                ancestor = entries.get(ancestor.parent)
            else:
                # bpmn-js and external BPMN editors display sequenceFlow.name,
                # not conditionExpression. Copy the existing text verbatim only
                # in generated snapshots: imports and manual XML stay untouched.
                # Mixed-content expressions need an explicit name: using only
                # .text would omit tails and can change the displayed condition.
                flow.attributes["name"] = condition.text
        return snapshot
