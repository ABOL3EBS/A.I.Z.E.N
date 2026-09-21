"""System-domain builtins: get_current_time and a safe Decimal calculator."""

from __future__ import annotations

import ast
import decimal
import operator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel

from aizen.domains import Domain
from aizen.facts import date_fact, number, text_fact
from aizen.tools.base import (
    FunctionTool,
    OrbState,
    PermissionTier,
    Tool,
    ToolContext,
    ToolResult,
    ToolSpec,
)

_MAXIMUM_RESULT: Decimal = Decimal("1E+18")
_PRECISION = 28

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}


class CurrentTimeArgs(BaseModel):
    pass


class CalculateArgs(BaseModel):
    expression: str


def _eval_node(node: ast.AST) -> Decimal:
    match node:
        case ast.Constant(value=int(_)) | ast.Constant(value=float(_)):
            return Decimal(str(node.value))
        case ast.BinOp():
            left = _eval_node(node.left)  # type: ignore[attr-defined]
            right = _eval_node(node.right)  # type: ignore[attr-defined]
            op = _BIN_OPS.get(type(node.op))
            if op is None:
                raise ValueError(f"unsupported operator: {type(node.op).__name__}")
            if type(node.op) is ast.Div and right == 0:
                raise ValueError("division by zero")
            return op(left, right)
        case ast.UnaryOp():
            operand = _eval_node(node.operand)  # type: ignore[attr-defined]
            if isinstance(node.op, ast.USub):
                return -operand
            if isinstance(node.op, ast.UAdd):
                return operand
            raise ValueError("unsupported unary operator")
        case _:
            raise ValueError(f"unsupported expression element: {type(node).__name__}")


def evaluate_decimal(expression: str) -> Decimal:
    """Safe arithmetic over Decimal; rejects calls, attrs, exponentiation, etc."""
    clean = expression.strip()
    if not clean:
        raise ValueError("empty expression")
    try:
        tree = ast.parse(clean, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid expression: {exc.msg}") from exc
    if not isinstance(tree, ast.Expression):
        raise ValueError("not an expression")
    with decimal.localcontext() as c:  # precision scoped to evaluation
        c.prec = _PRECISION
        try:
            result = _eval_node(tree.body)
        except (ValueError, TypeError, InvalidOperation, ArithmeticError) as exc:
            raise ValueError(str(exc)) from exc
    if abs(result) > _MAXIMUM_RESULT:
        raise ValueError("result out of range")
    return result


async def _run_calculate(args: CalculateArgs, ctx: ToolContext) -> ToolResult:
    try:
        value = evaluate_decimal(args.expression)
    except ValueError as exc:
        return ToolResult.fail(f"could not evaluate: {exc}")
    return ToolResult(
        facts=[number(value, path="result")],
        narration_template="The result of {expression} is {result}.",
    )


async def _run_current_time(args: CurrentTimeArgs, ctx: ToolContext) -> ToolResult:
    now = datetime.now(UTC)
    return ToolResult(
        facts=[
            date_fact(now, path="iso_utc"),
            number(int(now.timestamp()), path="unix"),
            text_fact(now.strftime("%A"), path="weekday"),
        ],
        narration_template="It is {iso_utc} UTC.",
    )


def _spec(
    name: str,
    description: str,
    args_model: type[BaseModel],
    *,
    orb_state: OrbState,
    tier: PermissionTier,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        domain=Domain.SYSTEM,
        description=description,
        args_model=args_model,
        permission=tier,
        orb_state=orb_state,
    )


get_current_time: Tool = FunctionTool(
    _run_current_time,
    _spec(
        "system.get_current_time",
        "Current UTC time and day of week. Example: user asks 'what time is it' -> returns it.",
        CurrentTimeArgs,
        orb_state=OrbState.CALCULATING,
        tier=PermissionTier.T0,
    ),
)


calculate: Tool = FunctionTool(
    _run_calculate,
    _spec(
        "system.calculate",
        "Evaluates a plain arithmetic expression (+, -, *, /, %, parentheses, decimals). "
        "Example: user asks 'what is 2500 * 0.08' -> expression='2500 * 0.08'.",
        CalculateArgs,
        orb_state=OrbState.CALCULATING,
        tier=PermissionTier.T0,
    ),
)
