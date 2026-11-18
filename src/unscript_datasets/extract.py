"""Find ``load_dataset`` calls in ``.py`` files and ``.ipynb`` notebooks.

Only the standard library ``ast`` module is used.  Every call is reported as a
:class:`CallSite` that remembers the exact source span of the call so that
``rewrite.py`` can replace just that span.

Columns reported by ``ast`` are UTF-8 byte offsets, so all span arithmetic here
is done on ``bytes``.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# Builders that ship inside ``datasets`` itself (they never need a Hub script).
PACKAGED_BUILDERS = frozenset(
    {
        "json", "csv", "parquet", "text", "arrow", "pandas", "imagefolder",
        "audiofolder", "videofolder", "webdataset", "xml", "sql", "hdf5", "eval",
        "generator", "folder",
    }
)

# Positional order of the first parameters of ``datasets.load_dataset``.
POSITIONAL_PARAMS = ("path", "name", "data_dir", "data_files", "split")

DEFAULT_EXCLUDES = (".venv", "venv", ".git", "node_modules", "__pycache__", "site-packages", ".tox", ".ipynb_checkpoints")

_NEWLINE = re.compile(rb"\r\n|\r|\n")


@dataclass
class CallSite:
    """One ``load_dataset(...)`` call and everything needed to judge/rewrite it."""

    file: str                       # path as shown to the user
    line: int                       # 1-based line (inside the cell for notebooks)
    col: int                        # 1-based column
    cell: int | None                # notebook code-cell index, else None
    kind: str                       # "HUB" | "DYNAMIC" | "SKIP"
    reason: str                     # reason code for DYNAMIC / SKIP, "" for HUB
    func_src: str                   # source text of the called expression, e.g. "ld"
    dataset: str | None = None      # literal path argument
    config: str | None = None
    split: str | None = None
    revision: str | None = None
    split_dynamic: bool = False
    # (name or None for positional, source text) in original order.
    args: list[tuple[str | None, str]] = field(default_factory=list)
    has_comment: bool = False
    has_star: bool = False
    # byte span inside ``unit_text`` (the file or the notebook cell)
    start: int = 0
    end: int = 0
    unit_text: str = ""

    @property
    def location(self) -> str:
        if self.cell is not None:
            return f"{self.file}#cell{self.cell}:{self.line}"
        return f"{self.file}:{self.line}"

    @property
    def original(self) -> str:
        return self.unit_text.encode("utf-8")[self.start:self.end].decode("utf-8")

    def has_arg(self, name: str) -> bool:
        if name in POSITIONAL_PARAMS:
            index = POSITIONAL_PARAMS.index(name)
            if sum(1 for key, _ in self.args if key is None) > index:
                return True
        return any(key == name for key, _ in self.args)


@dataclass
class ScanResult:
    calls: list[CallSite] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # "code=detail"
    files: int = 0


def split_lines(data: bytes) -> list[bytes]:
    """Split like Python's tokenizer does (``\\n``, ``\\r\\n`` and ``\\r``)."""
    lines: list[bytes] = []
    pos = 0
    for match in _NEWLINE.finditer(data):
        lines.append(data[pos:match.end()])
        pos = match.end()
    if pos < len(data):
        lines.append(data[pos:])
    return lines


def mask_magics(text: str) -> str:
    """Blank out ``!`` / ``%`` lines of a notebook cell, keeping line numbers."""
    out: list[str] = []
    for raw in split_lines(text.encode("utf-8")):
        line = raw.decode("utf-8")
        stripped = line.lstrip()
        if stripped.startswith(("!", "%")):
            ending = line[len(line.rstrip("\r\n")):]
            out.append(ending)
        else:
            out.append(line)
    return "".join(out)


def _segment(lines: list[bytes], node: ast.AST) -> tuple[int, int]:
    """Absolute byte span of ``node`` in the source made of ``lines``."""
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    start = offsets[node.lineno - 1] + node.col_offset  # type: ignore[attr-defined]
    end = offsets[node.end_lineno - 1] + node.end_col_offset  # type: ignore[attr-defined]
    return start, end


