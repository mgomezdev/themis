from app.api.routes.jobs import PrinterConfigInput


def test_printer_config_input_accepts_tool_index():
    c = PrinterConfigInput(printer_id=1, print_profile="p", filament_type="any", filament_color="any", tool_index=2)
    assert c.tool_index == 2
    # default is None (single-tool / legacy)
    assert PrinterConfigInput(printer_id=1, print_profile="p", filament_type="any", filament_color="any").tool_index is None
