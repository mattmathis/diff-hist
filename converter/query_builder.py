"""Resolve Grafana ``rawSql`` variable references against concrete values.

Grafana dashboards reference template variables inside the BigQuery SQL using
several syntaxes; this module turns a ``rawSql`` template plus a value context
into runnable BigQuery Standard SQL.

Supported references
--------------------
* ``$name`` / ``${name}`` — plain substitution. Multi-value lists are joined
  with commas (Grafana's default), though these dashboards only use multi-value
  variables via the ``:regex`` format.
* ``${name:regex}`` — regex format: each value is regex-escaped; multiple values
  become ``(v1|v2|v3)``. Used to splice multi-select dropdowns into
  ``REGEXP_CONTAINS`` / ``^(...)`` patterns.
* ``${name:raw}`` — raw value(s), no escaping (list joined with ``,``).
* ``${__from:date:iso}`` / ``${__to:date:iso}`` — the dashboard time range as an
  ISO-8601 timestamp. The SQL further extracts ``YYYY-MM-DD`` from it via
  ``REGEXP_EXTRACT``.

A *value context* is a ``dict`` mapping variable name -> ``str`` (scalar) or
``list[str]`` (multi-value). The time range is supplied separately as
``datetime`` objects.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone


class Raw:
    """A pre-formatted value that passes through ``interpolate`` unchanged.

    Use this when you have already built the exact string that should appear in
    the SQL (e.g. a pre-computed regex) and must not be escaped or reformatted
    by :func:`format_regex` or any other formatter.

    Example::

        ctx["ClientISP"] = Raw("18881 |28573 ")   # ASN regex, no escaping wanted
    """

    def __init__(self, value: str):
        self.value = str(value)

    def __str__(self) -> str:
        return self.value

# ${ name [:fmt[:fmt...]] }
_BRACED = re.compile(r"\$\{([^}:]+)(?::([^}]+))?\}")
# $name  (bare). Restricted to identifier chars so it never eats ${...}.
_BARE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


# Grafana's regex formatter escapes only true regex metacharacters; notably it
# does NOT escape spaces (unlike Python's re.escape, whose ``\ `` is an illegal
# escape sequence inside a BigQuery string literal). Mirror Grafana exactly:
# https://github.com/grafana/grafana -> kbn.regexpEscape / escapeRegExp.
_GRAFANA_REGEX_META = re.compile(r"[\\^$.*+?()\[\]{}|]")


def _grafana_regex_escape(value: str) -> str:
    return _GRAFANA_REGEX_META.sub(lambda m: "\\" + m.group(0), value)


def format_regex(value) -> str:
    """Grafana ``:regex`` format. Single value -> escaped; multi -> ``(a|b)``."""
    vals = _as_list(value)
    if not vals:
        return ""
    escaped = [_grafana_regex_escape(v) for v in vals]
    if len(escaped) == 1:
        return escaped[0]
    return "(" + "|".join(escaped) + ")"


def format_raw(value) -> str:
    return ",".join(_as_list(value))


def format_plain(value) -> str:
    """Plain ``${var}`` / ``$var`` substitution (multi joined with commas)."""
    return ",".join(_as_list(value))


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def interpolate(
    raw_sql: str,
    values: dict,
    *,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    missing: str = "keep",
) -> str:
    """Interpolate ``raw_sql`` using ``values`` and an optional time range.

    ``missing`` controls unknown variables: ``"error"`` raises, ``"keep"`` leaves
    the placeholder untouched, ``"blank"`` replaces with an empty string. The
    default is ``"keep"`` because dashboards frequently reference variables only
    on commented-out lines (e.g. ``-- FROM `${dataset}.foo`("${site:regex}")``),
    which must not break interpolation. Use :func:`active_unresolved` to verify
    no *executable* placeholder was left behind.
    """

    def resolve_time(name: str) -> str | None:
        dt = from_dt if name == "__from" else to_dt if name == "__to" else None
        if dt is None:
            if name in ("__from", "__to"):
                raise ValueError(f"time range not supplied for ${{{name}}}")
            return None
        return _iso(dt)

    def on_missing(token: str) -> str:
        if missing == "keep":
            return token
        if missing == "blank":
            return ""
        raise KeyError(f"no value for variable in {token!r}")

    def braced(m: re.Match) -> str:
        name = m.group(1).strip()
        fmt = (m.group(2) or "").strip()
        # Time macros: ${__from:date:iso}, ${__to:date:iso}
        if name in ("__from", "__to"):
            return resolve_time(name) or on_missing(m.group(0))
        if name not in values:
            return on_missing(m.group(0))
        val = values[name]
        if isinstance(val, Raw):
            return val.value  # pass through unchanged, ignoring any format modifier
        first_fmt = fmt.split(":")[0] if fmt else ""
        if first_fmt == "regex":
            return format_regex(val)
        if first_fmt == "raw":
            return format_raw(val)
        return format_plain(val)

    def bare(m: re.Match) -> str:
        name = m.group(1)
        if name not in values:
            return on_missing(m.group(0))
        return format_plain(values[name])

    out = _BRACED.sub(braced, raw_sql)
    out = _BARE.sub(bare, out)
    return out


def _strip_sql_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return "\n".join(re.sub(r"(--|#).*$", "", line) for line in sql.splitlines())


def active_unresolved(sql: str) -> list[str]:
    """Return ``${...}``/``$name`` placeholders left in *executable* SQL.

    Comments are ignored. A non-empty result means interpolation missed a
    variable that BigQuery would choke on.
    """
    active = _strip_sql_comments(sql)
    found = _BRACED.findall(active)
    found_bare = _BARE.findall(active)
    tokens = [f"${{{n}{(':'+f) if f else ''}}}" for n, f in found]
    tokens += [f"${n}" for n in found_bare]
    return tokens


def default_values(variables) -> dict:
    """Extract a value context from a variable list (or a Dashboard object).

    Accepts either a ``list[dict]`` (as serialized into notebooks) or a
    ``Dashboard`` object (for use during conversion). Uses each variable's
    ``current.value`` (falling back to the first option or the constant string).
    Multi-value variables yield a list; others a scalar string.
    """
    if hasattr(variables, "variables"):
        variables = variables.variables
    ctx: dict = {}
    for v in variables:
        if isinstance(v, dict):
            name = v["name"]
            cur = (v.get("current") or {}).get("value")
            is_const = v.get("type") == "constant"
            query_sql = v.get("query_sql")
            opts = v.get("options") or []
            multi = v.get("multi", False)
        else:
            name = v.name
            cur = v.current.get("value") if v.current else None
            is_const = v.type == "constant"
            query_sql = v.query_sql
            opts = v.options or []
            multi = v.multi
        if cur is None:
            if is_const:
                cur = query_sql
            elif opts:
                first = opts[0]
                cur = first.get("value") if isinstance(first, dict) else first
            else:
                cur = ""
        if multi and not isinstance(cur, (list, tuple)):
            cur = [cur]
        if not multi and isinstance(cur, (list, tuple)):
            cur = cur[0] if cur else ""
        ctx[name] = cur
    return ctx
