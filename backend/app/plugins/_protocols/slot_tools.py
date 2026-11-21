"""Opt-in routing for plugins whose filament positions correspond to slicer tools."""
class SlotTools:
    slice_tool_mapping = True

    @classmethod
    def slice_tool_for_position(cls, pinned_position, plan, loaded, multicolor=False):
        if multicolor:
            return None
        if pinned_position is not None:
            return pinned_position
        return plan[0].slot if len(loaded) > 1 and plan else None
