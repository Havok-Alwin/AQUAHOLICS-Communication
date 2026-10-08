from decision.world_model import WorldModel
from decision.world_model_models import WorldObjectStatus
from interfaces.messages import MarkerType

def test_get_objects_returns_all():
    model = WorldModel()

    assert model.get_objects() == ()