"""Limited model review: exact evidence, useful questions, no BPMN false claims."""

import json
from pathlib import Path

import pytest

from backend.app.audit import CLARIFICATION_CODES, audit
from backend.app.domain import Provenance
from worker.interpreter import interpret

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / "examples/cases.json").read_text(encoding="utf-8"))["cases"]
READY = [case for case in CASES if case["expected_status"] == "ready"]


def fixture(case_id="P01"):
    source = next(case["text"] for case in CASES if case["id"] == case_id)
    code = (ROOT / "examples/programs" / f"{case_id}.py").read_text(encoding="utf-8")
    return interpret(code, source)


@pytest.mark.parametrize("case", READY, ids=lambda case: case["id"])
def test_ten_grounded_positive_fixtures_have_no_model_warnings(case):
    ir = fixture(case["id"])
    before = ir.model_dump_json()
    assert audit(ir) == []
    assert ir.model_dump_json() == before


def test_unassigned_role_is_model_warning_with_verified_quote():
    ir = fixture()
    ir.nodes[1].lane_id = None
    issues = audit(ir)
    assert len(issues) == 1
    issue = issues[0]
    assert (issue.code, issue.category, issue.severity) == ("UNASSIGNED_ROLE", "model", "warning")
    assert issue.element_ids == ["p01_check"]
    assert issue.source_quote in ir.source_text


@pytest.mark.parametrize("name", ["Роль не указана", "Не указан", " Не указана ", "ИСПОЛНИТЕЛЬ НЕ УКАЗАН"])
def test_neutral_lane_requires_confirmation(name):
    ir = fixture()
    ir.lanes[0].name = name
    issues = audit(ir)
    assert {issue.code for issue in issues} == {"UNASSIGNED_ROLE"}
    assert len(issues) == 2


def test_does_not_guess_role_from_arbitrary_substring():
    ir = fixture()
    ir.lanes[0].name = "Отдел неизвестных обращений"
    assert audit(ir) == []


@pytest.mark.parametrize("mutation", ["missing", "offset", "quote", "proposed", "structural", "negative"])
def test_missing_or_unverified_task_evidence_is_question_without_false_quote(mutation):
    ir = fixture()
    entry = next(item for item in ir.provenance if item.element_id == "p01_check")
    if mutation == "missing":
        ir.provenance.remove(entry)
    elif mutation == "offset":
        entry.source_start += 1
    elif mutation == "quote":
        entry.exact_quote = "Оплатить счёт"
    elif mutation in {"proposed", "structural"}:
        entry.origin = mutation
    elif mutation == "negative":
        entry.source_start = -1
    issue = next(item for item in audit(ir) if item.code == "ACTION_WITHOUT_SOURCE")
    assert issue.category == "question" and issue.severity == "warning"
    assert issue.element_ids == ["p01_check"]
    assert issue.source_quote is None


def test_unverified_role_source_is_never_displayed_as_exact_quote():
    ir = fixture()
    ir.nodes[1].lane_id = None
    entry = next(item for item in ir.provenance if item.element_id == "p01_check")
    entry.source_end += 2
    issue = next(item for item in audit(ir) if item.code == "UNASSIGNED_ROLE")
    assert issue.source_quote is None


def test_structural_gateways_events_and_multiple_ends_do_not_need_action_evidence():
    ir = fixture("P02")
    assert sum(node.type == "endEvent" for node in ir.nodes) == 2
    assert audit(ir) == []


def test_unlabelled_xor_branch_is_warning_linked_to_flow():
    ir = fixture("P02")
    gateway = next(node for node in ir.nodes if node.type == "exclusiveGateway")
    branch = next(flow for flow in ir.flows if flow.source == gateway.id)
    branch.name, branch.condition, branch.is_default = "", None, False
    issue = next(item for item in audit(ir) if item.code == "XOR_BRANCH_UNLABELED")
    assert issue.element_ids == [branch.id]
    assert issue.code not in CLARIFICATION_CODES


def test_unlabelled_and_paths_are_not_xor_choices_and_not_rewritten():
    ir = fixture("P03")
    before = ir.model_dump_json()
    assert audit(ir) == []
    assert ir.model_dump_json() == before


@pytest.mark.parametrize(
    "text",
    [
        "Что делать после отклонения, пока не определено.",
        "Что произойдёт при отрицательном результате, ещё не описано.",
        "Условие выхода из повторной обработки ещё не согласовано.",
        "Критерии завершения проверки пока не определены.",
    ],
)
def test_narrow_uncertainty_cues_use_real_exact_source_span(text):
    ir = fixture()
    ir.source_text += " " + text
    issues = audit(ir)
    assert len(issues) == 1
    issue = issues[0]
    assert (issue.code, issue.category) == ("SOURCE_UNCERTAINTY", "question")
    assert issue.source_quote and issue.source_quote in text and issue.source_quote in ir.source_text
    assert issue.element_ids == []


def test_conditional_business_rule_is_not_unspecified_description():
    ir = fixture()
    ir.source_text += " Если условие завершения не согласовано, менеджер уточняет его у автора."
    assert audit(ir) == []


def test_uncertainty_source_can_link_existing_gateway_without_inventing_steps():
    ir = fixture("P02")
    quote = "Что делать при отказе, пока не определено"
    start = len(ir.source_text) + 1
    ir.source_text += " " + quote + "."
    gateway = next(node for node in ir.nodes if node.type == "exclusiveGateway")
    ir.provenance.append(
        Provenance(
            element_id=gateway.id,
            exact_quote=quote,
            source_start=start,
            source_end=start + len(quote),
        )
    )
    before = ir.model_dump_json()
    issue = next(item for item in audit(ir) if item.code == "SOURCE_UNCERTAINTY")
    assert issue.element_ids == [gateway.id]
    assert issue.source_quote == quote
    assert ir.model_dump_json() == before


def test_assumptions_are_not_silently_accepted_as_business_facts():
    ir = fixture()
    ir.assumptions = ["После отказа инициатор получает уведомление", ""]
    issue = audit(ir)[0]
    assert issue.code == "UNACCEPTED_ASSUMPTION" and issue.code in CLARIFICATION_CODES
    assert issue.category == "model" and issue.source_quote is None


def test_quote_existence_does_not_claim_semantic_entailment_or_formal_proof():
    ir = fixture()
    ir.nodes[1].name = "Несоответствующее цитате действие"
    # This rule set intentionally makes no natural-language entailment claim.
    # Manual/source semantic evaluation remains a separate acceptance gate.
    assert audit(ir) == []
