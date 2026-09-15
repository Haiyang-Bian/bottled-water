"""
条件表达式求值

安全地求值 condition 节点的表达式，变量从 Blackboard kv_state 注入。
"""

from __future__ import annotations

import ast
import operator
from typing import Any

from agent_contracts.logging import get_logger

logger = get_logger(__name__)

# 允许的比较操作符
_COMPARISON_OPS: dict[type[ast.cmpop], Any] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

# 允许的布尔操作符
_BOOL_OPS: dict[type[ast.boolop], Any] = {
    ast.And: all,
    ast.Or: any,
}

# 允许的算术操作符
_ARITHMETIC_OPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

# 允许的一元操作符
_UNARY_OPS: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Not: operator.not_,
}


def evaluate_condition(expression: str, context: dict[str, Any]) -> bool:
    """安全地求值条件表达式。

    支持：比较运算、布尔运算、算术运算、变量引用、常量。
    不支持：函数调用、属性访问、列表/字典推导式、lambda 等。

    Args:
        expression: 条件表达式字符串，如 "score > 80 and passed == True"
        context: 变量上下文，通常来自 Blackboard kv_state

    Returns:
        求值结果，异常时返回 False
    """
    if not expression or not isinstance(expression, str):
        return True

    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval_node(tree.body, context)
        return bool(result)
    except Exception as e:
        logger.warning("条件表达式求值失败", expression=expression, error=str(e))
        return False


def _eval_node(node: ast.AST, context: dict[str, Any]) -> Any:
    """递归求值 AST 节点"""
    # 常量
    if isinstance(node, ast.Constant):
        return node.value

    # 变量名
    if isinstance(node, ast.Name):
        return context.get(node.id, False)

    # 字符串（Python 3.7 兼容性）
    if isinstance(node, ast.Str):  # type: ignore[attr-defined]
        return node.s

    # 数值（Python 3.7 兼容性）
    if isinstance(node, ast.Num):  # type: ignore[attr-defined]
        return node.n

    # 布尔运算 (and / or)
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(v, context) for v in node.values]
        op_fn = _BOOL_OPS.get(type(node.op))
        if op_fn:
            return op_fn(values)
        raise ValueError(f"不支持的布尔操作符: {type(node.op).__name__}")

    # 比较运算 (== != < > <= >= is in)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, context)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, context)
            op_fn = _COMPARISON_OPS.get(type(op))
            if not op_fn:
                raise ValueError(f"不支持的比较操作符: {type(op).__name__}")
            if not op_fn(left, right):
                return False
            left = right
        return True

    # 一元运算 (not - +)
    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, context)
        op_fn = _UNARY_OPS.get(type(node.op))
        if not op_fn:
            raise ValueError(f"不支持的一元操作符: {type(node.op).__name__}")
        return op_fn(operand)

    # 二元运算 (+ - * / % **)
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, context)
        right = _eval_node(node.right, context)
        op_fn = _ARITHMETIC_OPS.get(type(node.op))
        if not op_fn:
            raise ValueError(f"不支持的二元操作符: {type(node.op).__name__}")
        return op_fn(left, right)

    # 元组/列表（用于 in 操作）
    if isinstance(node, (ast.Tuple, ast.List)):
        return [_eval_node(elt, context) for elt in node.elts]

    raise ValueError(f"不支持的 AST 节点类型: {type(node).__name__}")
