from ..filament_inventory import register_inventory_provider
from .adapter import SpoolmanInventoryProvider

register_inventory_provider("spoolman", SpoolmanInventoryProvider)

__all__ = ["SpoolmanInventoryProvider"]
