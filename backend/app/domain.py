"""Stable IR shared by the builder, isolated worker and deterministic compiler."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Process(Model):
    id: str
    name: str


class Participant(Model):
    id: str
    name: str
    process_id: str


class Lane(Model):
    id: str
    name: str
    process_id: str


class Node(Model):
    id: str
    type: Literal[
        "startEvent",
        "endEvent",
        "task",
        "userTask",
        "serviceTask",
        "sendTask",
        "receiveTask",
        "exclusiveGateway",
        "parallelGateway",
    ]
    name: str = ""
    process_id: str
    lane_id: str | None = None


class Flow(Model):
    id: str
    kind: Literal["sequence", "message"] = "sequence"
    source: str
    target: str
    name: str = ""
    condition: str | None = None
    is_default: bool = False


class Provenance(Model):
    element_id: str
    exact_quote: str
    source_start: int | None = None
    source_end: int | None = None
    origin: Literal["explicit", "structural", "proposed"] = "explicit"


class BpmnElement(Model):
    """One schema element; key is an IR handle, not necessarily an XML ID."""

    key: str
    tag: str
    parent: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    text: str | None = None
    tail: str | None = None


class BpmnDocument(Model):
    elements: list[BpmnElement] = Field(default_factory=list)
    namespaces: dict[str, str] = Field(default_factory=dict)


class ProcessIR(Model):
    schema_version: Literal["1.0", "2.0"] = "1.0"
    document: BpmnDocument | None = None
    processes: list[Process] = Field(default_factory=list)
    participants: list[Participant] = Field(default_factory=list)
    lanes: list[Lane] = Field(default_factory=list)
    nodes: list[Node] = Field(default_factory=list)
    flows: list[Flow] = Field(default_factory=list)
    source_text: str = ""
    provenance: list[Provenance] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class Issue(Model):
    code: str
    category: Literal["technical", "model", "question"] = "technical"
    severity: Literal["error", "warning", "info"] = "error"
    message: str
    element_ids: list[str] = Field(default_factory=list)
    source_quote: str | None = None
    suggested_action: str | None = None
    witness_path: list[str] = Field(default_factory=list)


class PipelineError(Exception):
    def __init__(self, code: str, message: str, element_ids: list[str] | None = None,
                 *, issues: list[Issue] | None = None):
        super().__init__(message)
        self.issues = issues
        self.issue = issues[0] if issues else Issue(code=code, message=message, element_ids=element_ids or [])
