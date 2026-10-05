"""用官方 V3 测试引导隔离配置，并在收集阶段即禁止真实网络。"""
from __future__ import annotations

import atexit
from dataclasses import MISSING, fields
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
from types import SimpleNamespace

import pytest

REPOSITORY = Path(__file__).resolve().parents[1]
BACKEND = Path(os.environ.get(
    "MOVIEPILOT_BACKEND", REPOSITORY.parent / "moviepilot-v3-dev" / "upstream"
)).resolve()
if not (BACKEND / "app/testing/bootstrap.py").is_file():
    raise pytest.UsageError("请设置 MOVIEPILOT_BACKEND 为官方 MoviePilot V3 源码目录")

_patch = pytest.MonkeyPatch()
_config = tempfile.mkdtemp(prefix="javrecognize-contract-")
_attempts = []
_patch.syspath_prepend(str(BACKEND))
_patch.setenv("CONFIG_DIR", _config)
for _name, _value in {
    "MOVIEPILOT_SAFE_MODE": "true",
    "MOVIEPILOT_AUTO_UPDATE": "false",
    "MOVIEPILOT_UPDATE_DEV": "false",
    "AUTO_UPDATE_RESOURCE": "false",
    "AI_AGENT_ENABLE": "false",
    "DEV": "false",
}.items():
    _patch.setenv(_name, _value)


def _deny_network(*args, **kwargs):
    _attempts.append(repr(args))
    raise RuntimeError("离线契约测试禁止真实连接；请 mock SDK HTTP 边界")


_patch.setattr(socket, "getaddrinfo", _deny_network)
_patch.setattr(socket.socket, "connect", _deny_network)
_patch.setattr(socket.socket, "connect_ex", _deny_network)

from app.testing.bootstrap import prepare_v3_backend  # noqa: E402

prepare_v3_backend(REPOSITORY)


@pytest.fixture(autouse=True)
def no_network_attempts():
    """即使被测代码捕获连接异常，也必须判定该用例失败。"""
    before = len(_attempts)
    yield
    assert len(_attempts) == before, f"真实网络连接尝试：{_attempts[before:]}"


@pytest.fixture
def sample_payload():
    return {
        "record": {
            "_id": "fixture_native_123",
            "number": "TEST-123",
            "title": "TEST-123 synthetic fixture",
            "title_display_name": [{"language": "zh", "title": "测试 & <合成> \"标题\""}],
            "discription": [{"language": "zh", "overview": "Synthetic <plot> & text"}],
            "share_date": "2026-01-02T00:00:00Z",
            "cover_image": "https://images.example.test/unsafe/../../poster.png?token=fixture",
        },
        "number": "TEST-123",
        "actors": [{"name": "Synthetic <Actor> & Name"}],
    }


@pytest.fixture
def plugin_host_context(monkeypatch):
    """在组合根边界提供未使用的 Chain 依赖，保留真实插件基类构造逻辑。"""
    from app.application.chain import context
    from app.application import configuration
    from app.db.oper.systemconfig import SystemConfigOper

    required = {
        field.name: None for field in fields(context.ChainRuntimeContext)
        if field.default is MISSING and field.default_factory is MISSING
    }
    required.update(
        message_queue=SimpleNamespace(bind=lambda _callback: None),
        module_dispatcher_factory=lambda **_kwargs: None,
    )
    runtime = context.ChainRuntimeContext(**required)
    monkeypatch.setattr(context, "_context_provider", lambda: runtime)
    monkeypatch.setattr(
        configuration, "_configured_system_config",
        configuration.SystemConfigService(SystemConfigOper()),
    )
    return runtime


@pytest.fixture
def scope_context(tmp_path):
    """创建真实但无媒体内容的范围内文件，避免用标题伪造路径凭据。"""
    from app.plugins.xiaojiejiescraping.scope import RecognitionContext  # pylint: disable=import-error,no-name-in-module

    path = tmp_path / "allowed" / "TEST-123.mkv"
    path.parent.mkdir()
    path.write_bytes(b"synthetic fixture; no media content")
    return RecognitionContext("transfer", "local", str(path))


def pytest_sessionfinish(session, exitstatus):
    """收集期间或被捕获的网络尝试同样使会话失败。"""
    if _attempts:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def _cleanup():
    module = sys.modules.get("app.db.engine")
    peek = getattr(module, "peek_sync_engine", None)
    engine = peek() if peek else None
    if engine is not None:
        engine.dispose()
    _patch.undo()
    shutil.rmtree(_config, ignore_errors=True)


atexit.register(_cleanup)
