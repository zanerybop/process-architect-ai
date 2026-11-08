"""Schema repair feedback stays factual and does not accept or silently change XML."""

import pytest

from backend.app.bpmn_schema import catalog
from backend.app.builder import Builder
from backend.app.domain import PipelineError


def builder():
    value = Builder()
    value.methods["document"](id="d")
    value.methods["element"](id="p", tag="process", parent="d")
    value.methods["element"](id="work", tag="userTask", parent="p")
    return value


def test_local_io_field_is_recommended_from_its_xsd_type_without_auto_alias():
    schema = catalog()
    feedback = schema.construction_feedback("element", "userTask", "tUserTask", "inputOutputSpecification")
    assert feedback["context_type"] == "bpmn:tUserTask"
    assert feedback["same_type_local_children"] == [
        {"tag": "bpmn:ioSpecification", "type": "bpmn:tInputOutputSpecification"}
    ]
    assert schema.element_type("inputOutputSpecification", "tUserTask") is None
    value = builder()
    before = value.build()
    with pytest.raises(PipelineError) as caught:
        value.methods["element"](id="wrong", tag="inputOutputSpecification", parent="work")
    assert caught.value.issue.code == "SCHEMA_PARENT"
    assert caught.value.issue.element_ids == ["wrong"]
    assert "id=work" in caught.value.issue.message
    assert "bpmn:tUserTask" in caught.value.issue.message
    assert "bpmn:ioSpecification" in caught.value.issue.message
    assert value.build() == before


def test_global_store_recommendation_is_a_real_parent_not_metadata_escape():
    schema = catalog()
    feedback = schema.construction_feedback("element", "process", "tProcess", "dataStore")
    assert feedback["allowed_global_parents"] == [{"tag": "bpmn:definitions", "type": "bpmn:tDefinitions"}]
    assert "extensionElements" not in str(feedback["allowed_global_parents"])
    assert all(
        schema.element_type("dataStore", parent["type"]) == "bpmn:tDataStore"
        for parent in feedback["allowed_global_parents"]
    )
    value = builder()
    with pytest.raises(PipelineError) as caught:
        value.methods["element"](id="store", tag="dataStore", parent="p")
    assert "bpmn:definitions" in caught.value.issue.message
    assert "bpmn:tProcess" in caught.value.issue.message
    assert not any(item.key == "store" for item in value.build().document.elements)


@pytest.mark.parametrize("association", ["dataInputAssociation", "dataOutputAssociation"])
@pytest.mark.parametrize("field", ["sourceRef", "targetRef"])
def test_association_reference_is_a_text_child_not_an_attribute(association, field):
    value = builder()
    value.methods["element"](id="link", tag=association, parent="work")
    before = value.build()
    with pytest.raises(PipelineError) as caught:
        value.methods["attribute"](element="link", name=field, value="work")
    message = caught.value.issue.message
    assert caught.value.issue.code == "SCHEMA_ATTRIBUTE"
    assert f"bpmn:{field}" in message
    assert "не атрибут" in message and "text" in message
    assert "bpmn:tData" in message and "Атрибуты схемы:" in message
    assert value.build() == before


def test_polymorphic_parent_uses_its_actual_type_in_feedback():
    value = builder()
    value.methods["element"](id="poly", tag="flowElement", type="tUserTask", parent="p")
    with pytest.raises(PipelineError) as caught:
        value.methods["element"](id="io", tag="inputOutputSpecification", parent="poly")
    assert "bpmn:flowElement" in caught.value.issue.message
    assert "bpmn:tUserTask" in caught.value.issue.message
    assert "bpmn:ioSpecification" in caught.value.issue.message


def test_diagnostics_are_generic_for_other_local_xsd_fields():
    schema = catalog()
    feedback = schema.construction_feedback(
        "attribute", "timerEventDefinition", "tTimerEventDefinition", "timeDuration"
    )
    assert any(child["tag"] == "bpmn:timeDuration" for child in feedback["allowed_children"])
    assert any("не атрибут" in hint for hint in feedback["hints"])
    message = schema.construction_message("attribute", "sequenceFlow", "tSequenceFlow", "conditionExpression")
    assert "conditionExpression" in message and "не атрибут" in message
    assert "sourceRef" in message and "targetRef" in message


