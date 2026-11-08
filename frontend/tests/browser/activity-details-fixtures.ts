// Saved synthetic diagnostic outputs (no new model calls for these UI tests).

// DB07-1 SHA256 867b1946aafbc79171d4efbf1b637a9978f5b1eb73b0bfb27b761e8a18cdaaef
export const resourceProcess = `<?xml version='1.0' encoding='utf-8'?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" id="definitions" name="Подготовка выставки" targetNamespace="urn:process-architect:document" exporter="Process Architect AI" exporterVersion="2">
  <bpmn:process id="proc" name="Подготовка выставки">
    <bpmn:laneSet id="lanes">
      <bpmn:lane id="curator" name="Куратор выставки">
        <bpmn:flowNodeRef>check_exhibit</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>prepare_hall</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="start"/>
    <bpmn:userTask id="check_exhibit" name="Проверить экспонат">
      <bpmn:multiInstanceLoopCharacteristics id="mi" isSequential="false">
        <bpmn:loopCardinality id="cardinality" xsi:type="bpmn:tFormalExpression">3</bpmn:loopCardinality>
      </bpmn:multiInstanceLoopCharacteristics>
    </bpmn:userTask>
    <bpmn:adHocSubProcess id="prepare_hall" name="Подготовить зал" ordering="Parallel" cancelRemainingInstances="false">
      <bpmn:laneSet id="inner_lanes">
        <bpmn:lane id="inner_curator" name="Куратор выставки">
          <bpmn:flowNodeRef>place_signs</bpmn:flowNodeRef>
          <bpmn:flowNodeRef>check_lighting</bpmn:flowNodeRef>
        </bpmn:lane>
      </bpmn:laneSet>
      <bpmn:manualTask id="place_signs" name="Разместить таблички"/>
      <bpmn:manualTask id="check_lighting" name="Проверить освещение"/>
      <bpmn:completionCondition id="completion" xsi:type="bpmn:tFormalExpression">Обе работы выполнены</bpmn:completionCondition>
    </bpmn:adHocSubProcess>
    <bpmn:endEvent id="end_main"/>
    <bpmn:sequenceFlow id="flow1" sourceRef="start" targetRef="check_exhibit"/>
    <bpmn:sequenceFlow id="flow2" sourceRef="check_exhibit" targetRef="prepare_hall"/>
    <bpmn:sequenceFlow id="flow3" sourceRef="prepare_hall" targetRef="end_main"/>
    <bpmn:subProcess id="event_sub" name="Обработка срочного изменения" triggeredByEvent="true">
      <bpmn:startEvent id="event_start" isInterrupting="false">
        <bpmn:messageEventDefinition id="msg_def" messageRef="msg_urgent"/>
      </bpmn:startEvent>
      <bpmn:serviceTask id="register_change" name="Зарегистрировать изменение">
        <bpmn:performer id="performer">
          <bpmn:resourceRef>service</bpmn:resourceRef>
        </bpmn:performer>
      </bpmn:serviceTask>
      <bpmn:endEvent id="event_end"/>
      <bpmn:sequenceFlow id="flow4" sourceRef="event_start" targetRef="register_change"/>
      <bpmn:sequenceFlow id="flow5" sourceRef="register_change" targetRef="event_end"/>
    </bpmn:subProcess>
  </bpmn:process>
  <bpmn:message id="msg_urgent" name="Срочное изменение"/>
  <bpmn:resource id="service" name="Сервис"/>
  <bpmndi:BPMNDiagram id="DI_Diagram_1">
    <bpmndi:BPMNPlane id="DI_Plane_2" bpmnElement="proc">
      <bpmndi:BPMNShape id="DI_Shape_3" bpmnElement="curator" isHorizontal="true">
        <dc:Bounds x="110" y="136" width="1214" height="416.0"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_4" bpmnElement="start">
        <dc:Bounds x="344.0" y="325.0" width="38" height="38"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_5" bpmnElement="check_exhibit">
        <dc:Bounds x="636.0" y="306.0" width="150" height="76"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_6" bpmnElement="prepare_hall" isExpanded="true">
        <dc:Bounds x="846.0" y="166.0" width="360" height="356.0"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_7" bpmnElement="inner_curator" isHorizontal="true">
        <dc:Bounds x="876.0" y="222.0" width="310" height="264.0"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_8" bpmnElement="place_signs">
        <dc:Bounds x="916.0" y="252.0" width="150" height="76"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_9" bpmnElement="check_lighting">
        <dc:Bounds x="916.0" y="380.0" width="150" height="76"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_10" bpmnElement="end_main">
        <dc:Bounds x="1266.0" y="325.0" width="38" height="38"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_11" bpmnElement="event_sub" isExpanded="true">
        <dc:Bounds x="150.0" y="582.0" width="426" height="232"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_12" bpmnElement="event_start">
        <dc:Bounds x="190.0" y="689.0" width="38" height="38"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_13" bpmnElement="register_change">
        <dc:Bounds x="288.0" y="670.0" width="150" height="76"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_14" bpmnElement="event_end">
        <dc:Bounds x="498.0" y="689.0" width="38" height="38"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="DI_Edge_15" bpmnElement="flow1" sourceElement="DI_Shape_4" targetElement="DI_Shape_5">
        <di:waypoint x="382.0" y="344.0"/>
        <di:waypoint x="636.0" y="344.0"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_16" bpmnElement="flow2" sourceElement="DI_Shape_5" targetElement="DI_Shape_6">
        <di:waypoint x="786.0" y="344.0"/>
        <di:waypoint x="846.0" y="344.0"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_17" bpmnElement="flow3" sourceElement="DI_Shape_6" targetElement="DI_Shape_10">
        <di:waypoint x="1206.0" y="344.0"/>
        <di:waypoint x="1266.0" y="344.0"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_18" bpmnElement="flow4" sourceElement="DI_Shape_12" targetElement="DI_Shape_13">
        <di:waypoint x="228.0" y="708.0"/>
        <di:waypoint x="288.0" y="708.0"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_19" bpmnElement="flow5" sourceElement="DI_Shape_13" targetElement="DI_Shape_14">
        <di:waypoint x="438.0" y="708.0"/>
        <di:waypoint x="498.0" y="708.0"/>
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
`;

