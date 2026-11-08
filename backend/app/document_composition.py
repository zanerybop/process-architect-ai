"""Business wording selects notation context, never a generated answer or domain fixture."""

import re

TRIGGERS = {
    "parallel_instances": r"\bэкземпляр|для\s+кажд\w+[\s\S]{0,90}(?:отдельн|сво[яйе])",
    "unordered_required": r"(?:нет|без)[\s\S]{0,30}(?:обязательн|фиксированн|установленн)"
    r"[\s\S]{0,18}поряд|(?:когда|после|дожд\w*)[\s\S]{0,35}(?:обе|обоих|все)"
    r"[\s\S]{0,35}(?:работ|действ|провер|заверш)|без\s+порядк",
    "repeatable_handler": r"(?:пока|в\s+ходе|во\s+время)[\s\S]{0,150}сообщен|"
    r"сообщен[\s\S]{0,180}(?:не\s+останавлива|не\s+прерыва|повторн|новых\s+сообщен)|"
    r"непрерывающ\w*[\s\S]{0,40}(?:обработ|событ)",
    "escalation": r"эскалац|escalation|повышени\w*\s+уровн\w*\s+(?:проблем|вопрос|ответствен)|"
    r"(?:проблем|вопрос)\w*[\s\S]{0,25}на\s+вышестоящ\w*\s+уров",
    "compensation": r"компенсац|compensat|отмен\w*[\s\S]{0,55}(?:уже\s+)?"
    r"\b(?:выполненн|завершенн|успешн)\w*\s+(?:операц|действ|работ)|"
    r"обратн\w+\s+порядк[\s\S]{0,100}(?:вернут|возврат|освобод|отмен)",
    "transaction": r"transaction|транзакц|cancelEventDefinition|cancel\s+end",
    "event_race": r"(?:перв\w+\s+(?:наступивш|произошедш)|побеждает\s+перв|"
    r"одно\s+из\s+двух\s+событ|втор\w+\s+ожидани\w*\s+.*отмен)",
    "reusable_call": r"(?:типов\w*|переиспользуем\w*)\s+процедур|"
    r"(?:вызывается|вызвать|вызов)\s+(?:та\s+же\s+)?процедур",
}


def business_constructs(text: str) -> set[str]:
    """Broad hints for relevant schema/examples; they never assert source requirements."""
    normalized = text.lower().replace("ё", "е")
    return {name for name, pattern in TRIGGERS.items() if re.search(pattern, normalized)}


