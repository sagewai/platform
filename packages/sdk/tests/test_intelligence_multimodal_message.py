"""Tests for multimodal ChatMessage support (Intelligence I5)."""

from __future__ import annotations

import json

import pytest

from sagewai.intelligence.multimodal.message import ContentPart, ContentType
from sagewai.models.message import ChatMessage, Role


# ------------------------------------------------------------------
# ContentPart creation
# ------------------------------------------------------------------


class TestContentPart:
    """Tests for the ContentPart model."""

    def test_text_part(self) -> None:
        part = ContentPart(type=ContentType.TEXT, text="hello world")
        assert part.is_text is True
        assert part.is_media is False
        assert part.text == "hello world"

    def test_image_url_part(self) -> None:
        part = ContentPart(
            type=ContentType.IMAGE,
            media_url="https://example.com/photo.jpg",
            mime_type="image/jpeg",
            alt_text="A photo",
        )
        assert part.is_text is False
        assert part.is_media is True
        assert part.media_url == "https://example.com/photo.jpg"
        assert part.mime_type == "image/jpeg"
        assert part.alt_text == "A photo"

    def test_image_base64_part(self) -> None:
        part = ContentPart(
            type=ContentType.IMAGE,
            media_base64="iVBORw0KGgoAAAANSUhEUg==",
            mime_type="image/png",
        )
        assert part.is_media is True
        assert part.media_base64 == "iVBORw0KGgoAAAANSUhEUg=="

    def test_audio_part(self) -> None:
        part = ContentPart(
            type=ContentType.AUDIO,
            media_url="https://example.com/clip.mp3",
            mime_type="audio/mp3",
        )
        assert part.type == ContentType.AUDIO
        assert part.is_media is True

    def test_video_part(self) -> None:
        part = ContentPart(
            type=ContentType.VIDEO,
            media_url="https://example.com/clip.mp4",
            mime_type="video/mp4",
        )
        assert part.type == ContentType.VIDEO
        assert part.is_media is True

    def test_content_type_str_enum(self) -> None:
        """ContentType values serialize as plain strings."""
        assert ContentType.TEXT == "text"
        assert ContentType.IMAGE == "image"
        assert ContentType.AUDIO == "audio"
        assert ContentType.VIDEO == "video"

    def test_serialization_roundtrip(self) -> None:
        part = ContentPart(
            type=ContentType.IMAGE,
            media_url="https://example.com/img.png",
            mime_type="image/png",
            alt_text="diagram",
        )
        data = part.model_dump()
        restored = ContentPart.model_validate(data)
        assert restored == part

    def test_json_roundtrip(self) -> None:
        part = ContentPart(type=ContentType.TEXT, text="hi")
        json_str = part.model_dump_json()
        restored = ContentPart.model_validate_json(json_str)
        assert restored == part


# ------------------------------------------------------------------
# ChatMessage backward compatibility
# ------------------------------------------------------------------


class TestChatMessageBackwardCompat:
    """Ensure existing text-only usage is unchanged."""

    def test_text_only_message(self) -> None:
        msg = ChatMessage(role=Role.user, content="hello")
        assert msg.content == "hello"
        assert msg.parts is None
        assert msg.has_media is False

    def test_text_content_from_content_field(self) -> None:
        msg = ChatMessage(role=Role.user, content="hello")
        assert msg.text_content == "hello"

    def test_text_content_empty(self) -> None:
        msg = ChatMessage(role=Role.assistant)
        assert msg.text_content == ""

    def test_factory_methods_still_work(self) -> None:
        sys_msg = ChatMessage.system("You are helpful.")
        assert sys_msg.role == Role.system
        assert sys_msg.content == "You are helpful."
        assert sys_msg.parts is None

        user_msg = ChatMessage.user("hi")
        assert user_msg.role == Role.user
        assert user_msg.content == "hi"

    def test_serialization_without_parts(self) -> None:
        msg = ChatMessage(role=Role.user, content="hello")
        data = msg.model_dump()
        assert "parts" in data
        assert data["parts"] is None
        restored = ChatMessage.model_validate(data)
        assert restored.content == "hello"


# ------------------------------------------------------------------
# ChatMessage with multimodal parts
# ------------------------------------------------------------------


