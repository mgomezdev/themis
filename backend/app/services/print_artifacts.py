"""Print artifact requirements and format classification shared by jobs and settings."""
from .providers.contract import ArtifactKind, InputKind

MODEL_INPUTS = (InputKind.MODEL_3MF, InputKind.STL)
PRINT_OUTPUTS = (ArtifactKind.GCODE, ArtifactKind.GCODE_3MF)
SUFFIXES = {ArtifactKind.GCODE: ".gcode", ArtifactKind.GCODE_3MF: ".gcode.3mf"}


def model_input(filename: str | None) -> InputKind:
    return InputKind.STL if (filename or "").lower().endswith(".stl") else InputKind.MODEL_3MF


def required_output(printer_type: str | None) -> ArtifactKind:
    from .printer_client_factory import client_class

    driver = client_class(printer_type) if printer_type else None
    declared = getattr(driver, "print_artifact_kind", None)
    # Legacy archive-only drivers used the two acceptance flags.
    if declared != ArtifactKind.GCODE_3MF and getattr(driver, "raw_gcode_supported", None) is False \
            and getattr(driver, "sliced_archive_supported", None) is True:
        return ArtifactKind.GCODE_3MF
    return ArtifactKind(declared) if declared in PRINT_OUTPUTS else ArtifactKind.GCODE


def accepts_output(printer_type: str, kind: str) -> bool:
    from .printer_client_factory import client_class

    if kind == required_output(printer_type):
        return True
    driver = client_class(printer_type)
    flag = "raw_gcode_supported" if kind == ArtifactKind.GCODE else "sliced_archive_supported"
    # A driver can opt into a second format. Ignore inherited defaults below its declaration.
    for base in driver.__mro__ if driver is not None else ():
        if flag in base.__dict__:
            return bool(base.__dict__[flag])
        if "print_artifact_kind" in base.__dict__:
            break
    return False