COMPOSITION_GUIDANCE = {
    "parallel_instances": "Различай повтор всей процедуры и повтор одной задачи. Если для каждого объекта "
    "описана своя внутренняя работа, MI принадлежит содержащему её subProcess. Параллельные экземпляры: "
    "isSequential=false; количество только из текста; без completionCondition раннего выхода, "
    "если нужны все.",
    "unordered_required": "Если обе независимые работы обязательны один раз, подходит AND split → две "
    "ветки → AND join. Отсутствие порядка не означает необязательность. Если указан ad-hoc, используй его "
    "completionCondition и cancelRemainingInstances. Не смешивай разделение и синхронизацию в одном шлюзе "
    "с прямой выходной веткой: она пропускает обязательные работы. Inclusive выбирает подмножество по "
    "условиям, а не заменяет ожидание обеих обязательных работ.",
    "repeatable_handler": "Обработчик сообщения во время конкретного экземпляра помещается в его scope: "
    "event subProcess(triggeredByEvent=true), message start(isInterrupting=false), свой обычный end. "
    "Названное сообщение — message с точным name в definitions и messageRef на его ID, не пустое "
    "messageEventDefinition. Он не включён в основную sequenceFlow и может запускаться снова. "
    "Не выноси его из MI-контейнера "
    "и не заверши terminateEvent основной экземпляр. Назначь описанную роль и внутри обработчика.",
    "escalation": "Повышение уровня проблемы из дочерней процедуры — escalation, не message только "
    "потому, что текст говорит «уведомление». Определение escalation в definitions; throw и catch "
    "ссылаются escalationRef. Если процедура продолжается: intermediateThrowEvent внутри, boundary "
    "cancelActivity=false снаружи. Throw должен находиться только на описанной условной ветке. "
    "Обычный обмен сообщениями не превращай в escalation без смысла повышения уровня проблемы.",
    "compensation": "Handlers сами не запускаются. На описанной ветке отмены уже выполненных операций "
    "нужен throwing compensateEventDefinition; для последующего действия используй intermediateThrowEvent "
    "с waitForCompletion=true. Без activityRef запускаются все видимые успешные операции в обратном "
    "порядке зависимостей. Для конкретного указанного действия допустим activityRef. Операция, boundary "
    "compensation и handler — один scope; handler isForCompensation=true связан associationDirection=One, "
    "без обычных sequenceFlow. Альтернатива при явно заданной transaction: cancel end запускает "
    "компенсацию автоматически. Название задачи «Отменить» или cancel end вне transaction этого не делает.",
    "event_race": "Первое внешнее событие с отменой остальных ожиданий — eventBasedGateway, не XOR по "
    "булевому признаку и не AND. Выходы ведут прямо к catch events (либо receiveTask там, где допустимо); "
    "sequenceFlow шлюза без conditionExpression. Названное сообщение сохраняется как message.name "
    "и messageRef на этот ID; пустого messageEventDefinition недостаточно. "
    "Сохраняй отдельные исходы сообщения и таймера.",
    "reusable_call": "Переиспользуемая процедура объявляется отдельным process один раз; каждый "
    "callActivity.calledElement ссылается на этот process. У вызываемого процесса свои start/end и роли.",
}


