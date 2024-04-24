from __future__ import annotations

from public import public

import ibis.expr.datatypes as dt
import ibis.expr.rules as rlz
from ibis.common.annotations import ValidationError, attribute
from ibis.common.exceptions import IbisTypeError
from ibis.common.typing import VarTuple  # noqa: TCH001
from ibis.expr.operations.core import Binary, Unary, Value


@public
class LogicalBinary(Binary):
    left: Value[dt.Boolean]
    right: Value[dt.Boolean]

    dtype = dt.boolean


@public
class Not(Unary):
    arg: Value[dt.Boolean]

    dtype = dt.boolean


@public
class And(LogicalBinary):
    pass


@public
class Or(LogicalBinary):
    pass


@public
class Xor(LogicalBinary):
    pass


@public
class Comparison(Binary):
    left: Value
    right: Value

    dtype = dt.boolean

    def __init__(self, left, right):
        """Construct a comparison operation between `left` and `right`.

        Casting rules for type promotions (for resolving the output type) may
        depend on the target backend.

        TODO: how are overflows handled? Can we provide anything useful in
        Ibis to help the user avoid them?
        """
        if not rlz.comparable(left, right):
            raise IbisTypeError(
                f"Arguments {rlz._arg_type_error_format(left)} and "
                f"{rlz._arg_type_error_format(right)} are not comparable"
            )
        super().__init__(left=left, right=right)


@public
class Equals(Comparison):
    pass


@public
class NotEquals(Comparison):
    pass


@public
class GreaterEqual(Comparison):
    pass


@public
class Greater(Comparison):
    pass


@public
class LessEqual(Comparison):
    pass


@public
class Less(Comparison):
    pass


@public
class IdenticalTo(Comparison):
    pass


@public
class Between(Value):
    arg: Value
    lower_bound: Value
    upper_bound: Value

    dtype = dt.boolean
    shape = rlz.shape_like("args")

    def __init__(self, arg, lower_bound, upper_bound):
        if not rlz.comparable(arg, lower_bound):
            raise ValidationError(
                f"Arguments {rlz._arg_type_error_format(arg)} and "
                f"{rlz._arg_type_error_format(lower_bound)} are not comparable"
            )
        if not rlz.comparable(arg, upper_bound):
            raise ValidationError(
                f"Arguments {rlz._arg_type_error_format(arg)} and "
                f"{rlz._arg_type_error_format(upper_bound)} are not comparable"
            )
        super().__init__(arg=arg, lower_bound=lower_bound, upper_bound=upper_bound)


@public
class InValues(Value):
    value: Value
    options: VarTuple[Value]

    dtype = dt.boolean

    @attribute
    def shape(self):
        args = [self.value, *self.options]
        return rlz.highest_precedence_shape(args)


@public
class IfElse(Value):
    """Ternary case expression.

    Equivalent to bool_expr.cases((True, true_expr), else_=false_or_null_expr)

    Many backends implement this as a built-in function.
    """

    bool_expr: Value[dt.Boolean]
    true_expr: Value
    false_null_expr: Value

    shape = rlz.shape_like("args")

    @attribute
    def dtype(self):
        return rlz.highest_precedence_dtype([self.true_expr, self.false_null_expr])
