from thoth.application import services
from thoth.application.services.connector_contracts import ConnectorAcquisition as Contract
from thoth.application.services.connector_service import ConnectorAcquisition


def test_acquisition_compatibility_names_are_the_same_class() -> None:
    assert ConnectorAcquisition is Contract
    assert services.ConnectorAcquisition is Contract