PARALLEL_SOURCE = (
    "В фотостудии осветитель настраивает два светильника: для каждого запускается отдельная процедура, "
    "оба экземпляра одновременно. Внутри осветитель проверяет кабель и измеряет яркость, без обязательного "
    "порядка; после обеих работ осветитель сохраняет настройку. Пока процедура конкретного светильника "
    "идёт, каждое сообщение «Поправка» запускает отдельную обработку: осветитель записывает поправку. "
    "Она не прерывает настройку, заканчивается отдельно и может повторяться. Когда оба светильника "
    "готовы, осветитель завершает подготовку студии."
)
PARALLEL_CODE = """bpmn.document(id="d", name="Настройка света")
bpmn.element(id="m", tag="message", parent="d", name="Поправка")
bpmn.element(id="p", tag="process", parent="d")
bpmn.element(id="ls", tag="laneSet", parent="p")
bpmn.element(id="role", tag="lane", parent="ls", name="Осветитель")
bpmn.element(id="r1", tag="flowNodeRef", parent="role", text="unit")
bpmn.element(id="r2", tag="flowNodeRef", parent="role", text="finish")
bpmn.element(id="s", tag="startEvent", parent="p")
bpmn.element(id="unit", tag="subProcess", parent="p", name="Настроить светильник")
bpmn.element(id="mi", tag="multiInstanceLoopCharacteristics", parent="unit", isSequential=False)
bpmn.element(id="n", tag="loopCardinality", parent="mi", type="tFormalExpression", text="2")
bpmn.element(id="us", tag="startEvent", parent="unit")
bpmn.element(id="split", tag="parallelGateway", parent="unit")
bpmn.element(id="cable", tag="userTask", parent="unit", name="Проверить кабель",
             source_quote="осветитель проверяет кабель")
bpmn.element(id="light", tag="userTask", parent="unit", name="Измерить яркость",
             source_quote="измеряет яркость")
bpmn.element(id="join", tag="parallelGateway", parent="unit")
bpmn.element(id="save", tag="userTask", parent="unit", name="Сохранить настройку",
             source_quote="осветитель сохраняет настройку")
bpmn.element(id="ue", tag="endEvent", parent="unit")
bpmn.element(id="u1", tag="sequenceFlow", parent="unit", sourceRef="us", targetRef="split")
bpmn.element(id="u2", tag="sequenceFlow", parent="unit", sourceRef="split", targetRef="cable")
bpmn.element(id="u3", tag="sequenceFlow", parent="unit", sourceRef="split", targetRef="light")
bpmn.element(id="u4", tag="sequenceFlow", parent="unit", sourceRef="cable", targetRef="join")
bpmn.element(id="u5", tag="sequenceFlow", parent="unit", sourceRef="light", targetRef="join")
bpmn.element(id="u6", tag="sequenceFlow", parent="unit", sourceRef="join", targetRef="save")
bpmn.element(id="u7", tag="sequenceFlow", parent="unit", sourceRef="save", targetRef="ue")
bpmn.element(id="handler", tag="subProcess", parent="unit", triggeredByEvent=True)
bpmn.element(id="hs", tag="startEvent", parent="handler", isInterrupting=False)
bpmn.element(id="trigger", tag="messageEventDefinition", parent="hs", messageRef="m")
bpmn.element(id="note", tag="userTask", parent="handler", name="Записать поправку",
             source_quote="осветитель записывает поправку")
bpmn.element(id="he", tag="endEvent", parent="handler")
bpmn.element(id="h1", tag="sequenceFlow", parent="handler", sourceRef="hs", targetRef="note")
bpmn.element(id="h2", tag="sequenceFlow", parent="handler", sourceRef="note", targetRef="he")
bpmn.element(id="finish", tag="userTask", parent="p", name="Завершить подготовку студии",
             source_quote="осветитель завершает подготовку студии")
bpmn.element(id="end", tag="endEvent", parent="p")
bpmn.element(id="f1", tag="sequenceFlow", parent="p", sourceRef="s", targetRef="unit")
bpmn.element(id="f2", tag="sequenceFlow", parent="p", sourceRef="unit", targetRef="finish")
bpmn.element(id="f3", tag="sequenceFlow", parent="p", sourceRef="finish", targetRef="end")"""

