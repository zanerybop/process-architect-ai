"""Structural acceptance cases exercise real construction and invalid graphs."""

import pytest
from lxml import etree

from backend.app.builder import Builder
from backend.app.domain import Flow, PipelineError
from backend.app.graph import validate_graph
from backend.app.serialization import NS, serialize
from backend.app.xml_validation import validate_xml


def linear(*, source_text: str = "Менеджер проверяет заявку.") -> Builder:
    bpmn = Builder(source_text)
    bpmn.process(id="process", name="Обработка заявки")
    bpmn.pool(id="pool", name="Организация", process_id="process")
    bpmn.lane(id="lane", name="Менеджер", process_id="process")
    bpmn.start(id="start", process_id="process", lane_id="lane")
    bpmn.task(id="check", name="Проверить заявку", process_id="process", lane_id="lane")
    bpmn.end(id="end", process_id="process", lane_id="lane")
    bpmn.flow(id="first", source="start", target="check")
    bpmn.flow(id="last", source="check", target="end")
    return bpmn


def error_codes(bpmn: Builder) -> set[str]:
    return {issue.code for issue in validate_graph(bpmn.build()) if issue.severity == "error"}


def test_linear_and_snapshot_independence():
    bpmn = linear()
    assert validate_graph(bpmn.build()) == []
    snapshot = bpmn.build()
    snapshot.nodes[1].name = "Изменённая копия"
    assert bpmn.build().nodes[1].name == "Проверить заявку"
    assert set(bpmn.methods) == {
        "process",
        "pool",
        "lane",
        "start",
        "end",
        "task",
        "xor_gateway",
        "parallel_gateway",
        "flow",
        "message_flow",
        "document",
        "element",
        "attribute",
        "content",
        "namespace",
    }


def test_forward_references_are_resolved_by_graph_gate():
    bpmn = Builder()
    bpmn.flow(id="f", source="s", target="e")
    bpmn.start(id="s", process_id="p")
    bpmn.end(id="e", process_id="p")
    bpmn.process(id="p", name="Пустая обработка")
    assert validate_graph(bpmn.build()) == []


@pytest.mark.parametrize("element_id", ["1bad", "white space", "x:y", "../x", "PA_hidden", "__dict__", ""])
def test_xml_safe_and_reserved_ids(element_id):
    with pytest.raises(PipelineError) as caught:
        Builder().process(id=element_id, name="Процесс")
    assert caught.value.issue.code == "INVALID_ID"


def test_duplicate_id_is_global_and_does_not_replace_previous_object():
    bpmn = linear()
    with pytest.raises(PipelineError) as caught:
        bpmn.flow(id="check", source="start", target="end")
    assert caught.value.issue.code == "DUPLICATE_ID"
    assert len(bpmn.build().flows) == 2


@pytest.mark.parametrize("label", [10, None, "", "\x00", "x" * 1001])
def test_labels_reject_nonstring_blank_controls_and_oversize(label):
    with pytest.raises(PipelineError):
        Builder().process(id="p", name=label)


def test_escaping_characters_are_preserved_as_data():
    bpmn = Builder()
    bpmn.process(id="p", name='План & факт <2026> «Анализ "Север"»')
    assert bpmn.build().processes[0].name == 'План & факт <2026> «Анализ "Север"»'


def test_quote_uses_exact_python_string_offsets_without_model_supplied_offsets():
    text = "🔧 Менеджер проверяет заявку. Менеджер проверяет заявку."
    bpmn = Builder(text)
    quote = "Менеджер проверяет заявку"
    bpmn.task(id="t", process_id="p", name="Проверить заявку", source_quote=quote)
    evidence = bpmn.build().provenance[0]
    assert evidence.origin == "explicit"
    assert evidence.source_start == text.index(quote)
    assert text[evidence.source_start : evidence.source_end] == quote


