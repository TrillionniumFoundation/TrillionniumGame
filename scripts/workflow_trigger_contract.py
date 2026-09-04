"""Bounded source contract for mandatory pull-request workflow triggers.

This deliberately accepts the repository's canonical YAML trigger forms, not
arbitrary YAML. Ambiguous/complex forms fail and still require the independent
workflow syntax policy. It never makes claims about executed jobs or evidence.
"""
from __future__ import annotations

import re

MAX_BYTES = 1024 * 1024
SELECTORS = frozenset({"paths", "paths-ignore", "branches", "branches-ignore"})
REQUIRED_EVENTS = frozenset({"opened", "synchronize", "reopened"})


class TriggerContractError(ValueError):
    """The trigger is missing, ambiguous, filtered or unsupported."""


def _meaningful(line: str) -> bool:
    return bool(line.strip()) and not line.lstrip().startswith("#")


def _scalar_list(value: str) -> set[str]:
    text = value.strip()
    if not text.startswith("[") or not text.endswith("]"):
        raise TriggerContractError("only canonical flow event lists are supported")
    result = []
    for part in text[1:-1].split(","):
        item = part.strip().strip("'\"")
        if not re.fullmatch(r"[a-z_]+", item):
            raise TriggerContractError("invalid or complex event name")
        result.append(item)
    if len(set(result)) != len(result):
        raise TriggerContractError("duplicate event name")
    return set(result)


def _block(text: str) -> tuple[list[str], int, int] | None:
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_BYTES:
        raise TriggerContractError("workflow text must be bounded UTF-8")
    lines = text.splitlines(keepends=True)
    roots = [i for i, line in enumerate(lines) if re.match(r"^(?:on|'on'|\"on\")\s*:", line)]
    if len(roots) != 1:
        raise TriggerContractError("exactly one on trigger is required")
    start = roots[0]
    match = re.fullmatch(r"on:[ \t]*(.*?)[ \t]*(?:\r?\n)?", lines[start])
    if match is None:
        raise TriggerContractError("canonical unquoted on key required")
    inline = match.group(1).strip()
    if inline and not inline.startswith("#"):
        events = {inline} if re.fullmatch(r"[a-z_]+", inline) else _scalar_list(inline)
        if "pull_request" not in events:
            raise TriggerContractError("pull_request event is required")
        return None
    end = next((i for i in range(start + 1, len(lines))
                if _meaningful(lines[i]) and not lines[i][0].isspace()), len(lines))
    prs = [i for i in range(start + 1, end)
           if re.match(r"^  pull_request\s*:", lines[i])]
    if len(prs) != 1:
        raise TriggerContractError("exactly one pull_request mapping is required")
    pr = prs[0]
    if re.fullmatch(r"  pull_request:[ \t]*(?:\{\})?[ \t]*(?:#.*)?(?:\r?\n)?", lines[pr]) is None:
        raise TriggerContractError("complex pull_request mapping is unsupported")
    stop = next((i for i in range(pr + 1, end)
                 if _meaningful(lines[i]) and len(lines[i]) - len(lines[i].lstrip(" ")) <= 2), end)
    return lines, pr + 1, stop


def _entries(lines: list[str], start: int, end: int) -> list[tuple[str, int, int]]:
    keys = []
    for i in range(start, end):
        line = lines[i]
        if not _meaningful(line):
            continue
        if "\t" in line[:len(line) - len(line.lstrip())]:
            raise TriggerContractError("tab indentation is unsupported")
        depth = len(line) - len(line.lstrip(" "))
        if depth == 4:
            match = re.match(r"^    ([a-z][a-z-]*):", line)
            if match is None:
                raise TriggerContractError("canonical pull_request key required")
            keys.append((match.group(1), i))
        elif depth < 4 or not keys:
            raise TriggerContractError("unexpected pull_request indentation")
    if len({name for name, _ in keys}) != len(keys):
        raise TriggerContractError("duplicate pull_request key")
    return [(name, pos, keys[i + 1][1] if i + 1 < len(keys) else end)
            for i, (name, pos) in enumerate(keys)]


def validate_required_pr_trigger(text: str) -> None:
    block = _block(text)
    if block is None:
        return
    lines, start, end = block
    for name, pos, stop in _entries(lines, start, end):
        if name in SELECTORS:
            raise TriggerContractError(f"mandatory workflow has {name} filtering")
        if name != "types":
            raise TriggerContractError(f"unsupported pull_request key: {name}")
        value = lines[pos].split(":", 1)[1].strip()
        if value and not value.startswith("#"):
            events = _scalar_list(value)
        else:
            events = set()
            for line in lines[pos + 1:stop]:
                if not _meaningful(line):
                    continue
                match = re.fullmatch(r"      -[ \t]+['\"]?([a-z_]+)['\"]?[ \t]*(?:\r?\n)?", line)
                if match is None or match.group(1) in events:
                    raise TriggerContractError("invalid or duplicate PR event type")
                events.add(match.group(1))
        if not REQUIRED_EVENTS.issubset(events):
            raise TriggerContractError("required PR lifecycle event types are missing")


def remove_required_pr_selectors(text: str) -> tuple[str, tuple[str, ...]]:
    """Remove only PR path/branch selectors, retaining every other source byte."""
    block = _block(text)
    if block is None:
        validate_required_pr_trigger(text)
        return text, ()
    lines, start, end = block
    entries = _entries(lines, start, end)
    deleted: set[int] = set()
    selectors = []
    for name, pos, stop in entries:
        if name in SELECTORS:
            selectors.append(name)
            deleted.update(range(pos, stop))
    result = "".join(line for i, line in enumerate(lines) if i not in deleted)
    validate_required_pr_trigger(result)
    return result, tuple(selectors)
