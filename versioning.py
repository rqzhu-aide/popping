"""Authoritative semantic versions and compatibility rules for Popping.

Stored versions never include the public ``v`` prefix.  Database and data
compatibility is intentionally defined by the numeric major/minor pair: patch
releases may change the website without changing the database contract.
"""

import re


APP_VERSION = "1.2.1"
BASELINE_SCHEMA_VERSION = "1.0.0"
SCHEMA_VERSION = "1.2.0"
DATABASE_SCHEMA_VERSION = SCHEMA_VERSION
EXPORT_FORMAT_VERSION = "1.2.0"
BASELINE_DATA_VERSION = "1.0.0"
SCHEMA_VERSION_HISTORY = (
    BASELINE_SCHEMA_VERSION,
    "1.1.0",
    SCHEMA_VERSION,
)

_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)


def parse_version(value):
    """Return a strict ``(major, minor, patch)`` tuple.

    Internal versions must be plain canonical semantic versions.  In
    particular, ``v1.0.0``, whitespace, signs, and leading zeroes are rejected
    so stored provenance has one unambiguous representation.
    """
    if not isinstance(value, str):
        raise ValueError("Version must be a string")
    match = _SEMVER_RE.fullmatch(value)
    if not match:
        raise ValueError(f"Invalid semantic version: {value!r}")
    return tuple(int(part) for part in match.groups())


def public_version(value=APP_VERSION):
    """Return the public display form, such as ``v1.0.0``."""
    parse_version(value)
    return f"v{value}"


def compatible_series(value):
    """Return the numeric ``(major, minor)`` compatibility series."""
    return parse_version(value)[:2]


def compatibility_label(value):
    """Return the public compatibility label, such as ``v1.0.x``."""
    major, minor = compatible_series(value)
    return f"v{major}.{minor}.x"


def versions_compatible(left, right):
    """Return whether two versions share the same major/minor contract."""
    return compatible_series(left) == compatible_series(right)


def sqlite_versions_compatible(left, right):
    """SQLite-safe compatibility predicate returning integer 1 or 0.

    SQLite user-defined functions must not turn malformed provenance into a
    query failure.  Invalid, missing, or non-text versions therefore fail
    closed as incompatible and can be routed to a legacy export.
    """
    try:
        return int(versions_compatible(left, right))
    except (TypeError, ValueError):
        return 0


for _name, _version in (
    ("APP_VERSION", APP_VERSION),
    ("BASELINE_SCHEMA_VERSION", BASELINE_SCHEMA_VERSION),
    ("SCHEMA_VERSION", SCHEMA_VERSION),
    ("EXPORT_FORMAT_VERSION", EXPORT_FORMAT_VERSION),
    ("BASELINE_DATA_VERSION", BASELINE_DATA_VERSION),
):
    parse_version(_version)

if parse_version(SCHEMA_VERSION)[2] != 0:
    raise RuntimeError("Database schema versions must have a zero patch number")
_parsed_schema_history = tuple(
    parse_version(version) for version in SCHEMA_VERSION_HISTORY
)
if not _parsed_schema_history or SCHEMA_VERSION_HISTORY[-1] != SCHEMA_VERSION:
    raise RuntimeError("Schema version history must end at SCHEMA_VERSION")
if any(version[2] != 0 for version in _parsed_schema_history):
    raise RuntimeError("Schema version history cannot contain patch releases")
if any(
    current <= previous
    for previous, current in zip(
        _parsed_schema_history, _parsed_schema_history[1:]
    )
):
    raise RuntimeError("Schema version history must be strictly increasing")
if not versions_compatible(APP_VERSION, SCHEMA_VERSION):
    raise RuntimeError(
        "Application and database schema versions must share major/minor"
    )
