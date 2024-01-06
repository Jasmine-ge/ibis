from __future__ import annotations

import contextlib
import inspect
import typing
from pathlib import Path
from typing import TYPE_CHECKING, Any

import datafusion as df
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow_hotfix  # noqa: F401
import sqlglot as sg
import sqlglot.expressions as sge

import ibis
import ibis.common.exceptions as com
import ibis.expr.datatypes as dt
import ibis.expr.operations as ops
import ibis.expr.schema as sch
import ibis.expr.types as ir
from ibis.backends.base import CanCreateDatabase, CanCreateSchema
from ibis.backends.base.sqlglot import SQLGlotBackend
from ibis.backends.base.sqlglot.compiler import C
from ibis.backends.datafusion.compiler import DataFusionCompiler
from ibis.expr.operations.udf import InputType
from ibis.formats.pyarrow import PyArrowType
from ibis.util import gen_name, normalize_filename

try:
    from datafusion import ExecutionContext as SessionContext
except ImportError:
    from datafusion import SessionContext

try:
    from datafusion import SessionConfig
except ImportError:
    SessionConfig = None

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    import pandas as pd


class Backend(SQLGlotBackend, CanCreateDatabase, CanCreateSchema):
    name = "datafusion"
    dialect = "datafusion"
    builder = None
    supports_in_memory_tables = True
    supports_arrays = True
    compiler = DataFusionCompiler()

    @property
    def version(self):
        import importlib.metadata

        return importlib.metadata.version("datafusion")

    def do_connect(
        self, config: Mapping[str, str | Path] | SessionContext | None = None
    ) -> None:
        """Create a Datafusion backend for use with Ibis.

        Parameters
        ----------
        config
            Mapping of table names to files.

        Examples
        --------
        >>> import ibis
        >>> config = {"t": "path/to/file.parquet", "s": "path/to/file.csv"}
        >>> ibis.datafusion.connect(config)
        """
        if isinstance(config, SessionContext):
            (self.con, config) = (config, None)
        else:
            if SessionConfig is not None:
                df_config = SessionConfig(
                    {"datafusion.sql_parser.dialect": "PostgreSQL"}
                ).with_information_schema(True)
            else:
                df_config = None
            self.con = SessionContext(df_config)

        self._register_builtin_udfs()

        if not config:
            config = {}

        for name, path in config.items():
            self.register(path, table_name=name)

        self._temp_views = set()

    @contextlib.contextmanager
    def _safe_raw_sql(self, sql: sge.Statement) -> Any:
        yield self.raw_sql(sql)

    def _metadata(self, query: str) -> Iterator[tuple[str, dt.DataType]]:
        name = gen_name("datafusion_metadata_view")
        table = sg.table(name, quoted=self.compiler.quoted)
        src = sge.Create(
            this=table,
            kind="VIEW",
            expression=sg.parse_one(query, read="datafusion"),
            properties=sge.Properties(expressions=[sge.TemporaryProperty()]),
        )
        self.raw_sql(src)

        try:
            result = (
                self.raw_sql(f"DESCRIBE {table.sql(self.name)}")
                .to_arrow_table()
                .to_pydict()
            )
        finally:
            self.drop_view(name)
        return (
            (
                name,
                self.compiler.type_mapper.from_string(
                    type_string, nullable=is_nullable == "YES"
                ),
            )
            for name, type_string, is_nullable in zip(
                result["column_name"], result["data_type"], result["is_nullable"]
            )
        )

    def _register_builtin_udfs(self):
        from ibis.backends.datafusion import udfs

        for name, func in inspect.getmembers(
            udfs,
            predicate=lambda m: callable(m)
            and not m.__name__.startswith("_")
            and m.__module__ == udfs.__name__,
        ):
            annotations = typing.get_type_hints(func)
            argnames = list(inspect.signature(func).parameters.keys())
            input_types = [
                PyArrowType.from_ibis(dt.dtype(annotations.get(arg_name)))
                for arg_name in argnames
            ]
            return_type = PyArrowType.from_ibis(dt.dtype(annotations["return"]))
            udf = df.udf(
                func,
                input_types=input_types,
                return_type=return_type,
                volatility="immutable",
                name=name,
            )
            self.con.register_udf(udf)

    def _register_udfs(self, expr: ir.Expr) -> None:
        for udf_node in expr.op().find(ops.ScalarUDF):
            if udf_node.__input_type__ == InputType.PYARROW:
                udf = self._compile_pyarrow_udf(udf_node)
                self.con.register_udf(udf)

        for udf_node in expr.op().find(ops.ElementWiseVectorizedUDF):
            udf = self._compile_elementwise_udf(udf_node)
            self.con.register_udf(udf)

    def _compile_pyarrow_udf(self, udf_node):
        return df.udf(
            udf_node.__func__,
            input_types=[PyArrowType.from_ibis(arg.dtype) for arg in udf_node.args],
            return_type=PyArrowType.from_ibis(udf_node.dtype),
            volatility=getattr(udf_node, "__config__", {}).get(
                "volatility", "volatile"
            ),
            name=udf_node.__func_name__,
        )

    def _compile_elementwise_udf(self, udf_node):
        return df.udf(
            udf_node.func,
            input_types=list(map(PyArrowType.from_ibis, udf_node.input_type)),
            return_type=PyArrowType.from_ibis(udf_node.return_type),
            volatility="volatile",
            name=udf_node.func.__name__,
        )

    def raw_sql(self, query: str | sg.exp.Expression) -> Any:
        """Execute a SQL string `query` against the database.

        Parameters
        ----------
        query
            Raw SQL string
        kwargs
            Backend specific query arguments
        """
        with contextlib.suppress(AttributeError):
            query = query.sql(dialect=self.dialect, pretty=True)
        self._log(query)
        return self.con.sql(query)

    @property
    def current_database(self) -> str:
        raise NotImplementedError()

    @property
    def current_schema(self) -> str:
        return NotImplementedError()

    def list_databases(self, like: str | None = None) -> list[str]:
        code = (
            sg.select(C.table_catalog)
            .from_(sg.table("tables", db="information_schema"))
            .distinct()
        ).sql()
        result = self.con.sql(code).to_pydict()
        return self._filter_with_like(result["table_catalog"], like)

    def create_database(self, name: str, force: bool = False) -> None:
        self.raw_sql(
            sg.exp.Create(kind="DATABASE", this=sg.to_identifier(name), exists=force)
        )

    def drop_database(self, name: str, force: bool = False) -> None:
        raise com.UnsupportedOperationError(
            "DataFusion does not support dropping databases"
        )

    def list_schemas(
        self, like: str | None = None, database: str | None = None
    ) -> list[str]:
        return self._filter_with_like(
            self.con.catalog(
                database if database is not None else "datafusion"
            ).names(),
            like=like,
        )

    def create_schema(
        self, name: str, database: str | None = None, force: bool = False
    ) -> None:
        # not actually a table, but this is how sqlglot represents schema names
        schema_name = sg.table(name, db=database)
        self.raw_sql(sg.exp.Create(kind="SCHEMA", this=schema_name, exists=force))

    def drop_schema(
        self, name: str, database: str | None = None, force: bool = False
    ) -> None:
        schema_name = sg.table(name, db=database)
        self.raw_sql(sg.exp.Drop(kind="SCHEMA", this=schema_name, exists=force))

    def list_tables(
        self,
        like: str | None = None,
        database: str | None = None,
    ) -> list[str]:
        """List the available tables."""
        return self._filter_with_like(self.con.tables(), like)

    def get_schema(
        self, table_name: str, schema: str | None = None, database: str | None = None
    ) -> sch.Schema:
        if database is not None:
            catalog = self.con.catalog(database)
        else:
            catalog = self.con.catalog()

        if schema is not None:
            database = catalog.database(schema)
        else:
            database = catalog.database()

        table = database.table(table_name)
        return sch.schema(table.schema)

    def register(
        self,
        source: str | Path | pa.Table | pa.RecordBatch | pa.Dataset | pd.DataFrame,
        table_name: str | None = None,
        **kwargs: Any,
    ) -> ir.Table:
        """Register a data set with `table_name` located at `source`.

        Parameters
        ----------
        source
            The data source(s). May be a path to a file or directory of
            parquet/csv files, a pandas dataframe, or a pyarrow table, dataset
            or record batch.
        table_name
            The name of the table
        kwargs
            Datafusion-specific keyword arguments

        Examples
        --------
        Register a csv:

        >>> import ibis
        >>> conn = ibis.datafusion.connect(config)
        >>> conn.register("path/to/data.csv", "my_table")
        >>> conn.table("my_table")

        Register a PyArrow table:

        >>> import pyarrow as pa
        >>> tab = pa.table({"x": [1, 2, 3]})
        >>> conn.register(tab, "my_table")
        >>> conn.table("my_table")

        Register a PyArrow dataset:

        >>> import pyarrow.dataset as ds
        >>> dataset = ds.dataset("path/to/table")
        >>> conn.register(dataset, "my_table")
        >>> conn.table("my_table")
        """
        import pandas as pd

        if isinstance(source, (str, Path)):
            first = str(source)
        elif isinstance(source, pa.Table):
            self.con.deregister_table(table_name)
            self.con.register_record_batches(table_name, [source.to_batches()])
            return self.table(table_name)
        elif isinstance(source, pa.RecordBatch):
            self.con.deregister_table(table_name)
            self.con.register_record_batches(table_name, [[source]])
            return self.table(table_name)
        elif isinstance(source, pa.dataset.Dataset):
            self.con.deregister_table(table_name)
            self.con.register_dataset(table_name, source)
            return self.table(table_name)
        elif isinstance(source, pd.DataFrame):
            return self.register(pa.Table.from_pandas(source), table_name, **kwargs)
        else:
            raise ValueError("`source` must be either a string or a pathlib.Path")

        if first.startswith(("parquet://", "parq://")) or first.endswith(
            ("parq", "parquet")
        ):
            return self.read_parquet(source, table_name=table_name, **kwargs)
        elif first.startswith(("csv://", "txt://")) or first.endswith(
            ("csv", "tsv", "txt")
        ):
            return self.read_csv(source, table_name=table_name, **kwargs)
        else:
            self._register_failure()
            return None

    def _register_failure(self):
        import inspect

        msg = ", ".join(
            m[0] for m in inspect.getmembers(self) if m[0].startswith("read_")
        )
        raise ValueError(
            f"Cannot infer appropriate read function for input, "
            f"please call one of {msg} directly"
        )

    def _register_in_memory_table(self, op: ops.InMemoryTable) -> None:
        name = op.name
        schema = op.schema

        self.con.deregister_table(name)
        if batches := op.data.to_pyarrow(schema).to_batches():
            self.con.register_record_batches(name, [batches])
        else:
            empty_dataset = ds.dataset([], schema=schema.to_pyarrow())
            self.con.register_dataset(name=name, dataset=empty_dataset)

    def _register_in_memory_tables(self, expr: ir.Expr) -> None:
        if self.supports_in_memory_tables:
            for memtable in expr.op().find(ops.InMemoryTable):
                self._register_in_memory_table(memtable)

    def read_csv(
        self, path: str | Path, table_name: str | None = None, **kwargs: Any
    ) -> ir.Table:
        """Register a CSV file as a table in the current database.

        Parameters
        ----------
        path
            The data source. A string or Path to the CSV file.
        table_name
            An optional name to use for the created table. This defaults to
            a sequentially generated name.
        **kwargs
            Additional keyword arguments passed to Datafusion loading function.

        Returns
        -------
        ir.Table
            The just-registered table
        """
        path = normalize_filename(path)
        table_name = table_name or gen_name("read_csv")
        # Our other backends support overwriting views / tables when reregistering
        self.con.deregister_table(table_name)
        self.con.register_csv(table_name, path, **kwargs)
        return self.table(table_name)

    def read_parquet(
        self, path: str | Path, table_name: str | None = None, **kwargs: Any
    ) -> ir.Table:
        """Register a parquet file as a table in the current database.

        Parameters
        ----------
        path
            The data source.
        table_name
            An optional name to use for the created table. This defaults to
            a sequentially generated name.
        **kwargs
            Additional keyword arguments passed to Datafusion loading function.

        Returns
        -------
        ir.Table
            The just-registered table
        """
        path = normalize_filename(path)
        table_name = table_name or gen_name("read_parquet")
        # Our other backends support overwriting views / tables when reregistering
        self.con.deregister_table(table_name)
        self.con.register_parquet(table_name, path, **kwargs)
        return self.table(table_name)

    def read_delta(
        self, source_table: str | Path, table_name: str | None = None, **kwargs: Any
    ) -> ir.Table:
        """Register a Delta Lake table as a table in the current database.

        Parameters
        ----------
        source_table
            The data source. Must be a directory
            containing a Delta Lake table.
        table_name
            An optional name to use for the created table. This defaults to
            a sequentially generated name.
        **kwargs
            Additional keyword arguments passed to deltalake.DeltaTable.

        Returns
        -------
        ir.Table
            The just-registered table
        """
        source_table = normalize_filename(source_table)

        table_name = table_name or gen_name("read_delta")

        # Our other backends support overwriting views / tables when reregistering
        self.con.deregister_table(table_name)

        try:
            from deltalake import DeltaTable
        except ImportError:
            raise ImportError(
                "The deltalake extra is required to use the "
                "read_delta method. You can install it using pip:\n\n"
                "pip install 'ibis-framework[deltalake]'\n"
            )

        delta_table = DeltaTable(source_table, **kwargs)

        return self.register(delta_table.to_pyarrow_dataset(), table_name=table_name)

    def to_pyarrow_batches(
        self,
        expr: ir.Expr,
        *,
        chunk_size: int = 1_000_000,
        **kwargs: Any,
    ) -> pa.ipc.RecordBatchReader:
        pa = self._import_pyarrow()

        self._register_udfs(expr)
        self._register_in_memory_tables(expr)

        table_expr = expr.as_table()
        raw_sql = self.compile(table_expr, **kwargs)

        frame = self.con.sql(raw_sql)

        schema = table_expr.schema()
        names = schema.names

        struct_schema = schema.as_struct().to_pyarrow()

        return pa.ipc.RecordBatchReader.from_batches(
            schema.to_pyarrow(),
            (
                # convert the renamed + casted columns into a record batch
                pa.RecordBatch.from_struct_array(
                    # rename columns to match schema because datafusion lowercases things
                    pa.RecordBatch.from_arrays(batch.columns, names=names)
                    # cast the struct array to the desired types to work around
                    # https://github.com/apache/arrow-datafusion-python/issues/534
                    .to_struct_array()
                    .cast(struct_schema)
                )
                for batch in frame.collect()
            ),
        )

    def to_pyarrow(self, expr: ir.Expr, **kwargs: Any) -> pa.Table:
        batch_reader = self.to_pyarrow_batches(expr, **kwargs)
        arrow_table = batch_reader.read_all()
        return expr.__pyarrow_result__(arrow_table)

    def execute(self, expr: ir.Expr, **kwargs: Any):
        batch_reader = self.to_pyarrow_batches(expr, **kwargs)
        return expr.__pandas_result__(
            batch_reader.read_pandas(timestamp_as_object=True)
        )

    def create_table(
        self,
        name: str,
        obj: pd.DataFrame | pa.Table | ir.Table | None = None,
        *,
        schema: sch.Schema | None = None,
        database: str | None = None,
        temp: bool = False,
        overwrite: bool = False,
    ) -> ir.Table:
        """Create a table in DataFusion.

        Parameters
        ----------
        name
            Name of the table to create
        obj
            The data with which to populate the table; optional, but at least
            one of `obj` or `schema` must be specified
        schema
            The schema of the table to create; optional, but at least one of
            `obj` or `schema` must be specified
        database
            The name of the database in which to create the table; if not
            passed, the current database is used.
        temp
            Create a temporary table
        overwrite
            If `True`, replace the table if it already exists, otherwise fail
            if the table exists
        """
        if obj is None and schema is None:
            raise ValueError("Either `obj` or `schema` must be specified")

        column_defs = [
            sg.exp.ColumnDef(
                this=sg.to_identifier(name, quoted=self.compiler.quoted),
                kind=self.compiler.type_mapper.from_ibis(typ),
                constraints=(
                    None
                    if typ.nullable
                    else [
                        sg.exp.ColumnConstraint(kind=sg.exp.NotNullColumnConstraint())
                    ]
                ),
            )
            for name, typ in (schema or {}).items()
        ]

        target = sg.table(name, db=database, quoted=self.compiler.quoted)

        if column_defs:
            target = sg.exp.Schema(this=target, expressions=column_defs)

        properties = []

        if temp:
            properties.append(sg.exp.TemporaryProperty())

        if obj is not None:
            if not isinstance(obj, ir.Expr):
                table = ibis.memtable(obj)
            else:
                table = obj

            self._run_pre_execute_hooks(table)

            query = self._to_sqlglot(table)
        else:
            query = None

        create_stmt = sg.exp.Create(
            kind="TABLE",
            this=target,
            replace=overwrite,
            properties=sg.exp.Properties(expressions=properties),
            expression=query,
        )

        with self._safe_raw_sql(create_stmt):
            pass

        return self.table(name, schema=database)
