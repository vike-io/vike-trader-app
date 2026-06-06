"""Optimization-surface data-prep.

Pivots optimization trials over two chosen parameter axes into a row-major
Z-grid ready for a 3D/2D optimization-surface render, fixing the other params.
The grid follows the heatmap convention: ``z[y_index][x_index]`` (rows = y
values, cols = x values). A cell is ``None`` when no trial matches that
``(x, y)`` pair under the ``fixed`` constraints. Pure/Qt-free; rendering is a
separate UI concern.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Surface:
    """A 2D Z-grid over two param axes plus the values that produced it.

    ``z`` is row-major: ``z[y_index][x_index]``; a cell is ``None`` when no
    trial matches that ``(values_x[x_index], values_y[y_index])`` pair under
    ``fixed``.
    """

    param_x: str
    values_x: list
    param_y: str
    values_y: list
    z: list  # z[y_index][x_index]; None when no matching trial
    fixed: dict = field(default_factory=dict)


def _params_and_score(item):
    """Duck-type one trial into ``(params, score)``.

    Accepts tester ``OptimizeTrial`` / analysis ``OptimizeResult`` (both expose
    ``params`` and ``score`` attributes) as well as plain ``(params, score)``
    2-tuples.
    """
    params = getattr(item, "params", None)
    if params is not None:
        return params, getattr(item, "score")
    params, score = item  # plain 2-tuple
    return params, score


def _matches_fixed(params, fixed):
    return all(params.get(key) == val for key, val in fixed.items())


def surface_from_trials(trials, param_x, param_y, *, fixed=None):
    """Pivot ``trials`` into a :class:`Surface` over ``param_x`` x ``param_y``.

    ``values_x`` / ``values_y`` are the sorted distinct values of the two axes
    across trials matching ``fixed`` on all its keys. For each ``(x, y)`` cell,
    the MAX score among matching trials is used; ``None`` when none match.
    """
    fixed = dict(fixed or {})
    pairs = [_params_and_score(item) for item in trials]
    matched = [(p, s) for (p, s) in pairs if _matches_fixed(p, fixed)]

    values_x = sorted({p[param_x] for (p, _) in matched})
    values_y = sorted({p[param_y] for (p, _) in matched})

    best = {}
    for params, score in matched:
        key = (params[param_x], params[param_y])
        if key not in best or score > best[key]:
            best[key] = score

    z = [[best.get((x, y)) for x in values_x] for y in values_y]

    return Surface(
        param_x=param_x,
        values_x=values_x,
        param_y=param_y,
        values_y=values_y,
        z=z,
        fixed=fixed,
    )


def best_axes(param_grid):
    """Pick two axes to surface over from a ``{param: [values]}`` grid.

    Returns the first two keys (insertion order) whose value-list has length
    >= 2; falls back to the first two keys otherwise. Raises ``ValueError`` if
    the grid has fewer than two params.
    """
    keys = list(param_grid)
    if len(keys) < 2:
        raise ValueError("best_axes needs at least two params")
    multi = [k for k in keys if len(param_grid[k]) >= 2]
    if len(multi) >= 2:
        return multi[0], multi[1]
    return keys[0], keys[1]
