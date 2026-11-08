"""Materialize six curated standard-notation fixtures as literal document API calls.

The XML below is an authored fixture source, never a model response. Generation
of Python here is mechanical; bounded interpretation happens in the build script.
"""

import hashlib
import json
from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    {
        "id": "F01_nested_timer",
        "title": "Вложенная проверка заявления с непрерывающим таймером",
        "features": ["subProcess", "nested subProcess", "boundary timer", "non-interrupting event", "lanes"],
        "source": (
            "В ведомстве оператор принимает заявление. Затем выполняется раскрытый подпроцесс "
            "проверки: эксперт проверяет комплектность, а внутри вложенного подпроцесса "
            "оператор сверяет реквизиты и эксперт подтверждает результат. После проверки "
            "оператор закрывает заявление. К внешнему подпроцессу прикреплён непрерывающий "
            "граничный таймер PT15M: при срабатывании оператор отправляет напоминание, а "
            "проверка продолжает выполняться. Ветка напоминания завершается отдельно."
        ),
        "body": """
  <bpmn:process id="Process">
    <bpmn:laneSet id="Roles">
      <bpmn:lane id="Operator" name="Оператор">
        <bpmn:flowNodeRef>Start</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Receive</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Review</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Close</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>End</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Remind</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Reminded</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Deadline</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="Start"/>
    <bpmn:userTask id="Receive" name="Принять заявление"/>
    <bpmn:subProcess id="Review" name="Проверка заявления">
      <bpmn:startEvent id="ReviewStart"/>
      <bpmn:userTask id="Completeness" name="Проверить комплектность"/>
      <bpmn:subProcess id="Details" name="Сверка реквизитов">
        <bpmn:startEvent id="DetailsStart"/>
        <bpmn:userTask id="Compare" name="Сверить реквизиты"/>
        <bpmn:userTask id="Confirm" name="Подтвердить результат"/>
        <bpmn:endEvent id="DetailsEnd"/>
        <bpmn:sequenceFlow id="d1" sourceRef="DetailsStart" targetRef="Compare"/>
        <bpmn:sequenceFlow id="d2" sourceRef="Compare" targetRef="Confirm"/>
        <bpmn:sequenceFlow id="d3" sourceRef="Confirm" targetRef="DetailsEnd"/>
      </bpmn:subProcess>
      <bpmn:endEvent id="ReviewEnd"/>
      <bpmn:sequenceFlow id="r1" sourceRef="ReviewStart" targetRef="Completeness"/>
      <bpmn:sequenceFlow id="r2" sourceRef="Completeness" targetRef="Details"/>
      <bpmn:sequenceFlow id="r3" sourceRef="Details" targetRef="ReviewEnd"/>
    </bpmn:subProcess>
    <bpmn:boundaryEvent
        id="Deadline"
        name="15 минут"
        attachedToRef="Review"
        cancelActivity="false">
      <bpmn:timerEventDefinition id="Timer">
        <bpmn:timeDuration xsi:type="bpmn:tFormalExpression">PT15M</bpmn:timeDuration>
      </bpmn:timerEventDefinition>
    </bpmn:boundaryEvent>
    <bpmn:sendTask id="Remind" name="Отправить напоминание"/>
    <bpmn:endEvent id="Reminded"/>
    <bpmn:userTask id="Close" name="Закрыть заявление"/>
    <bpmn:endEvent id="End"/>
    <bpmn:sequenceFlow id="f1" sourceRef="Start" targetRef="Receive"/>
    <bpmn:sequenceFlow id="f2" sourceRef="Receive" targetRef="Review"/>
    <bpmn:sequenceFlow id="f3" sourceRef="Review" targetRef="Close"/>
    <bpmn:sequenceFlow id="f4" sourceRef="Close" targetRef="End"/>
    <bpmn:sequenceFlow id="f5" sourceRef="Deadline" targetRef="Remind"/>
    <bpmn:sequenceFlow id="f6" sourceRef="Remind" targetRef="Reminded"/>
  </bpmn:process>
  <bpmn:collaboration id="Collaboration">
    <bpmn:participant id="Office" name="Ведомство" processRef="Process"/>
  </bpmn:collaboration>
""",
    },
    {
        "id": "F02_transaction_compensation",
        "title": "Отмена транзакции и компенсация завершённого резервирования",
        "features": [
            "transaction",
            "cancel end event",
            "cancel boundary event",
            "compensation boundary",
            "compensation handler",
            "association",
        ],
        "source": (
            "Процесс бронирования начинается транзакцией. Внутри транзакции сервис резервирует "
            "место, затем оператор выбирает подтверждение или отмену. При подтверждении "
            "транзакция завершается нормально, после чего сервис подтверждает бронирование. При"
            " отмене внутри транзакции срабатывает конечное событие Cancel. Для уже "
            "завершённого резервирования задан обработчик компенсации «Снять резерв»: он связан"
            " направленной association с граничным событием Compensation на задаче "
            "резервирования и не включён в обычный sequence flow. На границе транзакции Cancel "
            "перехватывается; после завершения компенсации сервис сообщает об отмене и процесс "
            "завершается. Требуются именно BPMN transaction, cancel и compensation, без подмены"
            " обычными задачами."
        ),
        "body": """
  <bpmn:process id="Process">
    <bpmn:startEvent id="Start"/>
    <bpmn:transaction id="Booking" name="Транзакция бронирования">
      <bpmn:startEvent id="TxStart"/>
      <bpmn:serviceTask id="Reserve" name="Зарезервировать место"/>
      <bpmn:boundaryEvent id="Compensation" attachedToRef="Reserve" cancelActivity="false">
        <bpmn:compensateEventDefinition id="CompensationDefinition"/>
      </bpmn:boundaryEvent>
      <bpmn:serviceTask id="UndoReserve" name="Снять резерв" isForCompensation="true"/>
      <bpmn:association
        id="CompensationHandler"
        sourceRef="Compensation"
        targetRef="UndoReserve"
        associationDirection="One"/>
      <bpmn:userTask id="Decide" name="Выбрать подтверждение или отмену"/>
      <bpmn:exclusiveGateway id="Decision" name="Бронирование подтверждено?"/>
      <bpmn:endEvent id="TxComplete"/>
      <bpmn:endEvent id="TxCancel" name="Отмена">
        <bpmn:cancelEventDefinition id="CancelDefinition"/>
      </bpmn:endEvent>
      <bpmn:sequenceFlow id="t1" sourceRef="TxStart" targetRef="Reserve"/>
      <bpmn:sequenceFlow id="t2" sourceRef="Reserve" targetRef="Decide"/>
      <bpmn:sequenceFlow id="t3" sourceRef="Decide" targetRef="Decision"/>
      <bpmn:sequenceFlow
        id="t4"
        sourceRef="Decision"
        targetRef="TxComplete"
        name="Подтверждение"/>
      <bpmn:sequenceFlow id="t5" sourceRef="Decision" targetRef="TxCancel" name="Отмена"/>
    </bpmn:transaction>
    <bpmn:boundaryEvent id="Cancelled" attachedToRef="Booking">
      <bpmn:cancelEventDefinition id="CancelBoundaryDefinition"/>
    </bpmn:boundaryEvent>
    <bpmn:serviceTask id="Confirm" name="Подтвердить бронирование"/>
    <bpmn:serviceTask id="NotifyCancellation" name="Сообщить об отмене"/>
    <bpmn:endEvent id="Success"/>
    <bpmn:endEvent id="Failure"/>
    <bpmn:sequenceFlow id="f1" sourceRef="Start" targetRef="Booking"/>
    <bpmn:sequenceFlow id="f2" sourceRef="Booking" targetRef="Confirm"/>
    <bpmn:sequenceFlow id="f3" sourceRef="Confirm" targetRef="Success"/>
    <bpmn:sequenceFlow id="f4" sourceRef="Cancelled" targetRef="NotifyCancellation"/>
    <bpmn:sequenceFlow id="f5" sourceRef="NotifyCancellation" targetRef="Failure"/>
  </bpmn:process>
""",
    },
    {
        "id": "F03_multiinstance_adhoc_events",
        "title": "Пакетная проверка, ad-hoc подготовка и событийный подпроцесс",
        "features": [
            "parallel multiInstance",
            "loopCardinality",
            "completionCondition",
            "adHocSubProcess",
            "event subprocess",
            "message start",
        ],
        "source": (
            "Архив запускает параллельную многоэкземплярную проверку трёх карточек: ровно три "
            "экземпляра одной пользовательской задачи, ожидание завершения всех. Затем в ad-hoc"
            " подпроцессе параллельно и без заданной последовательности составляют памятку и "
            "перечень документов. Подпроцесс завершается, когда готовы и памятка, и перечень; "
            "оставшиеся экземпляры не отменяются. Основной процесс после этого завершается. "
            "Пока процесс работает, сообщение «Срочное уведомление» запускает непрерывающий "
            "событийный подпроцесс: сервис регистрирует уведомление и завершает только этот "
            "событийный экземпляр."
        ),
        "body": """
  <bpmn:message id="UrgentMessage" name="Срочное уведомление"/>
  <bpmn:process id="Process">
    <bpmn:startEvent id="Start"/>
    <bpmn:userTask id="CheckCards" name="Проверить карточку">
      <bpmn:multiInstanceLoopCharacteristics id="Multi" isSequential="false">
        <bpmn:loopCardinality xsi:type="bpmn:tFormalExpression">3</bpmn:loopCardinality>
        <bpmn:completionCondition
        xsi:type="bpmn:tFormalExpression">nrOfCompletedInstances = nrOfInstances</bpmn:completionCondition>
      </bpmn:multiInstanceLoopCharacteristics>
    </bpmn:userTask>
    <bpmn:adHocSubProcess
        id="Prepare"
        name="Подготовить комплект"
        ordering="Parallel"
        cancelRemainingInstances="false">
      <bpmn:manualTask id="Memo" name="Составить памятку"/>
      <bpmn:manualTask id="Checklist" name="Составить перечень документов"/>
      <bpmn:completionCondition
        xsi:type="bpmn:tFormalExpression">memoReady and checklistReady</bpmn:completionCondition>
    </bpmn:adHocSubProcess>
    <bpmn:subProcess id="Urgent" name="Обработка срочного уведомления" triggeredByEvent="true">
      <bpmn:startEvent id="UrgentStart" isInterrupting="false">
        <bpmn:messageEventDefinition id="UrgentDefinition" messageRef="UrgentMessage"/>
      </bpmn:startEvent>
      <bpmn:serviceTask id="LogUrgent" name="Зарегистрировать уведомление"/>
      <bpmn:endEvent id="UrgentEnd"/>
      <bpmn:sequenceFlow id="u1" sourceRef="UrgentStart" targetRef="LogUrgent"/>
      <bpmn:sequenceFlow id="u2" sourceRef="LogUrgent" targetRef="UrgentEnd"/>
    </bpmn:subProcess>
    <bpmn:endEvent id="End"/>
    <bpmn:sequenceFlow id="f1" sourceRef="Start" targetRef="CheckCards"/>
    <bpmn:sequenceFlow id="f2" sourceRef="CheckCards" targetRef="Prepare"/>
    <bpmn:sequenceFlow id="f3" sourceRef="Prepare" targetRef="End"/>
  </bpmn:process>
""",
    },
    {
        "id": "F04_data_io_resources",
        "title": "Входы и выходы архивирования с ресурсом исполнителя",
        "features": [
            "itemDefinition",
            "dataObjectReference",
            "dataStoreReference",
            "ioSpecification",
            "data associations",
            "potentialOwner",
            "resource",
            "annotation",
            "group",
        ],
        "source": (
            "Архивариус получает объект данных «Рукопись.txt» в состоянии «Черновик», проверяет"
            " файл и сохраняет результат в хранилище «Архив рукописей». Пользовательская задача"
            " имеет явно заданные входной и выходной параметры типа строка, inputSet и "
            "outputSet; вход и выход связаны data associations с объектом и хранилищем. Ресурс "
            "исполнителя — «Архивариус», указан как potentialOwner. Задача дополнена текстовым "
            "пояснением «Оригинал не удаляется» и группой «Архивирование». Процесс: начало, "
            "проверка и сохранение, завершение. Data associations не заменять потоками "
            "управления."
        ),
        "body": """
  <bpmn:itemDefinition id="TextItem" structureRef="xsd:string"/>
  <bpmn:dataStore id="ArchiveStore" name="Архив рукописей"/>
  <bpmn:resource id="Archivist" name="Архивариус"/>
  <bpmn:category id="ArchiveCategory">
    <bpmn:categoryValue id="ArchiveValue" value="Архивирование"/>
  </bpmn:category>
  <bpmn:process id="Process">
    <bpmn:startEvent id="Start"/>
    <bpmn:userTask id="Archive" name="Проверить файл и сохранить результат">
      <bpmn:ioSpecification id="IO">
        <bpmn:dataInput id="InputFile" name="Файл" itemSubjectRef="TextItem"/>
        <bpmn:dataOutput id="OutputFile" name="Результат" itemSubjectRef="TextItem"/>
        <bpmn:inputSet id="InputSet">
          <bpmn:dataInputRefs>InputFile</bpmn:dataInputRefs>
        </bpmn:inputSet>
        <bpmn:outputSet id="OutputSet">
          <bpmn:dataOutputRefs>OutputFile</bpmn:dataOutputRefs>
        </bpmn:outputSet>
      </bpmn:ioSpecification>
      <bpmn:dataInputAssociation id="ReadFile">
        <bpmn:sourceRef>Manuscript</bpmn:sourceRef>
        <bpmn:targetRef>InputFile</bpmn:targetRef>
      </bpmn:dataInputAssociation>
      <bpmn:dataOutputAssociation id="WriteFile">
        <bpmn:sourceRef>OutputFile</bpmn:sourceRef>
        <bpmn:targetRef>ArchiveReference</bpmn:targetRef>
      </bpmn:dataOutputAssociation>
      <bpmn:potentialOwner id="Owner">
        <bpmn:resourceRef>Archivist</bpmn:resourceRef>
      </bpmn:potentialOwner>
    </bpmn:userTask>
    <bpmn:endEvent id="End"/>
    <bpmn:sequenceFlow id="f1" sourceRef="Start" targetRef="Archive"/>
    <bpmn:sequenceFlow id="f2" sourceRef="Archive" targetRef="End"/>
    <bpmn:dataObject id="FileObject" itemSubjectRef="TextItem"/>
    <bpmn:dataObjectReference id="Manuscript" name="Рукопись.txt" dataObjectRef="FileObject">
      <bpmn:dataState id="DraftState" name="Черновик"/>
    </bpmn:dataObjectReference>
    <bpmn:dataStoreReference
        id="ArchiveReference"
        name="Архив рукописей"
        dataStoreRef="ArchiveStore"/>
    <bpmn:textAnnotation id="Annotation">
      <bpmn:text>Оригинал не удаляется</bpmn:text>
    </bpmn:textAnnotation>
    <bpmn:association id="Comment" sourceRef="Archive" targetRef="Annotation"/>
    <bpmn:group id="ArchiveGroup" categoryValueRef="ArchiveValue"/>
  </bpmn:process>
""",
    },
    {
        "id": "F05_choreography",
        "title": "Хореография заказа и передачи перевозчику",
        "features": [
            "choreography",
            "choreographyTask",
            "subChoreography",
            "participant bands",
            "initiating participant",
            "messageFlowRef",
        ],
        "source": (
            "Покажите именно BPMN-хореографию трёх участников: покупатель, поставщик и "
            "перевозчик. Сначала покупатель инициирует хореографическую задачу «Передать "
            "заказ», отправляя поставщику сообщение «Заказ». Затем начинается раскрытая "
            "субхореография «Передать груз перевозчику»: поставщик инициирует задачу «Передать "
            "накладную», отправляя перевозчику сообщение «Накладная». После этой задачи "
            "субхореография и основной маршрут завершаются. Для каждой хореографической задачи "
            "покажите полосы участников и инициатора; не заменяйте хореографию дорожками "
            "обычного процесса."
        ),
        "body": """
  <bpmn:message id="OrderMessage" name="Заказ"/>
  <bpmn:message id="WaybillMessage" name="Накладная"/>
  <bpmn:choreography id="Choreography" name="Заказ и перевозка">
    <bpmn:participant id="Buyer" name="Покупатель"/>
    <bpmn:participant id="Supplier" name="Поставщик"/>
    <bpmn:participant id="Carrier" name="Перевозчик"/>
    <bpmn:messageFlow
        id="OrderFlow"
        sourceRef="Buyer"
        targetRef="Supplier"
        messageRef="OrderMessage"/>
    <bpmn:messageFlow
        id="WaybillFlow"
        sourceRef="Supplier"
        targetRef="Carrier"
        messageRef="WaybillMessage"/>
    <bpmn:startEvent id="Start"/>
    <bpmn:choreographyTask id="Order" name="Передать заказ" initiatingParticipantRef="Buyer">
      <bpmn:participantRef>Buyer</bpmn:participantRef>
      <bpmn:participantRef>Supplier</bpmn:participantRef>
      <bpmn:messageFlowRef>OrderFlow</bpmn:messageFlowRef>
    </bpmn:choreographyTask>
    <bpmn:subChoreography
        id="Delivery"
        name="Передать груз перевозчику"
        initiatingParticipantRef="Supplier">
      <bpmn:participantRef>Supplier</bpmn:participantRef>
      <bpmn:participantRef>Carrier</bpmn:participantRef>
      <bpmn:startEvent id="DeliveryStart"/>
      <bpmn:choreographyTask
        id="Waybill"
        name="Передать накладную"
        initiatingParticipantRef="Supplier">
        <bpmn:participantRef>Supplier</bpmn:participantRef>
        <bpmn:participantRef>Carrier</bpmn:participantRef>
        <bpmn:messageFlowRef>WaybillFlow</bpmn:messageFlowRef>
      </bpmn:choreographyTask>
      <bpmn:endEvent id="DeliveryEnd"/>
      <bpmn:sequenceFlow id="d1" sourceRef="DeliveryStart" targetRef="Waybill"/>
      <bpmn:sequenceFlow id="d2" sourceRef="Waybill" targetRef="DeliveryEnd"/>
    </bpmn:subChoreography>
    <bpmn:endEvent id="End"/>
    <bpmn:sequenceFlow id="f1" sourceRef="Start" targetRef="Order"/>
    <bpmn:sequenceFlow id="f2" sourceRef="Order" targetRef="Delivery"/>
    <bpmn:sequenceFlow id="f3" sourceRef="Delivery" targetRef="End"/>
  </bpmn:choreography>
""",
    },
    {
        "id": "F06_conversations",
        "title": "Разговоры участников и вложенная тема",
        "features": [
            "conversation",
            "subConversation",
            "conversationLink",
            "callConversation",
            "globalConversation",
            "multiple diagrams",
        ],
        "source": (
            "Покажите BPMN conversation diagram для заказчика, склада и перевозчика. Общая "
            "раскрытая тема «Отгрузка» содержит два разговора: «Комплектация» между заказчиком "
            "и складом, и «Доставка» между складом и перевозчиком. Сообщение по комплектации "
            "идёт от заказчика складу, сообщение по доставке — от склада перевозчику. Свяжите "
            "участников с общей темой conversation links. Отдельный callConversation "
            "«Согласовать условия» между заказчиком и перевозчиком ссылается на "
            "globalConversation «Типовое согласование» с двумя собственными участниками. Все "
            "темы должны оставаться conversation/subConversation/callConversation, а не "
            "превращаться в задачи процесса."
        ),
        "body": """
  <bpmn:globalConversation id="StandardAgreement" name="Типовое согласование">
    <bpmn:participant id="AgreementCustomer" name="Инициатор условий"/>
    <bpmn:participant id="AgreementCarrier" name="Получатель условий"/>
  </bpmn:globalConversation>
  <bpmn:collaboration id="Collaboration" name="Разговоры об отгрузке">
    <bpmn:participant id="Customer" name="Заказчик"/>
    <bpmn:participant id="Warehouse" name="Склад"/>
    <bpmn:participant id="Carrier" name="Перевозчик"/>
    <bpmn:messageFlow
        id="PickingMessage"
        sourceRef="Customer"
        targetRef="Warehouse"
        name="Комплектация"/>
    <bpmn:messageFlow
        id="DeliveryMessage"
        sourceRef="Warehouse"
        targetRef="Carrier"
        name="Доставка"/>
    <bpmn:subConversation id="Shipment" name="Отгрузка">
      <bpmn:participantRef>Customer</bpmn:participantRef>
      <bpmn:participantRef>Warehouse</bpmn:participantRef>
      <bpmn:participantRef>Carrier</bpmn:participantRef>
      <bpmn:conversation id="Picking" name="Комплектация">
        <bpmn:participantRef>Customer</bpmn:participantRef>
        <bpmn:participantRef>Warehouse</bpmn:participantRef>
        <bpmn:messageFlowRef>PickingMessage</bpmn:messageFlowRef>
      </bpmn:conversation>
      <bpmn:conversation id="Delivery" name="Доставка">
        <bpmn:participantRef>Warehouse</bpmn:participantRef>
        <bpmn:participantRef>Carrier</bpmn:participantRef>
        <bpmn:messageFlowRef>DeliveryMessage</bpmn:messageFlowRef>
      </bpmn:conversation>
    </bpmn:subConversation>
    <bpmn:callConversation
        id="Agreement"
        name="Согласовать условия"
        calledCollaborationRef="StandardAgreement">
      <bpmn:participantRef>Customer</bpmn:participantRef>
      <bpmn:participantRef>Carrier</bpmn:participantRef>
    </bpmn:callConversation>
    <bpmn:conversationLink id="c1" sourceRef="Customer" targetRef="Shipment"/>
    <bpmn:conversationLink id="c2" sourceRef="Warehouse" targetRef="Shipment"/>
    <bpmn:conversationLink id="c3" sourceRef="Carrier" targetRef="Shipment"/>
    <bpmn:conversationLink id="c4" sourceRef="Customer" targetRef="Agreement"/>
    <bpmn:conversationLink id="c5" sourceRef="Carrier" targetRef="Agreement"/>
  </bpmn:collaboration>
""",
    },
]

