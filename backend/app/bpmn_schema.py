"""Catalogue derived from the vendored, unmodified OMG XSD, never from user URLs.

The catalogue describes construction/editing. lxml's official XMLSchema remains
the authority for content models, cardinalities, lexical types and restrictions.
"""

from functools import lru_cache
from pathlib import Path

from lxml import etree

NAMESPACES = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
    "dc": "http://www.omg.org/spec/DD/20100524/DC",
    "di": "http://www.omg.org/spec/DD/20100524/DI",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "xsd": "http://www.w3.org/2001/XMLSchema",
    "xml": "http://www.w3.org/XML/1998/namespace",
}
X = {"x": NAMESPACES["xsd"]}
SCHEMA_DIRECTORY = Path(__file__).resolve().parents[2] / "schemas" / "bpmn"
SCHEMA_NAMES = ("BPMN20.xsd", "Semantic.xsd", "BPMNDI.xsd", "DI.xsd", "DC.xsd")


def qualified(name: str, namespaces=None, *, default="bpmn") -> str:
    if name.startswith("{"):
        return name
    prefix, local = name.split(":", 1) if ":" in name else (default, name)
    return f"{{{(namespaces or NAMESPACES)[prefix]}}}{local}"


def canonical(name: str) -> str:
    if not name.startswith("{"):
        return name if ":" in name else "bpmn:" + name
    uri, local = name[1:].split("}", 1)
    prefix = next((key for key, value in NAMESPACES.items() if value == uri), None)
    return f"{prefix}:{local}" if prefix else name