def test_false_quote_rejected_atomically():
    bpmn = Builder("Менеджер проверяет заявку")
    with pytest.raises(PipelineError) as caught:
        bpmn.task(id="t", process_id="p", name="Заплатить", source_quote="Менеджер оплачивает заявку")
    assert caught.value.issue.code == "SOURCE_QUOTE_NOT_FOUND"
    assert not bpmn.build().nodes
    bpmn.task(id="t", process_id="p", name="Проверить", source_quote="Менеджер проверяет заявку")


def test_nonquoted_tasks_are_never_marked_explicit():
    bpmn = linear()
    origins = {item.element_id: item.origin for item in bpmn.build().provenance}
    assert origins == {"start": "structural", "check": "proposed", "end": "structural"}


@pytest.mark.parametrize("kind", ["task", "userTask", "serviceTask", "sendTask", "receiveTask"])
def test_supported_task_subtypes(kind):
    bpmn = Builder()
    bpmn.task(id="t", process_id="p", name="Действие", kind=kind)
    assert bpmn.build().nodes[0].type == kind


@pytest.mark.parametrize("kind", ["scriptTask", "subProcess", "inclusiveGateway", [], 5])
def test_unsupported_task_type_is_controlled(kind):
    with pytest.raises(PipelineError) as caught:
        Builder().task(id="t", process_id="p", name="Действие", kind=kind)
    assert caught.value.issue.code == "UNSUPPORTED_ELEMENT"


def test_profile_limits_are_rejections_without_truncation():
    bpmn = Builder()
    for index in range(30):
        bpmn.task(id=f"task_{index}", process_id="p", name="Действие")
    with pytest.raises(PipelineError) as caught:
        bpmn.task(id="too_many", process_id="p", name="Ещё действие")
    assert caught.value.issue.code == "PROFILE_LIMIT"
    assert len(bpmn.build().nodes) == 30
    for index in range(50):
        bpmn.xor_gateway(id=f"gateway_{index}", process_id="p")
    with pytest.raises(PipelineError):
        bpmn.end(id="extra", process_id="p")
    assert len(bpmn.build().nodes) == 80
    for index in range(5):
        bpmn.lane(id=f"lane_{index}", name="Роль", process_id="p")
    with pytest.raises(PipelineError):
        bpmn.lane(id="extra_lane", name="Роль", process_id="p")
    for index in range(2):
        bpmn.pool(id=f"pool_{index}", name="Участник", process_id=f"p_{index}")
    with pytest.raises(PipelineError):
        bpmn.pool(id="extra_pool", name="Участник", process_id="p")


def test_boolean_is_not_coerced_and_empty_condition_is_invalid():
    for value in (1, "false", None):
        with pytest.raises(PipelineError):
            Builder().flow(id="f", source="s", target="e", is_default=value)
    with pytest.raises(PipelineError):
        Builder().flow(id="f", source="s", target="e", condition="")


def test_unknown_flow_endpoint_and_wrong_lane_detected():
    bpmn = linear()
    bpmn.flow(id="broken", source="check", target="missing")
    assert "UNKNOWN_FLOW_ENDPOINT" in error_codes(bpmn)
    ir = linear().build()
    ir.nodes[1].lane_id = "absent"
    assert "UNKNOWN_LANE" in {issue.code for issue in validate_graph(ir)}
    ir.lanes[0].process_id = "other_process"
    ir.nodes[1].lane_id = "lane"
    assert "LANE_PROCESS_MISMATCH" in {issue.code for issue in validate_graph(ir)}


def test_missing_lane_is_model_warning_not_technical_error():
    ir = linear().build()
    ir.nodes[1].lane_id = None
    issues = validate_graph(ir)
    assert [(item.code, item.category, item.severity) for item in issues] == [
        ("ROLE_UNSPECIFIED", "model", "warning"),
    ]


