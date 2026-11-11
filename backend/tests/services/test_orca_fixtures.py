"""Real OrcaSlicer output (BIZ-296), so plate parsing is checked against what Laminus actually returns, not hand-built zips.

`fixtures/orca/pack_2plate.3mf`: Laminus `POST /api/v1/pack` (Orca 2.4.2) of `slab_a.stl` x2 + `peg_b.stl` x2 on a 220 x 220 bed
(machine "Creality Ender-3 V3 SE 0.4 nozzle"): plate 1 = one slab + both pegs, plate 2 = the other slab. A real two-plate
`.gcode.3mf` is not in the repo yet: Laminus cannot export plate >= 2 today (reported to laminus-dev)."""
from __future__ import annotations

import zipfile
from pathlib import Path

from app.services.three_mf_parser import parse_three_mf, plate_numbers, plate_thumbnail_bytes

FIXTURES = Path(__file__).parents[1] / "fixtures" / "orca"
PACK = FIXTURES / "pack_2plate.3mf"


def test_a_real_two_plate_pack_has_plates_1_and_2():
    with zipfile.ZipFile(PACK) as zf:
        assert plate_numbers(zf) == [1, 2]


def test_a_real_pack_embeds_no_plate_thumbnails_so_its_plates_parse_without_one(tmp_path):
    """Observed fact (Orca 2.4.2 via Laminus): pack output has no Metadata/plate_N.png or any PNG; plates still parse."""
    with zipfile.ZipFile(PACK) as zf:
        assert not [n for n in zf.namelist() if n.lower().endswith(".png")]
        assert plate_thumbnail_bytes(zf, 1) is None and plate_thumbnail_bytes(zf, 2) is None
    plates = parse_three_mf(str(PACK), thumbnail_dir=str(tmp_path / "thumbs"))
    assert [(p.plate_number, p.thumbnail_path) for p in plates] == [(1, None), (2, None)]
