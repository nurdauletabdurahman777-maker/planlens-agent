"""Reading plan, contract and catalog documents safely.

Inputs come from three places: a ``.json`` file, a ``.toml`` file, or a mapping
handed to the SDK. Whatever the source, the result is a fresh, JSON-compatible
copy, so a caller mutating their own object afterwards cannot change what was
checked. Problems with the input become diagnostics, not exceptions.

Defences, all applied before evaluation:

* a byte limit, checked before parsing;
* duplicate JSON object keys are rejected (TOML forbids them already);
* only JSON value types are accepted - TOML dates, NaN and infinity are refused;
* nesting depth is bounded, so a hostile document cannot exhaust the stack.

Nothing in an input is ever executed or evaluated as code.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .diagnostics import Diagnostic, Document, Location, SourceSpan

MAX_DOCUMENT_BYTES = 1_000_000
MAX_DEPTH = 32
#: Integers longer than this are always refused as too large, whatever the host
#: process's sys.set_int_max_str_digits() setting. A host that lowers that setting
#: can make shorter integers fail too; either way the result is a ``limit``
#: diagnostic, never an exception.
MAX_INT_DIGITS = 4300
_INT_BOUND = 10**MAX_INT_DIGITS

Source = str | os.PathLike[str] | Mapping[str, Any]


class _DuplicateKey(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


class _NotJsonValue(ValueError):
    """Valid text, but a value outside the JSON value model: reported as ``schema``."""

    def __init__(self, message: str, path: str = "") -> None:
        super().__init__(message)
        self.path = path


class _TooLarge(ValueError):
    """Too deep or a number too long: reported as ``limit``."""

    def __init__(self, message: str, path: str = "") -> None:
        super().__init__(message)
        self.path = path


def _pointer(path: str, key: str | int) -> str:
    token = str(key).replace("~", "~0").replace("/", "~1")
    return f"{path}/{token}"


def _is_unicode_text(text: str) -> bool:
    """False for strings holding unpaired UTF-16 surrogates (e.g. a JSON "\\ud800"
    escape). Such a string is not Unicode text and has no UTF-8 encoding."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


@dataclass(frozen=True)
class Loaded:
    """A document read from any source, plus where it came from."""

    data: dict[str, Any] | None
    origin: str
    diagnostics: tuple[Diagnostic, ...]
    # Absolute path of the file read, fixed at load time, because a relative ``origin``
    # means nothing once the working directory changes. None for a mapping or a file
    # that does not exist. It says where the document was read, not that it is unchanged.
    source: str | None = None

    @property
    def ok(self) -> bool:
        return self.data is not None and not self.diagnostics


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateKey(key)
        out[key] = value
    return out


def _reject_constant(token: str) -> Any:
    raise _NotJsonValue(f"non-finite number {token} is not allowed")


def _parse_int(token: str) -> int:
    if len(token.lstrip("-")) > MAX_INT_DIGITS:
        raise _TooLarge(f"an integer has more than {MAX_INT_DIGITS} digits")
    return int(token)


def parse_json_document(text: str, max_depth: int) -> Any:
    """Parse untrusted JSON under the same rules as a document, at a chosen depth limit.

    For files that wrap documents, such as saved runs. Raises ValueError with a reason.
    """
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
            parse_int=_parse_int,
        )
    except _DuplicateKey as exc:
        raise ValueError(f"duplicate key {exc.key!r}") from None
    except RecursionError:
        raise ValueError(f"nesting deeper than {max_depth} levels") from None
    _check_json_value(value, limit=max_depth)
    return value


