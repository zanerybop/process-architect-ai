export const fullDocument = `<?xml version="1.0" encoding="UTF-8"?>
<b:definitions xmlns:b="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:v="urn:example:private" xmlns:t="urn:example:process" id="Definitions_full" targetNamespace="urn:example:process">
  <!-- Сохранить комментарий документа -->
  <b:message id="Message_order" name="Заказ" />
  <b:choreography id="Choreography_order" name="Согласование заказа">
    <b:participant id="Buyer" name="Покупатель" /><b:participant id="Seller" name="Поставщик" />
    <b:messageFlow id="Offer" sourceRef="Buyer" targetRef="Seller" messageRef="Message_order" />
    <b:startEvent id="Ch_start"><b:outgoing>Ch_first</b:outgoing></b:startEvent>
    <b:choreographyTask id="Ch_order" name="Передать заказ" initiatingParticipantRef="Buyer">
      <b:extensionElements><v:note empty="">до<v:part value="1"/>после</v:note></b:extensionElements>
      <b:incoming>Ch_first</b:incoming><b:outgoing>Ch_last</b:outgoing>
      <b:participantRef>Buyer</b:participantRef><b:participantRef>Seller</b:participantRef><b:messageFlowRef>Offer</b:messageFlowRef>
    </b:choreographyTask>
    <b:endEvent id="Ch_end"><b:incoming>Ch_last</b:incoming></b:endEvent>
    <b:sequenceFlow id="Ch_first" sourceRef="Ch_start" targetRef="Ch_order"/><b:sequenceFlow id="Ch_last" sourceRef="Ch_order" targetRef="Ch_end"/>
  </b:choreography>
  <b:collaboration id="Collaboration_talk" name="Обсуждение заказа">
    <b:participant id="Talk_buyer" name="Покупатель"/><b:participant id="Talk_seller" name="Поставщик"/>
    <b:conversation id="Talk" name="Условия заказа"><b:participantRef>Talk_buyer</b:participantRef><b:participantRef>Talk_seller</b:participantRef></b:conversation>
    <b:conversationLink id="Talk_left" sourceRef="Talk_buyer" targetRef="Talk"/><b:conversationLink id="Talk_right" sourceRef="Talk" targetRef="Talk_seller"/>
  </b:collaboration>
  <bpmndi:BPMNDiagram id="Diagram_choreography" name="Хореография заказа"><bpmndi:BPMNPlane id="Plane_choreography" bpmnElement="Choreography_order">
    <bpmndi:BPMNShape id="Sh_start" bpmnElement="Ch_start"><dc:Bounds x="80" y="152" width="36" height="36"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="Sh_order" bpmnElement="Ch_order"><dc:Bounds x="190" y="110" width="180" height="120"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="Sh_buyer" bpmnElement="Buyer" participantBandKind="top_initiating" choreographyActivityShape="Sh_order"><dc:Bounds x="190" y="110" width="180" height="25"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="Sh_seller" bpmnElement="Seller" participantBandKind="bottom_non_initiating" choreographyActivityShape="Sh_order"><dc:Bounds x="190" y="205" width="180" height="25"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="Sh_end" bpmnElement="Ch_end"><dc:Bounds x="444" y="152" width="36" height="36"/></bpmndi:BPMNShape>
    <bpmndi:BPMNEdge id="Edge_first" bpmnElement="Ch_first"><di:waypoint x="116" y="170"/><di:waypoint x="190" y="170"/></bpmndi:BPMNEdge>
    <bpmndi:BPMNEdge id="Edge_last" bpmnElement="Ch_last"><di:waypoint x="370" y="170"/><di:waypoint x="444" y="170"/></bpmndi:BPMNEdge>
  </bpmndi:BPMNPlane></bpmndi:BPMNDiagram>
  <bpmndi:BPMNDiagram id="Diagram_conversation" name="Разговор о заказе"><bpmndi:BPMNPlane id="Plane_conversation" bpmnElement="Collaboration_talk">
    <bpmndi:BPMNShape id="Sh_talk_buyer" bpmnElement="Talk_buyer" isHorizontal="true"><dc:Bounds x="45" y="70" width="170" height="60"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="Sh_talk_seller" bpmnElement="Talk_seller" isHorizontal="true"><dc:Bounds x="435" y="70" width="170" height="60"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="Sh_talk" bpmnElement="Talk"><dc:Bounds x="285" y="70" width="80" height="60"/><bpmndi:BPMNLabel><dc:Bounds x="250" y="140" width="150" height="35"/></bpmndi:BPMNLabel></bpmndi:BPMNShape>
    <bpmndi:BPMNEdge id="Edge_talk_left" bpmnElement="Talk_left"><di:waypoint x="215" y="100"/><di:waypoint x="285" y="100"/></bpmndi:BPMNEdge>
    <bpmndi:BPMNEdge id="Edge_talk_right" bpmnElement="Talk_right"><di:waypoint x="365" y="100"/><di:waypoint x="435" y="100"/></bpmndi:BPMNEdge>
  </bpmndi:BPMNPlane></bpmndi:BPMNDiagram>
</b:definitions>`;

