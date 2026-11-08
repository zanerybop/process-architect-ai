// Saved live CH09 v7 XML; portable read-only UI regression fixture.
// Source SHA256: 03a58f36054c9fd1dbb01f6595e2b5f1fce1c3b8d15336925d52c5cfbaa50896
export const sensorRace = String.raw`<?xml version='1.0' encoding='utf-8'?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema" id="definitions" name="Промывка линии" targetNamespace="urn:process-architect:document" exporter="Process Architect AI" exporterVersion="2">
  <bpmn:process id="proc" name="Промывка линии" isExecutable="true">
    <bpmn:laneSet id="lanes">
      <bpmn:lane id="lane_auto" name="Автоматика">
        <bpmn:flowNodeRef>wash</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>log_success</bpmn:flowNodeRef>
      </bpmn:lane>
      <bpmn:lane id="lane_tech" name="Техник">
        <bpmn:flowNodeRef>inspect</bpmn:flowNodeRef>
      </bpmn:lane>
    </bpmn:laneSet>
    <bpmn:startEvent id="start" name="Начало промывки"/>
    <bpmn:serviceTask id="wash" name="Запустить промывку линии"/>
    <bpmn:eventBasedGateway id="ebg" name="Ожидание события"/>
    <bpmn:intermediateCatchEvent id="catch_msg" name="Сообщение датчика">
      <bpmn:messageEventDefinition id="def_msg" messageRef="msg_water"/>
    </bpmn:intermediateCatchEvent>
    <bpmn:intermediateCatchEvent id="catch_timer" name="Таймер 30 минут">
      <bpmn:timerEventDefinition id="def_timer">
        <bpmn:timeDuration id="dur" xsi:type="bpmn:tFormalExpression">PT30M</bpmn:timeDuration>
      </bpmn:timerEventDefinition>
    </bpmn:intermediateCatchEvent>
    <bpmn:serviceTask id="log_success" name="Записать успешную промывку в журнал"/>
    <bpmn:userTask id="inspect" name="Осмотреть фильтр"/>
    <bpmn:endEvent id="end_success" name="Конец (успех)"/>
    <bpmn:endEvent id="end_timeout" name="Конец (таймаут)"/>
    <bpmn:sequenceFlow id="f1" sourceRef="start" targetRef="wash"/>
    <bpmn:sequenceFlow id="f2" sourceRef="wash" targetRef="ebg"/>
    <bpmn:sequenceFlow id="f3" sourceRef="ebg" targetRef="catch_msg"/>
    <bpmn:sequenceFlow id="f4" sourceRef="ebg" targetRef="catch_timer"/>
    <bpmn:sequenceFlow id="f5" sourceRef="catch_msg" targetRef="log_success"/>
    <bpmn:sequenceFlow id="f6" sourceRef="log_success" targetRef="end_success"/>
    <bpmn:sequenceFlow id="f7" sourceRef="catch_timer" targetRef="inspect"/>
    <bpmn:sequenceFlow id="f8" sourceRef="inspect" targetRef="end_timeout"/>
  </bpmn:process>
  <bpmn:message id="msg_water" name="Вода прозрачная"/>
  <bpmndi:BPMNDiagram id="DI_Diagram_1">
    <bpmndi:BPMNPlane id="DI_Plane_2" bpmnElement="proc">
      <bpmndi:BPMNShape id="DI_Shape_3" bpmnElement="lane_tech" isHorizontal="true">
        <dc:Bounds x="110" y="294.0" width="1117.5" height="142.0"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_4" bpmnElement="lane_auto" isHorizontal="true">
        <dc:Bounds x="110" y="136" width="1117.5" height="158.0"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_5" bpmnElement="start">
        <dc:Bounds x="185.75" y="174.0" width="38" height="38"/>
        <bpmndi:BPMNLabel>
          <dc:Bounds x="150.0" y="220.0" width="109.5" height="32"/>
        </bpmndi:BPMNLabel>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_6" bpmnElement="wash">
        <dc:Bounds x="319.5" y="177.0" width="150" height="76"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_7" bpmnElement="ebg">
        <dc:Bounds x="560.5" y="166.0" width="54" height="54"/>
        <bpmndi:BPMNLabel>
          <dc:Bounds x="529.5" y="228.0" width="116.0" height="32"/>
        </bpmndi:BPMNLabel>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_8" bpmnElement="catch_msg">
        <dc:Bounds x="747.75" y="174.0" width="38" height="38"/>
        <bpmndi:BPMNLabel>
          <dc:Bounds x="705.5" y="220.0" width="122.5" height="32"/>
        </bpmndi:BPMNLabel>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_9" bpmnElement="log_success">
        <dc:Bounds x="888.0" y="177.0" width="150" height="76"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_10" bpmnElement="end_success">
        <dc:Bounds x="1133.75" y="174.0" width="38" height="38"/>
        <bpmndi:BPMNLabel>
          <dc:Bounds x="1104.5" y="220.0" width="96.5" height="32"/>
        </bpmndi:BPMNLabel>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_11" bpmnElement="catch_timer">
        <dc:Bounds x="747.75" y="324.0" width="38" height="38"/>
        <bpmndi:BPMNLabel>
          <dc:Bounds x="712.0" y="370.0" width="109.5" height="32"/>
        </bpmndi:BPMNLabel>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_12" bpmnElement="inspect">
        <dc:Bounds x="888.0" y="327.0" width="150" height="76"/>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNShape id="DI_Shape_13" bpmnElement="end_timeout">
        <dc:Bounds x="1133.75" y="324.0" width="38" height="38"/>
        <bpmndi:BPMNLabel>
          <dc:Bounds x="1098.0" y="370.0" width="109.5" height="32"/>
        </bpmndi:BPMNLabel>
      </bpmndi:BPMNShape>
      <bpmndi:BPMNEdge id="DI_Edge_14" bpmnElement="f1" sourceElement="DI_Shape_5" targetElement="DI_Shape_6">
        <di:waypoint x="223.75" y="193.0"/>
        <di:waypoint x="223.75" y="215.0"/>
        <di:waypoint x="319.5" y="215.0"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_15" bpmnElement="f2" sourceElement="DI_Shape_6" targetElement="DI_Shape_7">
        <di:waypoint x="469.5" y="215.0"/>
        <di:waypoint x="469.5" y="193.0"/>
        <di:waypoint x="560.5" y="193.0"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_16" bpmnElement="f3" sourceElement="DI_Shape_7" targetElement="DI_Shape_8">
        <di:waypoint x="614.5" y="193.0"/>
        <di:waypoint x="747.75" y="193.0"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_17" bpmnElement="f4" sourceElement="DI_Shape_7" targetElement="DI_Shape_11">
        <di:waypoint x="614.5" y="193.0"/>
        <di:waypoint x="655.5" y="193.0"/>
        <di:waypoint x="655.5" y="343.0"/>
        <di:waypoint x="747.75" y="343.0"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_18" bpmnElement="f5" sourceElement="DI_Shape_8" targetElement="DI_Shape_9">
        <di:waypoint x="785.75" y="193.0"/>
        <di:waypoint x="785.75" y="215.0"/>
        <di:waypoint x="888.0" y="215.0"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_19" bpmnElement="f6" sourceElement="DI_Shape_9" targetElement="DI_Shape_10">
        <di:waypoint x="1038.0" y="215.0"/>
        <di:waypoint x="1038.0" y="193.0"/>
        <di:waypoint x="1133.75" y="193.0"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_20" bpmnElement="f7" sourceElement="DI_Shape_11" targetElement="DI_Shape_12">
        <di:waypoint x="785.75" y="343.0"/>
        <di:waypoint x="785.75" y="365.0"/>
        <di:waypoint x="888.0" y="365.0"/>
      </bpmndi:BPMNEdge>
      <bpmndi:BPMNEdge id="DI_Edge_21" bpmnElement="f8" sourceElement="DI_Shape_12" targetElement="DI_Shape_13">
        <di:waypoint x="1038.0" y="365.0"/>
        <di:waypoint x="1038.0" y="343.0"/>
        <di:waypoint x="1133.75" y="343.0"/>
      </bpmndi:BPMNEdge>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
`;