def _literal(node: ast.AST) -> str | None | bool:
    """Return the string value, or ``False`` when it is not a string literal."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        if node.value is None:
            return None
    return False


def _find_aliases(tree: ast.AST) -> tuple[set[str], set[str]]:
    funcs: set[str] = set()
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "datasets" and node.level == 0:
            for alias in node.names:
                if alias.name == "load_dataset":
                    funcs.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "datasets":
                    modules.add(alias.asname or alias.name)
    return funcs, modules


def _is_local(path: str, roots: list[Path]) -> bool:
    if path.startswith(("./", "../", "/", "~", ".\\")) or path in {".", ".."}:
        return True
    return any((root / path).exists() for root in roots)


def extract_calls(
    text: str,
    file: str,
    cell: int | None = None,
    roots: list[Path] | None = None,
    parse_text: str | None = None,
    known_aliases: tuple[set[str], set[str]] | None = None,
) -> list[CallSite]:
    """Extract calls from one Python source unit.

    ``parse_text`` is the text that is parsed (notebook cells have their magic
    lines blanked); spans are always reported against ``text``.  Both texts must
    have identical line structure.  Raises ``SyntaxError`` for unparsable input.
    """
    roots = roots or []
    tree = ast.parse(parse_text if parse_text is not None else text)
    funcs, modules = _find_aliases(tree)
    if known_aliases:  # notebooks share imports between cells
        funcs, modules = funcs | known_aliases[0], modules | known_aliases[1]
    if not funcs and not modules:
        return []
    raw = text.encode("utf-8")
    lines = split_lines(raw)
    sites: list[CallSite] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        hit = (isinstance(func, ast.Name) and func.id in funcs) or (
            isinstance(func, ast.Attribute)
            and func.attr == "load_dataset"
            and isinstance(func.value, ast.Name)
            and func.value.id in modules
        )
        if not hit:
            continue
        sites.append(_build_site(node, raw, lines, file, cell, roots, text))
    sites.sort(key=lambda s: (s.line, s.col))
    return sites


def _build_site(
    node: ast.Call, raw: bytes, lines: list[bytes], file: str, cell: int | None,
    roots: list[Path], text: str,
) -> CallSite:
    start, end = _segment(lines, node)
    func_start, func_end = _segment(lines, node.func)
    site = CallSite(
        file=file, line=node.lineno, col=len(raw[start - node.col_offset:start].decode("utf-8")) + 1,
        cell=cell, kind="HUB", reason="",
        func_src=raw[func_start:func_end].decode("utf-8"),
        start=start, end=end, unit_text=text,
    )
    values: dict[str, ast.AST] = {}
    # Positional arguments first, then keywords, in source order.
    for index, arg in enumerate(node.args):
        if isinstance(arg, ast.Starred):
            site.has_star = True
            site.args.append((None, ast.get_source_segment(text, arg) or ""))
            continue
        a_start, a_end = _segment(lines, arg)
        site.args.append((None, raw[a_start:a_end].decode("utf-8")))
        if index < len(POSITIONAL_PARAMS):
            values.setdefault(POSITIONAL_PARAMS[index], arg)
    for kw in node.keywords:
        v_start, v_end = _segment(lines, kw.value)
        value_src = raw[v_start:v_end].decode("utf-8")
        if kw.arg is None:
            site.has_star = True
            site.args.append((None, "**" + value_src))
            continue
        site.args.append((kw.arg, value_src))
        values[kw.arg] = kw.value
    site.has_comment = b"#" in raw[start:end] and _has_comment(raw[start:end])

    path_node = values.get("path")
    if path_node is None:
        site.kind, site.reason = "DYNAMIC", "dynamic_arg"
        return site
    path = _literal(path_node)
    if not isinstance(path, str):
        site.kind, site.reason = "DYNAMIC", "dynamic_arg"
        return site
    site.dataset = path
    config = _literal(values["name"]) if "name" in values else None
    revision = _literal(values["revision"]) if "revision" in values else None
    if config is False or revision is False:
        site.kind, site.reason = "DYNAMIC", "dynamic_arg"
        return site
    site.config, site.revision = config, revision
    if "split" in values:
        split = _literal(values["split"])
        if isinstance(split, str):
            site.split = split
        else:
            site.split_dynamic = True
    if path in PACKAGED_BUILDERS:
        site.kind, site.reason = "SKIP", "packaged_builder"
    elif _is_local(path, roots):
        site.kind, site.reason = "SKIP", "local_path"
    return site


def _has_comment(chunk: bytes) -> bool:
    """True if the call text contains a real ``#`` comment (not one in a string)."""
    import io
    import tokenize

    try:
        for tok in tokenize.generate_tokens(io.StringIO(chunk.decode("utf-8")).readline):
            if tok.type == tokenize.COMMENT:
                return True
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # A cut-out call can look unbalanced to the tokenizer; fall back to "maybe".
        return True
    return False


