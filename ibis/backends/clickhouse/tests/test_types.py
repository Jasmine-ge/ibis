import pytest
from pytest import param

import ibis.expr.datatypes as dt
from ibis.backends.clickhouse.datatypes import parse

pytest.importorskip("clickhouse_driver")


def test_column_types(alltypes):
    df = alltypes.execute()
    assert df.tinyint_col.dtype.name == 'int8'
    assert df.smallint_col.dtype.name == 'int16'
    assert df.int_col.dtype.name == 'int32'
    assert df.bigint_col.dtype.name == 'int64'
    assert df.float_col.dtype.name == 'float32'
    assert df.double_col.dtype.name == 'float64'
    assert df.timestamp_col.dtype.name == 'datetime64[ns]'


def test_columns_types_with_additional_argument(con):
    sql_types = [
        "toFixedString('foo', 8) AS fixedstring_col",
        "toDateTime('2018-07-02 00:00:00', 'UTC') AS datetime_col",
    ]
    df = con.sql(f"SELECT {', '.join(sql_types)}").execute()
    assert df.fixedstring_col.dtype.name == 'object'
    assert df.datetime_col.dtype.name == 'datetime64[ns, UTC]'


@pytest.mark.parametrize(
    ('ch_type', 'ibis_type'),
    [
        param(
            "Enum8('' = 0, 'CDMA' = 1, 'GSM' = 2, 'LTE' = 3, 'NR' = 4)",
            dt.String(nullable=False),
            id="enum",
        ),
        param('IPv4', dt.inet(nullable=False), id="ipv4"),
        param('IPv6', dt.inet(nullable=False), id="ipv6"),
        param('JSON', dt.json(nullable=False), id="json"),
        param("Object('json')", dt.json(nullable=False), id="object_json"),
        param(
            'LowCardinality(String)', dt.String(nullable=False), id="low_card_string"
        ),
        param(
            'Array(Int8)',
            dt.Array(dt.Int8(nullable=False), nullable=False),
            id="array_int8",
        ),
        param(
            'Array(Int16)',
            dt.Array(dt.Int16(nullable=False), nullable=False),
            id="array_int16",
        ),
        param(
            'Array(Int32)',
            dt.Array(dt.Int32(nullable=False), nullable=False),
            id="array_int32",
        ),
        param(
            'Array(Int64)',
            dt.Array(dt.Int64(nullable=False), nullable=False),
            id="array_int64",
        ),
        param(
            'Array(UInt8)',
            dt.Array(dt.UInt8(nullable=False), nullable=False),
            id="array_uint8",
        ),
        param(
            'Array(UInt16)',
            dt.Array(dt.UInt16(nullable=False), nullable=False),
            id="array_uint16",
        ),
        param(
            'Array(UInt32)',
            dt.Array(dt.UInt32(nullable=False), nullable=False),
            id="array_uint32",
        ),
        param(
            'Array(UInt64)',
            dt.Array(dt.UInt64(nullable=False), nullable=False),
            id="array_uint64",
        ),
        param(
            'Array(Float32)',
            dt.Array(dt.Float32(nullable=False), nullable=False),
            id="array_float32",
        ),
        param(
            'Array(Float64)',
            dt.Array(dt.Float64(nullable=False), nullable=False),
            id="array_float64",
        ),
        param(
            'Array(String)',
            dt.Array(dt.String(nullable=False), nullable=False),
            id="array_string",
        ),
        param(
            'Array(FixedString(32))',
            dt.Array(dt.String(nullable=False), nullable=False),
            id="array_fixed_string",
        ),
        param(
            'Array(Date)',
            dt.Array(dt.Date(nullable=False), nullable=False),
            id="array_date",
        ),
        param(
            'Array(DateTime)',
            dt.Array(dt.Timestamp(nullable=False), nullable=False),
            id="array_datetime",
        ),
        param(
            'Array(DateTime64)',
            dt.Array(dt.Timestamp(nullable=False), nullable=False),
            id="array_datetime64",
        ),
        param('Array(Nothing)', dt.Array(dt.null, nullable=False), id="array_nothing"),
        param('Array(Null)', dt.Array(dt.null, nullable=False), id="array_null"),
        param(
            'Array(Array(Int8))',
            dt.Array(
                dt.Array(dt.Int8(nullable=False), nullable=False),
                nullable=False,
            ),
            id="double_array",
        ),
        param(
            'Array(Array(Array(Int8)))',
            dt.Array(
                dt.Array(
                    dt.Array(dt.Int8(nullable=False), nullable=False),
                    nullable=False,
                ),
                nullable=False,
            ),
            id="triple_array",
        ),
        param(
            'Array(Array(Array(Array(Int8))))',
            dt.Array(
                dt.Array(
                    dt.Array(
                        dt.Array(dt.Int8(nullable=False), nullable=False),
                        nullable=False,
                    ),
                    nullable=False,
                ),
                nullable=False,
            ),
            id="quad_array",
        ),
        param(
            "Map(Nullable(String), Nullable(UInt64))",
            dt.Map(dt.string, dt.uint64, nullable=False),
            id="map",
        ),
        param("Decimal(10, 3)", dt.Decimal(10, 3, nullable=False), id="decimal"),
        param(
            "Tuple(a String, b Array(Nullable(Float64)))",
            dt.Struct.from_dict(
                dict(
                    a=dt.String(nullable=False),
                    b=dt.Array(dt.float64, nullable=False),
                ),
                nullable=False,
            ),
            id="named_tuple",
        ),
        param(
            "Tuple(String, Array(Nullable(Float64)))",
            dt.Struct.from_dict(
                dict(
                    f0=dt.String(nullable=False),
                    f1=dt.Array(dt.float64, nullable=False),
                ),
                nullable=False,
            ),
            id="unnamed_tuple",
        ),
        param(
            "Tuple(a String, Array(Nullable(Float64)))",
            dt.Struct.from_dict(
                dict(
                    a=dt.String(nullable=False),
                    f1=dt.Array(dt.float64, nullable=False),
                ),
                nullable=False,
            ),
            id="partially_named",
        ),
        param(
            "Nested(a String, b Array(Nullable(Float64)))",
            dt.Struct.from_dict(
                dict(
                    a=dt.Array(dt.String(nullable=False), nullable=False),
                    b=dt.Array(dt.Array(dt.float64, nullable=False), nullable=False),
                ),
                nullable=False,
            ),
            id="nested",
        ),
        param(
            "DateTime64(0)", dt.Timestamp(scale=0, nullable=False), id="datetime64_zero"
        ),
        param(
            "DateTime64(1)", dt.Timestamp(scale=1, nullable=False), id="datetime64_one"
        ),
        param("DateTime64", dt.Timestamp(nullable=False), id="datetime64"),
    ]
    + [
        param(
            f"DateTime64({scale}, '{tz}')",
            dt.Timestamp(scale=scale, timezone=tz, nullable=False),
            id=f"datetime64_{scale}_{tz}",
        )
        for scale in range(10)
        for tz in ("UTC", "America/New_York", "America/Chicago", "America/Los_Angeles")
    ],
)
def test_parse_type(ch_type, ibis_type):
    assert parse(ch_type) == ibis_type
