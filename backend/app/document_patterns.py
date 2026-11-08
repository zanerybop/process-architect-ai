"""Executable notation examples, selected by construct; never response fallbacks.

The synthetic domains are separate from the evaluation corpus. The programs use
only the public literal API and are tested through interpreter, graph, XML and
source review. They are context for the provider, not runtime substitutes.
"""

import re

from backend.app.document_composition import COMPOSITION_PATTERNS, business_constructs

DATA_SOURCE = (
    "Лаборант обрабатывает файл «Замеры.csv» в состоянии «Исходный» и сохраняет результат "
    "в хранилище «Каталог измерений». У пользовательской задачи есть строковые вход и выход, "
    "ioSpecification, inputSet, outputSet и обе data associations. "
    "Ресурс «Лаборант» назначен через potentialOwner. "
    "Примечание к задаче: Исходные измерения сохраняются. После обработки процесс завершается."
)
DATA_CODE = """bpmn.document(id="d", name="Лабораторные измерения")
bpmn.element(id="string_type", tag="itemDefinition", parent="d", structureRef="xsd:string")
bpmn.element(id="catalog", tag="dataStore", parent="d", name="Каталог измерений")
bpmn.element(id="technician", tag="resource", parent="d", name="Лаборант")
bpmn.element(id="p", tag="process", parent="d")
bpmn.element(id="measurements", tag="dataObject", parent="p", itemSubjectRef="string_type")
bpmn.element(id="file", tag="dataObjectReference", parent="p", name="Замеры.csv",
             dataObjectRef="measurements")
bpmn.element(id="state", tag="dataState", parent="file", name="Исходный")
bpmn.element(id="store", tag="dataStoreReference", parent="p", name="Каталог измерений",
             dataStoreRef="catalog")
bpmn.element(id="start", tag="startEvent", parent="p")
bpmn.element(id="process_file", tag="userTask", parent="p", name="Обработать Замеры.csv",
             source_quote="Лаборант обрабатывает файл «Замеры.csv» в состоянии «Исходный» "
                          "и сохраняет результат в хранилище «Каталог измерений»")
bpmn.element(id="io", tag="ioSpecification", parent="process_file")
bpmn.element(id="input", tag="dataInput", parent="io", name="Измерения", itemSubjectRef="string_type")
bpmn.element(id="output", tag="dataOutput", parent="io", name="Результат", itemSubjectRef="string_type")
bpmn.element(id="inputs", tag="inputSet", parent="io")
bpmn.element(id="in_ref", tag="dataInputRefs", parent="inputs", text="input")
bpmn.element(id="outputs", tag="outputSet", parent="io")
bpmn.element(id="out_ref", tag="dataOutputRefs", parent="outputs", text="output")
bpmn.element(id="read", tag="dataInputAssociation", parent="process_file")
bpmn.element(id="read_source", tag="sourceRef", parent="read", text="file")
bpmn.element(id="read_target", tag="targetRef", parent="read", text="input")
bpmn.element(id="write", tag="dataOutputAssociation", parent="process_file")
bpmn.element(id="write_source", tag="sourceRef", parent="write", text="output")
bpmn.element(id="write_target", tag="targetRef", parent="write", text="store")
bpmn.element(id="owner", tag="potentialOwner", parent="process_file")
bpmn.element(id="owner_ref", tag="resourceRef", parent="owner", text="technician")
bpmn.element(id="note", tag="textAnnotation", parent="p")
bpmn.element(id="note_text", tag="text", parent="note", text="Исходные измерения сохраняются")
bpmn.element(id="note_link", tag="association", parent="p", sourceRef="process_file", targetRef="note")
bpmn.element(id="end", tag="endEvent", parent="p")
bpmn.element(id="f1", tag="sequenceFlow", parent="p", sourceRef="start", targetRef="process_file")
bpmn.element(id="f2", tag="sequenceFlow", parent="p", sourceRef="process_file", targetRef="end")"""