class TestChatMessageMultimodal:
    """Tests for ChatMessage with ContentPart list."""

    def test_text_content_from_parts(self) -> None:
        msg = ChatMessage(
            role=Role.user,
            parts=[
                ContentPart(type=ContentType.TEXT, text="Describe this image:"),
                ContentPart(
                    type=ContentType.IMAGE,
                    media_url="https://example.com/img.png",
                ),
            ],
        )
        assert msg.text_content == "Describe this image:"

    def test_text_content_multiple_text_parts(self) -> None:
        msg = ChatMessage(
            role=Role.user,
            parts=[
                ContentPart(type=ContentType.TEXT, text="Hello"),
                ContentPart(type=ContentType.TEXT, text="World"),
            ],
        )
        assert msg.text_content == "Hello World"

    def test_has_media_true(self) -> None:
        msg = ChatMessage(
            role=Role.user,
            parts=[
                ContentPart(type=ContentType.TEXT, text="Look at this:"),
                ContentPart(
                    type=ContentType.IMAGE,
                    media_base64="abc123",
                    mime_type="image/png",
                ),
            ],
        )
        assert msg.has_media is True

    def test_has_media_false_text_only_parts(self) -> None:
        msg = ChatMessage(
            role=Role.user,
            parts=[ContentPart(type=ContentType.TEXT, text="just text")],
        )
        assert msg.has_media is False

    def test_content_and_parts_coexist(self) -> None:
        """Both content and parts can be set (content takes priority for text_content)."""
        msg = ChatMessage(
            role=Role.user,
            content="legacy text",
            parts=[
                ContentPart(type=ContentType.IMAGE, media_url="https://img.com/a.png"),
            ],
        )
        assert msg.text_content == "legacy text"
        assert msg.has_media is True

    def test_serialization_with_parts(self) -> None:
        parts = [
            ContentPart(type=ContentType.TEXT, text="hello"),
            ContentPart(
                type=ContentType.IMAGE,
                media_url="https://example.com/img.png",
                mime_type="image/png",
            ),
        ]
        msg = ChatMessage(role=Role.user, parts=parts)
        data = msg.model_dump()
        restored = ChatMessage.model_validate(data)
        assert len(restored.parts) == 2  # type: ignore[arg-type]
        assert restored.parts[0].type == ContentType.TEXT  # type: ignore[index]
        assert restored.parts[1].type == ContentType.IMAGE  # type: ignore[index]
        assert restored.parts[1].media_url == "https://example.com/img.png"  # type: ignore[index]

    def test_json_roundtrip_with_parts(self) -> None:
        parts = [
            ContentPart(type=ContentType.TEXT, text="describe"),
            ContentPart(
                type=ContentType.IMAGE,
                media_base64="data==",
                mime_type="image/jpeg",
            ),
        ]
        msg = ChatMessage(role=Role.user, parts=parts)
        json_str = msg.model_dump_json()
        restored = ChatMessage.model_validate_json(json_str)
        assert restored.has_media is True
        assert restored.text_content == "describe"


# ------------------------------------------------------------------
# UniversalAgent._message_to_dict conversion
# ------------------------------------------------------------------


class TestUniversalAgentConversion:
    """Test the _message_to_dict static method handles multimodal parts."""

    def test_text_only_unchanged(self) -> None:
        from sagewai.engines.universal import UniversalAgent

        msg = ChatMessage(role=Role.user, content="hello")
        d = UniversalAgent._message_to_dict(msg)
        assert d["content"] == "hello"

    def test_image_url_part(self) -> None:
        from sagewai.engines.universal import UniversalAgent

        msg = ChatMessage(
            role=Role.user,
            parts=[
                ContentPart(type=ContentType.TEXT, text="What is this?"),
                ContentPart(
                    type=ContentType.IMAGE,
                    media_url="https://example.com/photo.jpg",
                ),
            ],
        )
        d = UniversalAgent._message_to_dict(msg)
        assert isinstance(d["content"], list)
        assert len(d["content"]) == 2
        assert d["content"][0] == {"type": "text", "text": "What is this?"}
        assert d["content"][1]["type"] == "image_url"
        assert d["content"][1]["image_url"]["url"] == "https://example.com/photo.jpg"

    def test_image_base64_part(self) -> None:
        from sagewai.engines.universal import UniversalAgent

        msg = ChatMessage(
            role=Role.user,
            parts=[
                ContentPart(
                    type=ContentType.IMAGE,
                    media_base64="abc123",
                    mime_type="image/jpeg",
                ),
            ],
        )
        d = UniversalAgent._message_to_dict(msg)
        assert isinstance(d["content"], list)
        url = d["content"][0]["image_url"]["url"]
        assert url == "data:image/jpeg;base64,abc123"

    def test_image_base64_default_mime(self) -> None:
        from sagewai.engines.universal import UniversalAgent

        msg = ChatMessage(
            role=Role.user,
            parts=[
                ContentPart(type=ContentType.IMAGE, media_base64="xyz"),
            ],
        )
        d = UniversalAgent._message_to_dict(msg)
        url = d["content"][0]["image_url"]["url"]
        assert url.startswith("data:image/png;base64,")

    def test_parts_takes_priority_over_content(self) -> None:
        from sagewai.engines.universal import UniversalAgent

        msg = ChatMessage(
            role=Role.user,
            content="ignored",
            parts=[ContentPart(type=ContentType.TEXT, text="used")],
        )
        d = UniversalAgent._message_to_dict(msg)
        assert isinstance(d["content"], list)
        assert d["content"][0]["text"] == "used"
