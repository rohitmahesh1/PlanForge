from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def ensure_package(module_name: str) -> ModuleType:
    module = sys.modules.get(module_name)
    if module is not None:
        return module

    package = ModuleType(module_name)
    package.__path__ = []  # type: ignore[attr-defined]
    sys.modules[module_name] = package

    if "." in module_name:
        parent_name, child_name = module_name.rsplit(".", 1)
        parent = ensure_package(parent_name)
        setattr(parent, child_name, package)

    return package


def install_module(module_name: str, module: ModuleType) -> ModuleType:
    if "." in module_name:
        parent_name, child_name = module_name.rsplit(".", 1)
        parent = ensure_package(parent_name)
        setattr(parent, child_name, module)
    sys.modules[module_name] = module
    return module


def load_source_module(module_name: str, path: Path) -> ModuleType:
    if "." in module_name:
        ensure_package(module_name.rsplit(".", 1)[0])

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module {module_name} from {path}")

    module = importlib.util.module_from_spec(spec)
    install_module(module_name, module)
    spec.loader.exec_module(module)
    return module
