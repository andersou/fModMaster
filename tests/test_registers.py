from __future__ import annotations

import math

import flet as ft

from fmodmaster.registers import (
    Base,
    FloatEndian,
    RegistersModel,
    build_grid,
    float_from_regs,
    float_to_regs,
    format_value,
    is_signed_visible,
)


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
        assert is_signed_visible(Base.Float) is False


class TestFloatFromRegs:
    """Verify IEEE 754 float32 conversion from register pairs."""

    def test_abcd_mode_1_0(self) -> None:
        # 1.0f = 0x3F800000 → reg0=0x3F80, reg1=0x0000
        assert float_from_regs(0x3F80, 0x0000, FloatEndian.ABCD) == 1.0

    def test_abcd_mode_negative_value(self) -> None:
        # -12.5f = 0xC1480000 → reg0=0xC148, reg1=0x0000
        result = float_from_regs(0xC148, 0x0000, FloatEndian.ABCD)
        assert result == -12.5

    def test_cdab_is_abcd_word_swapped(self) -> None:
        # Same 1.0f but words swapped: reg0=0x0000, reg1=0x3F80
        assert float_from_regs(0x0000, 0x3F80, FloatEndian.CDAB) == 1.0

    def test_dcba_is_fully_reversed(self) -> None:
        # 1.0f fully reversed: 0x0000803F
        # reg0=0x0000 (bytes 00 00), reg1=0x803F (bytes 80 3F)
        result = float_from_regs(0x0000, 0x803F, FloatEndian.DCBA)
        assert result == 1.0

    def test_badc_is_byte_swapped_within_words(self) -> None:
        # 1.0f: reg0=0x803F (byte-swapped 0x3F80), reg1=0x0000 (byte-swapped 0x0000)
        result = float_from_regs(0x803F, 0x0000, FloatEndian.BADC)
        assert result == 1.0

    def test_all_modes_produce_same_float_from_paired_registers(self) -> None:
        # 1.0 in each encoding
        assert float_from_regs(0x3F80, 0x0000, FloatEndian.ABCD) == 1.0
        assert float_from_regs(0x0000, 0x3F80, FloatEndian.CDAB) == 1.0
        assert float_from_regs(0x0000, 0x803F, FloatEndian.DCBA) == 1.0
        assert float_from_regs(0x803F, 0x0000, FloatEndian.BADC) == 1.0


class TestFloatToRegs:
    """Verify float → register-pair roundtrip for all endian modes."""

    def test_abcd_roundtrip(self) -> None:
        reg0, reg1 = float_to_regs(1.0, FloatEndian.ABCD)
        assert float_from_regs(reg0, reg1, FloatEndian.ABCD) == 1.0

    def test_dcba_roundtrip(self) -> None:
        reg0, reg1 = float_to_regs(1.0, FloatEndian.DCBA)
        assert float_from_regs(reg0, reg1, FloatEndian.DCBA) == 1.0

    def test_badc_roundtrip(self) -> None:
        reg0, reg1 = float_to_regs(1.0, FloatEndian.BADC)
        assert float_from_regs(reg0, reg1, FloatEndian.BADC) == 1.0

    def test_cdab_roundtrip(self) -> None:
        reg0, reg1 = float_to_regs(1.0, FloatEndian.CDAB)
        assert float_from_regs(reg0, reg1, FloatEndian.CDAB) == 1.0

    def test_negative_roundtrip(self) -> None:
        reg0, reg1 = float_to_regs(-273.15, FloatEndian.ABCD)
        assert math.isclose(float_from_regs(reg0, reg1, FloatEndian.ABCD), -273.15, rel_tol=1e-6)

    def test_small_fraction_roundtrip(self) -> None:
        reg0, reg1 = float_to_regs(0.001953125, FloatEndian.ABCD)
        assert math.isclose(float_from_regs(reg0, reg1, FloatEndian.ABCD), 0.001953125, rel_tol=1e-6)


class TestFloatGridDisplay:
    """Verify grid rendering in float mode."""

    def test_float_pair_shows_value_and_continuation(self) -> None:
        # 1.0f ABCD: reg0=0x3F80 (16256), reg1=0x0000 (0)
        table = build_grid(
            start_addr=0, qty=2, base=Base.Float, signed=False, is_write=False,
            values=[16256, 0], float_endian=FloatEndian.ABCD,
        )

        assert _text_cell(table.rows[0].cells[0]).value == "1"
        assert _text_cell(table.rows[0].cells[1]).value == "—"

    def test_tooltip_shows_address_pair_range(self) -> None:
        table = build_grid(
            start_addr=0, qty=2, base=Base.Float, signed=False, is_write=False,
            values=[16256, 0], float_endian=FloatEndian.ABCD,
        )

        assert table.rows[0].cells[0].tooltip == "Address : 00 → 01"

    def test_missing_value_shows_dash(self) -> None:
        table = build_grid(
            start_addr=0, qty=2, base=Base.Float, signed=False, is_write=False,
            values=[16256], float_endian=FloatEndian.ABCD,
        )

        assert _text_cell(table.rows[0].cells[0]).value == "-"
        assert _text_cell(table.rows[0].cells[1]).value == "—"

    def test_invalid_mode_shows_dash_slash_dash(self) -> None:
        table = build_grid(
            start_addr=0, qty=2, base=Base.Float, signed=False, is_write=False,
            valid=False, float_endian=FloatEndian.ABCD,
        )

        assert _text_cell(table.rows[0].cells[0]).value == "-/-"
        assert _text_cell(table.rows[0].cells[1]).value == "—"

    def test_odd_quantity_shows_last_register_as_int_fallback(self) -> None:
        # 3 registers → 1 float (reg0+reg1) + 1 int fallback (reg2)
        table = build_grid(
            start_addr=0, qty=3, base=Base.Float, signed=False, is_write=False,
            values=[16256, 0, 42], float_endian=FloatEndian.ABCD,
        )

        # First cell: float from reg0+reg1 = 1.0
        assert _text_cell(table.rows[0].cells[0]).value == "1"
        # Second cell: continuation "—"
        assert _text_cell(table.rows[0].cells[1]).value == "—"
        # Third cell: reg2 alone (odd, no pair) → integer fallback
        assert _text_cell(table.rows[0].cells[2]).value == "42"


