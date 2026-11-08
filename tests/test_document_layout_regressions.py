"""Regressions found by rendering the October full-document model benchmark."""

from backend.app.bpmn_schema import NAMESPACES as NS
from backend.app.document_serialization import arrange_document
from backend.app.serialization import _clear_route, _overlap_area
from tests.test_document_serialization import assert_xsd, boxes, document, inside, semantic


def edge_points(root, identifier):
    edge = root.find(f'.//bpmndi:BPMNEdge[@bpmnElement="{identifier}"]', NS)
    assert edge is not None
    return [(float(point.get("x")), float(point.get("y"))) for point in edge.findall("di:waypoint", NS)]


def test_control_nodes_fit_near_work_without_inventing_lane_membership():
    original = document("""<bpmn:process id="P">
      <bpmn:laneSet id="Lanes"><bpmn:lane id="Operator" name="Оператор">
        <bpmn:flowNodeRef>Check</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Accept</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Reject</bpmn:flowNodeRef>
      </bpmn:lane></bpmn:laneSet>
      <bpmn:startEvent id="Start" name="Получено"/>
      <bpmn:task id="Check" name="Проверить заявку"/>
      <bpmn:exclusiveGateway id="Decision" name="Данные полные?" default="No"/>
      <bpmn:task id="Accept" name="Принять заявку"/>
      <bpmn:task id="Reject" name="Вернуть заявку"/>
      <bpmn:endEvent id="Accepted" name="Принято"/>
      <bpmn:endEvent id="Rejected" name="Возвращено"/>
      <bpmn:sequenceFlow id="F1" sourceRef="Start" targetRef="Check"/>
      <bpmn:sequenceFlow id="F2" sourceRef="Check" targetRef="Decision"/>
      <bpmn:sequenceFlow id="Yes" name="Да" sourceRef="Decision" targetRef="Accept">
        <bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">Данные полные</bpmn:conditionExpression>
      </bpmn:sequenceFlow>
      <bpmn:sequenceFlow id="No" name="Нет" sourceRef="Decision" targetRef="Reject"/>
      <bpmn:sequenceFlow id="F3" sourceRef="Accept" targetRef="Accepted"/>
      <bpmn:sequenceFlow id="F4" sourceRef="Reject" targetRef="Rejected"/>
    </bpmn:process>""")
    laid_out = arrange_document(original)
    assert_xsd(laid_out)
    assert semantic(laid_out) == semantic(original)
    geometry = boxes(laid_out)
    for identifier in ("Start", "Decision", "Accepted", "Rejected"):
        assert inside(geometry[identifier][0], geometry["Operator"][0])
    assert [ref.text for ref in laid_out.findall(".//bpmn:flowNodeRef", NS)] == ["Check", "Accept", "Reject"]
    # This small graph should remain readable in a typical desktop canvas.
    assert geometry["Operator"][0][2] < 1200
    assert geometry["Operator"][0][3] < 400


def test_return_message_avoids_its_source_and_destination_task_interiors():
    original = document("""<bpmn:process id="Upper"><bpmn:receiveTask id="Receive"/></bpmn:process>
      <bpmn:process id="Lower"><bpmn:sendTask id="Send"/></bpmn:process>
      <bpmn:collaboration id="C">
        <bpmn:participant id="A" processRef="Upper"/>
        <bpmn:participant id="B" processRef="Lower"/>
        <bpmn:messageFlow id="Return" sourceRef="Send" targetRef="Receive"/>
      </bpmn:collaboration>""")
    laid_out = arrange_document(original)
    assert_xsd(laid_out)
    assert semantic(laid_out) == semantic(original)
    geometry = boxes(laid_out)
    assert _clear_route(edge_points(laid_out, "Return"), [geometry["Send"][0], geometry["Receive"][0]])


def test_conversation_link_routes_around_other_participants():
    original = document("""<bpmn:collaboration id="C">
      <bpmn:participant id="A"/><bpmn:participant id="B"/><bpmn:participant id="C3"/>
      <bpmn:conversation id="Topic"><bpmn:participantRef>A</bpmn:participantRef>
        <bpmn:participantRef>C3</bpmn:participantRef></bpmn:conversation>
      <bpmn:conversationLink id="Link" sourceRef="A" targetRef="Topic"/>
    </bpmn:collaboration>""")
    laid_out = arrange_document(original)
    assert_xsd(laid_out)
    assert semantic(laid_out) == semantic(original)
    geometry = boxes(laid_out)
    assert _clear_route(
        edge_points(laid_out, "Link"), [geometry[name][0] for name in ("A", "B", "C3", "Topic")]
    )


def test_branch_routes_leave_a_gap_to_unrelated_tasks():
    original = document("""<bpmn:process id="P">
      <bpmn:parallelGateway id="Split"/><bpmn:task id="A"/><bpmn:task id="B"/>
      <bpmn:parallelGateway id="Join"/>
      <bpmn:sequenceFlow id="FA" sourceRef="Split" targetRef="A"/>
      <bpmn:sequenceFlow id="FB" sourceRef="Split" targetRef="B"/>
      <bpmn:sequenceFlow id="FJ1" sourceRef="A" targetRef="Join"/>
      <bpmn:sequenceFlow id="FJ2" sourceRef="B" targetRef="Join"/>
    </bpmn:process>""")
    laid_out = arrange_document(original)
    assert_xsd(laid_out)
    geometry = boxes(laid_out)
    for edge_id, other_task in (("FA", "B"), ("FB", "A"), ("FJ1", "B"), ("FJ2", "A")):
        x, y, width, height = geometry[other_task][0]
        assert _clear_route(edge_points(laid_out, edge_id), [(x - 8, y - 8, width + 16, height + 16)])


