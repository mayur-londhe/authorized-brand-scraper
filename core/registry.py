"""
Auto-discovers all brand plugins placed in the /brands/ directory.

Convention:
  brands/
    crompton.py   ← must contain a class that subclasses BaseBrandHandler
    havells.py
    ...

The registry scans every .py file in /brands/, imports it, finds the
BaseBrandHandler subclass inside, and registers it keyed by BRAND_NAME
(case-insensitive).
"""
import importlib
import inspect
import pkgutil
import sys
from pathlib import Path
from typing import Dict, Optional, Type

from .base_handler import BaseBrandHandler


class PluginRegistry:
    def __init__(self):
        self._registry: Dict[str, Type[BaseBrandHandler]] = {}

    def discover(self, brands_dir: Path) -> None:
        """
        Walk brands_dir, import each module, and register any
        BaseBrandHandler subclass found inside.
        """
        brands_dir = Path(brands_dir).resolve()
        if str(brands_dir) not in sys.path:
            sys.path.insert(0, str(brands_dir.parent))

        for finder, module_name, _ in pkgutil.iter_modules([str(brands_dir)]):
            try:
                module = importlib.import_module(f"brands.{module_name}")
            except Exception as e:
                print(f"[Registry] Could not import brands.{module_name}: {e}")
                continue

            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, BaseBrandHandler)
                    and obj is not BaseBrandHandler
                    and obj.BRAND_NAME
                ):
                    key = obj.BRAND_NAME.lower()
                    self._registry[key] = obj
                    print(f"[Registry] Registered: {obj.BRAND_NAME}")

    def get(self, brand_name: str) -> Optional[Type[BaseBrandHandler]]:
        return self._registry.get(brand_name.lower())

    def list_brands(self) -> list:
        return sorted(
            (handler.BRAND_NAME for handler in self._registry.values()),
            key=str.casefold,
        )

    def __repr__(self):
        return f"<PluginRegistry brands={self.list_brands()}>"