def _check_json_value(value: Any, depth: int = 0, path: str = "", limit: int = MAX_DEPTH) -> None:
    """Accept only the JSON value model of Unicode text, and bound nesting depth and
    integer size. ``path`` is the JSON pointer of ``value``, used in the finding."""
    if depth > limit:
        raise _TooLarge(f"nesting deeper than {limit} levels", path)
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, str):
        if not _is_unicode_text(value):
            raise _NotJsonValue(f"a string {_SURROGATE}", path)
        return
    if isinstance(value, int):
        if abs(value) >= _INT_BOUND:
            raise _TooLarge(f"an integer has more than {MAX_INT_DIGITS} digits", path)
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _NotJsonValue("non-finite number is not allowed", path)
        return
    if isinstance(value, (list, tuple)):
        for i, item in enumerate(value):
            _check_json_value(item, depth + 1, _pointer(path, i), limit)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise _NotJsonValue(f"object key {key!r} is not a string", path)
            if not _is_unicode_text(key):
                raise _NotJsonValue(f"an object key {_SURROGATE}", path)
            _check_json_value(item, depth + 1, _pointer(path, key), limit)
        return
    raise _NotJsonValue(f"value of type {type(value).__name__} is not a JSON value", path)


_SURROGATE = (
    "contains an unpaired UTF-16 surrogate (such as a \\ud800 escape), which is not "
    "Unicode text; it is refused rather than replaced, because replacing it would "
    "change the value"
)