ESCALATION_SOURCE = (
    "Исследователь выполняет процедуру «Измерить образец»: измеряет температуру. Если нужна старшая "
    "экспертиза, из процедуры посылается повышение уровня проблемы «Нужна экспертиза»; иначе оно не "
    "посылается. Процедура продолжает работать: исследователь сохраняет измерения и завершает её. "
    "Повышение уровня проблемы не прерывает процедуру, но запускает снаружи реакцию: старший "
    "исследователь оценивает аномалию и завершает только реакцию. После процедуры процесс завершается."
)
ESCALATION_CODE = """bpmn.document(id="d", name="Измерение с экспертизой")
bpmn.element(id="escalation", tag="escalation", parent="d", name="Нужна экспертиза")
bpmn.element(id="p", tag="process", parent="d")
bpmn.element(id="ls", tag="laneSet", parent="p")
bpmn.element(id="researcher", tag="lane", parent="ls", name="Исследователь")
bpmn.element(id="expert", tag="lane", parent="ls", name="Старший исследователь")
bpmn.element(id="r1", tag="flowNodeRef", parent="researcher", text="sub")
bpmn.element(id="r2", tag="flowNodeRef", parent="expert", text="review")
bpmn.element(id="s", tag="startEvent", parent="p")
bpmn.element(id="sub", tag="subProcess", parent="p", name="Измерить образец")
bpmn.element(id="ss", tag="startEvent", parent="sub")
bpmn.element(id="measure", tag="userTask", parent="sub", name="Измерить температуру",
             source_quote="измеряет температуру")
bpmn.element(id="choice", tag="exclusiveGateway", parent="sub", default="skip")
bpmn.element(id="raise", tag="intermediateThrowEvent", parent="sub", name="Нужна экспертиза")
bpmn.element(id="raised", tag="escalationEventDefinition", parent="raise", escalationRef="escalation")
bpmn.element(id="save", tag="userTask", parent="sub", name="Сохранить измерения",
             source_quote="исследователь сохраняет измерения")
bpmn.element(id="se", tag="endEvent", parent="sub")
bpmn.element(id="a", tag="sequenceFlow", parent="sub", sourceRef="ss", targetRef="measure")
bpmn.element(id="b", tag="sequenceFlow", parent="sub", sourceRef="measure", targetRef="choice")
bpmn.element(id="send", tag="sequenceFlow", parent="sub", sourceRef="choice", targetRef="raise")
bpmn.element(id="condition", tag="conditionExpression", parent="send", type="tFormalExpression",
             text="Нужна старшая экспертиза")
bpmn.element(id="skip", tag="sequenceFlow", parent="sub", sourceRef="choice", targetRef="save",
             name="Экспертиза не нужна")
bpmn.element(id="c", tag="sequenceFlow", parent="sub", sourceRef="raise", targetRef="save")
bpmn.element(id="e", tag="sequenceFlow", parent="sub", sourceRef="save", targetRef="se")
bpmn.element(id="catch", tag="boundaryEvent", parent="p", attachedToRef="sub", cancelActivity=False)
bpmn.element(id="caught", tag="escalationEventDefinition", parent="catch", escalationRef="escalation")
bpmn.element(id="review", tag="userTask", parent="p", name="Оценить аномалию",
             source_quote="старший исследователь оценивает аномалию")
bpmn.element(id="reaction_end", tag="endEvent", parent="p")
bpmn.element(id="end", tag="endEvent", parent="p")
bpmn.element(id="f1", tag="sequenceFlow", parent="p", sourceRef="s", targetRef="sub")
bpmn.element(id="f2", tag="sequenceFlow", parent="p", sourceRef="sub", targetRef="end")
bpmn.element(id="f3", tag="sequenceFlow", parent="p", sourceRef="catch", targetRef="review")
bpmn.element(id="f4", tag="sequenceFlow", parent="p", sourceRef="review", targetRef="reaction_end")"""

