"""真实文件路径白名单与来源注册表的严格离线契约。"""
import asyncio
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.plugins.xiaojiejiescraping.scope import RecognitionContext, matches_scope  # pylint: disable=import-error,no-name-in-module
from app.plugins.xiaojiejiescraping.registry import PROVIDERS, ProviderRegistry  # pylint: disable=import-error,no-name-in-module


def test_scope_requires_existing_real_file_inside_existing_root(scope_context):
    root = Path(scope_context.path).parent
    assert matches_scope(scope_context, "local", (root,)) is True
    with pytest.raises(FrozenInstanceError):
        scope_context.path = "/outside/TEST-123.mkv"
    assert matches_scope(None, "local", (root,)) is False
    assert matches_scope(SimpleNamespace(**{"operation": "transfer", "storage": "local", "path": scope_context.path}), "local", (root,)) is False
    assert matches_scope(scope_context, "local", ()) is False
    assert matches_scope(scope_context, "local", (Path("/"),)) is False
    assert matches_scope(scope_context, "local", (Path("relative"),)) is False
    assert matches_scope(scope_context, "local", (root / "missing",)) is False
    assert matches_scope(scope_context, "local", (Path(scope_context.path),)) is False


@pytest.mark.parametrize("operation", ["search", "recognize", "manual", "", None])
def test_nontransfer_operations_never_have_directory_scope(scope_context, operation):
    context = RecognitionContext(operation, "local", scope_context.path)
    assert matches_scope(context, "local", (Path(scope_context.path).parent,)) is False


@pytest.mark.parametrize("path", [None, 123, {}, [], ""])
def test_missing_or_wrong_path_type_fails_closed(scope_context, path):
    context = RecognitionContext("transfer", "local", path)
    assert matches_scope(context, "local", (Path(scope_context.path).parent,)) is False


@pytest.mark.parametrize(("context_storage", "configured_storage"), [
    ("remote", "local"), ("local", "remote"), ("remote", "remote"), ("", "local"),
])
def test_unsupported_or_mismatched_storage_fails_closed(scope_context, context_storage, configured_storage):
    context = RecognitionContext("transfer", context_storage, scope_context.path)
    assert matches_scope(context, configured_storage, (Path(scope_context.path).parent,)) is False


def test_component_prefix_and_traversal_do_not_match(scope_context, tmp_path):
    root = Path(scope_context.path).parent
    neighbor = tmp_path / "allowed-other"
    neighbor.mkdir()
    sibling = neighbor / "TEST-123.mkv"
    sibling.write_text("synthetic fixture")
    paths = (
        str(sibling), "allowed/TEST-123.mkv", "", str(root),
        str(root / "missing.mkv"), str(root / ".." / "allowed" / "TEST-123.mkv"),
        "\x00invalid",
    )
    for path in paths:
        assert matches_scope(RecognitionContext("transfer", "local", path), "local", (root,)) is False


def test_symlink_target_and_lexical_path_both_must_be_inside(scope_context, tmp_path):
    root = Path(scope_context.path).parent
    outside = tmp_path / "outside"
    outside.mkdir()
    other = outside / "other.mkv"
    other.write_text("synthetic fixture")
    escaping = root / "escape.mkv"
    escaping.symlink_to(other)
    entering = outside / "enter.mkv"
    entering.symlink_to(scope_context.path)
    allowed = root / "allowed-link.mkv"
    allowed.symlink_to(scope_context.path)
    assert matches_scope(RecognitionContext("transfer", "local", str(escaping)), "local", (root,)) is False
    assert matches_scope(RecognitionContext("transfer", "local", str(entering)), "local", (root,)) is False
    assert matches_scope(RecognitionContext("transfer", "local", str(allowed)), "local", (root,)) is True


def test_missing_root_does_not_mask_later_valid_root(scope_context, tmp_path):
    roots = (tmp_path / "not-mounted", Path(scope_context.path).parent)
    assert matches_scope(scope_context, "local", roots) is True


def test_registered_source_status_preserves_offline_contracts_and_marks_fc2_limited_live():
    statuses = {entry.key: entry for entry in PROVIDERS}
    metadata_sources = {"manko", "fanza", "mgstage", "duga", "sod", "javbus", "jav321", "fc2hub", "fc2ppvdb"}
    assert set(statuses) == metadata_sources | {"fc2", "gfriends"}
    assert all(statuses[key].state == "offline_adapter" for key in metadata_sources)
    assert statuses["fc2"].state == "limited_live"
    assert statuses["gfriends"].purpose == "actor_images"
    assert statuses["gfriends"].state == "offline_adapter"
    assert "metatube" not in statuses


@pytest.mark.parametrize("asynchronous", [False, True])
def test_registry_uses_ordered_unique_available_sources_and_preserves_source_identity(asynchronous):
    calls = []

    class Adapter:
        def __init__(self, name, response):
            self.name, self.response = name, response

        def result(self, number):
            calls.append((self.name, number))
            if isinstance(self.response, Exception):
                raise self.response
            return self.response

        def search(self, number):
            assert asynchronous is False
            return self.result(number)

        async def async_search(self, number):
            assert asynchronous is True
            return self.result(number)

    packet = {"record": {"_id": "source_owned_id", "number": "TEST-123"}, "number": "TEST-123"}
    registry = ProviderRegistry({
        "fails": Adapter("fails", RuntimeError("synthetic failure")),
        "miss": Adapter("miss", None),
        "hit": Adapter("hit", packet),
        "later": Adapter("later", {"record": {"_id": "must_not_mix"}}),
    })
    order = ("pending", "fails", "miss", "miss", "hit", "later")
    result = asyncio.run(registry.async_search("TEST-123", order)) if asynchronous else registry.search("TEST-123", order)
    assert result == ("hit", packet)
    assert calls == [("fails", "TEST-123"), ("miss", "TEST-123"), ("hit", "TEST-123")]
    assert ProviderRegistry({}).search("TEST-123", ("fanza", "javbus")) is None


def test_registry_async_cancellation_is_not_hidden_as_provider_miss():
    class CancelledAdapter:
        async def async_search(self, _number):
            raise asyncio.CancelledError

    registry = ProviderRegistry({"cancel": CancelledAdapter()})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(registry.async_search("TEST-123", ("cancel",)))
