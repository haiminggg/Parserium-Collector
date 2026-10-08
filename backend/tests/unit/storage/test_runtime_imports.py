import builtins
import importlib
import sys
from collections.abc import Callable
from types import ModuleType
from typing import Any

import pytest


def test_storage_modules_do_not_require_development_type_stubs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import: Callable[..., ModuleType] = builtins.__import__

    def import_without_stubs(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> ModuleType:
        if name == "mypy_boto3_s3":
            raise ModuleNotFoundError("development-only boto3 stubs are unavailable")
        return original_import(name, globals, locals, fromlist, level)

    for module_name in (
        "parserium_collector.features.storage.factory",
        "parserium_collector.features.storage.s3",
    ):
        sys.modules.pop(module_name, None)
    monkeypatch.setattr(builtins, "__import__", import_without_stubs)

    importlib.import_module("parserium_collector.features.storage.factory")
    importlib.import_module("parserium_collector.features.storage.s3")