def test_diagnostic_options_agree_with_schema_and_do_not_mutate_catalogue():
    schema = catalog()
    for parent in ("bpmn:tUserTask", "bpmn:tProcess", "bpmn:tChoreographyTask", "bpmn:tDataInputAssociation"):
        feedback = schema.construction_feedback("attribute", "task", parent, "notAField")
        assert all(
            schema.element_type(child["tag"], parent) == child["type"]
            for child in feedback["allowed_children"]
        )
        assert {item["name"] for item in feedback["allowed_attributes"]} == {
            item["name"] for item in schema.types[parent]["attributes"]
        }
        feedback["allowed_children"].clear()
        assert schema.children(parent)


def test_feedback_message_fits_worker_transport_even_for_long_bad_names():
    value = builder()
    for name in ("invalid" * 1000, "bad\n" * 2000):
        with pytest.raises(PipelineError) as caught:
            value.methods["attribute"](element="work", name=name, value="x")
        assert caught.value.issue.code == "SCHEMA_ATTRIBUTE"
        assert 0 < len(caught.value.issue.message) <= 1900
        assert "bpmn:tUserTask" in caught.value.issue.message


def test_unknown_element_at_definitions_has_bounded_valid_suggestions():
    value = builder()
    with pytest.raises(PipelineError) as caught:
        value.methods["element"](id="unknown", tag="unknownElement", parent="d")
    assert caught.value.issue.code == "SCHEMA_PARENT"
    assert 0 < len(caught.value.issue.message) <= 1900
    assert "bpmn:tDefinitions" in caught.value.issue.message
    assert "Дочерние поля схемы:" in caught.value.issue.message


def test_unknown_foreign_type_keeps_a_controlled_diagnostic():
    value = builder()
    value.methods["namespace"](prefix="vendor", uri="urn:vendor")
    value.methods["element"](id="ext", tag="extensionElements", parent="work")
    value.methods["element"](id="foreign", tag="vendor:properties", parent="ext")
    with pytest.raises(PipelineError) as caught:
        value.methods["attribute"](element="foreign", name="bad name", value="x")
    assert caught.value.issue.code == "SCHEMA_ATTRIBUTE"
    assert "urn:vendor" in caught.value.issue.message
    assert len(caught.value.issue.message) <= 1900


def test_corrected_schema_constructions_validate_against_unmodified_xsd():
    from backend.app.document_serialization import build_document_tree
    from backend.app.xml_validation import _schema

    value = builder()
    add = value.methods["element"]
    add(id="text_type", tag="itemDefinition", parent="d", structureRef="xsd:string")
    add(id="io", tag="ioSpecification", parent="work")
    add(id="in_value", tag="dataInput", parent="io", itemSubjectRef="text_type")
    add(id="out_value", tag="dataOutput", parent="io", itemSubjectRef="text_type")
    add(id="in_set", tag="inputSet", parent="io")
    add(id="out_set", tag="outputSet", parent="io")
    add(id="in_set_ref", tag="dataInputRefs", parent="in_set", text="in_value")
    add(id="out_set_ref", tag="dataOutputRefs", parent="out_set", text="out_value")
    add(id="data", tag="dataObject", parent="p", itemSubjectRef="text_type")
    add(id="data_ref", tag="dataObjectReference", parent="p", dataObjectRef="data")
    add(id="store", tag="dataStore", parent="d", itemSubjectRef="text_type")
    add(id="store_ref", tag="dataStoreReference", parent="p", dataStoreRef="store")
    add(id="in_link", tag="dataInputAssociation", parent="work")
    add(id="in_source", tag="sourceRef", parent="in_link", text="data_ref")
    add(id="in_target", tag="targetRef", parent="in_link", text="in_value")
    add(id="out_link", tag="dataOutputAssociation", parent="work")
    add(id="out_source", tag="sourceRef", parent="out_link", text="out_value")
    add(id="out_target", tag="targetRef", parent="out_link", text="store_ref")
    tree = build_document_tree(value.build())
    assert _schema().validate(tree), _schema().error_log
