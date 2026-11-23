"""Application capabilities are delegated without Qt or filesystem work."""

from unittest.mock import Mock

import pytest

from iPhoto.application.ports.map_extension import MapExtensionPort
from iPhoto.application.services.map_extension_service import MapExtensionService


@pytest.mark.parametrize("supported", [False, True])
def test_local_install_support_is_delegated_to_port(supported):
    adapter = Mock(spec=MapExtensionPort)
    adapter.supports_local_install.return_value = supported
    service = MapExtensionService(adapter)
    adapter.supports_local_install.assert_not_called()
    assert service.supports_local_install("darwin") is supported
    adapter.supports_local_install.assert_called_once_with("darwin")
    adapter.execute.assert_not_called()
