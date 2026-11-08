"""Business-language composition examples, independent of challenge names and outcomes."""

import json

import pytest
from lxml import etree

from backend.app.bpmn_schema import NAMESPACES as NS
from backend.app.document_composition import (
    COMPENSATE_CODE,
    COMPENSATE_SOURCE,
    COMPOSITION_PATTERNS,
    ESCALATION_CODE,
    ESCALATION_SOURCE,
    PARALLEL_CODE,
    PARALLEL_SOURCE,
    business_constructs,
)
from backend.app.document_patterns import select_patterns
from backend.app.document_prompt import schema_context
from backend.app.document_serialization import serialize_document
from worker.interpreter import interpret


def tree(code, source):
    return etree.fromstring(serialize_document(interpret(code, source)).encode())


@pytest.mark.parametrize(
    ("text", "construct", "types", "example"),
    [
        (
            "Для каждого конверта запускается отдельная процедура; пять экземпляров идут одновременно.",
            "parallel_instances",
            {"tSubProcess", "tMultiInstanceLoopCharacteristics"},
            "parallel_scope_composition",
        ),
        (
            "Повар готовит гарнир и соус без обязательного порядка. После обеих работ подаёт блюдо.",
            "unordered_required",
            {"tParallelGateway", "tSubProcess"},
            "parallel_scope_composition",
        ),
        (
            "Во время занятия сообщение о пересадке запускает обработку. "
            "Она не прерывает занятие и может повторяться.",
            "repeatable_handler",
            {"tSubProcess", "tMessageEventDefinition"},
            "parallel_scope_composition",
        ),
        (
            "Из проверки отправляется уведомление о повышении уровня проблемы старшему бухгалтеру.",
            "escalation",
            {"tEscalation", "tEscalationEventDefinition", "tIntermediateThrowEvent"},
            "noninterrupting_escalation",
        ),
        (
            "Нужно отменить уже выполненные операции выдачи сертификата.",
            "compensation",
            {"tCompensateEventDefinition", "tIntermediateThrowEvent"},
            "triggered_compensation",
        ),
        (
            "Запустить компенсацию завершённого размещения объявления.",
            "compensation",
            {"tCompensateEventDefinition"},
            "triggered_compensation",
        ),
        (
            "Почта ожидает одно из двух событий: ответ адресата или срок ожидания. "
            "Побеждает первое наступившее событие.",
            "event_race",
            {"tEventBasedGateway", "tMessageEventDefinition", "tTimerEventDefinition"},
            None,
        ),
        (
            "Дважды вызывается типовая процедура проверки допуска к станку.",
            "reusable_call",
            {"tCallActivity", "tProcess"},
            None,
        ),
    ],
)
def test_natural_business_phrases_select_needed_schema_and_compact_example(text, construct, types, example):
    assert construct in business_constructs(text)
    context = schema_context(text)
    data = json.loads(context[context.index("{") :])
    assert types <= data["selected_types"].keys()
    assert data["composition_guidance"]
    if example:
        assert example in {item["id"] for item in data["family_examples"]}
    assert len(context) < 35000


@pytest.mark.parametrize(
    "text",
    [
        "Начальник повышает зарплату сотруднику.",
        "Курьер передаёт письмо руководителю.",
        "Секретарь уведомляет коллегу о встрече.",
    "Кассир отменяет ещё не начатую работу.",
    "Подготовка завершается, не отменяя незавершённые действия.",
    ],
)
def test_routine_handoff_notification_and_cancel_are_not_compensation_or_escalation(text):
    assert not business_constructs(text) & {"escalation", "compensation"}
    assert not select_patterns(text)


def test_parallel_scope_has_two_distinct_and_gateways_and_no_early_completion_or_bypass():
    root = tree(PARALLEL_CODE, PARALLEL_SOURCE)
    unit = root.find('.//bpmn:subProcess[@id="unit"]', NS)
    mi = unit.find("bpmn:multiInstanceLoopCharacteristics", NS)
    assert mi.get("isSequential") == "false"
    assert mi.find("bpmn:loopCardinality", NS).text == "2"
    assert mi.find("bpmn:completionCondition", NS) is None
    assert len(unit.findall("bpmn:parallelGateway", NS)) == 2
    assert not unit.findall("bpmn:inclusiveGateway", NS)
    flows = {(f.get("sourceRef"), f.get("targetRef")) for f in unit.findall("bpmn:sequenceFlow", NS)}
    assert {target for source, target in flows if source == "split"} == {"cable", "light"}
    assert {source for source, target in flows if target == "join"} == {"cable", "light"}
    assert {target for source, target in flows if source == "join"} == {"save"}
    assert {source for source, target in flows if target == "save"} == {"join"}
    event = unit.find('bpmn:subProcess[@triggeredByEvent="true"]', NS)
    assert event is not None and event.get("id") == "handler"
    start = event.find("bpmn:startEvent", NS)
    assert start.get("isInterrupting") == "false"
    assert start.find("bpmn:messageEventDefinition", NS).get("messageRef") == "m"
    assert root.find('bpmn:message[@id="m"]', NS).get("name") == "Поправка"
    assert not any("handler" in flow for flow in flows)
    assert not root.findall(".//bpmn:terminateEventDefinition", NS)