SOURCE_OVERRIDES = {
    "F03_multiinstance_adhoc_events": (
        "В архиве эксперт выполняет параллельную многоэкземплярную проверку трёх карточек: "
        "ровно три экземпляра одной пользовательской задачи, ожидание завершения всех. "
        "Затем архивариус в ad-hoc подпроцессе параллельно и без заданной последовательности "
        "составляет памятку и составляет перечень документов. "
        "Подпроцесс завершается, когда готовы и памятка, и перечень; "
        "оставшиеся экземпляры не отменяются. Основной процесс после этого завершается. "
        "Пока процесс работает, сообщение «Срочное уведомление» запускает непрерывающий "
        "событийный подпроцесс: сервис регистрирует уведомление и завершает только "
        "этот событийный экземпляр."
    ),
}

CITATIONS = {
    "F01_nested_timer": {
        "Receive": "оператор принимает заявление",
        "Completeness": "эксперт проверяет комплектность",
        "Compare": "оператор сверяет реквизиты",
        "Confirm": "эксперт подтверждает результат",
        "Close": "оператор закрывает заявление",
        "Remind": "оператор отправляет напоминание",
        "Review": "выполняется раскрытый подпроцесс проверки",
        "Deadline": "непрерывающий граничный таймер PT15M",
    },
    "F02_transaction_compensation": {
        "Reserve": "сервис резервирует место",
        "Decide": "оператор выбирает подтверждение или отмену",
        "UndoReserve": "Для уже завершённого резервирования задан обработчик компенсации «Снять резерв»",
        "Confirm": "сервис подтверждает бронирование",
        "NotifyCancellation": "сервис сообщает об отмене",
        "t4": "При подтверждении транзакция завершается нормально",
        "t5": "При отмене внутри транзакции срабатывает конечное событие Cancel",
    },
    "F03_multiinstance_adhoc_events": {
        "CheckCards": "эксперт выполняет параллельную многоэкземплярную проверку трёх карточек",
        "Memo": "составляет памятку",
        "Checklist": "составляет перечень документов",
        "LogUrgent": "сервис регистрирует уведомление",
        "Prepare": "архивариус в ad-hoc подпроцессе",
        "UrgentStart": "сообщение «Срочное уведомление» запускает непрерывающий событийный подпроцесс",
    },
    "F04_data_io_resources": {
        "Archive": (
            "Архивариус получает объект данных «Рукопись.txt» в состоянии «Черновик», проверяет"
            " файл и сохраняет результат в хранилище «Архив рукописей»"
        ),
        "Manuscript": "объект данных «Рукопись.txt» в состоянии «Черновик»",
        "ArchiveReference": "в хранилище «Архив рукописей»",
        "Owner": "Ресурс исполнителя — «Архивариус», указан как potentialOwner",
    },
    "F05_choreography": {
        "Order": (
            "покупатель инициирует хореографическую задачу «Передать заказ», отправляя "
            "поставщику сообщение «Заказ»"
        ),
        "Waybill": (
            "поставщик инициирует задачу «Передать накладную», отправляя перевозчику сообщение «Накладная»"
        ),
        "Delivery": "раскрытая субхореография «Передать груз перевозчику»",
    },
    "F06_conversations": {
        "Shipment": "Общая раскрытая тема «Отгрузка» содержит два разговора",
        "Picking": "«Комплектация» между заказчиком и складом",
        "Delivery": "«Доставка» между складом и перевозчиком",
        "Agreement": "callConversation «Согласовать условия» между заказчиком и перевозчиком",
    },
}
ROLE_SCOPES = {
    "F01_nested_timer": {
        "Process": [
            ("Оператор", ["Start", "Receive", "Close", "End", "Remind", "Reminded"]),
            ("Эксперт", ["Review", "Deadline"]),
        ],
        "Review": [("Эксперт", ["ReviewStart", "Completeness", "Details", "ReviewEnd"])],
        "Details": [("Оператор", ["DetailsStart", "Compare"]), ("Эксперт", ["Confirm", "DetailsEnd"])],
    },
    "F02_transaction_compensation": {
        "Process": [
            (
                "Сервис бронирования",
                ["Start", "Booking", "Cancelled", "Confirm", "NotifyCancellation", "Success", "Failure"],
            )
        ],
        "Booking": [
            (
                "Сервис бронирования",
                ["TxStart", "Reserve", "Compensation", "UndoReserve", "TxComplete", "TxCancel"],
            ),
            ("Оператор", ["Decide", "Decision"]),
        ],
    },
    "F03_multiinstance_adhoc_events": {
        "Process": [
            ("Эксперт", ["Start", "CheckCards"]),
            ("Архивариус", ["Prepare", "End"]),
            ("Сервис уведомлений", ["Urgent"]),
        ],
        "Prepare": [("Архивариус", ["Memo", "Checklist"])],
        "Urgent": [("Сервис уведомлений", ["UrgentStart", "LogUrgent", "UrgentEnd"])],
    },
    "F04_data_io_resources": {"Process": [("Архивариус", ["Start", "Archive", "End"])]},
}


