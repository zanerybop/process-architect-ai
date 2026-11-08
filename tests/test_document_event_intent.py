"""Source intent gates accept equivalent standard representations, not benchmark IDs."""

import pytest

from backend.app.builder import Builder
from backend.app.document_intent import event_intent_issues


def document(source):
    builder = Builder(source_text=source)
    builder.methods["document"](id="D")
    builder.methods["element"](id="P", tag="process", parent="D")
    return builder


def add(builder, identifier, tag, parent="P", **attributes):
    builder.methods["element"](id=identifier, tag=tag, parent=parent, **attributes)


def codes(builder):
    return {issue.code for issue in event_intent_issues(builder.build())}


@pytest.mark.parametrize("source", [
    "При задержке нужно показать запуск компенсации на ветке возврата.",
    "Диспетчер должен инициировать компенсацию после отказа.",
])
def test_handler_alone_does_not_fulfil_explicit_compensation_invocation(source):
    b = document(source)
    add(b, "T", "task")
    add(b, "B", "boundaryEvent", attachedToRef="T")
    add(b, "C", "compensateEventDefinition", "B")
    add(b, "H", "task", isForCompensation=True)
    assert "SOURCE_COMPENSATION_NOT_TRIGGERED" in codes(b)


@pytest.mark.parametrize("reference", [False, True])
def test_compensation_throw_inline_or_event_definition_reference_is_accepted(reference):
    b = document("Нужно показать запуск компенсации после отказа. Для неё задан обработчик.")
    add(b, "E", "intermediateThrowEvent")
    add(b, "Def", "compensateEventDefinition", "D" if reference else "E")
    if reference:
        add(b, "Ref", "eventDefinitionRef", "E", text="Def")
    assert not codes(b)


def test_transaction_cancel_can_trigger_compensation():
    b = document("При отмене требуется запуск компенсации.")
    add(b, "Txn", "transaction")
    add(b, "E", "endEvent", "Txn")
    add(b, "Def", "cancelEventDefinition", "E")
    assert not codes(b)


@pytest.mark.parametrize("source", [
    "Запуск компенсации не нужен.",
    "Не нужен запуск компенсации.",
    "Процесс без запуска компенсации.",
    "В документации обсуждают запуск компенсации.",
    "Обработчик компенсации вызывается другим процессом.",
    "Не отправлять сообщение «Списать оплату».",
    "Не добавляйте сообщение «Отмена».",
    "Не посылать уведомление о повышении уровня проблемы.",
    "Сервис отправляет ответ, но не инициирует эскалацию.",
    "Отправить уведомление без эскалации.",
    "Бухгалтеру нужно выполнить компенсацию расходов сотруднику.",
    "Кадровик должен инициировать компенсацию за отпуск.",
    "В процессе нет сообщения «Готово».",
    "Сообщение «Готово» не отправляется.",
    "При ошибке оператор выбирает «Повторить».",
])
def test_negation_discussion_and_reusable_handlers_do_not_invent_obligations(source):
    assert not codes(document(source))


def test_explicit_escalation_cannot_be_replaced_by_message():
    b = document("Процедура посылает уведомление о повышении уровня проблемы, затем продолжает работу.")
    add(b, "E", "intermediateThrowEvent")
    add(b, "Def", "messageEventDefinition", "E")
    assert "SOURCE_ESCALATION_NOT_RAISED" in codes(b)


@pytest.mark.parametrize("reference", [False, True])
def test_escalation_throw_uses_inline_or_referenced_definition(reference):
    b = document("Процедура передаёт эскалацию руководителю.")
    add(b, "E", "intermediateThrowEvent")
    add(b, "Def", "escalationEventDefinition", "D" if reference else "E")
    if reference:
        add(b, "Ref", "eventDefinitionRef", "E", text="Def")
    assert not codes(b)