def alternatives(*, parallel: bool = False) -> Builder:
    bpmn = Builder()
    bpmn.process(id="p", name="Разветвление")
    bpmn.start(id="s", process_id="p")
    method = bpmn.parallel_gateway if parallel else bpmn.xor_gateway
    method(id="g", process_id="p")
    bpmn.end(id="yes", process_id="p")
    bpmn.end(id="no", process_id="p")
    bpmn.flow(id="entry", source="s", target="g")
    bpmn.flow(id="f_yes", source="g", target="yes", name="Согласовано")
    bpmn.flow(id="f_no", source="g", target="no", name="Отклонено")
    return bpmn


def test_xor_alternative_ends_valid_without_artificial_merge():
    assert validate_graph(alternatives().build()) == []


def test_multiple_defaults_and_default_condition_rejected():
    ir = alternatives().build()
    ir.flows[1].is_default = True
    ir.flows[2].is_default = True
    ir.flows[1].condition = "Согласовано"
    codes = {issue.code for issue in validate_graph(ir)}
    assert {"MULTIPLE_DEFAULTS", "DEFAULT_CONDITION"} <= codes


def test_and_conditions_are_rejected_without_changing_gateway():
    ir = alternatives(parallel=True).build()
    ir.flows[1].condition = "Если согласовано"
    assert "PARALLEL_CONDITION" in {issue.code for issue in validate_graph(ir)}
    assert ir.nodes[1].type == "parallelGateway"


def test_xor_missing_branch_description_is_warning():
    ir = alternatives().build()
    ir.flows[1].name = ""
    issue = next(item for item in validate_graph(ir) if item.code == "XOR_BRANCH_UNLABELED")
    assert issue.category == "model"
    assert issue.severity == "warning"


def test_parallel_split_join_has_both_paths_and_is_valid():
    bpmn = alternatives(parallel=True)
    bpmn.parallel_gateway(id="join", process_id="p")
    bpmn.end(id="final", process_id="p")
    ir = bpmn.build()
    ir.nodes[2].type = "task"
    ir.nodes[3].type = "task"
    ir.flows.extend(
        [
            Flow(id="left_join", source="yes", target="join"),
            Flow(id="right_join", source="no", target="join"),
            Flow(id="finish", source="join", target="final"),
        ]
    )
    assert not [item for item in validate_graph(ir) if item.severity == "error"]


def test_cycle_with_exit_is_valid_but_closed_cycle_has_no_path_to_end():
    bpmn = linear()
    bpmn.xor_gateway(id="choice", process_id="process", lane_id="lane")
    ir = bpmn.build()
    ir.flows[1].target = "choice"
    ir.flows.extend(
        [
            Flow(id="retry", source="choice", target="check", name="Неполно"),
            Flow(id="accepted", source="choice", target="end", name="Полно"),
        ]
    )
    assert validate_graph(ir) == []
    ir.flows.pop()
    assert "NO_PATH_TO_END" in {item.code for item in validate_graph(ir)}


def two_pools() -> Builder:
    bpmn = linear()
    bpmn.process(id="p2", name="Ответ")
    bpmn.pool(id="pool2", process_id="p2", name="Поставщик")
    bpmn.start(id="s2", process_id="p2")
    bpmn.end(id="e2", process_id="p2")
    bpmn.flow(id="p2_flow", source="s2", target="e2")
    return bpmn


def test_distinct_pools_message_flows_and_cross_pool_sequence_rejection():
    bpmn = two_pools()
    bpmn.message_flow(id="request", source="check", target="s2", name="Запрос")
    bpmn.message_flow(id="reply", source="pool2", target="pool", name="Ответ")
    assert validate_graph(bpmn.build()) == []
    bpmn.flow(id="illegal_sequence", source="check", target="e2")
    assert "CROSS_PROCESS_SEQUENCE" in error_codes(bpmn)


def test_message_inside_pool_rejected():
    bpmn = linear()
    bpmn.message_flow(id="illegal_message", source="start", target="check")
    assert "MESSAGE_SAME_POOL" in error_codes(bpmn)


