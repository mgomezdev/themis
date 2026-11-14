"""A realistic packed 3MF: what the Laminus pack returns, in OrcaSlicer's `Metadata/model_settings.config` shape.

`packed_3mf([["part__m0", "part__m0", "b__m1"], ["part__m0"]])` is a two-plate project: plate 1 holds two copies of the
object named `part__m0` and one `b__m1`, plate 2 one more `part__m0`. One `<object>` element is written per instance (as the
sidecar does), plus a `Metadata/plate_N.png` per plate so the plate-number reader sees the plates.
"""
import io
import zipfile
from xml.sax.saxutils import quoteattr


def model_settings_xml(plates: list[list[str]], plater_ids: list[int] | None = None) -> str:
    objects, plate_xml, next_id = [], [], 2
    for pos, names in enumerate(plates):
        plater_id = plater_ids[pos] if plater_ids is not None else pos + 1
        instances = []
        for name in names:
            objects.append(f'  <object id="{next_id}"><metadata key="name" value={quoteattr(name)}/></object>')
            instances.append(f'    <model_instance><metadata key="object_id" value="{next_id}"/></model_instance>')
            next_id += 1
        plate_xml.append(
            f'  <plate><metadata key="plater_id" value="{plater_id}"/>\n' + "\n".join(instances) + "\n  </plate>")
    return "<config>\n" + "\n".join(objects + plate_xml) + "\n</config>"


def packed_3mf(plates: list[list[str]], *, plater_ids: list[int] | None = None, png_plates: list[int] | None = None) -> bytes:
    """`plates` = object names per plate (an empty list is a plate holding nothing). `plater_ids` overrides the plate numbers
    (default 1..N); `png_plates` the plate numbers that get a thumbnail (default the same)."""
    ids = plater_ids if plater_ids is not None else list(range(1, len(plates) + 1))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in (png_plates if png_plates is not None else ids):
            zf.writestr(f"Metadata/plate_{n}.png", b"\x89PNG\r\n\x1a\n")
        zf.writestr("Metadata/model_settings.config", model_settings_xml(plates, plater_ids))
    return buf.getvalue()


def zip_with(files: dict[str, str | bytes]) -> bytes:
    """A bare zip with exactly these entries (for malformed / hostile model_settings cases)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def png_only_3mf(plate_count: int) -> bytes:
    """A pack with plate thumbnails but no `model_settings.config` — what an older packer returned (no readable layout)."""
    return zip_with({f"Metadata/plate_{i}.png": b"\x89PNG\r\n\x1a\n" for i in range(1, plate_count + 1)})


def auto_pack(per_plate: int):
    """A `pack_models` side effect that honours `counts` / `labels` like a real packer: every copy of every model is placed,
    `per_plate` parts to a plate, in order."""
    def _pack(paths, *, counts=None, labels=None, **_options):
        names = [label for label, n in zip(labels, counts) for _ in range(n)]
        return packed_3mf([names[i:i + per_plate] for i in range(0, len(names), per_plate)])
    return _pack
