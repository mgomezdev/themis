from __future__ import annotations
import importlib
import inspect
from dataclasses import asdict

from ..models import Printer
from .abstract_printer_client import AbstractPrinterClient

REGISTRY: dict[str, str] = {
    "bambu": "app.services.bambu_mqtt.BambuMQTTClient",
    "elegoo_centauri": "app.services.elegoo_centauri_client.ElegooCentauriClient",
}

_DISPLAY_NAMES: dict[str, str] = {
    "bambu": "Bambu Lab",
    "elegoo_centauri": "Elegoo Centauri",
}


def _load_class(printer_type: str) -> type[AbstractPrinterClient]:
    if printer_type not in REGISTRY:
        raise ValueError(f"Unknown printer type: {printer_type!r}")
    module_path, class_name = REGISTRY[printer_type].rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def get_printer_types_for_ui() -> list[dict]:
    result = []
    for printer_type, dotted in REGISTRY.items():
        cls = _load_class(printer_type)
        fields = [asdict(f) for f in cls.connection_fields()]
        result.append({
            "printer_type": printer_type,
            "display_name": _DISPLAY_NAMES.get(printer_type, printer_type),
            "connection_fields": fields,
        })
    return result


def create_client(printer: Printer, **callbacks) -> AbstractPrinterClient:
    cls = _load_class(printer.printer_type)
    cfg = printer.connection_config or {}
    accepted = {f.name for f in cls.connection_fields()}
    kwargs = {k: v for k, v in cfg.items() if k in accepted}
    sig = inspect.signature(cls.__init__)
    for k, v in callbacks.items():
        if k in sig.parameters:
            kwargs[k] = v
    return cls(**kwargs)


def create_client_from_config(printer_type: str, connection_config: dict) -> AbstractPrinterClient:
    cls = _load_class(printer_type)
    accepted = {f.name for f in cls.connection_fields()}
    kwargs = {k: v for k, v in connection_config.items() if k in accepted}
    return cls(**kwargs)
