"""The bundled plugins declare their models with the manufacturers' published build volumes (BIZ-251)."""
from app.plugins.bambu import MANIFEST as BAMBU
from app.plugins.snapmaker import MANIFEST as SNAPMAKER


def _models(manifest):
    return {m.id: (m.bed_mm, m.toolheads) for mf in manifest.manufacturers for m in mf.models}


def test_bambu_offers_every_published_model_with_its_build_plate():
    assert _models(BAMBU) == {
        "x1c": ((256, 256), 1), "x1e": ((256, 256), 1), "p1p": ((256, 256), 1), "p1s": ((256, 256), 1),
        "p2s": ((256, 256), 1), "a1": ((256, 256), 1), "a1_mini": ((180, 180), 1),
        "h2s": ((340, 320), 1), "h2d": ((350, 320), 2),
    }


def test_the_legacy_bambu_migration_target_is_still_offered():
    assert "p1s" in _models(BAMBU)          # existing `bambu` rows migrate to P1S


def test_snapmaker_u1_has_a_270mm_plate_and_four_toolheads():
    assert _models(SNAPMAKER) == {"u1_extended": ((270, 270), 4)}