def canonical_bytes(value: Any) -> bytes:
    """Deterministic encoding used for every digest: sorted keys, no spaces, UTF-8."""
    return json.dumps(
        _thaw(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    """SHA-256 of the canonical encoding. Identifies content; proves nothing else."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _thaw(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(v) for v in value]
    return value


def freeze(value: Any) -> Any:
    """Read-only deep view: mappings become MappingProxyType, lists become tuples."""
    if isinstance(value, Mapping):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value: Any) -> Any:
    """Mutable deep copy of a frozen value."""
    return _thaw(value)


def _err(
    document: Document,
    rule: str,
    message: str,
    *,
    span: SourceSpan | None = None,
    observed: Any = None,
    suggestion: str = "",
    path: str = "",
) -> Diagnostic:
    return Diagnostic(
        f"{document}/{rule}",
        message,
        Location(document, path, source_span=span),
        observed=observed,
        suggestion=suggestion,
    )


def load(source: Source, document: Document) -> Loaded:
    """Read ``source`` into a fresh JSON-compatible dict, or explain why not."""
    if isinstance(source, Mapping):
        return _load_mapping(source, document)
    if isinstance(source, (str, os.PathLike)):
        return _load_path(Path(source), document)
    raise TypeError(f"{document} must be a path or a mapping, got {type(source).__name__}")


def _load_mapping(source: Mapping[str, Any], document: Document) -> Loaded:
    origin = f"<{document} mapping>"
    try:
        _check_json_value(source)
        raw = canonical_bytes(source)
    except _TooLarge as exc:
        return Loaded(
            None,
            origin,
            (_err(document, "limit", f"{document} is too large: {exc}", path=exc.path),),
        )
    except _NotJsonValue as exc:
        return Loaded(
            None,
            origin,
            (_err(document, "schema", f"{document} is not JSON-compatible: {exc}", path=exc.path),),
        )
    except (TypeError, ValueError) as exc:
        return Loaded(
            None, origin, (_err(document, "schema", f"{document} is not JSON-compatible: {exc}"),)
        )
    if len(raw) > MAX_DOCUMENT_BYTES:
        return Loaded(
            None,
            origin,
            (
                _err(
                    document,
                    "limit",
                    f"{document} is {len(raw)} bytes; the limit is {MAX_DOCUMENT_BYTES}",
                    observed=len(raw),
                ),
            ),
        )
    data = json.loads(raw)
    return Loaded(data, origin, ())


def _load_path(path: Path, document: Document) -> Loaded:
    return replace(_read_path(path, document), source=_source_of(path))


def _source_of(path: Path) -> str | None:
    """The file's absolute path, or None if that cannot be established.

    Only metadata for reports, so a failure here never stops a check and never
    produces a guessed location. Reading errors are reported by ``_read_path``.
    """
    try:
        return str(path.resolve()) if path.is_file() else None
    except (OSError, ValueError, RuntimeError):
        return None


def _read_path(path: Path, document: Document) -> Loaded:
    origin = str(path)
    try:
        if not path.is_file():
            return Loaded(
                None,
                origin,
                (
                    _err(
                        document,
                        "unreadable",
                        f"{document} file not found: {origin}",
                        suggestion="Check the path; paths are resolved from the current directory.",
                    ),
                ),
            )
        size = path.stat().st_size
        if size > MAX_DOCUMENT_BYTES:
            return Loaded(
                None,
                origin,
                (
                    _err(
                        document,
                        "limit",
                        f"{document} file is {size} bytes; the limit is {MAX_DOCUMENT_BYTES}",
                        observed=size,
                    ),
                ),
            )
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return Loaded(
            None, origin, (_err(document, "unreadable", f"{document} file is not UTF-8: {origin}"),)
        )
    except OSError as exc:
        return Loaded(
            None,
            origin,
            (_err(document, "unreadable", f"cannot read {document} file: {exc.strerror}"),),
        )

    suffix = path.suffix.lower()
    if suffix == ".json":
        return _parse_json(text, origin, document)
    if suffix == ".toml":
        return _parse_toml(text, origin, document)
    return Loaded(
        None,
        origin,
        (
            _err(
                document,
                "unreadable",
                f"unsupported file type {suffix or '(none)'!r}; use .json or .toml",
            ),
        ),
    )


def _parse_json(text: str, origin: str, document: Document) -> Loaded:
    try:
        data = json.loads(
            text,
            object_pairs_hook=_reject_duplicates,
            parse_constant=_reject_constant,
            parse_int=_parse_int,
        )
    except _DuplicateKey as exc:
        return Loaded(
            None,
            origin,
            (
                _err(
                    document,
                    "malformed",
                    f"duplicate key {exc.key!r} in a JSON object",
                    observed=exc.key,
                    suggestion="Remove the repeated key so the document has one meaning.",
                ),
            ),
        )
    except json.JSONDecodeError as exc:
        return Loaded(
            None,
            origin,
            (
                _err(
                    document,
                    "malformed",
                    f"invalid JSON: {exc.msg}",
                    span=SourceSpan(exc.lineno, exc.colno),
                ),
            ),
        )
    except _NotJsonValue as exc:  # NaN / Infinity tokens are not JSON
        return Loaded(None, origin, (_err(document, "malformed", f"invalid JSON: {exc}"),))
    except (_TooLarge, RecursionError, ValueError) as exc:
        # _TooLarge: our integer hook. RecursionError: nesting deeper than the
        # parser can follow. ValueError: CPython's own int/str limit, if the host
        # process set it lower than MAX_INT_DIGITS.
        return Loaded(None, origin, (_err(document, "limit", _too_large(exc)),))
    return _finish(data, origin, document)


def _too_large(exc: BaseException) -> str:
    if isinstance(exc, _TooLarge):
        return f"document is too large: {exc}"
    if isinstance(exc, RecursionError):
        return f"document is too large: nesting deeper than {MAX_DEPTH} levels"
    return "document is too large: a number exceeds the integer size limit"


def _parse_toml(text: str, origin: str, document: Document) -> Loaded:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        line, col = getattr(exc, "lineno", None), getattr(exc, "colno", None)
        span = SourceSpan(line, col) if isinstance(line, int) and isinstance(col, int) else None
        msg = getattr(exc, "msg", None) or str(exc)
        return Loaded(
            None, origin, (_err(document, "malformed", f"invalid TOML: {msg}", span=span),)
        )
    except (RecursionError, ValueError) as exc:  # tomllib converts integers with int()
        return Loaded(None, origin, (_err(document, "limit", _too_large(exc)),))
    return _finish(data, origin, document)


def _finish(data: Any, origin: str, document: Document) -> Loaded:
    if not isinstance(data, dict):
        return Loaded(
            None,
            origin,
            (_err(document, "schema", f"top level must be an object, got {type(data).__name__}"),),
        )
    try:
        _check_json_value(data)
    except _TooLarge as exc:
        return Loaded(
            None, origin, (_err(document, "limit", f"document is too large: {exc}", path=exc.path),)
        )
    except _NotJsonValue as exc:
        return Loaded(
            None,
            origin,
            (_err(document, "schema", f"{document} is not JSON-compatible: {exc}", path=exc.path),),
        )
    return Loaded(data, origin, ())
