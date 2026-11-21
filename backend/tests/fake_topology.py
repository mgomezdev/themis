"""Explicit simulated hardware declarations for existing queue tests, never production defaults."""
def declared_topology(loaded=None):
    slots = loaded or [{'type': 'PLA'}]
    return {'tools': [{'id': 'T0', 'nozzle_mm': .4}],
            'positions': [{'id': str(i), 'filament': None if slot.get('empty') else slot} for i, slot in enumerate(slots)],
            'swapping': len(slots) > 1}