@pytest.mark.parametrize("source", ["check", "end", "pool"])
@pytest.mark.parametrize("target", ["s2", "pool2"])
def test_message_allows_activity_throwing_end_participant_and_catching_start(source, target):
    bpmn = two_pools()
    bpmn.message_flow(id="message", source=source, target=target)
    assert error_codes(bpmn) == set()
    report = validate_xml(serialize(bpmn.build()))
    assert report["checks"]["graph"] == "passed"
    assert report["checks"]["xsd"] == "passed"


@pytest.mark.parametrize("kind", ["task", "userTask", "serviceTask", "sendTask", "receiveTask"])
def test_message_all_supported_activity_types_can_send_and_receive(kind):
    bpmn = two_pools()
    bpmn.message_flow(id="request", source="check", target="pool2")
    bpmn.message_flow(id="reply", source="e2", target="check")
    ir = bpmn.build()
    next(node for node in ir.nodes if node.id == "check").type = kind
    assert not [issue for issue in validate_graph(ir) if issue.severity == "error"]


@pytest.mark.parametrize("gateway", ["exclusiveGateway", "parallelGateway"])
@pytest.mark.parametrize("direction", ["source", "target"])
def test_message_gateway_endpoints_are_rejected(gateway, direction):
    bpmn = two_pools()
    bpmn.message_flow(
        id="message",
        source="check" if direction == "source" else "e2",
        target="s2" if direction == "source" else "check",
    )
    ir = bpmn.build()
    next(node for node in ir.nodes if node.id == "check").type = gateway
    assert "MESSAGE_ENDPOINT_TYPE" in {issue.code for issue in validate_graph(ir)}
    with pytest.raises(PipelineError) as caught:
        serialize(ir)
    assert caught.value.issue.code == "MESSAGE_ENDPOINT_TYPE"


@pytest.mark.parametrize("source,target", [("start", "s2"), ("check", "e2")])
def test_message_start_source_and_end_target_rejected(source, target):
    bpmn = two_pools()
    bpmn.message_flow(id="message", source=source, target=target)
    assert "MESSAGE_ENDPOINT_TYPE" in error_codes(bpmn)


@pytest.mark.parametrize("attribute,value", [("sourceRef", "start"), ("targetRef", "e2")])
def test_current_xml_message_event_direction_fails_graph_despite_xsd(attribute, value):
    bpmn = two_pools()
    bpmn.message_flow(id="message", source="check", target="s2")
    root = etree.fromstring(serialize(bpmn.build()).encode("utf-8"))
    root.find("bpmn:collaboration/bpmn:messageFlow", NS).set(attribute, value)
    report = validate_xml(etree.tostring(root, encoding="unicode"))
    assert report["checks"]["xsd"] == "passed"
    assert report["checks"]["graph"] == "failed"
    assert any(issue["code"] == "MESSAGE_ENDPOINT_TYPE" for issue in report["issues"])


def test_unreachable_island_and_event_direction_are_detected():
    bpmn = linear()
    bpmn.task(id="island", process_id="process", lane_id="lane", name="Действие")
    bpmn.flow(id="self_loop", source="island", target="island")
    bpmn.flow(id="end_to_start", source="end", target="start")
    assert {"UNREACHABLE_NODE", "NO_PATH_TO_END", "START_INCOMING", "END_OUTGOING"} <= error_codes(bpmn)


def test_graph_duplicate_and_invalid_provenance_do_not_slip_through_direct_ir():
    ir = linear().build()
    ir.nodes[1].id = "start"
    ir.provenance[0].origin = "explicit"
    ir.provenance[0].exact_quote = "Несуществующая цитата"
    codes = {issue.code for issue in validate_graph(ir)}
    assert {"DUPLICATE_ID", "SOURCE_QUOTE_NOT_FOUND"} <= codes


def test_empty_model_reports_missing_process():
    assert "PROCESS_REQUIRED" in error_codes(Builder())
