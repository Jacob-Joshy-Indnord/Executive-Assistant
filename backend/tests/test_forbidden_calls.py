"""Story 1.1: no forbidden calls in module code outside the stand-ins."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1] / "assistant"

BANNED_IMPORTS = {
    # network
    "socket", "ssl", "http", "urllib", "httpx", "requests", "aiohttp", "websockets",
    "smtplib", "ftplib", "telnetlib", "asyncio.streams",
    # processes
    "subprocess", "multiprocessing", "pty", "asyncio.subprocess",
    # machine clock
    "time",
    # language models
    "openai", "anthropic", "google.genai", "langchain",
    # secrets
    "dotenv", "keyring", "getpass",
}  # fmt: skip
BANNED_FROM_NAMES = {("os", "environ"), ("os", "getenv"), ("os", "system"), ("os", "popen")}
BANNED_ATTRIBUTES = {
    ("os", "environ"), ("os", "getenv"), ("os", "system"), ("os", "popen"), ("os", "fork"),
    ("os", "execv"), ("os", "execvp"), ("os", "spawnv"), ("os", "startfile"),
    ("asyncio", "create_subprocess_exec"), ("asyncio", "create_subprocess_shell"),
    ("asyncio", "open_connection"), ("asyncio", "start_server"),
}  # fmt: skip
CLOCK_READS = {"now", "utcnow", "today", "fromtimestamp", "utcfromtimestamp"}
CLOCK_OWNERS = {"datetime", "date"}
BANNED_BUILTINS = {"eval", "exec", "__import__", "breakpoint"}


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def forbidden_uses(source: str, filename: str = "<string>") -> list[str]:
    """Every forbidden use in ``source``, as ``file:line: what``."""
    found: list[str] = []
    tree = ast.parse(source, filename=filename)

    def flag(node: ast.AST, what: str) -> None:
        found.append(f"{filename}:{getattr(node, 'lineno', 0)}: {what}")

    def banned_module(name: str) -> bool:
        return any(name == b or name.startswith(b + ".") for b in BANNED_IMPORTS)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if banned_module(alias.name):
                    flag(node, f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if banned_module(node.module):
                flag(node, f"from {node.module} import")
            for alias in node.names:
                if (node.module, alias.name) in BANNED_FROM_NAMES or banned_module(
                    f"{node.module}.{alias.name}"
                ):
                    flag(node, f"from {node.module} import {alias.name}")
        elif isinstance(node, ast.Attribute):
            owner = _dotted(node.value)
            if owner and (owner.split(".")[-1], node.attr) in BANNED_ATTRIBUTES:
                flag(node, f"{owner}.{node.attr}")
            if owner and owner.split(".")[-1] in CLOCK_OWNERS and node.attr in CLOCK_READS:
                flag(node, f"machine clock read {owner}.{node.attr}")
        elif isinstance(node, ast.Name) and node.id in BANNED_BUILTINS:
            flag(node, node.id)
    return found


class ForbiddenCallsTest(unittest.TestCase):
    """Story 1.1 - when a test searches the module's code outside the stand-ins."""

    def test_module_code_has_no_forbidden_calls(self) -> None:
        files = sorted(MODULE_ROOT.rglob("*.py"))
        self.assertTrue(files, "module code not found")
        problems: list[str] = []
        for path in files:
            problems += forbidden_uses(path.read_text(), str(path.relative_to(MODULE_ROOT)))
        self.assertEqual(problems, [])

    def test_scanner_finds_each_kind_of_forbidden_call(self) -> None:
        samples = {
            "network": "import socket",
            "network client": "from httpx import AsyncClient",
            "process": "import subprocess",
            "process via os": "import os\nos.system('x')",
            "async process": "import asyncio\nasyncio.create_subprocess_exec('x')",
            "machine clock": "from datetime import datetime\ndatetime.now()",
            "machine clock utc": "import datetime\ndatetime.datetime.utcnow()",
            "date today": "from datetime import date\ndate.today()",
            "time module": "import time\ntime.time()",
            "language model": "import anthropic",
            "language model 2": "from openai import OpenAI",
            "secret env": "import os\nos.environ['KEY']",
            "secret getenv": "from os import getenv",
            "dotenv": "from dotenv import load_dotenv",
            "eval": "eval('1')",
        }
        for label, source in samples.items():
            with self.subTest(label):
                self.assertNotEqual(forbidden_uses(source), [], label)

    def test_scanner_allows_the_boundary_clock(self) -> None:
        self.assertEqual(forbidden_uses("async def f(clock):\n    return await clock.now()"), [])


if __name__ == "__main__":
    unittest.main()