NESTED_SOURCE = (
    "Агроном проверяет четыре датчика параллельно: многоэкземплярная задача завершается после всех "
    "четырёх проверок. Затем в ad-hoc подпроцессе агроном заполняет журнал и настраивает полив, "
    "без заданного порядка; подпроцесс завершится после обеих работ, незавершённые работы не "
    "отменяются. Основной процесс завершается. Пока он работает, сообщение «Засуха» запускает "
    "непрерывающий событийный подпроцесс: сервис регистрирует предупреждение, затем завершает "
    "только этот событийный экземпляр."
)
NESTED_CODE = """bpmn.document(id="d", name="Контроль теплицы")
bpmn.element(id="drought", tag="message", parent="d", name="Засуха")
bpmn.element(id="service", tag="resource", parent="d", name="Сервис")
bpmn.element(id="p", tag="process", parent="d")
bpmn.element(id="roles", tag="laneSet", parent="p")
bpmn.element(id="agronomist", tag="lane", parent="roles", name="Агроном")
bpmn.element(id="role_check", tag="flowNodeRef", parent="agronomist", text="check")
bpmn.element(id="role_prepare", tag="flowNodeRef", parent="agronomist", text="prepare")
bpmn.element(id="start", tag="startEvent", parent="p")
bpmn.element(id="check", tag="userTask", parent="p", name="Проверить датчик",
             source_quote="Агроном проверяет четыре датчика параллельно")
bpmn.element(id="mi", tag="multiInstanceLoopCharacteristics", parent="check", isSequential=False)
bpmn.element(id="count", tag="loopCardinality", parent="mi", type="tFormalExpression", text="4")
bpmn.element(id="prepare", tag="adHocSubProcess", parent="p", name="Подготовить полив",
             ordering="Parallel", cancelRemainingInstances=False)
bpmn.element(id="done_prepare", tag="completionCondition", parent="prepare", type="tFormalExpression",
             text="Журнал заполнен и полив настроен")
bpmn.element(id="inner_roles", tag="laneSet", parent="prepare")
bpmn.element(id="inner_agronomist", tag="lane", parent="inner_roles", name="Агроном")
bpmn.element(id="role_log", tag="flowNodeRef", parent="inner_agronomist", text="log")
bpmn.element(id="role_water", tag="flowNodeRef", parent="inner_agronomist", text="water")
bpmn.element(id="log", tag="manualTask", parent="prepare", name="Заполнить журнал",
             source_quote="агроном заполняет журнал")
bpmn.element(id="water", tag="manualTask", parent="prepare", name="Настроить полив",
             source_quote="настраивает полив")
bpmn.element(id="urgent", tag="subProcess", parent="p", name="Обработка засухи", triggeredByEvent=True)
bpmn.element(id="event_start", tag="startEvent", parent="urgent", isInterrupting=False)
bpmn.element(id="message_trigger", tag="messageEventDefinition", parent="event_start", messageRef="drought")
bpmn.element(id="register", tag="serviceTask", parent="urgent", name="Зарегистрировать предупреждение",
             source_quote="сервис регистрирует предупреждение")
bpmn.element(id="assigned_service", tag="performer", parent="register")
bpmn.element(id="service_ref", tag="resourceRef", parent="assigned_service", text="service")
bpmn.element(id="event_end", tag="endEvent", parent="urgent")
bpmn.element(id="ef1", tag="sequenceFlow", parent="urgent", sourceRef="event_start", targetRef="register")
bpmn.element(id="ef2", tag="sequenceFlow", parent="urgent", sourceRef="register", targetRef="event_end")
bpmn.element(id="end", tag="endEvent", parent="p")
bpmn.element(id="f1", tag="sequenceFlow", parent="p", sourceRef="start", targetRef="check")
bpmn.element(id="f2", tag="sequenceFlow", parent="p", sourceRef="check", targetRef="prepare")
bpmn.element(id="f3", tag="sequenceFlow", parent="p", sourceRef="prepare", targetRef="end")"""

CONVERSATION_SOURCE = (
    "Издатель и Типография обсуждают выпуск книги. Общая тема «Тираж» — subConversation с "
    "двумя вложенными conversation: «Макет» и «Бумага», в обеих участвуют Издатель и Типография. "
    "Оба участника связаны conversationLink с общей темой. Отдельный callConversation "
    "«Согласовать тираж» между ними вызывает globalConversation «Типовая печать»; в нём свои "
    "два участника для этих же ролей, связанные с вызывающими через participantAssociation. "
    "Это только схема общения без задач, процессов и sequence flow."
)
CONVERSATION_CODE = """bpmn.document(id="d", name="Обсуждение тиража")
bpmn.element(id="template", tag="globalConversation", parent="d", name="Типовая печать")
bpmn.element(id="inner_publisher", tag="participant", parent="template", name="Издатель")
bpmn.element(id="inner_printer", tag="participant", parent="template", name="Типография")
bpmn.element(id="c", tag="collaboration", parent="d")
bpmn.element(id="publisher", tag="participant", parent="c", name="Издатель")
bpmn.element(id="printer", tag="participant", parent="c", name="Типография")
bpmn.element(id="edition", tag="subConversation", parent="c", name="Тираж")
bpmn.element(id="edition_pub", tag="participantRef", parent="edition", text="publisher")
bpmn.element(id="edition_print", tag="participantRef", parent="edition", text="printer")
bpmn.element(id="layout", tag="conversation", parent="edition", name="Макет")
bpmn.element(id="layout_pub", tag="participantRef", parent="layout", text="publisher")
bpmn.element(id="layout_print", tag="participantRef", parent="layout", text="printer")
bpmn.element(id="paper", tag="conversation", parent="edition", name="Бумага")
bpmn.element(id="paper_pub", tag="participantRef", parent="paper", text="publisher")
bpmn.element(id="paper_print", tag="participantRef", parent="paper", text="printer")
bpmn.element(id="link_pub", tag="conversationLink", parent="c", sourceRef="publisher", targetRef="edition")
bpmn.element(id="link_print", tag="conversationLink", parent="c", sourceRef="printer", targetRef="edition")
bpmn.element(id="call", tag="callConversation", parent="c", name="Согласовать тираж",
             calledCollaborationRef="template")
bpmn.element(id="call_pub", tag="participantRef", parent="call", text="publisher")
bpmn.element(id="call_print", tag="participantRef", parent="call", text="printer")
bpmn.element(id="map_pub", tag="participantAssociation", parent="call")
bpmn.element(id="map_pub_inner", tag="innerParticipantRef", parent="map_pub", text="inner_publisher")
bpmn.element(id="map_pub_outer", tag="outerParticipantRef", parent="map_pub", text="publisher")
bpmn.element(id="map_print", tag="participantAssociation", parent="call")
bpmn.element(id="map_print_inner", tag="innerParticipantRef", parent="map_print", text="inner_printer")
bpmn.element(id="map_print_outer", tag="outerParticipantRef", parent="map_print", text="printer")"""

