from tdxman.codec.bitmap import FieldBit, PresetField, build_bitmap, get_active_fields


def test_handicap_bitmap_preserves_fields_in_control_word():
    bitmap = build_bitmap(PresetField.HANDICAP)

    assert len(bitmap) == 20
    for field in (FieldBit.BID5_PRICE, FieldBit.ASK5_PRICE, FieldBit.ASK5_VOLUME):
        byte = field.value // 8
        bit = field.value % 8
        assert bitmap[byte] & (1 << bit)
    assert FieldBit.BID5_PRICE in [field for field, _ in get_active_fields(bitmap)]
