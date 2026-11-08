"""The deliberately small, literal-only BPMN construction API."""

import re
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from backend.app.document_builder import DocumentBuilder
from backend.app.domain import (
    Flow,
    Lane,
    Node,
    Participant,
    PipelineError,
    Process,
    ProcessIR,
    Provenance,
)

ID_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,99}\Z")
RESERVED_PREFIXES = (
    "__",
    "PA_",
    "Diagram_",
    "Plane_",
    "Shape_",
    "Edge_",
    "LaneSet_",
    "Collaboration_",
    "Definitions_",
)
TASK_TYPES = frozenset({"task", "userTask", "serviceTask", "sendTask", "receiveTask"})
MAX_PROCESSES = 2
MAX_PARTICIPANTS = 2
MAX_LANES = 5
MAX_NODES = 80
MAX_TASKS = 30
MAX_FLOWS = 320
MAX_TEXT = 20_000
MAX_LABEL = 1_000


def valid_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and ID_PATTERN.fullmatch(value) is not None
        and not value.startswith(RESERVED_PREFIXES)
    )


def valid_xml_text(value: str) -> bool:
    return all(
        char in "\t\n\r"
        or 0x20 <= ord(char) <= 0xD7FF
        or 0xE000 <= ord(char) <= 0xFFFD
        or 0x10000 <= ord(char) <= 0x10FFFF
        for char in value
    )


