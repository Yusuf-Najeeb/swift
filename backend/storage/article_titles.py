
from __future__ import annotations

import re

_FRONT_MATTER = re.compile(
    r"^---\r?\n(?P<body>.*?)\r?\n---\r?\n",
    re.DOTALL,
)


def _unquote_simple_yaml_string(raw: str) -> str:
    t = raw.strip()
    if len(t) >= 2 and t[0] == t[-1] == '"':
        return t[1:-1].replace(r"\"", '"')
    return t


def title_from_first_bytes(content: str) -> str | None:
    m = _FRONT_MATTER.match(content)
    if not m:
        return None
    for line in m.group("body").splitlines():
        line = line.rstrip()
        if line.lower().startswith("title:"):
            return _unquote_simple_yaml_string(line.split(":", 1)[1])
    return None