class BpmnSchema:
    def __init__(self):
        self.elements: dict[str, dict] = {}
        self.types: dict[str, dict] = {}
        self._raw: dict[str, etree._Element] = {}
        for filename in SCHEMA_NAMES:
            root = etree.parse(
                str(SCHEMA_DIRECTORY / filename),
                etree.XMLParser(
                    resolve_entities=False,
                    no_network=True,
                    load_dtd=False,
                ),
            ).getroot()
            namespace = root.get("targetNamespace")
            for node in root:
                kind = etree.QName(node).localname if isinstance(node.tag, str) else ""
                if kind == "element":
                    tag = canonical(f"{{{namespace}}}{node.get('name')}")
                    self.elements[tag] = {
                        "tag": tag,
                        "type": self._qname(node, node.get("type", "xsd:anyType")),
                        "substitution": self._qname(node, node.get("substitutionGroup")),
                        "abstract": node.get("abstract") == "true",
                    }
                elif kind in {"complexType", "simpleType"}:
                    self._raw[canonical(f"{{{namespace}}}{node.get('name')}")] = node
        for name in tuple(self._raw):
            self._type(name)
        for item in self.elements.values():
            item["abstract"] = item["abstract"] or self.types.get(item["type"], {}).get("abstract", False)

    @staticmethod
    def _qname(node, value):
        if not value:
            return None
        prefix, local = value.split(":", 1) if ":" in value else (None, value)
        namespace = node.nsmap.get(prefix)
        return canonical(f"{{{namespace}}}{local}") if namespace else value

    def _type(self, name):
        if name in self.types:
            return self.types[name]
        node = self._raw.get(name)
        if node is None:
            return {"name": name, "attributes": [], "children": [], "enum": []}
        extension = node.find("x:complexContent/x:extension", X)
        restriction = node.find("x:complexContent/x:restriction", X)
        simple = node.find("x:simpleContent/x:extension", X)
        if simple is None:
            simple = node.find("x:simpleContent/x:restriction", X)
        direct = node.find("x:restriction", X)
        content = next((item for item in (extension, restriction, simple, direct) if item is not None), node)
        base_name = self._qname(content, content.get("base"))
        base = self._type(base_name) if base_name else {}
        result = {
            "name": name,
            "base": base_name,
            "abstract": node.get("abstract") == "true",
            "mixed": node.get("mixed") == "true" or base.get("mixed", False),
            "text": etree.QName(node).localname == "simpleType" or simple is not None,
            "attributes": [dict(item) for item in base.get("attributes", [])],
            "children": [] if restriction is not None else [dict(item) for item in base.get("children", [])],
            "any_child": base.get("any_child", False),
            "any_attribute": base.get("any_attribute", False),
            "enum": [item.get("value") for item in content.findall("x:enumeration", X)]
            or base.get("enum", []),
        }
        # Publish before following attribute types (a schema can be recursive).
        self.types[name] = result
        for attr in content.findall("x:attribute", X):
            attr_name = attr.get("name") or self._qname(attr, attr.get("ref"))
            attr_type = self._qname(attr, attr.get("type", "xsd:string"))
            data = {
                "name": attr_name,
                "type": attr_type,
                "required": attr.get("use") == "required",
                "default": attr.get("default"),
                "enum": self._type(attr_type).get("enum", []),
            }
            result["attributes"] = [item for item in result["attributes"] if item["name"] != attr_name]
            if attr.get("use") != "prohibited":
                result["attributes"].append(data)
        if content.find("x:anyAttribute", X) is not None:
            result["any_attribute"] = True

        def particles(parent):
            for item in parent:
                if not isinstance(item.tag, str):
                    continue
                kind = etree.QName(item).localname
                if kind in {"element", "any"}:
                    yield item
                elif kind in {"sequence", "choice", "all"}:
                    yield from particles(item)

        for child in particles(content):
            if etree.QName(child).localname == "any":
                result["any_child"] = True
                continue
            tag = self._qname(child, child.get("ref"))
            if tag is None:
                namespace = node.getroottree().getroot().get("targetNamespace")
                tag = canonical(f"{{{namespace}}}{child.get('name')}")
            child_type = self._qname(child, child.get("type")) or self.elements.get(tag, {}).get("type")
            inline = child.find("x:complexType", X)
            if inline is None:
                inline = child.find("x:simpleType", X)
            if inline is not None:
                child_type = name + "." + child.get("name", "element")
                self._raw[child_type] = inline
                self._type(child_type)
            result["children"].append(
                {
                    "tag": tag,
                    "type": child_type,
                    "min": child.get("minOccurs", "1"),
                    "max": child.get("maxOccurs", "1"),
                }
            )
        return result

    def derived_from(self, type_name: str, base: str) -> bool:
        type_name, base = canonical(type_name), canonical(base)
        seen = set()
        while type_name and type_name not in seen:
            if type_name == base:
                return True
            seen.add(type_name)
            type_name = self.types.get(type_name, {}).get("base")
        return False

    def substitutes(self, tag: str, head: str) -> bool:
        tag, head = canonical(tag), canonical(head)
        seen = set()
        while tag and tag not in seen:
            if tag == head:
                return True
            seen.add(tag)
            tag = self.elements.get(tag, {}).get("substitution")
        return False

    def children(self, type_name: str) -> list[dict]:
        result = []
        for child in self.types.get(canonical(type_name), {}).get("children", []):
            if child["tag"] not in self.elements:
                result.append(dict(child))
            else:
                result.extend(
                    {**child, "tag": tag, "type": entry["type"]}
                    for tag, entry in self.elements.items()
                    if not entry["abstract"] and self.substitutes(tag, child["tag"])
                )
        return result

    def element_type(self, tag: str, parent_type: str | None = None) -> str | None:
        tag = canonical(tag)
        if parent_type:
            for child in self.types.get(canonical(parent_type), {}).get("children", []):
                if tag == child["tag"]:
                    return child["type"]
                if self.substitutes(tag, child["tag"]):
                    return self.elements.get(tag, {}).get("type")
            if not self.types.get(canonical(parent_type), {}).get("any_child"):
                return None
        return self.elements.get(tag, {}).get("type")

    def child_rank(self, parent_type: str, child_tag: str) -> int:
        for index, child in enumerate(self.types.get(canonical(parent_type), {}).get("children", [])):
            if self.substitutes(child_tag, child["tag"]):
                return index
        return 10000

    def construction_feedback(self, kind, context_tag, context_type, attempted):
        """Describe a rejected construction using actual schema relations only.

        This is diagnostic data, never an alias map or permission to repair IR.
        Wildcard metadata containers are deliberately excluded as suggested
        business parents: moving a node into extensionElements would hide it.
        """
        if kind not in {"element", "attribute"}:
            raise ValueError("Construction feedback kind must be element or attribute")
        context_tag = canonical(context_tag)
        context_type = canonical(context_type) if context_type else None
        attempted = canonical(attempted) if kind == "element" else attempted
        children = self.children(context_type) if context_type else []
        attributes = self.types.get(context_type, {}).get("attributes", [])
        attempted_type = self.elements.get(attempted, {}).get("type") if kind == "element" else None
        type_from_name = False
        if kind == "element" and attempted_type is None and not attempted.startswith("{"):
            prefix, local = attempted.split(":", 1)
            convention_type = f"{prefix}:t{local[:1].upper()}{local[1:]}"
            for candidate in (attempted, convention_type):
                if candidate in self.types:
                    attempted_type, type_from_name = candidate, True
                    break
        same_type = [
            {"tag": child["tag"], "type": child["type"]}
            for child in children
            if attempted_type and child["type"] == attempted_type and child["tag"] != attempted
        ]
        parents = []
        if kind == "element":
            for parent_tag, declaration in self.elements.items():
                if declaration["abstract"]:
                    continue
                parent_type = declaration["type"]
                if any(
                    self.substitutes(attempted, child["tag"])
                    for child in self.types.get(parent_type, {}).get("children", [])
                ):
                    parents.append({"tag": parent_tag, "type": parent_type})
        hints = []
        if same_type:
            names = ", ".join(child["tag"] for child in same_type)
            hints.append(
                (
                    f"Запрошенное имя {attempted} не является объявленным XML-элементом. "
                    if type_from_name
                    else ""
                )
                + f"Тип {attempted_type} внутри {context_tag} задаётся полем {names}. "
                "В tag нужно имя XML-поля, а не имя типа XSD."
            )
        if parents:
            names = ", ".join(parent["tag"] for parent in parents)
            hints.append(f"Объявление {attempted} допускается внутри: {names}.")
        if kind == "attribute":
            child_tag = canonical(attempted)
            for child in children:
                if child["tag"] != child_tag:
                    continue
                child_type = child["type"]
                simple = bool(child_type) and (
                    child_type.startswith("xsd:") or self.types.get(child_type, {}).get("text", False)
                )
                hints.append(
                    f"{child['tag']} — дочернее поле типа {child_type}, не атрибут. "
                    "Создайте его через bpmn.element с parent=ID текущего элемента"
                    + (" и значением в text." if simple else ".")
                )
                break
        return {
            "kind": kind,
            "context_tag": context_tag,
            "context_type": context_type,
            "attempted": attempted,
            "attempted_type": attempted_type,
            "allowed_children": [{"tag": item["tag"], "type": item["type"]} for item in children],
            "allowed_attributes": [{"name": item["name"], "type": item["type"]} for item in attributes],
            "same_type_local_children": same_type,
            "allowed_global_parents": parents,
            "hints": hints,
        }

    def construction_message(self, kind, context_tag, context_type, attempted, *, context_id=None):
        """Bound a schema diagnostic below the unchanged worker transport limit."""
        feedback = self.construction_feedback(kind, context_tag, context_type, attempted)

        def short(value, limit=180):
            value = str(value)
            return value if len(value) <= limit else value[: limit - 1] + "…"

        def listing(items, key, limit):
            rendered, used = [], 0
            for item in items:
                value = short(item[key].removeprefix("bpmn:"), 100)
                value += " (" + short(item["type"], 100) + ")"
                if used + len(value) + 2 > limit:
                    break
                rendered.append(value)
                used += len(value) + 2
            if len(rendered) < len(items):
                rendered.append(f"… ещё {len(items) - len(rendered)}")
            return ", ".join(rendered) or "нет"

        container = (
            f"{short(feedback['context_tag'])}"
            + (f" [id={short(context_id, 100)}]" if context_id else "")
            + f" (фактический тип {short(feedback['context_type'])})"
        )
        if kind == "element":
            message = f"{short(feedback['attempted'])} не допускается внутри {container}."
        else:
            message = f"Атрибут {short(feedback['attempted'])} не предусмотрен для {container}."
        for hint in feedback["hints"]:
            message += " " + short(hint, 400)
        message += " Дочерние поля схемы: " + listing(feedback["allowed_children"], "tag", 650) + "."
        message += " Атрибуты схемы: " + listing(feedback["allowed_attributes"], "name", 380) + "."
        if len(message) > 1900:
            message = message[:1869] + "… (перечень сокращён)."
        return message

    def export(self):
        return {
            "version": "BPMN-2.0.2",
            "namespaces": NAMESPACES,
            "elements": self.elements,
            "types": {name: {**item, "children": self.children(name)} for name, item in self.types.items()},
        }


@lru_cache(maxsize=1)
def catalog() -> BpmnSchema:
    return BpmnSchema()
