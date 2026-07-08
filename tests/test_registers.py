from __future__ import annotations

import flet as ft

from fmodmaster.registers import Base, RegistersModel, build_grid, format_value, is_signed_visible


def _text_cell(cell: ft.DataCell) -> ft.Text:
    content = cell.content
    assert isinstance(content, ft.Text)
    return content


def _field_cell(cell: ft.DataCell) -> ft.TextField:
    content = cell.content
    assert isinstance(content, ft.TextField)
    return content


def _table_model(table: ft.DataTable) -> RegistersModel:
    model = table.data
    assert isinstance(model, RegistersModel)
    return model


class TestFormatValue:
    def test_hex_register_is_four_digit_lowercase(self) -> None:
        assert format_value(255, Base.Hex, True, False) == "00ff"

    def test_signed_decimal_register_preserves_negative_input(self) -> None:
        assert format_value(-1, Base.Dec, True, True) == "-1"

    def test_unsigned_decimal_register_wraps_to_uint16(self) -> None:
        assert format_value(-1, Base.Dec, True, False) == "65535"

    def test_binary_register_is_sixteen_bits(self) -> None:
        assert format_value(5, Base.Bin, True, False) == "0000000000000101"

    def test_binary_coil_is_one_bit_text(self) -> None:
        assert format_value(1, Base.Bin, False, False) == "1"


class TestBuildGrid:
    def test_address_alignment_places_first_used_cell_at_start_modulo_ten(self) -> None:
        table = build_grid(start_addr=12, qty=5, base=Base.Dec, signed=False, is_write=False)

        assert len(table.columns) == 10
        assert [column.label for column in table.columns] == [f"{i:02d}" for i in range(10)]
        assert len(table.rows) == 1
        assert table.rows[0].data == "10"
        assert _text_cell(table.rows[0].cells[0]).value == "x"
        assert _text_cell(table.rows[0].cells[1]).value == "x"
        assert _text_cell(table.rows[0].cells[2]).value == "-"
        assert _text_cell(table.rows[0].cells[6]).value == "-"
        assert _text_cell(table.rows[0].cells[7]).value == "x"

    def test_out_of_range_cells_render_x_with_error_coloring(self) -> None:
        table = build_grid(start_addr=12, qty=1, base=Base.Dec, signed=False, is_write=False)
        multi = build_grid(start_addr=12, qty=5, base=Base.Dec, signed=False, is_write=False)

        assert len(table.columns) == 1
        assert table.rows[0].data == "12"
        unused = _text_cell(multi.rows[0].cells[0])
        assert unused.value == "x"
        assert unused.color == ft.Colors.ERROR
        assert unused.bgcolor == ft.Colors.SURFACE_CONTAINER_HIGHEST

    def test_values_and_tooltips_render_in_display_base(self) -> None:
        table = build_grid(
            start_addr=15,
            qty=1,
            base=Base.Hex,
            signed=False,
            is_write=False,
            values=[255],
        )

        assert _text_cell(table.rows[0].cells[0]).value == "00ff"
        assert table.rows[0].cells[0].tooltip == "Address : f"

    def test_invalid_after_error_renders_dash_slash_dash_red(self) -> None:
        table = build_grid(start_addr=0, qty=2, base=Base.Dec, signed=False, is_write=False, valid=False)

        invalid = _text_cell(table.rows[0].cells[0])
        assert invalid.value == "-/-"
        assert invalid.color == ft.Colors.ERROR


class TestEditing:
    def test_write_cells_render_text_fields(self) -> None:
        table = build_grid(start_addr=0, qty=1, base=Base.Dec, signed=False, is_write=True)

        assert isinstance(table.rows[0].cells[0].content, ft.TextField)

    def test_coil_cell_rejects_two(self) -> None:
        table = build_grid(start_addr=0, qty=1, base=Base.Bin, signed=False, is_write=True, is_16bit=False)
        model = _table_model(table)
        field = _field_cell(table.rows[0].cells[0])

        field.value = "2"
        result = model.collect_write_values()

        assert result is None
        assert field.error == "Invalid value"
        assert field.color == ft.Colors.ERROR

    def test_register_cell_rejects_value_above_uint16(self) -> None:
        table = build_grid(start_addr=0, qty=1, base=Base.Dec, signed=False, is_write=True)
        model = _table_model(table)
        field = _field_cell(table.rows[0].cells[0])

        field.value = "70000"
        result = model.collect_write_values()

        assert result is None
        assert field.error == "Invalid value"

    def test_signed_decimal_accepts_negative_lower_bound(self) -> None:
        table = build_grid(start_addr=0, qty=1, base=Base.Dec, signed=True, is_write=True)
        model = _table_model(table)
        field = _field_cell(table.rows[0].cells[0])

        field.value = "-32768"

        assert model.collect_write_values() == [-32768]
        assert field.error is None

    def test_hex_register_accepts_four_digit_mask_value(self) -> None:
        table = build_grid(start_addr=0, qty=1, base=Base.Hex, signed=False, is_write=True)
        model = _table_model(table)
        field = _field_cell(table.rows[0].cells[0])

        field.value = "ffff"

        assert model.collect_write_values() == [65535]
        assert field.error is None
        assert field.max_length == 4

    def test_bin_register_rejects_more_than_sixteen_bits(self) -> None:
        table = build_grid(start_addr=0, qty=1, base=Base.Bin, signed=False, is_write=True)
        model = _table_model(table)
        field = _field_cell(table.rows[0].cells[0])

        field.value = "1" * 17

        assert model.collect_write_values() is None
        assert field.error == "Invalid value"

    def test_signed_checkbox_visible_only_for_decimal(self) -> None:
        assert is_signed_visible(Base.Dec) is True
        assert is_signed_visible(Base.Bin) is False
        assert is_signed_visible(Base.Hex) is False