class TestFloatEditValidation:
    """Verify float input parsing and validation."""

    def test_valid_float_accepted(self) -> None:
        model = RegistersModel(0, 2, Base.Float, is_write=True, float_endian=FloatEndian.ABCD)
        assert model._parse_edit_float("3.14") == 3.14

    def test_negative_float_accepted(self) -> None:
        model = RegistersModel(0, 2, Base.Float, is_write=True)
        assert model._parse_edit_float("-273.15") == -273.15

    def test_scientific_notation_accepted(self) -> None:
        model = RegistersModel(0, 2, Base.Float, is_write=True)
        assert model._parse_edit_float("1.5e3") == 1500.0

    def test_blank_rejected(self) -> None:
        model = RegistersModel(0, 2, Base.Float, is_write=True)
        assert model._parse_edit_float("   ") is None

    def test_garbage_rejected(self) -> None:
        model = RegistersModel(0, 2, Base.Float, is_write=True)
        assert model._parse_edit_float("not-a-number") is None

    def test_nan_rejected(self) -> None:
        model = RegistersModel(0, 2, Base.Float, is_write=True)
        assert model._parse_edit_float("nan") is None

    def test_infinity_rejected(self) -> None:
        model = RegistersModel(0, 2, Base.Float, is_write=True)
        assert model._parse_edit_float("inf") is None
        assert model._parse_edit_float("-inf") is None

    def test_out_of_float32_range_rejected(self) -> None:
        model = RegistersModel(0, 2, Base.Float, is_write=True)
        assert model._parse_edit_float("4e38") is None


class TestFloatWriteCollection:
    """Verify collect_write_values in float mode."""

    def test_collects_two_registers_from_one_float(self) -> None:
        table = build_grid(
            start_addr=0, qty=2, base=Base.Float, signed=False, is_write=True,
            values=[0, 0], float_endian=FloatEndian.ABCD,
        )
        model = _table_model(table)
        field = _field_cell(table.rows[0].cells[0])
        field.value = "1.0"

        result = model.collect_write_values()

        assert result == [0x3F80, 0x0000]

    def test_odd_cell_not_editable_in_float_mode(self) -> None:
        table = build_grid(
            start_addr=0, qty=2, base=Base.Float, signed=False, is_write=True,
            values=[0, 0], float_endian=FloatEndian.ABCD,
        )

        assert isinstance(table.rows[0].cells[0].content, ft.TextField)
        assert isinstance(table.rows[0].cells[1].content, ft.Text)

    def test_invalid_float_rejected_in_collection(self) -> None:
        table = build_grid(
            start_addr=0, qty=2, base=Base.Float, signed=False, is_write=True,
            values=[0, 0], float_endian=FloatEndian.ABCD,
        )
        model = _table_model(table)
        field = _field_cell(table.rows[0].cells[0])
        field.value = "not-a-float"

        result = model.collect_write_values()

        assert result is None
        assert field.error == "Invalid value"

    def test_float_write_uses_configured_endian(self) -> None:
        table = build_grid(
            start_addr=0, qty=2, base=Base.Float, signed=False, is_write=True,
            values=[0, 0], float_endian=FloatEndian.CDAB,
        )
        model = _table_model(table)
        field = _field_cell(table.rows[0].cells[0])
        field.value = "1.0"

        result = model.collect_write_values()

        # CDAB: reg0=0x0000, reg1=0x3F80 (words swapped compared to ABCD)
        assert result == [0x0000, 0x3F80]

    def test_multiple_float_pairs_collected(self) -> None:
        table = build_grid(
            start_addr=0, qty=4, base=Base.Float, signed=False, is_write=True,
            values=[0, 0, 0, 0], float_endian=FloatEndian.ABCD,
        )
        model = _table_model(table)
        _field_cell(table.rows[0].cells[0]).value = "1.0"
        _field_cell(table.rows[0].cells[2]).value = "-5.0"

        result = model.collect_write_values()

        # -5.0f = 0xC0A00000 → reg0=0xC0A0, reg1=0x0000
        assert result == [0x3F80, 0x0000, 0xC0A0, 0x0000]