class Builder:
    """Build an IR without file access, imports, computed expressions or execution."""

    def __init__(self, source_text: str = "") -> None:
        if not isinstance(source_text, str) or len(source_text) > MAX_TEXT or not valid_xml_text(source_text):
            raise PipelineError("SOURCE_INVALID", "Описание должно быть строкой до 20 000 символов.")
        self._ir = ProcessIR(source_text=source_text)
        self._ids: set[str] = set()
        self._document = DocumentBuilder(self._ir)
        # No getattr or arbitrary attribute name is used by the interpreter.
        self.methods: dict[str, Callable[..., None]] = {
            "process": self.process,
            "pool": self.pool,
            "lane": self.lane,
            "start": self.start,
            "end": self.end,
            "task": self.task,
            "xor_gateway": self.xor_gateway,
            "parallel_gateway": self.parallel_gateway,
            "flow": self.flow,
            "message_flow": self.message_flow,
            "document": self._document.document,
            "element": self._document.element,
            "attribute": self._document.attribute,
            "content": self._document.content,
            "namespace": self._document.namespace,
        }

    def _id(self, value: object) -> None:
        if not valid_id(value):
            raise PipelineError("INVALID_ID", "ID должен быть XML-safe; внутренние префиксы зарезервированы.")
        if value in self._ids:
            raise PipelineError("DUPLICATE_ID", "Идентификатор уже используется.", [value])

    @staticmethod
    def _text(value: object, field: str, *, empty: bool = True) -> None:
        if (
            not isinstance(value, str)
            or len(value) > MAX_LABEL
            or (not empty and not value.strip())
            or not valid_xml_text(value)
        ):
            raise PipelineError("INVALID_ARGUMENT", f"Поле {field}: нужна строка до {MAX_LABEL} символов.")

    @staticmethod
    def _reference(value: object, field: str, *, optional: bool = False) -> None:
        if optional and value is None:
            return
        if not valid_id(value):
            raise PipelineError("INVALID_ARGUMENT", f"Поле {field}: нужен корректный идентификатор.")

    @staticmethod
    def _limit(items: list[Any], limit: int, title: str) -> None:
        if len(items) >= limit:
            raise PipelineError("PROFILE_LIMIT", f"Превышен лимит: {title} — {limit}.")

    def process(self, *, id: str, name: str) -> None:
        self._id(id)
        self._text(name, "name", empty=False)
        self._limit(self._ir.processes, MAX_PROCESSES, "процессы")
        self._ir.processes.append(Process(id=id, name=name))
        self._ids.add(id)

    def pool(self, *, id: str, name: str, process_id: str) -> None:
        self._id(id)
        self._text(name, "name", empty=False)
        self._reference(process_id, "process_id")
        self._limit(self._ir.participants, MAX_PARTICIPANTS, "пулы")
        self._ir.participants.append(Participant(id=id, name=name, process_id=process_id))
        self._ids.add(id)

    def lane(self, *, id: str, name: str, process_id: str) -> None:
        self._id(id)
        self._text(name, "name", empty=False)
        self._reference(process_id, "process_id")
        self._limit(self._ir.lanes, MAX_LANES, "дорожки")
        self._ir.lanes.append(Lane(id=id, name=name, process_id=process_id))
        self._ids.add(id)

    def _node(
        self,
        *,
        id: str,
        process_id: str,
        lane_id: str | None,
        name: str,
        node_type: str,
        source_quote: str | None,
    ) -> None:
        self._id(id)
        self._reference(process_id, "process_id")
        self._reference(lane_id, "lane_id", optional=True)
        self._text(name, "name", empty=node_type not in TASK_TYPES)
        self._limit(self._ir.nodes, MAX_NODES, "узлы")
        if node_type in TASK_TYPES:
            self._limit([node for node in self._ir.nodes if node.type in TASK_TYPES], MAX_TASKS, "действия")
        if source_quote is not None:
            self._text(source_quote, "source_quote", empty=False)
            offset = self._ir.source_text.find(source_quote)
            if offset == -1:
                raise PipelineError(
                    "SOURCE_QUOTE_NOT_FOUND", "Цитата не совпадает с исходным описанием.", [id]
                )
            provenance = Provenance(
                element_id=id,
                exact_quote=source_quote,
                source_start=offset,
                source_end=offset + len(source_quote),
                origin="explicit",
            )
        else:
            provenance = Provenance(
                element_id=id,
                exact_quote="",
                origin="proposed" if node_type in TASK_TYPES else "structural",
            )
        try:
            node = Node(id=id, type=node_type, process_id=process_id, lane_id=lane_id, name=name)
        except ValidationError as exc:
            raise PipelineError("UNSUPPORTED_ELEMENT", "Этот тип узла не поддерживается.", [id]) from exc
        self._ir.nodes.append(node)
        self._ir.provenance.append(provenance)
        self._ids.add(id)

    def start(
        self,
        *,
        id: str,
        process_id: str,
        lane_id: str | None = None,
        name: str = "",
        source_quote: str | None = None,
    ) -> None:
        self._node(
            id=id,
            process_id=process_id,
            lane_id=lane_id,
            name=name,
            node_type="startEvent",
            source_quote=source_quote,
        )

    def end(
        self,
        *,
        id: str,
        process_id: str,
        lane_id: str | None = None,
        name: str = "",
        source_quote: str | None = None,
    ) -> None:
        self._node(
            id=id,
            process_id=process_id,
            lane_id=lane_id,
            name=name,
            node_type="endEvent",
            source_quote=source_quote,
        )

    def task(
        self,
        *,
        id: str,
        process_id: str,
        lane_id: str | None = None,
        name: str,
        kind: str = "task",
        source_quote: str | None = None,
    ) -> None:
        if not isinstance(kind, str) or kind not in TASK_TYPES:
            raise PipelineError("UNSUPPORTED_ELEMENT", "Этот тип задачи не поддерживается.")
        self._node(
            id=id,
            process_id=process_id,
            lane_id=lane_id,
            name=name,
            node_type=kind,
            source_quote=source_quote,
        )

    def xor_gateway(
        self,
        *,
        id: str,
        process_id: str,
        lane_id: str | None = None,
        name: str = "",
        source_quote: str | None = None,
    ) -> None:
        self._node(
            id=id,
            process_id=process_id,
            lane_id=lane_id,
            name=name,
            node_type="exclusiveGateway",
            source_quote=source_quote,
        )

    def parallel_gateway(
        self,
        *,
        id: str,
        process_id: str,
        lane_id: str | None = None,
        name: str = "",
        source_quote: str | None = None,
    ) -> None:
        self._node(
            id=id,
            process_id=process_id,
            lane_id=lane_id,
            name=name,
            node_type="parallelGateway",
            source_quote=source_quote,
        )

    def flow(
        self,
        *,
        id: str,
        source: str,
        target: str,
        name: str = "",
        condition: str | None = None,
        is_default: bool = False,
        source_quote: str | None = None,
    ) -> None:
        self._flow(
            id=id,
            source=source,
            target=target,
            name=name,
            condition=condition,
            is_default=is_default,
            kind="sequence",
            source_quote=source_quote,
        )

    def message_flow(
        self, *, id: str, source: str, target: str, name: str = "", source_quote: str | None = None,
    ) -> None:
        self._flow(id=id, source=source, target=target, name=name, kind="message", source_quote=source_quote)

    def _flow(
        self,
        *,
        id: str,
        source: str,
        target: str,
        name: str,
        kind: str,
        condition: str | None = None,
        is_default: bool = False,
        source_quote: str | None = None,
    ) -> None:
        self._id(id)
        self._reference(source, "source")
        self._reference(target, "target")
        self._text(name, "name")
        if condition is not None:
            self._text(condition, "condition", empty=False)
        if not isinstance(is_default, bool):
            raise PipelineError("INVALID_ARGUMENT", "Поле is_default должно быть true/false.")
        provenance = None
        if source_quote is not None:
            self._text(source_quote, "source_quote", empty=False)
            offset = self._ir.source_text.find(source_quote)
            if offset < 0:
                raise PipelineError("SOURCE_QUOTE_NOT_FOUND", "Цитата связи отсутствует в описании.", [id])
            provenance = Provenance(
                element_id=id, exact_quote=source_quote, source_start=offset,
                source_end=offset + len(source_quote), origin="explicit",
            )
        self._limit(self._ir.flows, MAX_FLOWS, "связи")
        self._ir.flows.append(
            Flow(
                id=id,
                source=source,
                target=target,
                name=name,
                kind=kind,
                condition=condition,
                is_default=is_default,
            )
        )
        self._ids.add(id)

        if provenance is not None:
            self._ir.provenance.append(provenance)

    def build(self) -> ProcessIR:
        """Return an independent snapshot; graph validation is a separate gate."""
        return self._document.build()
