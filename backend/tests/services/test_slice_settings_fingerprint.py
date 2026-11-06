from app.services.providers.contract import FilamentChoice, SliceSettings


def _settings(**kw) -> SliceSettings:
    base = dict(process_profile="0.20mm Standard", filaments=(FilamentChoice(1, "PLA", "Generic PLA", "#ff0000"),),
                overrides={"a": "1", "b": "2"})
    return SliceSettings(**{**base, **kw})


def test_fingerprint_is_stable_and_ignores_override_order_and_colour():
    a = _settings()
    b = _settings(overrides={"b": "2", "a": "1"}, filaments=(FilamentChoice(1, "PLA", "Generic PLA", "#00ff00"),))
    assert a.fingerprint() == b.fingerprint()
    assert len(a.fingerprint()) == 64


def test_fingerprint_changes_with_every_cache_relevant_field():
    base = _settings().fingerprint()
    variants = [
        _settings(process_profile="0.28mm Draft"),
        _settings(overrides={"a": "9"}),
        _settings(tool_index=1),
        _settings(plate_type="Textured PEI"),
        _settings(filament_map=((1, 0),)),
        _settings(filaments=(FilamentChoice(1, "PETG", "Generic PETG"),)),
        _settings(filaments=(FilamentChoice(1, "PLA", "Other PLA", "#ff0000"),)),   # profile only
        _settings(filaments=(FilamentChoice(2, "PLA", "Generic PLA", "#ff0000"),)),   # slot only
    ]
    assert len({v.fingerprint() for v in variants} | {base}) == len(variants) + 1
