import importlib.util


def test_device_collection_dependencies_are_available():
    assert importlib.util.find_spec("pysnmp") is not None
    assert importlib.util.find_spec("netmiko") is not None
