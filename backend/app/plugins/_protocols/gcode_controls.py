"""Opt-in G-code command recipes, owned by plugin implementations."""
class GcodeControls:
    @property
    def gcode_supported(self):
        return True

    def home(self) -> bool:
        return self.send_gcode("G28")

    def jog(self, axis: str, distance_mm: float) -> bool:
        """Relative move of one axis (X/Y/Z). Default: G-code; vendors with a native command override."""
        axis = axis.upper()
        if not self.send_gcode("G91"):
            return False
        try:
            return bool(self.send_gcode(f"G1 {axis}{distance_mm}"))
        finally:
            self.send_gcode("G90")      # never leave the printer in relative mode

    def home_axes(self, axes: str) -> bool:
        """Home specific axes (e.g. 'X', 'XY'). Default: G28 with the listed axes."""
        return self.send_gcode("G28 " + " ".join(axes.upper()))

    def jog_z(self, distance_mm: float, force: bool = False) -> bool:
        if force:
            self.send_gcode("M211 S0")
        self.send_gcode("G91")
        self.send_gcode(f"G1 Z{distance_mm}")
        self.send_gcode("G90")
        if force:
            self.send_gcode("M211 S1")
        return True

    def set_nozzle_temp(self, celsius: int) -> bool:
        return self.send_gcode(f"M104 S{int(celsius)}") if self.gcode_supported else False
