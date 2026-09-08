import pytest
from pydantic import ValidationError

from app.api.routes.chat import ChatRequest, PlanPreviewRequest


@pytest.mark.parametrize("model", [ChatRequest, PlanPreviewRequest])
def test_long_valid_request_is_preserved(model):
    text = "安排工作" * 8000
    assert model(message=text).message == text


@pytest.mark.parametrize("model", [ChatRequest, PlanPreviewRequest])
@pytest.mark.parametrize("field,value", [("message", "x" * 32001), ("session_id", "x" * 129), ("skill", "x" * 81)])
def test_oversized_input_rejected_before_model_or_database(model, field, value):
    with pytest.raises(ValidationError):
        model(**{"message": "查群", field: value})


def test_oversized_direct_command_rejected():
    with pytest.raises(ValidationError):
        ChatRequest(message="执行", command="x" * 16001)
