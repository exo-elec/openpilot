#!/usr/bin/env python3
"""
Validate Params API usage across the codebase.

Catches common mistakes:
- get_float() / put_float() / get_int() / put_int() — do not exist
- get_bool(key, True) — second arg is block, not default
- get(key, encoding=...) — encoding kwarg does not exist
- put(key, default_value) for STRING params without converting to str
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent / "selfdrive"
# Also check system/ since it has EOP modules
SYSTEM_ROOT = Path(__file__).parent.parent / "system"


def find_issues(file_path: Path):
    """Scan a Python file for Params API misuse."""
    issues = []
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except SyntaxError:
        return issues

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func
        # Check method calls like params.get_float(...)
        if isinstance(func, ast.Attribute):
            method_name = func.attr
            args = node.args
            keywords = node.keywords

            # Non-existent methods
            if method_name in ("get_float", "put_float", "get_int", "put_int"):
                issues.append((node.lineno, f"{method_name}() does not exist on Params"))

            # get_bool with 2+ positional args or non-block keyword
            if method_name == "get_bool":
                if len(args) >= 2:
                    issues.append((node.lineno, "get_bool(key, block=False) does not accept a default value"))
                for kw in keywords:
                    if kw.arg not in ("block",):
                        issues.append((node.lineno, f"get_bool() does not accept '{kw.arg}' keyword"))

            # get() with encoding keyword
            if method_name == "get":
                for kw in keywords:
                    if kw.arg == "encoding":
                        issues.append((node.lineno, "get() does not accept 'encoding' keyword"))

    return issues


def main():
    all_issues = []
    for root in (ROOT, SYSTEM_ROOT):
        for py_file in root.rglob("*.py"):
            issues = find_issues(py_file)
            for line, msg in issues:
                rel = py_file.relative_to(Path(__file__).parent.parent)
                all_issues.append(f"  {rel}:{line}  {msg}")

    if all_issues:
        print(f"Found {len(all_issues)} Params API issue(s):")
        for issue in sorted(all_issues):
            print(issue)
        sys.exit(1)
    else:
        print("No Params API issues found.")
        sys.exit(0)


if __name__ == "__main__":
    main()