def main():
    namespace = 'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
    namespace += 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    namespace += 'xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
    for case in CASES:
        case["source"] = SOURCE_OVERRIDES.get(case["id"], case["source"])
        root = etree.fromstring(f"<bpmn:definitions {namespace}>{case['body']}</bpmn:definitions>".encode())
        bpmn = "{http://www.omg.org/spec/BPMN/20100524/MODEL}"
        for scope_id, roles in ROLE_SCOPES.get(case["id"], {}).items():
            scope = root.xpath("//*[@id=$value]", value=scope_id)[0]
            for old in scope.findall(bpmn + "laneSet"):
                scope.remove(old)
            lane_set = etree.Element(bpmn + "laneSet", id=scope_id + "_Roles")
            scope.insert(0, lane_set)
            for index, (role_name, node_ids) in enumerate(roles):
                lane = etree.SubElement(
                    lane_set, bpmn + "lane", id=f"{scope_id}_Role_{index}", name=role_name
                )
                for node_id in node_ids:
                    etree.SubElement(lane, bpmn + "flowNodeRef").text = node_id
        if case["id"] == "F04_data_io_resources":
            root.xpath('//*[@id="Archive"]')[0].set(
                "name", "Проверить Рукопись.txt и сохранить в Архив рукописей"
            )
        lines = [
            "# Authored fixture: no LLM response or claim of live generation.",
            f'bpmn.document(id="Definitions", name={json.dumps(case["title"], ensure_ascii=False)})',
        ]
        counter = 0

        def walk(element, parent):
            nonlocal counter
            counter += 1
            key = element.get("id") or f"Field_{counter}"
            arguments = {"id": key, "tag": "bpmn:" + etree.QName(element).localname, "parent": parent}
            if element.get("name") is not None:
                arguments["name"] = element.get("name")
            if element.text and element.text.strip():
                arguments["text"] = element.text.strip()
            actual_type = element.get("{http://www.w3.org/2001/XMLSchema-instance}type")
            if actual_type:
                arguments["type"] = actual_type
            quote = CITATIONS.get(case["id"], {}).get(key)
            if quote:
                assert quote in case["source"], (case["id"], key, quote)
                arguments["source_quote"] = quote
            lines.append(
                "bpmn.element("
                + ", ".join(
                    f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in arguments.items()
                )
                + ")"
            )
            for name, value in element.attrib.items():
                if name in {"id", "name", "{http://www.w3.org/2001/XMLSchema-instance}type"}:
                    continue
                lines.append(
                    "bpmn.attribute("
                    + ", ".join(
                        f"{key}={json.dumps(value, ensure_ascii=False)}"
                        for key, value in {"element": key, "name": name, "value": value}.items()
                    )
                    + ")"
                )
            for child in element:
                walk(child, key)

        for child in root:
            walk(child, "Definitions")
        directory = ROOT / "examples/full_bpmn" / case["id"]
        directory.mkdir(parents=True, exist_ok=True)
        code = "\n".join(lines) + "\n"
        (directory / "process.py").write_text(code, encoding="utf-8")
        (directory / "source.txt").write_text(case["source"], encoding="utf-8")
        manifest = {key: value for key, value in case.items() if key not in {"body", "source"}}
        manifest.update(
            {
                "origin": "curated_agent_authored_fixture",
                "model_requests": 0,
                "expected_status": "ready",
                "expected_behavior": "inconclusive",
                "source_sha256": hashlib.sha256(case["source"].encode()).hexdigest(),
                "python_sha256": hashlib.sha256(code.encode()).hexdigest(),
                "scope": "Deterministic standard notation support; not evidence of LLM quality.",
            }
        )
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(case["id"])


if __name__ == "__main__":
    main()
