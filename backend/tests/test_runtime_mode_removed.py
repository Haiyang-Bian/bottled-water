import pytest
from pydantic import ValidationError

from app.schemas.requests import CreateConversationRequest, UpdateConversationRequest


pytestmark = [pytest.mark.integration, pytest.mark.runtime]


@pytest.mark.parametrize(
    "schema",
    [CreateConversationRequest, UpdateConversationRequest],
)
def test_runtime_mode_is_rejected_with_stable_422_error_code(schema):
    with pytest.raises(ValidationError) as caught:
        schema.model_validate({"runtime_mode": "actor"})

    assert caught.value.errors()[0]["type"] == "runtime_mode_removed"
