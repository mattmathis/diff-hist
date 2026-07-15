"""Build ipywidgets controls from a dashboard's template variables.

Handles three variable kinds present in these dashboards:

* ``custom`` / ``constant`` — fixed option lists (or a single constant value);
  rendered as a ``Dropdown`` (or kept as a hidden fixed value).
* ``query`` — options come from a BigQuery query (``query.rawSql``); rendered as
  a ``Dropdown`` (single) or ``SelectMultiple`` (multi). These are populated by
  running the query, and **re-populated automatically** when any variable they
  depend on changes (chained dropdowns, e.g. ``anchor`` -> ``region`` /
  ``ClientISP``).

The :class:`Controls` object exposes ``.box`` (the UI), ``.widgets`` (name ->
widget), and ``.context()`` (the current value dict for query interpolation).
"""

from __future__ import annotations

import ipywidgets as widgets

from . import query_builder as qb
from . import runtime as rt

# Variables that are hidden in Grafana (hide == 2) are not given a widget; their
# default value is used directly.
_HIDDEN = 2


class CheckboxGroup:
    """A group of ``Checkbox`` widgets that behaves like a multi-select.

    Exposes ``.value`` (tuple of selected values) and ``.observe()`` so it is
    a drop-in replacement for ``SelectMultiple`` in :class:`Controls`.  Use
    ``.widget`` to get the displayable ``VBox``.

    Supports dynamic ``options`` updates (needed for query variables whose
    option lists are refreshed when a parent dropdown changes).
    """

    def __init__(self, options: list[tuple] | None = None, description: str = ""):
        self._callbacks: list = []
        self._checks: dict[str, widgets.Checkbox] = {}
        self._label = widgets.HTML(f"<b>{description}</b>" if description else "")
        self._container = widgets.VBox([])
        self.widget = widgets.VBox(
            ([self._label] if description else []) + [self._container]
        )
        if options:
            self.options = options

    @property
    def options(self) -> list[tuple]:
        return [(cb.description, val) for val, cb in self._checks.items()]

    @options.setter
    def options(self, new_opts: list[tuple]):
        prev = set(self.value)
        self._checks = {}
        for lbl, val in new_opts:
            cb = widgets.Checkbox(
                value=val in prev, description=lbl, indent=False,
                layout=widgets.Layout(width="auto"),
            )
            cb.observe(self._fire, names="value")
            self._checks[val] = cb
        self._container.children = tuple(self._checks.values())

    def _fire(self, _change):
        for fn in self._callbacks:
            fn({"name": "value", "new": self.value})

    @property
    def value(self) -> tuple:
        return tuple(v for v, cb in self._checks.items() if cb.value)

    @value.setter
    def value(self, vals):
        s = set(vals) if vals else set()
        for val, cb in self._checks.items():
            cb.value = val in s

    def observe(self, callback, names="value"):
        self._callbacks.append(callback)


def _v_to_dict(v) -> dict:
    """Convert a ``Variable`` dataclass to the serialized dict format."""
    return {
        "name": v.name, "type": v.type, "label": v.label or "",
        "description": v.description or "", "hide": v.hide, "multi": v.multi,
        "options": [{"text": o.get("text", o.get("value", "")),
                     "value": o.get("value", "")} for o in (v.options or [])],
        "current": {"value": v.current.get("value")} if v.current else {},
        "query_sql": v.query_sql,
    }


def _referenced_vars(sql: str, names: set[str]) -> set[str]:
    """Which of ``names`` does ``sql`` reference (ignoring comments)?"""
    active = qb._strip_sql_comments(sql)
    refs = {n for n, _ in qb._BRACED.findall(active)}
    refs |= set(qb._BARE.findall(active))
    return refs & names