def test_escalation_preserves_conditional_throw_matching_ref_and_continues_procedure():
    root = tree(ESCALATION_CODE, ESCALATION_SOURCE)
    thrown = root.find(".//bpmn:intermediateThrowEvent/bpmn:escalationEventDefinition", NS)
    caught = root.find(".//bpmn:boundaryEvent/bpmn:escalationEventDefinition", NS)
    assert thrown.get("escalationRef") == caught.get("escalationRef") == "escalation"
    assert caught.getparent().get("cancelActivity") == "false"
    assert caught.getparent().get("attachedToRef") == "sub"
    assert thrown.getparent().getparent().get("id") == "sub"
    gateway = root.find(".//bpmn:exclusiveGateway", NS)
    assert gateway.get("default") == "skip"
    direct = root.find('.//bpmn:sequenceFlow[@id="skip"]', NS)
    assert direct.get("targetRef") == "save"
    assert (
        root.find('.//bpmn:sequenceFlow[@id="send"]/bpmn:conditionExpression', NS).text
        == "Нужна старшая экспертиза"
    )
    assert root.find('.//bpmn:sequenceFlow[@sourceRef="raise"]', NS).get("targetRef") == "save"
    assert not root.findall(".//bpmn:messageEventDefinition", NS)


def test_compensation_requires_both_handlers_and_synchronous_throw_before_cancel_completion():
    root = tree(COMPENSATE_CODE, COMPENSATE_SOURCE)
    thrown = root.find(".//bpmn:intermediateThrowEvent/bpmn:compensateEventDefinition", NS)
    assert thrown is not None and thrown.get("waitForCompletion") == "true"
    assert thrown.get("activityRef") is None
    associations = root.findall(".//bpmn:association", NS)
    assert {(a.get("sourceRef"), a.get("targetRef")) for a in associations} == {
        ("undo_create", "remove"),
        ("undo_bind", "release"),
    }
    for boundary, host in [("undo_create", "create"), ("undo_bind", "bind")]:
        node = root.find(f'.//bpmn:boundaryEvent[@id="{boundary}"]', NS)
        assert node.get("attachedToRef") == host
        assert node.getparent() is thrown.getparent().getparent()
    handlers = root.findall('.//*[@isForCompensation="true"]', NS)
    assert {n.get("id") for n in handlers} == {"remove", "release"}
    flows = {(n.get("sourceRef"), n.get("targetRef")) for n in root.findall(".//bpmn:sequenceFlow", NS)}
    assert not any({"remove", "release"} & set(pair) for pair in flows)
    assert ("create", "bind") in flows
    assert ("choice", "compensate") in flows
    assert {source for source, target in flows if target == "record"} == {"compensate"}
    assert (
        root.find('.//bpmn:sequenceFlow[@id="cancel"]/bpmn:conditionExpression', NS).text
        == "Профиль не нужен"
    )
    assert root.find('.//bpmn:sequenceFlow[@id="keep"]', NS).get("targetRef") == "end"


def test_family_context_additions_are_bounded_and_do_not_contain_challenge_identifiers():
    for example in COMPOSITION_PATTERNS:
        assert len(example["python_code"]) < 5400
        assert not any(
            value in example["python_code"] + example["description"]
            for value in ["CH02", "CH03", "CH04", "шкаф", "выпуск", "ложу"]
        )
    text = schema_context("Компенсация уже выполненных действий.")
    data = json.loads(text[text.index("{") :])
    assert [item["id"] for item in data["family_examples"]] == ["triggered_compensation"]


def test_explicitly_rejected_multi_instance_example_does_not_bloat_choreography_context():
    text = "Хореография содержит Standard loopType, не multi-instance и не цикл обычных задач."
    assert select_patterns(text) == []