def test_branch_label_stays_inside_lane_and_near_its_own_route():
    original = document("""<bpmn:process id="P">
      <bpmn:laneSet id="LS"><bpmn:lane id="Lane">
        <bpmn:flowNodeRef>Work</bpmn:flowNodeRef><bpmn:flowNodeRef>Accept</bpmn:flowNodeRef>
        <bpmn:flowNodeRef>Reject</bpmn:flowNodeRef>
      </bpmn:lane></bpmn:laneSet>
      <bpmn:task id="Work" name="Проверить исправность проектора"/>
      <bpmn:exclusiveGateway id="Choice" name="Исправен ли проектор?"/>
      <bpmn:task id="Accept" name="Вернуть залог клиенту"/>
      <bpmn:task id="Reject" name="Составить акт повреждения"/>
      <bpmn:sequenceFlow id="Next" sourceRef="Work" targetRef="Choice"/>
      <bpmn:sequenceFlow id="Yes" sourceRef="Choice" targetRef="Accept" name="Проектор исправен"/>
      <bpmn:sequenceFlow id="No" sourceRef="Choice" targetRef="Reject" name="Проектор неисправен"/>
    </bpmn:process>""")
    laid_out = arrange_document(original)
    assert_xsd(laid_out)
    assert semantic(laid_out) == semantic(original)
    geometry = boxes(laid_out)
    for identifier in ("Yes", "No"):
        edge = laid_out.find(f'.//bpmndi:BPMNEdge[@bpmnElement="{identifier}"]', NS)
        bounds = edge.find("bpmndi:BPMNLabel/dc:Bounds", NS)
        label = tuple(float(bounds.get(name)) for name in ("x", "y", "width", "height"))
        assert inside(label, geometry["Lane"][0])
        x, y, width, height = label
        points = edge_points(laid_out, identifier)
        distances = []
        for first, second in zip(points, points[1:]):
            route_left, route_right = sorted([first[0], second[0]])
            route_top, route_bottom = sorted([first[1], second[1]])
            dx = max(x - route_right, route_left - x - width, 0)
            dy = max(y - route_bottom, route_top - y - height, 0)
            distances.append(dx + dy)
        assert min(distances) <= 20


def test_short_choreography_message_label_does_not_enter_the_next_task():
    original = document("""<bpmn:choreography id="C">
      <bpmn:participant id="A"/><bpmn:participant id="B"/>
      <bpmn:messageFlow id="Message" name="Заявление" sourceRef="A" targetRef="B"/>
      <bpmn:messageFlow id="Reply" sourceRef="B" targetRef="A"/>
      <bpmn:choreographyTask id="First" initiatingParticipantRef="A">
        <bpmn:participantRef>A</bpmn:participantRef><bpmn:participantRef>B</bpmn:participantRef>
        <bpmn:messageFlowRef>Message</bpmn:messageFlowRef>
      </bpmn:choreographyTask>
      <bpmn:choreographyTask id="Second" initiatingParticipantRef="B">
        <bpmn:participantRef>A</bpmn:participantRef><bpmn:participantRef>B</bpmn:participantRef>
        <bpmn:messageFlowRef>Reply</bpmn:messageFlowRef>
      </bpmn:choreographyTask>
      <bpmn:sequenceFlow id="Next" sourceRef="First" targetRef="Second"/>
    </bpmn:choreography>""")
    laid_out = arrange_document(original)
    assert_xsd(laid_out)
    assert semantic(laid_out) == semantic(original)
    bounds = laid_out.find('.//bpmndi:BPMNEdge[@bpmnElement="Message"]/bpmndi:BPMNLabel/dc:Bounds', NS)
    label = tuple(float(bounds.get(name)) for name in ("x", "y", "width", "height"))
    geometry = boxes(laid_out)
    assert _overlap_area(label, geometry["First"][0]) == 0
    assert _overlap_area(label, geometry["Second"][0]) == 0


def test_flow_caption_avoids_the_message_reference_envelope_and_caption():
    original = document("""<bpmn:message id="M" name="Заказ"/>
      <bpmn:process id="Upper"><bpmn:sendTask id="Send"/></bpmn:process>
      <bpmn:process id="Lower"><bpmn:receiveTask id="Receive"/></bpmn:process>
      <bpmn:collaboration id="C">
        <bpmn:participant id="A" processRef="Upper"/><bpmn:participant id="B" processRef="Lower"/>
        <bpmn:messageFlow id="Order" name="Заказ" messageRef="M" sourceRef="Send" targetRef="Receive"/>
      </bpmn:collaboration>""")
    laid_out = arrange_document(original)
    assert_xsd(laid_out)
    assert semantic(laid_out) == semantic(original)
    points = edge_points(laid_out, "Order")
    distance = sum(abs(b[0] - a[0]) + abs(b[1] - a[1]) for a, b in zip(points, points[1:])) / 2
    midpoint = None
    for a, b in zip(points, points[1:]):
        length = abs(b[0] - a[0]) + abs(b[1] - a[1])
        if length and distance <= length:
            midpoint = (a[0] + (b[0] - a[0]) * distance / length, a[1] + (b[1] - a[1]) * distance / length)
            break
        distance -= length
    assert midpoint is not None
    bounds = laid_out.find('.//bpmndi:BPMNEdge[@bpmnElement="Order"]/bpmndi:BPMNLabel/dc:Bounds', NS)
    label = tuple(float(bounds.get(name)) for name in ("x", "y", "width", "height"))
    mx, my = midpoint
    # Actual renderer envelope: 21 x 14; its text starts 17 px below the midpoint.
    assert _overlap_area(label, (mx - 10.5, my - 7, 21, 14)) == 0
    assert _overlap_area(label, (mx - 30, my + 17, 60, 18)) == 0
