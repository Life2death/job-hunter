import pytest

def pytest_addoption(parser):
    parser.addoption("--skip-integration", action="store_true",
                     help="Skip tests that make real HTTP calls")

def pytest_configure(config):
    config.addinivalue_line("markers", "integration: marks tests that hit real APIs")

def pytest_runtest_setup(item):
    if item.get_closest_marker("integration") and item.config.getoption("--skip-integration"):
        pytest.skip("skipping integration test")
