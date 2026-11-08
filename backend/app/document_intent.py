"""Narrow source-backed event obligations, separate from BPMN schema validity.

A compensation handler can legitimately be invoked by a caller, and an
escalation definition can legitimately be unused in a library. Only explicit
positive source instructions to *raise* these events create this obligation.
"""

import re

from backend.app.document_review import _review_index
from backend.app.document_validation import is_type, semantic_tag
from backend.app.domain import Issue, ProcessIR

_RAISE = r"(?:посыла|посыл|отправ|переда|выда|выбрас|генер|подним|инициир)\w*"
_ESCALATION = re.compile(
    _RAISE + r"(?:(?!\b(?:не|но|без)\b)[^.!?\n]){0,140}"
    r"(?:эскалаци\w*|повышени\w+\s+уровня\s+проблем\w*)", re.I
)
_COMPENSATION = re.compile(
    r"(?:запуск\w*|запуст\w*|выз[ов]\w*|инициир\w*|выполн\w*)\s+компенсаци\w*", re.I
)
_NAMED_EVENT = re.compile(
    r'\b(?:(?:сообщени\w*|сигнал\w*)\s+'
    r'(?:(?:от\s+)?(?:[\w-]+(?:а|я|ов|ев|ы|и)\s+){1,2})?|(?:ошибк\w*|эскалаци\w*)\s+)'
    r'(?:с\s+(?:названием|кодом|именем)\s+)?'
    r'(?:«(?P<angle>[^»\n]{1,120})»|"(?P<straight>[^"\n]{1,120})")', re.I
)


def _positive_matches(source, pattern):
    for match in pattern.finditer(source):
        start = max(source.rfind(char, 0, match.start()) for char in ".!?;\n") + 1
        stops = [pos for char in ".!?;\n" if (pos := source.find(char, match.end())) >= 0]
        end = min(stops, default=len(source))
        before, after = source[start:match.start()], source[match.end():end]
        clause = re.split(r"\b(?:но|а|зато)\b", before, flags=re.I)[-1]
        if re.search(r"\b(?:без|вместо|не\s+(?:нуж\w*|треб\w*|след\w*|долж\w*))\b|\bне\s*$", clause, re.I):
            continue
        if re.match(r"\s+не\s+(?:нуж\w*|треб\w*|предусмотр\w*|выполня\w*)", after, re.I):
            continue
        if re.match(r"\s+не\s+(?:отправля|использу|генериру|переда|посыла)\w*", after, re.I):
            continue
        if re.search(r"\bнет\s*$", before, re.I) and not re.search(r"\b(?:если|когда)\b", before, re.I):
            continue
        if re.search(r"\b(?:обсужд|упомина|документаци|справк)\w*", before, re.I):
            continue
        if re.search(r"\bне\s+(?:добавля|добавь|создава|создай|отправля|отправь|использ)\w*", clause, re.I):
            continue
        yield source[start:end].strip()


def event_intent_issues(ir: ProcessIR) -> list[Issue]:
    if ir.document is None:
        return []
    index = _review_index(ir)

    def definitions(event):
        for child in event:
            if is_type(child, "EventDefinition"):
                yield child
            elif semantic_tag(child) == "eventDefinitionRef":
                target = index.resolve(child.text, child, "EventDefinition", report=False)
                if target is not None:
                    yield target

    thrown = set()
    used_definitions = set()
    catch_ids = {}
    for event in index.elements:
        if not is_type(event, "Event"):
            continue
        event_definitions = list(definitions(event))
        used_definitions.update(event_definitions)
        kinds = {semantic_tag(item) for item in event_definitions}
        if semantic_tag(event) in {"endEvent", "intermediateThrowEvent"}:
            thrown.update(kinds)
        else:
            for kind in kinds:
                catch_ids.setdefault(kind, []).append(event.get("id", ""))

    issues = []
    obligations = (
        (_ESCALATION, "escalationEventDefinition", "SOURCE_ESCALATION_NOT_RAISED",
         "Описание требует повышения уровня проблемы, но событие эскалации нигде не выбрасывается.",
         "Сохраните escalation throw и соответствующий обработчик в заданной области; "
         "messageEventDefinition имеет другую семантику."),
        (_COMPENSATION, "compensateEventDefinition", "SOURCE_COMPENSATION_NOT_TRIGGERED",
         "Описание требует запуска компенсации, но в модели есть только определения или обработчики.",
         "Добавьте запуск компенсации на указанной в описании ветке. Обработчик isForCompensation "
         "сам по себе не запускается. Сохраните порядок отмены и ожидание обработчиков."),
    )
    for pattern, kind, code, message, action in obligations:
        # A cancel end in a transaction invokes its compensation semantics.
        if kind in thrown or (kind == "compensateEventDefinition" and "cancelEventDefinition" in thrown):
            continue
        if kind == "compensateEventDefinition" and not (
            catch_ids.get(kind)
            or any(item.get("isForCompensation") in {"true", "1"} for item in index.elements)
            or re.search(
                r"\bобработчик\w*|\b(?:уже\s+)?выполненн\w*\s+операци\w*|"
                r"\bуспешн\w*\s+операци\w*|\bcompensateEventDefinition\b|\bisForCompensation\b",
                ir.source_text, re.I,
            )
        ):
            # Ordinary reimbursement is business work, not automatically the
            # BPMN mechanism for undoing a previously completed activity.
            continue
        quote = next(_positive_matches(ir.source_text, pattern), None)
        if quote:
            issues.append(Issue(
                code=code, category="model", message=message,
                source_quote=quote, element_ids=[value for value in catch_ids.get(kind, []) if value],
                suggested_action=action,
            ))
    # A named external event must remain identifiable. A label is sufficient for
    # natural-language input; a referenced standard definition is also sufficient.
    # Unused definitions and names buried in documentation are not visible evidence.
    visible_names = {
        item.get("name", "") for item in index.elements
        if is_type(item, "FlowNode") or is_type(item, "MessageFlow")
    }
    library = not any(is_type(item, "Process") or is_type(item, "Collaboration") for item in index.elements)
    if library:
        visible_names.update(
            item.get("name", "") for item in index.elements
            if any(is_type(item, kind) for kind in ("Message", "Signal", "Error", "Escalation"))
        )
    for item in index.elements:
        if item not in used_definitions and not (is_type(item, "FlowNode") or is_type(item, "MessageFlow")):
            continue
        for attribute, target_type in (
            ("messageRef", "Message"), ("signalRef", "Signal"), ("errorRef", "Error"),
            ("escalationRef", "Escalation"),
        ):
            if item.get(attribute):
                target = index.ref(item, attribute, target_type)
                if target is not None:
                    visible_names.add(target.get("name", ""))
                    visible_names.add(target.get("errorCode", ""))
                    visible_names.add(target.get("escalationCode", ""))
    for match in _NAMED_EVENT.finditer(ir.source_text):
        name = match.group("angle") or match.group("straight")
        if any(name.casefold() in text.casefold() for text in visible_names):
            continue
        # Reuse the positive-clause filtering at the original source position.
        literal = re.compile(re.escape(match.group()), re.I)
        quote = next(_positive_matches(ir.source_text, literal), None)
        if quote:
            issues.append(Issue(
                code="SOURCE_NAMED_EVENT_OMITTED", category="model",
                message=f"Потеряно имя события «{name}», указанное в описании.", source_quote=quote,
                suggested_action=(
                    "Сохраните указанное имя в соответствующем событии или задаче обмена, "
                    "либо в используемом message/signal/error с корректной ссылкой."
                ),
            ))
    return issues