export const advancedProcess = `<b:definitions xmlns:b="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:ext="urn:external" id="Advanced" targetNamespace="urn:example:advanced">
  <b:import namespace="urn:external" location="https://example.invalid/process.bpmn" importType="http://www.omg.org/spec/BPMN/20100524/MODEL"/>
  <b:message id="Message" name="Заявка"/>
  <b:process id="Process" name="Расширенная нотация">
    <b:startEvent id="Start" name="Заявка"><b:messageEventDefinition messageRef="Message"/></b:startEvent>
    <b:userTask id="Review" name="Проверить заявку"><b:multiInstanceLoopCharacteristics isSequential="true"/></b:userTask>
    <b:boundaryEvent id="Timer" name="Срок истёк" attachedToRef="Review" cancelActivity="false"><b:timerEventDefinition><b:timeDuration>PT1H</b:timeDuration></b:timerEventDefinition></b:boundaryEvent>
    <b:transaction id="Transaction" name="Транзакция"><b:startEvent id="InnerStart"/><b:serviceTask id="Service" name="Обновить запись"/><b:endEvent id="InnerEnd"/><b:sequenceFlow id="I1" sourceRef="InnerStart" targetRef="Service"/><b:sequenceFlow id="I2" sourceRef="Service" targetRef="InnerEnd"/></b:transaction>
    <b:callActivity id="Call" name="Вызвать процесс" calledElement="ext:Remote"><b:standardLoopCharacteristics/></b:callActivity>
    <b:endEvent id="End" name="Завершить"><b:terminateEventDefinition/></b:endEvent>
    <b:endEvent id="Timeout" name="Уведомление отправлено"><b:messageEventDefinition messageRef="Message"/></b:endEvent>
    <b:dataObject id="Data" isCollection="true"/><b:dataObjectReference id="DataRef" name="Документы" dataObjectRef="Data"/>
    <b:sequenceFlow id="F1" sourceRef="Start" targetRef="Review"/><b:sequenceFlow id="F2" sourceRef="Review" targetRef="Transaction"/><b:sequenceFlow id="F3" sourceRef="Transaction" targetRef="Call"/><b:sequenceFlow id="F4" sourceRef="Call" targetRef="End"/><b:sequenceFlow id="FT" sourceRef="Timer" targetRef="Timeout"/>
    <b:textAnnotation id="Note"><b:text>Проверка таймера и транзакции</b:text></b:textAnnotation>
  </b:process>
  <bpmndi:BPMNDiagram id="AdvancedDiagram"><bpmndi:BPMNPlane id="AdvancedPlane" bpmnElement="Process">
    <bpmndi:BPMNShape id="ShStart" bpmnElement="Start"><dc:Bounds x="40" y="102" width="36" height="36"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShReview" bpmnElement="Review"><dc:Bounds x="125" y="80" width="140" height="80"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShTimer" bpmnElement="Timer"><dc:Bounds x="180" y="142" width="36" height="36"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShTransaction" bpmnElement="Transaction" isExpanded="true"><dc:Bounds x="315" y="50" width="260" height="145"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShInnerStart" bpmnElement="InnerStart"><dc:Bounds x="335" y="109" width="24" height="24"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShService" bpmnElement="Service"><dc:Bounds x="384" y="89" width="118" height="65"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShInnerEnd" bpmnElement="InnerEnd"><dc:Bounds x="527" y="109" width="24" height="24"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShCall" bpmnElement="Call"><dc:Bounds x="625" y="80" width="140" height="80"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShEnd" bpmnElement="End"><dc:Bounds x="810" y="102" width="36" height="36"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShTimeout" bpmnElement="Timeout"><dc:Bounds x="300" y="230" width="36" height="36"/></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShData" bpmnElement="DataRef"><dc:Bounds x="640" y="220" width="45" height="60"/><bpmndi:BPMNLabel><dc:Bounds x="610" y="292" width="100" height="20"/></bpmndi:BPMNLabel></bpmndi:BPMNShape>
    <bpmndi:BPMNShape id="ShNote" bpmnElement="Note"><dc:Bounds x="370" y="230" width="180" height="60"/></bpmndi:BPMNShape>
    <bpmndi:BPMNEdge id="EdgeF1" bpmnElement="F1"><di:waypoint x="76" y="120"/><di:waypoint x="125" y="120"/></bpmndi:BPMNEdge>
    <bpmndi:BPMNEdge id="EdgeF2" bpmnElement="F2"><di:waypoint x="265" y="120"/><di:waypoint x="315" y="120"/></bpmndi:BPMNEdge>
    <bpmndi:BPMNEdge id="EdgeF3" bpmnElement="F3"><di:waypoint x="575" y="120"/><di:waypoint x="625" y="120"/></bpmndi:BPMNEdge>
    <bpmndi:BPMNEdge id="EdgeF4" bpmnElement="F4"><di:waypoint x="765" y="120"/><di:waypoint x="810" y="120"/></bpmndi:BPMNEdge>
    <bpmndi:BPMNEdge id="EdgeFT" bpmnElement="FT"><di:waypoint x="198" y="178"/><di:waypoint x="198" y="248"/><di:waypoint x="300" y="248"/></bpmndi:BPMNEdge>
    <bpmndi:BPMNEdge id="EdgeI1" bpmnElement="I1"><di:waypoint x="359" y="121"/><di:waypoint x="384" y="121"/></bpmndi:BPMNEdge>
    <bpmndi:BPMNEdge id="EdgeI2" bpmnElement="I2"><di:waypoint x="502" y="121"/><di:waypoint x="527" y="121"/></bpmndi:BPMNEdge>
  </bpmndi:BPMNPlane></bpmndi:BPMNDiagram>
</b:definitions>`;