def _read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def load_notebook(path: Path) -> dict:
    return json.loads(_read_text(path))


def cell_source(cell: dict) -> str:
    source = cell.get("source", "")
    return "".join(source) if isinstance(source, list) else str(source)


def scan_file(path: Path, display: str, roots: list[Path], result: ScanResult) -> None:
    """Scan one file, adding calls and warnings to ``result``."""
    result.files += 1
    try:
        if path.suffix == ".ipynb":
            notebook = load_notebook(path)
            cells = [(i, c) for i, c in enumerate(notebook.get("cells", [])) if c.get("cell_type") == "code"]
            shared: tuple[set[str], set[str]] = (set(), set())
            for _, cell in cells:
                try:
                    funcs, modules = _find_aliases(ast.parse(mask_magics(cell_source(cell))))
                except SyntaxError:
                    continue
                shared[0].update(funcs)
                shared[1].update(modules)
            for index, cell in cells:
                text = cell_source(cell)
                try:
                    result.calls.extend(
                        extract_calls(text, display, index, roots, parse_text=mask_magics(text),
                                      known_aliases=shared)
                    )
                except SyntaxError as exc:
                    result.warnings.append(f"syntax_error={display}#cell{index}:{exc.lineno}")
        else:
            result.calls.extend(extract_calls(_read_text(path), display, None, roots))
    except SyntaxError as exc:
        result.warnings.append(f"syntax_error={display}:{exc.lineno}")
    except UnicodeDecodeError:
        result.warnings.append(f"unreadable={display}@not_utf8")
    except json.JSONDecodeError:
        result.warnings.append(f"unreadable={display}@bad_notebook")
    except OSError as exc:
        result.warnings.append(f"unreadable={display}@os_error:{exc.strerror or type(exc).__name__}")


def iter_files(paths: list[str], excludes: list[str]) -> list[tuple[Path, str]]:
    """Expand files/folders into ``(path, display)`` pairs in sorted order."""
    patterns = list(DEFAULT_EXCLUDES) + list(excludes)

    def excluded(rel: str) -> bool:
        parts = rel.replace(os.sep, "/").split("/")
        return any(fnmatch.fnmatch(part, pat) or fnmatch.fnmatch(rel, pat) for pat in patterns for part in parts)

    found: list[tuple[Path, str]] = []
    for given in paths:
        root = Path(given)
        if root.is_file():
            found.append((root, root.as_posix()))
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not excluded(d))
            for name in sorted(filenames):
                if name.endswith((".py", ".ipynb")):
                    full = Path(dirpath) / name
                    rel = full.relative_to(root).as_posix()
                    if not excluded(rel):
                        found.append((full, full.as_posix()))
    return found


def scan_paths(paths: list[str], excludes: list[str] | None = None) -> ScanResult:
    """Scan files/folders and return every ``load_dataset`` call found."""
    roots = [Path(p) if Path(p).is_dir() else Path(p).parent for p in paths] + [Path.cwd()]
    result = ScanResult()
    for path, display in iter_files(paths, excludes or []):
        scan_file(path, display, roots, result)
    result.calls.sort(key=lambda s: (s.file, s.cell if s.cell is not None else -1, s.line, s.col))
    return result