COMPENSATE_SOURCE = (
    "Сервис сначала создаёт временный профиль, затем закрепляет за ним адрес. Обе операции успешны. "
    "Оператор принимает решение о сохранении. Если профиль нужен, процесс завершается без отмены. "
    "Если профиль не нужен, запустить компенсацию уже выполненных операций в обратном порядке: сервис "
    "освобождает адрес и удаляет временный профиль. Это обработчики закрепления и создания, не обычные "
    "задачи успешной ветки. Только после обеих компенсаций оператор отмечает отмену и процесс завершается."
)
COMPENSATE_CODE = """bpmn.document(id="d", name="Временный профиль")
bpmn.element(id="p", tag="process", parent="d")
bpmn.element(id="ls", tag="laneSet", parent="p")
bpmn.element(id="service", tag="lane", parent="ls", name="Сервис")
bpmn.element(id="operator", tag="lane", parent="ls", name="Оператор")
bpmn.element(id="r1", tag="flowNodeRef", parent="service", text="create")
bpmn.element(id="r2", tag="flowNodeRef", parent="service", text="bind")
bpmn.element(id="r3", tag="flowNodeRef", parent="service", text="remove")
bpmn.element(id="r4", tag="flowNodeRef", parent="service", text="release")
bpmn.element(id="r5", tag="flowNodeRef", parent="operator", text="decide")
bpmn.element(id="r6", tag="flowNodeRef", parent="operator", text="record")
bpmn.element(id="s", tag="startEvent", parent="p")
bpmn.element(id="create", tag="serviceTask", parent="p", name="Создать временный профиль",
             source_quote="Сервис сначала создаёт временный профиль")
bpmn.element(id="bind", tag="serviceTask", parent="p", name="Закрепить адрес",
             source_quote="закрепляет за ним адрес")
bpmn.element(id="undo_create", tag="boundaryEvent", parent="p", attachedToRef="create",
             cancelActivity=False)
bpmn.element(id="uc", tag="compensateEventDefinition", parent="undo_create")
bpmn.element(id="remove", tag="serviceTask", parent="p", name="Удалить временный профиль",
             isForCompensation=True, source_quote="удаляет временный профиль")
bpmn.element(id="link1", tag="association", parent="p", sourceRef="undo_create", targetRef="remove",
             associationDirection="One")
bpmn.element(id="undo_bind", tag="boundaryEvent", parent="p", attachedToRef="bind", cancelActivity=False)
bpmn.element(id="ub", tag="compensateEventDefinition", parent="undo_bind")
bpmn.element(id="release", tag="serviceTask", parent="p", name="Освободить адрес", isForCompensation=True,
             source_quote="сервис освобождает адрес")
bpmn.element(id="link2", tag="association", parent="p", sourceRef="undo_bind", targetRef="release",
             associationDirection="One")
bpmn.element(id="decide", tag="userTask", parent="p", name="Принять решение о сохранении",
             source_quote="Оператор принимает решение о сохранении")
bpmn.element(id="choice", tag="exclusiveGateway", parent="p", default="keep")
bpmn.element(id="compensate", tag="intermediateThrowEvent", parent="p", name="Компенсировать операции")
bpmn.element(id="trigger", tag="compensateEventDefinition", parent="compensate", waitForCompletion=True)
bpmn.element(id="record", tag="userTask", parent="p", name="Отметить отмену",
             source_quote="оператор отмечает отмену")
bpmn.element(id="end", tag="endEvent", parent="p")
bpmn.element(id="f1", tag="sequenceFlow", parent="p", sourceRef="s", targetRef="create")
bpmn.element(id="f2", tag="sequenceFlow", parent="p", sourceRef="create", targetRef="bind")
bpmn.element(id="f3", tag="sequenceFlow", parent="p", sourceRef="bind", targetRef="decide")
bpmn.element(id="f4", tag="sequenceFlow", parent="p", sourceRef="decide", targetRef="choice")
bpmn.element(id="keep", tag="sequenceFlow", parent="p", sourceRef="choice", targetRef="end",
             name="Профиль нужен")
bpmn.element(id="cancel", tag="sequenceFlow", parent="p", sourceRef="choice", targetRef="compensate")
bpmn.element(id="condition", tag="conditionExpression", parent="cancel", type="tFormalExpression",
             text="Профиль не нужен")
bpmn.element(id="f5", tag="sequenceFlow", parent="p", sourceRef="compensate", targetRef="record")
bpmn.element(id="f6", tag="sequenceFlow", parent="p", sourceRef="record", targetRef="end")"""

COMPOSITION_PATTERNS = (
    {
        "id": "parallel_scope_composition",
        "constructs": {"parallel_instances", "unordered_required", "repeatable_handler"},
        "description": PARALLEL_SOURCE,
        "python_code": PARALLEL_CODE,
        "invariants": [
            COMPOSITION_GUIDANCE[key]
            for key in ("parallel_instances", "unordered_required", "repeatable_handler")
        ],
    },
    {
        "id": "noninterrupting_escalation",
        "constructs": {"escalation"},
        "description": ESCALATION_SOURCE,
        "python_code": ESCALATION_CODE,
        "invariants": [COMPOSITION_GUIDANCE["escalation"]],
    },
    {
        "id": "triggered_compensation",
        "constructs": {"compensation"},
        "exclude_constructs": {"transaction"},
        "description": COMPENSATE_SOURCE,
        "python_code": COMPENSATE_CODE,
        "invariants": [COMPOSITION_GUIDANCE["compensation"]],
    },
)
