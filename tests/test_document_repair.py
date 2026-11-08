"""Repairs receive evidence and cannot pass by erasing requested constructs."""

import json
from types import SimpleNamespace

import pytest

from backend.app.audit import audit
from backend.app.document_requirements import explicit_requirements, required_construct_issues
from backend.app.domain import PipelineError
from backend.app.graph import validate_graph
from tests.test_document_service import CODE, SOURCE
from tests.test_document_source_review import assigned_lane, builder, element
from tests.test_service import SequenceProvider, service


class AssumptionProvider(SequenceProvider):
    def __init__(self, assumptions):
        super().__init__([CODE] * len(assumptions))
        self.assumptions = iter(assumptions)

    async def generate(self, description, feedback=None):
        answer = await super().generate(description, feedback)
        answer.envelope.assumptions = next(self.assumptions)
        return answer


async def test_technical_assumption_gets_a_real_repair_and_original_is_preserved(tmp_path):
    provider = AssumptionProvider([["ID дорожки выбран manager."], []])
    result = await service(tmp_path, provider).generate(SOURCE)
    assert result["status"] == "ready" and result["attempt_count"] == 2
    assert provider.calls[1][0]["issues"][0]["code"] == "UNACCEPTED_ASSUMPTION"
    metadata = json.loads((tmp_path / result["id"] / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["attempts"][0]["ir"]["assumptions"] == ["ID дорожки выбран manager."]
    assert metadata["attempts"][1]["ir"]["assumptions"] == []


async def test_business_assumption_is_never_automatically_accepted(tmp_path):
    provider = AssumptionProvider([["При отказе деньги возвращаются."]] * 3)
    result = await service(tmp_path, provider).generate(SOURCE)
    assert result["status"] == "needs_clarification" and result["attempt_count"] == 3
    assert result["bpmn_xml"] is None and result["questions"]
    assert result["ir"]["assumptions"] == ["При отказе деньги возвращаются."]


async def test_real_ambiguity_can_be_returned_as_clarification_after_repair(tmp_path):
    class Provider(AssumptionProvider):
        async def generate(self, description, feedback=None):
            if feedback:
                self.calls.append(feedback)
                return SimpleNamespace(envelope=SimpleNamespace(
                    mode="clarify", questions=[SimpleNamespace(model_dump=lambda: {
                        "question": "Что происходит при отказе?", "source_quote": SOURCE,
                    })],
                ), usage=None)
            return await super().generate(description, feedback)

    result = await service(tmp_path, Provider([["Не определён исход отказа."]])).generate(SOURCE)
    assert result["status"] == "needs_clarification" and result["attempt_count"] == 2
    assert result["questions"][0]["question"] == "Что происходит при отказе?"
    assert result["bpmn_xml"] is None and result["ir"] is None


async def test_role_repair_receives_nested_scope_and_existing_roles(tmp_path):
    bad = CODE.replace('bpmn.element(id="assignment",tag="flowNodeRef",parent="manager",text="sub")', '')
    provider = SequenceProvider([bad, CODE])
    result = await service(tmp_path, provider).generate(SOURCE)
    assert result["status"] == "ready"
    context = provider.calls[1][0]["document_context"]
    task = next(item for item in context["affected_elements"] if item["id"] == "task")
    assert task["parent"] == "sub" and task["source_quote"] == SOURCE.rstrip(".")
    assert [item["id"] for item in task["ancestors"]] == ["sub", "p", "d"]
    assert any(role["name"] == "Менеджер" for role in context["declared_roles"])


async def test_all_omitted_constructs_are_reported_together_and_restored(tmp_path):
    source = SOURCE + " У задачи явно заданы inputSet и outputSet."
    corrected = CODE + '''
bpmn.element(id="io",tag="ioSpecification",parent="task")
bpmn.element(id="inputs",tag="inputSet",parent="io")
bpmn.element(id="outputs",tag="outputSet",parent="io")'''
    provider = SequenceProvider([CODE, corrected])
    result = await service(tmp_path, provider).generate(source)
    assert result["status"] == "ready", result["issues"]
    issues = provider.calls[1][0]["issues"]
    assert len(issues) == 2 and all(item["code"] == "EXPLICIT_CONSTRUCT_OMITTED" for item in issues)
    assert "inputSet" in result["bpmn_xml"] and "outputSet" in result["bpmn_xml"]


async def test_missing_role_and_constructs_share_one_repair_attempt(tmp_path):
    bad = CODE.replace('bpmn.element(id="assignment",tag="flowNodeRef",parent="manager",text="sub")', '')
    corrected = CODE + '''
bpmn.element(id="io",tag="ioSpecification",parent="task")
bpmn.element(id="inputs",tag="inputSet",parent="io")
bpmn.element(id="outputs",tag="outputSet",parent="io")'''
    provider = SequenceProvider([bad, corrected])
    result = await service(tmp_path, provider).generate(SOURCE + " Нужны inputSet и outputSet.")
    assert result["status"] == "ready" and result["attempt_count"] == 2
    assert {issue["code"] for issue in provider.calls[1][0]["issues"]} == {
        "UNASSIGNED_ROLE", "EXPLICIT_CONSTRUCT_OMITTED",
    }


async def test_repeated_deletion_cannot_become_a_ready_diagram(tmp_path):
    result = await service(tmp_path, SequenceProvider([CODE] * 3)).generate(
        SOURCE + " Используйте ioSpecification, inputSet и outputSet."
    )
    assert result["status"] == "error" and result["bpmn_xml"] is None
    assert len(result["issues"]) == 3 and result["attempt_count"] == 3


async def test_oversized_repair_context_preserves_failed_attempt_instead_of_crashing(tmp_path, monkeypatch):
    def fail(_):
        raise PipelineError("LLM_FEEDBACK_LIMIT", "Контекст слишком велик.")

    monkeypatch.setattr("backend.app.service.bounded_repair_feedback", fail)
    result = await service(tmp_path, SequenceProvider([CODE])).generate(SOURCE + " Нужен inputSet.")
    assert result["status"] == "error" and result["attempt_count"] == 1
    assert result["python_code"] == CODE and result["bpmn_xml"] is None
    assert {item["code"] for item in result["issues"]} == {
        "EXPLICIT_CONSTRUCT_OMITTED", "LLM_FEEDBACK_LIMIT",
    }
    metadata = json.loads((tmp_path / result["id"] / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["attempts"][0]["python_code"] == CODE


async def test_lost_named_message_is_repaired_before_ready(tmp_path):
    source = "Процесс начинается при поступлении сообщения «Заявка готова». " + SOURCE
    corrected = CODE + '''
bpmn.element(id="message",tag="message",parent="d",name="Заявка готова")
bpmn.element(id="trigger",tag="messageEventDefinition",parent="s",messageRef="message")'''
    provider = SequenceProvider([CODE, corrected])
    result = await service(tmp_path, provider).generate(source)
    assert result["status"] == "ready" and result["attempt_count"] == 2, result["issues"]
    assert provider.calls[1][0]["issues"][0]["code"] == "SOURCE_NAMED_EVENT_OMITTED"
    assert 'messageRef="message"' in result["bpmn_xml"]
    metadata = json.loads((tmp_path / result["id"] / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["attempts"][0]["python_code"] == CODE


async def test_compiler_failure_retains_diagnostic_without_model_rewrite(tmp_path, monkeypatch):
    from backend.app.service import validate_xml

    def layout_failure(xml):
        report = validate_xml(xml)
        report["checks"]["layout"] = "failed"
        report["issues"].append({
            "code": "MISSING_DI", "category": "model", "severity": "error",
            "message": "Нет геометрии связи.", "element_ids": ["sf1"],
        })
        return report

    monkeypatch.setattr("backend.app.service.validate_xml", layout_failure)
    provider = SequenceProvider([CODE])
    result = await service(tmp_path, provider).generate(SOURCE)
    assert result["status"] == "error" and result["bpmn_xml"] is None
    assert result["attempt_count"] == 1 and len(provider.calls) == 1
    assert any(
        issue["code"] == "MISSING_DI" and issue["element_ids"] == ["sf1"] for issue in result["issues"]
    )


@pytest.mark.parametrize("source", [
    "Без inputSet и outputSet.", "inputSet не нужен.", "Не используйте potentialOwner.",
    "Участники обсуждают callActivity.", "В документации упоминается dataStoreReference.",
    "inputSet и outputSet не нужны.",
])
def test_negated_or_discussed_xml_elements_are_not_requirements(source):
    assert explicit_requirements(source) == []


def test_positive_request_after_negation_is_preserved():
    assert [item["tag"] for item in explicit_requirements(
        "Не нужен callActivity, используйте subProcess с inputSet."
    )] == ["subProcess", "inputSet"]


@pytest.mark.parametrize("source", [
    "Используйте subProcess, а не callActivity.",
    "Вместо callActivity используйте subProcess.",
])
def test_replacement_request_preserves_only_positive_construct(source):
    assert [item["tag"] for item in explicit_requirements(source)] == ["subProcess"]


def test_type_and_foreign_namespace_cannot_fake_a_requested_construct():
    b = builder("Нужен dataStoreReference.")
    element(b, "Metadata", "extensionElements")
    b.methods["namespace"](prefix="vendor", uri="urn:vendor")
    element(b, "Foreign", "vendor:dataStoreReference", "Metadata")
    assert len(required_construct_issues(b.build())) == 1


def test_company_pool_cannot_hide_named_manager_assignment():
    b = builder()
    assigned_lane(b, task="Other")
    element(b, "Other", "task", name="Другое действие", source_quote=SOURCE)
    element(b, "T", "receiveTask", name="Проверить заявку", source_quote=SOURCE)
    element(b, "C", "collaboration", "D")
    element(b, "Pool", "participant", "C", name="Компания", processRef="P")
    issues = audit(b.build())
    assert any(issue.code == "UNASSIGNED_ROLE" and issue.element_ids == ["T"] for issue in issues)


def test_company_is_still_a_valid_actor_when_description_assigns_it_directly():
    source = "Компания проверяет заявку."
    b = builder(source)
    element(b, "T", "task", name="Проверить заявку", source_quote=source)
    element(b, "C", "collaboration", "D")
    element(b, "Pool", "participant", "C", name="Компания", processRef="P")
    assert not audit(b.build())


def test_xsd_missing_io_sets_identifies_the_actual_owner_for_repair():
    b = builder()
    element(b, "T", "task", name="Проверить заявку", source_quote=SOURCE)
    element(b, "IO", "ioSpecification", "T")
    issues = [issue for issue in validate_graph(b.build()) if issue.code == "XSD_INVALID"]
    assert issues and issues[0].element_ids == ["IO"]
    assert "inputSet" in issues[0].message


def test_nested_task_cannot_inherit_a_conflicting_outer_actor():
    source = "Сервис отправляет результат."
    b = builder(source)
    assigned_lane(b, task="Sub")
    element(b, "Service", "resource", "D", name="Сервис")
    element(b, "Sub", "subProcess", name="Проверка")
    element(b, "T", "serviceTask", "Sub", name="Отправить результат", source_quote=source)
    assert any(issue.code == "UNASSIGNED_ROLE" and issue.element_ids == ["T"] for issue in audit(b.build()))
    element(b, "Performer", "performer", "T")
    element(b, "Role", "resourceRef", "Performer", text="Service")
    assert not audit(b.build())


def test_direct_lane_assignment_cannot_contradict_one_explicit_quoted_role():
    source = "Сервис проверяет заявку."
    b = builder(source)
    assigned_lane(b)
    element(b, "Service", "resource", "D", name="Сервис")
    element(b, "T", "task", name="Проверить заявку", source_quote=source)
    assert any(issue.code == "UNASSIGNED_ROLE" for issue in audit(b.build()))


@pytest.mark.parametrize("source,quote,role", [
    ("Manager sends a request to SAP.", "sends a request to SAP", "Manager"),
    ("Менеджер отправляет заявку в SAP.", "отправляет заявку в SAP", "Менеджер"),
])
def test_recipient_in_partial_quote_does_not_override_assigned_actor(source, quote, role):
    b = builder(source)
    element(b, "LS", "laneSet")
    element(b, "L", "lane", "LS", name=role)
    element(b, "Ref", "flowNodeRef", "L", text="T")
    element(b, "SAP", "resource", "D", name="SAP")
    element(b, "T", "task", name="Передать заявку", source_quote=quote)
    assert not audit(b.build())


def test_scoped_actor_in_source_is_not_replaced_by_organization_pool():
    source = "У Поставщика оператор получает заказ."
    b = builder(source)
    element(b, "LS", "laneSet")
    element(b, "L", "lane", "LS", name="Оператор")
    element(b, "C", "collaboration", "D")
    element(b, "Pool", "participant", "C", name="Поставщик", processRef="P")
    element(b, "T", "receiveTask", name="Получить заказ", source_quote=source)
    assert any(issue.code == "UNASSIGNED_ROLE" for issue in audit(b.build()))
