from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.server import create_app, create_server


def test_only_list_pets_tool_is_registered():
    server = create_server(Settings(base_url="http://test"))
    tools = server._tool_manager.list_tools()
    assert [tool.name for tool in tools] == ["list_pets"]


def test_stateless_streamable_http_app_has_required_routes():
    app = create_app(Settings(base_url="http://test"))
    paths = {route.path for route in app.routes}
    assert "/mcp" in paths
    assert "/health" in paths