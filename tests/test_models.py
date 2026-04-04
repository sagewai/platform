"""Tests for Sagewai Pydantic models."""

from sagewai.models.agent import AgentConfig
from sagewai.models.message import ChatMessage, Conversation, Role, ToolCall
from sagewai.models.tool import ToolResult, ToolSpec, tool


class TestChatMessage:
    def test_system_message(self):
        msg = ChatMessage.system("You are helpful.")
        assert msg.role == Role.system
        assert msg.content == "You are helpful."

    def test_user_message(self):
        msg = ChatMessage.user("Hello")
        assert msg.role == Role.user
        assert msg.content == "Hello"

    def test_assistant_message(self):
        msg = ChatMessage.assistant("Hi there")
        assert msg.role == Role.assistant
        assert msg.content == "Hi there"
        assert msg.tool_calls is None

    def test_assistant_with_tool_calls(self):
        tc = ToolCall(id="1", name="get_weather", arguments={"city": "Paris"})
        msg = ChatMessage.assistant(tool_calls=[tc])
        assert msg.content is None
        assert msg.tool_calls is not None
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].name == "get_weather"

    def test_tool_result_message(self):
        msg = ChatMessage.tool_result("1", "get_weather", "Sunny, 22°C")
        assert msg.role == Role.tool
        assert msg.tool_call_id == "1"
        assert msg.name == "get_weather"
        assert msg.content == "Sunny, 22°C"

    def test_serialization_roundtrip(self):
        msg = ChatMessage.user("test")
        data = msg.model_dump()
        restored = ChatMessage.model_validate(data)
        assert restored == msg


class TestConversation:
    def test_add_messages(self):
        conv = Conversation()
        conv.add_system("System prompt")
        conv.add_user("Hello")
        conv.add_assistant("Hi")
        assert len(conv) == 3

    def test_iteration(self):
        conv = Conversation()
        conv.add_user("A")
        conv.add_assistant("B")
        messages = list(conv)
        assert len(messages) == 2
        assert messages[0].content == "A"
        assert messages[1].content == "B"

    def test_tool_result(self):
        conv = Conversation()
        conv.add_tool_result("tc1", "search", "results here")
        assert len(conv) == 1
        assert conv.messages[0].role == Role.tool


class TestToolSpec:
    def test_basic_creation(self):
        spec = ToolSpec(
            name="search",
            description="Search the web",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
        assert spec.name == "search"
        assert spec.handler is None

    def test_serialization_excludes_handler(self):
        spec = ToolSpec(
            name="test",
            description="test tool",
            handler=lambda: "hi",
        )
        data = spec.model_dump()
        assert "handler" not in data


class TestToolDecorator:
    def test_sync_function(self):
        @tool
        def add(a: int, b: int) -> int:
            """Add two numbers."""
            return a + b

        assert isinstance(add, ToolSpec)
        assert add.name == "add"
        assert add.description == "Add two numbers."
        assert add.parameters["properties"]["a"] == {"type": "integer"}
        assert add.parameters["properties"]["b"] == {"type": "integer"}
        assert add.parameters["required"] == ["a", "b"]
        assert add.handler is not None

    def test_async_function(self):
        @tool
        async def fetch(url: str) -> str:
            """Fetch a URL."""
            return ""

        assert isinstance(fetch, ToolSpec)
        assert fetch.name == "fetch"
        assert fetch.parameters["properties"]["url"] == {"type": "string"}
        assert fetch.parameters["required"] == ["url"]

    def test_optional_params(self):
        @tool
        def greet(name: str, greeting: str = "Hello") -> str:
            """Greet someone."""
            return f"{greeting}, {name}!"

        assert "name" in greet.parameters["required"]
        assert "greeting" not in greet.parameters["required"]


class TestToolResult:
    def test_success(self):
        result = ToolResult(tool_call_id="1", name="test", content="ok")
        assert result.error is None

    def test_error(self):
        result = ToolResult(tool_call_id="1", name="test", content="", error="fail")
        assert result.error == "fail"


class TestAgentConfig:
    def test_defaults(self):
        config = AgentConfig(name="test")
        assert config.model == "gpt-4o"
        assert config.inference.temperature == 0.7
        assert config.max_iterations == 10
        assert config.tools == []

    def test_custom(self):
        config = AgentConfig(
            name="custom",
            model="claude-sonnet-4-5-20250929",
            temperature=0.3,
            max_iterations=5,
        )
        assert config.model == "claude-sonnet-4-5-20250929"
        assert config.inference.temperature == 0.3