def test_catch_does_not_replace_escalation_raise():
    b = document("Процедура передаёт эскалацию руководителю.")
    add(b, "T", "task")
    add(b, "B", "boundaryEvent", attachedToRef="T")
    add(b, "Def", "escalationEventDefinition", "B")
    assert "SOURCE_ESCALATION_NOT_RAISED" in codes(b)


@pytest.mark.parametrize("kind, tag, attribute, definition", [
    ("сообщение", "message", "messageRef", "messageEventDefinition"),
    ("сигнал", "signal", "signalRef", "signalEventDefinition"),
    ("ошибка", "error", "errorRef", "errorEventDefinition"),
])
def test_named_event_definition_must_be_used(kind, tag, attribute, definition):
    b = document(f"Затем поступает {kind} «Пакет готов».")
    add(b, "Named", tag, "D", name="Пакет готов")
    add(b, "E", "intermediateCatchEvent", name="Ожидание")
    assert "SOURCE_NAMED_EVENT_OMITTED" in codes(b)
    add(b, "Def", definition, "E", **{attribute: "Named"})
    assert not codes(b)


@pytest.mark.parametrize("tag", ["receiveTask", "sendTask", "intermediateCatchEvent"])
def test_event_name_in_visible_label_preserves_natural_requirement(tag):
    b = document("Затем поступает сообщение «Ключ получен».")
    add(b, "E", tag, name="Ключ получен")
    assert not codes(b)


def test_foreign_message_reference_cannot_hide_missing_local_event_name():
    b = document("Затем поступает сообщение «Ключ получен».")
    b.methods["namespace"](prefix="ext", uri="urn:other")
    add(b, "M", "message", "D", name="Ключ получен")
    add(b, "E", "intermediateCatchEvent")
    add(b, "Def", "messageEventDefinition", "E", messageRef="ext:M")
    assert "SOURCE_NAMED_EVENT_OMITTED" in codes(b)


def test_metadata_name_cannot_hide_missing_event():
    b = document("Затем поступает сообщение «Ключ получен».")
    add(b, "Docs", "documentation")
    add(b, "Sample", "receiveTask", "Docs", name="Ключ получен")
    assert "SOURCE_NAMED_EVENT_OMITTED" in codes(b)


@pytest.mark.parametrize("description", ["сообщение датчика", "сообщение от банка", "сигнал контроллера"])
def test_named_event_with_sender_modifier_is_preserved(description):
    b = document(f"Ожидается {description} «Давление в норме».")
    add(b, "E", "intermediateCatchEvent", name="Сообщение")
    add(b, "Def", "messageEventDefinition", "E")
    assert "SOURCE_NAMED_EVENT_OMITTED" in codes(b)


def test_unused_root_event_definition_cannot_hide_missing_event_name():
    b = document("Ожидается сообщение «Запас пополнен».")
    add(b, "M", "message", "D", name="Запас пополнен")
    add(b, "Def", "messageEventDefinition", "D", messageRef="M")
    assert "SOURCE_NAMED_EVENT_OMITTED" in codes(b)


def test_explicit_undo_handler_requirement_cannot_be_omitted_together_with_throw():
    b = document("Нужен запуск компенсации уже выполненных операций. Каждая имеет обработчик.")
    assert "SOURCE_COMPENSATION_NOT_TRIGGERED" in codes(b)


def test_error_code_is_a_standard_event_identity_without_display_name():
    b = document("Процесс завершается ошибкой «E42».")
    add(b, "Error", "error", "D", errorCode="E42")
    add(b, "End", "endEvent")
    add(b, "Def", "errorEventDefinition", "End", errorRef="Error")
    assert not codes(b)


def test_definitions_library_may_declare_unused_named_messages():
    b = Builder(source_text="Сообщение «Ответ» и ресурс «Исполнитель».")
    b.methods["document"](id="D")
    add(b, "Message", "message", "D", name="Ответ")
    add(b, "Resource", "resource", "D", name="Исполнитель")
    assert not codes(b)