class Controls:
    def __init__(self, variables: list[dict], client, *, presets: dict | None = None,
                 asn_presets: dict | None = None, after: dict | None = None,
                 hgroups: list[list[str]] | None = None):
        """``variables`` is a list of serialized variable dicts as produced by
        :func:`converter.notebook_builder.serialize_variables`.
        Also accepts a ``Dashboard`` object for use outside the notebook context.

        ``asn_presets`` maps variable names to lists of AS number strings.  After
        a chained query populates that variable's options, any option whose value
        starts with a matching AS number is pre-selected (overrides default_select).
        Format: ``{'ClientISP': ['7922', '8030']}``

        ``after`` maps a variable name to an extra widget to splice into the
        layout immediately after that variable's row (e.g. a date-picker row
        placed right after the ``method`` selector).  Ignored if the named
        variable has no widget (hidden/constant).  Format: ``{'method': w}``

        ``hgroups`` is a list of variable-name groups to render side by side on
        one row (an ``HBox``), in the listed order, positioned where the first
        present member would appear.  Members with no widget are skipped; a group
        with fewer than two present members lays out normally.  Example:
        ``[['endDate', 'duration']]``.
        """
        if hasattr(variables, "variables"):
            variables = [_v_to_dict(v) for v in variables.variables]
        self._variables: list[dict] = variables
        self.client = client
        self.presets = presets or {}
        self.asn_presets = asn_presets or {}
        self.after = after or {}
        self.hgroups = hgroups or []
        self.defaults = qb.default_values(variables)
        self.widgets: dict[str, widgets.Widget] = {}
        self._fixed: dict[str, object] = {}  # hidden/constant values, no widget
        self._var_names = {v["name"] for v in variables}
        self._suspend = False  # guard against observer storms during refresh
        self._build()

    # -- context -----------------------------------------------------------
    def context(self) -> dict:
        ctx = dict(self._fixed)
        for name, w in self.widgets.items():
            val = w.value
            ctx[name] = list(val) if isinstance(val, tuple) else val
        return ctx

    def _preset(self, name, fallback):
        """URL/preset override for a variable, falling back to its default."""
        if name in self.presets:
            return self.presets[name]
        return fallback

    # -- build -------------------------------------------------------------
    def _build(self):
        style = {"description_width": "110px"}
        # NB: each widget gets its OWN Layout object.  A shared Layout is mutated
        # in place by the visibility toggles (e.g. _toggle_extra_rows sets
        # .layout.display='none'), which would hide every widget sharing it.
        def layout():
            return widgets.Layout(width="420px")
        query_vars = []
        for v in self._variables:
            name = v["name"]
            if v["hide"] == _HIDDEN or v["type"] in ("constant", "datasource"):
                self._fixed[name] = self.defaults.get(name)
                continue
            label = v.get("label") or name
            desc = v.get("description", "")
            if v["type"] == "query":
                query_vars.append(v)
                # Created empty; options filled in dependency order below.
                if v["multi"]:
                    w = CheckboxGroup(description=label)
                else:
                    w = widgets.Dropdown(description=label, style=style, layout=layout())
                w._var_description = desc
                self.widgets[name] = w
            elif v["type"] == "textbox":
                _expr = v.get("dynamic_default")
                if _expr:
                    from datetime import date, timedelta  # noqa: PLC0415
                    try:
                        _default_val = str(eval(_expr))
                    except Exception:
                        _default_val = str((v.get("current") or {}).get("value", "") or "")
                else:
                    _default_val = str((v.get("current") or {}).get("value", "") or "")
                w = widgets.Text(
                    value=_default_val,
                    description=label,
                    style=style, layout=layout(),
                )
                w._var_description = desc
                self.widgets[name] = w
            else:  # custom (and anything else with an option list)
                opts = [(o.get("text", o.get("value")), o.get("value"))
                        for o in (v.get("options") or [])] or []
                if v.get("multi"):
                    w = CheckboxGroup(opts, description=label)
                else:
                    w = widgets.Dropdown(options=opts or None, description=label,
                                         style=style, layout=layout())
                want = self._preset(name, self.defaults.get(name))
                _set_value(w, want)
                w._var_description = desc
                self.widgets[name] = w

        # Populate query dropdowns and wire chaining.
        self._query_deps = {
            v["name"]: _referenced_vars(v.get("query_sql") or "", self._var_names)
            for v in query_vars
        }
        for v in query_vars:
            self._refresh_query_var(v["name"], set_default=True)
        for v in query_vars:
            deps = self._query_deps[v["name"]]
            for dep in deps:
                if dep in self.widgets:
                    self.widgets[dep].observe(
                        self._make_observer(v["name"]), names="value")

        self.box = self._layout_box()

    def _make_observer(self, var_name):
        def _cb(_change):
            if self._suspend:
                return
            self._refresh_query_var(var_name, set_default=False)
        return _cb

    def _refresh_query_var(self, name, *, set_default: bool):
        v = next(v for v in self._variables if v["name"] == name)
        sql = qb.interpolate(v.get("query_sql") or "", self.context())
        try:
            opts = rt.variable_options(self.client, sql)
        except Exception as exc:  # keep the UI alive on transient query errors
            print(f"[{name}] option query failed: {exc}")
            return
        w = self.widgets[name]
        self._suspend = True
        try:
            prev = w.value
            w.options = opts
            values = [val for _, val in opts]
            # ASN preset: select options whose AS number (first space-delimited
            # token) matches a requested AS number.  Takes priority over
            # default_select and anchor-change retention.
            if name in self.asn_presets:
                _asns = set(str(a) for a in self.asn_presets[name])
                _matches = [val for val in values if val.split(" ")[0] in _asns]
                if _matches:
                    _set_value(w, _matches, valid=values)
                    return
            if set_default:
                ds = v.get("default_select")
                if ds == "all":
                    want = values
                elif ds == "half":
                    want = values[:max(1, len(values) // 2)]
                else:
                    want = self._preset(name, self.defaults.get(name))
                _set_value(w, want, valid=values)
            else:
                ds = v.get("default_select")
                if ds:
                    # If none of the previous values survive the anchor change,
                    # re-apply the default_select policy rather than leaving
                    # the widget with an empty selection.
                    prev_list = list(prev) if isinstance(prev, tuple) else ([prev] if prev else [])
                    valid_set = set(values)
                    if not any(x in valid_set for x in prev_list):
                        want = values if ds == "all" else values[:max(1, len(values) // 2)]
                        _set_value(w, want, valid=values)
                        return
                _set_value(w, prev, valid=values)
        finally:
            self._suspend = False

    def _layout_box(self):
        rows = [widgets.HTML("<b>Dashboard controls</b>")]
        # Horizontal groups: render listed vars side by side at the first present
        # member's position; the others are skipped in the normal flow.
        first_of, secondary = {}, set()
        for g in self.hgroups:
            present = [n for n in g if n in self.widgets]
            if len(present) >= 2:
                first_of[present[0]] = present
                secondary.update(present[1:])
        for name, w in self.widgets.items():
            if name in secondary:
                continue
            if name in first_of:
                rows.append(widgets.HBox(
                    [getattr(self.widgets[n], "widget", self.widgets[n])
                     for n in first_of[name]]))
            else:
                rows.append(getattr(w, "widget", w))
            if name in self.after:
                rows.append(self.after[name])
        return widgets.VBox(rows)


def _set_value(w, want, valid=None):
    """Best-effort assignment that tolerates missing/invalid selections."""
    is_multi = isinstance(w, (widgets.SelectMultiple, CheckboxGroup))
    if isinstance(w, CheckboxGroup):
        # CheckboxGroup has a fixed option set; filter want to valid values.
        if valid is not None:
            valid_set = set(valid)
            wants = want if isinstance(want, (list, tuple)) else ([want] if want else [])
            w.value = tuple(x for x in wants if x in valid_set)
        else:
            w.value = tuple(want) if isinstance(want, (list, tuple)) else ((want,) if want else ())
        return
    allowed = set(valid) if valid is not None else {
        val for _, val in (w.options or [])
    }
    if is_multi:
        wants = want if isinstance(want, (list, tuple)) else [want]
        keep = tuple(x for x in wants if not allowed or x in allowed)
        w.value = keep
    else:
        if isinstance(want, (list, tuple)):
            want = want[0] if want else None
        if not allowed or want in allowed:
            if want is not None:
                w.value = want
        elif w.options:
            w.value = w.options[0][1]