PATTERNS = (
    {
        "id": "data_io_resources",
        "pattern": r"ioSpecification|inputOutputSpecification|inputSet|outputSet|data.?association|"
        r"dataInput|dataOutput|potentialOwner|объект\w*\s+данных|входн\w*\s+и\s+выходн",
        "description": DATA_SOURCE,
        "python_code": DATA_CODE,
        "invariants": [
            "XML-тег ioSpecification, тип tInputOutputSpecification: это разные имена.",
            "itemSubjectRef ссылается на itemDefinition; xsd:string — его structureRef.",
            "dataStore находится в definitions, dataStoreReference — в process.",
            "В обеих data associations sourceRef/targetRef — дочерние текстовые элементы.",
            "inputSet/outputSet включают dataInputRefs/dataOutputRefs; IO не удаляется при repair.",
            "potentialOwner/resourceRef явно назначает названного в описании исполнителя.",
        ],
    },
    {
        "id": "nested_roles_events_loops",
        "pattern": r"ad.?hoc|multi.?instance|многоэкземпляр|событийн\w*\s+подпроцесс|triggeredByEvent",
        "description": NESTED_SOURCE,
        "python_code": NESTED_CODE,
        "invariants": [
            "laneSet внутри prepare ссылается только на его внутренние задачи.",
            "Роль Сервис взята из текста и назначена через performer/resourceRef, это не допущение.",
            "MI без раннего completionCondition ожидает все экземпляры; число взято из описания.",
            "ad-hoc не получает выдуманный sequence flow; completionCondition хранит заданное условие.",
            "Событийный подпроцесс не имеет внешних sequence flow и завершает собственный экземпляр.",
        ],
    },
    {
        "id": "reusable_conversations",
        "pattern": r"conversation|разговор",
        "description": CONVERSATION_SOURCE,
        "python_code": CONVERSATION_CODE,
        "invariants": [
            "У глобального определения свои ID участников; это роли шаблона, а не новые организации.",
            "calledCollaborationRef ссылается на globalConversation в definitions.",
            "participantAssociation связывает innerParticipantRef шаблона и outerParticipantRef вызова.",
            "Явные темы и conversationLink из описания не являются assumptions.",
            "Conversation diagram не требует добавлять process, задачи и sequence flow.",
        ],
    },
) + COMPOSITION_PATTERNS


def select_patterns(text: str) -> list[dict]:
    """Static examples chosen by BPMN construct, never by a benchmark case ID."""
    constructs = business_constructs(text)
    selection_text = re.sub(
        r"\b(?:не|not)\s+(?:multi.?instance|ad.?hoc|многоэкземпляр\w*|triggeredByEvent)\b",
        "",
        text,
        flags=re.I,
    )
    return [
        {
            **{
                key: value
                for key, value in pattern.items()
                if key not in {"pattern", "constructs", "exclude_constructs"}
            },
            "purpose": (
                "Исполняемый пример только нужной конструкции. Для ответа используй текущий текст: "
                "не переноси действия, имена, роли, количество экземпляров и условия из примера. "
                "При полном описании assumptions=[]; недостающий бизнес-факт требует clarify."
            ),
        }
        for pattern in PATTERNS
        if (
            (
                re.search(pattern["pattern"], selection_text, re.I)
                if "pattern" in pattern
                else bool(pattern["constructs"] & constructs)
            )
            and not pattern.get("exclude_constructs", set()) & constructs
        )
    ]
