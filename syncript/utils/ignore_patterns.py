"""
Ignore patterns handling (.stignore file parsing)
"""
import re
from pathlib import Path
from ..config import STIGNORE_FILE


def _compile_pattern(raw: str):
    """Compile a .stignore pattern into a regex"""
    p = raw.strip()
    if not p or p.startswith("#"):
        return None
    escaped = re.escape(p)
    escaped = escaped.replace(r"\*\*", "§DS§")
    escaped = escaped.replace(r"\*", "[^/]*")
    escaped = escaped.replace(r"\?", "[^/]")
    escaped = escaped.replace("§DS§", ".*")
    if not escaped.startswith("/"):
        escaped = r"(^|.*\/)" + escaped
    try:
        return re.compile(escaped + r"(/.*)?$")
    except re.error:
        return None


def load_ignore_patterns(root: Path) -> list:
    """Load ignore patterns from .stignore file"""
    f = root / STIGNORE_FILE
    if not f.exists():
        return []
    patterns = []
    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        c = _compile_pattern(line)
        if c:
            patterns.append(c)
    return patterns


def is_ignored(rel_path: str, patterns: list) -> bool:
    """Check if a path matches any ignore pattern"""
    norm = rel_path.replace("\\", "/")
    return any(p.search(norm) for p in patterns)


def _stignore_to_find_prunes(root: Path) -> str:
    """
    Emit a 'find ... ( <prune-expr> -prune ) -o ...' fragment.

    Handles three pattern shapes:
      ① simple name glob   e.g. *.jar, .DS_Store
          → -name "*.jar"
      ② **/name            e.g. **/node_modules, **/target
          → -name "node_modules"       (matches at any depth, cheapest)
      ③ **/path/segments   e.g. **/target/classes, **/build/generated
          → -path "*/target/classes"   (matches the full tail anywhere in tree)

    Patterns with a leading slash or other complex forms are skipped here and
    handled by the client-side is_ignored() filter that runs after the scan.
    """
    f = root / STIGNORE_FILE
    if not f.exists():
        return ""

    name_prunes: list[str] = []  # -name  "..."
    path_prunes: list[str] = []  # -path  "..."

    for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("**/"):
            tail = line[3:]  # everything after the **/
            if not tail:
                continue
            if "/" not in tail:
                # ② **/name — simple name match at any depth
                name_prunes.append(f'-name "{tail}"')
            else:
                # ③ **/path/with/segments — use -path with a leading wildcard
                path_prunes.append(f'-path "*/{tail}"')
        elif "/" not in line:
            # ① bare glob (no separators) — name match
            name_prunes.append(f'-name "{line}"')
        elif line.startswith("*/"):
            # ③ path with segments but no leading **/ — use -path
            path_prunes.append(f'-path "{line}"')
        elif line.startswith("./"):
            # ③ path with segments but no leading **/ — use -path, ignore leading ./
            path_prunes.append(f'-path "*/{line[2:]}"')
        elif line.endswith("/**"):
            tail = line[:-3]  # remove trailing '/**'
            if tail.startswith("./"):
                tail = tail[2:]
            if tail:
                path_prunes.append(
                    f'-path "*/{tail}"')  # else: leading-slash absolute patterns, *.ext/sub, etc. — skip;
        #       is_ignored() handles them after the scan.

    path_prunes.append('-path "*/.git/*"')  # always ignore .git contents
    all_prunes = name_prunes + path_prunes
    if not all_prunes:
        return ""

    parts = r" -o ".join(r"\( " + p + r" -prune \)" for p in all_prunes)
    return r"\( " + parts + r" \) -o"
