"""
Expression search utilities that use Pymbolic's mapping mechanism to
retrieve different types of symbols and functions using query
definitions.
"""

from pymbolic.mapper import WalkMapper
from pymbolic.primitives import Expression

__all__ = ['retrieve_expressions', 'retrieve_variables', 'ExpressionRetriever']


class ExpressionRetriever(WalkMapper):

    def __init__(self, query):
        super(ExpressionRetriever, self).__init__()

        self.query = query
        self.exprs = list()

    def post_visit(self, expr, *args, **kwargs):
        if self.query(expr):
            self.exprs.append(expr)

    map_scalar = WalkMapper.map_variable

    def map_array(self, expr, *args, **kwargs):
        self.visit(expr)
        if expr.dimensions:
            for d in expr.dimensions:
                self.rec(d, *args, **kwargs)
        self.post_visit(expr, *args, **kwargs)

    map_logic_literal = WalkMapper.map_constant
    map_float_literal = WalkMapper.map_constant
    map_int_literal = WalkMapper.map_constant
    map_string_literal = WalkMapper.map_constant
    map_inline_call = WalkMapper.map_call_with_kwargs

    def map_cast(self, expr, *args, **kwargs):
        self.visit(expr)
        for p in expr.parameters:
            self.rec(p, *args, **kwargs)
        if isinstance(expr.kind, Expression):
            self.rec(expr.kind, *args, **kwargs)
        self.post_visit(expr, *args, **kwargs)

    map_parenthesised_add = WalkMapper.map_sum
    map_parenthesised_mul = WalkMapper.map_product
    map_parenthesised_pow = WalkMapper.map_power

    def map_range_index(self, expr, *args, **kwargs):
        self.visit(expr)
        if expr.lower:
            self.rec(expr.lower, *args, **kwargs)
        if expr.upper:
            self.rec(expr.upper, *args, **kwargs)
        if expr.step:
            self.rec(expr.step, *args, **kwargs)
        self.post_visit(expr, *args, **kwargs)


def retrieve_expressions(expr):
    from pymbolic.primitives import Expression
    retriever = ExpressionRetriever(lambda e: isinstance(e, Expression))
    retriever(expr)
    return retriever.exprs


def retrieve_variables(expr):
    from pymbolic.primitives import Variable
    retriever = ExpressionRetriever(lambda e: isinstance(e, Variable))
    retriever(expr)
    return retriever.exprs


def retrieve_inline_calls(expr):
    from loki.expression.symbol_types import InlineCall
    retriever = ExpressionRetriever(lambda e: isinstance(e, InlineCall))
    retriever(expr)
    return retriever.exprs