// DB08-1 SHA256 cf1274efbac88007861a93d7a1d46c4de4bcc756aaf6572c96fb3c6a759c0162
export const dataProcess = `<?xml version='1.0' encoding='utf-8'?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" id="definitions" name="Проверка договора" targetNamespace="urn:process-architect:document" exporter="Process Architect AI" exporterVersion="2">
  <bpmn:itemDefinition id="string_type" structureRef="xsd:string"/>
  <bpmn:dataStore id="registry" name="Реестр договоров"/>
  <bpmn:resource id="lawyer" name="Юрист"/>
  <bpmn:process id="proc" name="Проверка договора">
    <bpmn:laneSet id="lanes">
      <bpmn:lane id="lawyer_lane" name="Юрист">
        <bpmn:flowNodeRef>check_contract</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>
    <bpmn:dataObject id="contract_obj" itemSubjectRef="string_type"/>
    <bpmn:dataObjectReference id="contract_ref" name="Договор.pdf" dataObjectRef="contract_obj">
      <bpmn:dataState id="state_draft" name="Черновик"/>
    </bpmn:dataObjectReference>
    <bpmn:dataStoreReference id="store_ref" name="Реестр договоров" dataStoreRef="registry"/>
    <bpmn:startEvent id="start"/>
    <bpmn:userTask id="check_contract" name="Проверить договор">
      <bpmn:ioSpecification id="io">
        <bpmn:dataInput id="input" name="Входной параметр" itemSubjectRef="string_type"/>
        <bpmn:dataOutput id="output" name="Выходной параметр" itemSubjectRef="string_type"/>
        <bpmn:inputSet id="inputs">
          <bpmn:dataInputRefs>input</bpmn:dataInputRefs>
        </bpmn:inputSet>
        <bpmn:outputSet id="outputs">
          <bpmn:dataOutputRefs>output</bpmn:dataOutputRefs>
        </bpmn:outputSet>
      </bpmn:ioSpecification>
      <bpmn:dataInputAssociation id="read">
        <bpmn:sourceRef>contract_ref</bpmn:sourceRef>
        <bpmn:targetRef>input</bpmn:targetRef>
      </bpmn:dataInputAssociation>
      <bpmn:dataOutputAssociation id="write">
        <bpmn:sourceRef>output</bpmn:sourceRef>
        <bpmn:targetRef>store_ref</bpmn:targetRef>
      </bpmn:dataOutputAssociation>
      <bpmn:potentialOwner id="owner">
        <bpmn:resourceRef>lawyer</bpmn:resourceRef>
      </bpmn:potentialOwner>
    </bpmn:userTask>
    <bpmn:endEvent id="end"/>
    <bpmn:sequenceFlow id="f1" sourceRef="start" targetRef="check_contract"/>
    <bpmn:sequenceFlow id="f2" sourceRef="check_contract" targetRef="end"/>
    <bpmn:textAnnotation id="note">
      <bpmn:text>Исходный файл не изменяется</bpmn:text>
    </bpmn:textAnnotation>
    <bpmn:association id="note_link" sourceRef="check_contract" targetRef="note"/>
  </bpmn:process>
  <bpmndi:BPMNDiagram id="DI_Diagram_1">
    <bpmndi:BPMNPlane id="DI_Plane_2" bpmnElement="proc">
      <bpmndi:BPMNShape id="DI_Shape_3" bpmnElement="lawyer_lane" isHorizontal="true">
        <dc:Bounds x="110" y="136" width="406" height="140"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_4" bpmnElement="start">
        <dc:Bounds x="150.0" y="187.0" width="38" height="38"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_5" bpmnElement="check_contract">
        <dc:Bounds x="248.0" y="168.0" width="150" height="76"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_6" bpmnElement="end">
        <dc:Bounds x="458.0" y="187.0" width="38" height="38"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_7" bpmnElement="contract_ref">
        <dc:Bounds x="150" y="320" width="50" height="66"/>
        <bpmndi:BPMNLabel>
          <dc:Bounds x="133.25" y="394" width="83.5" height="32"/>
        </bpmndi:BPMNLabel>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_8" bpmnElement="store_ref">
        <dc:Bounds x="380" y="320" width="66" height="70"/>
        <bpmndi:BPMNLabel>
          <dc:Bounds x="355.0" y="398" width="116.0" height="32"/>
        </bpmndi:BPMNLabel>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_9" bpmnElement="note">
        <dc:Bounds x="150" y="496" width="200" height="60"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_10" bpmnElement="input">
        <dc:Bounds x="410" y="496" width="50" height="66"/>
        <bpmndi:BPMNLabel>
          <dc:Bounds x="377.0" y="570" width="116.0" height="32"/>
        </bpmndi:BPMNLabel>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_11" bpmnElement="output">
        <dc:Bounds x="150" y="668" width="50" height="66"/>
        <bpmndi:BPMNLabel>
          <dc:Bounds x="113.75" y="742" width="122.5" height="32"/>
        </bpmndi:BPMNLabel>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="DI_Edge_12" bpmnElement="read" sourceElement="DI_Shape_7" targetElement="DI_Shape_5">
        <di:waypoint x="200" y="353.0"/>
        <di:waypoint x="200" y="206.0"/>
        <di:waypoint x="248.0" y="206.0"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_13" bpmnElement="write" sourceElement="DI_Shape_5" targetElement="DI_Shape_8">
        <di:waypoint x="323.0" y="244.0"/>
        <di:waypoint x="323.0" y="320"/>
        <di:waypoint x="413.0" y="320"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_14" bpmnElement="f1" sourceElement="DI_Shape_4" targetElement="DI_Shape_5">
        <di:waypoint x="188.0" y="206.0"/>
        <di:waypoint x="248.0" y="206.0"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_15" bpmnElement="f2" sourceElement="DI_Shape_5" targetElement="DI_Shape_6">
        <di:waypoint x="398.0" y="206.0"/>
        <di:waypoint x="458.0" y="206.0"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_16" bpmnElement="note_link" sourceElement="DI_Shape_5" targetElement="DI_Shape_9">
        <di:waypoint x="323.0" y="244.0"/>
        <di:waypoint x="323.0" y="496"/>
        <di:waypoint x="250.0" y="496"/>
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
`;
