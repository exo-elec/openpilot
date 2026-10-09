"""Require portable NGP source to stay identical in its EOP descendants.

Run: python3 nagaspilot/lineage.py --parent dev/NGP10 --child dev/EOP10
New EOP adapters are allowed; deleting or editing a parent policy is not.
"""
import argparse
import subprocess

SCOPES = ('nagaspilot/controls/', 'nagaspilot/runtime/', 'nagaspilot/mapd/')


def source_tree(ref):
  output = subprocess.check_output(['git', 'ls-tree', '-r', ref], text=True)
  entries = {}
  for row in output.splitlines():
    metadata, path = row.split('\t', 1)
    if path.startswith(SCOPES) and path.endswith('.py'):
      entries[path] = metadata.split()[2]
  return entries


def violations(parent, child):
  return [f'{"missing" if path not in child else "changed"}: {path}'
          for path, oid in sorted(parent.items()) if child.get(path) != oid]


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument('--parent', default='dev/NGP10')
  parser.add_argument('--child', default='HEAD')
  args = parser.parse_args()
  parent, child = source_tree(args.parent), source_tree(args.child)
  errors = violations(parent, child)
  if not parent:
    parser.error('parent contains no portable source; check the parent ref')
  if errors:
    print('\n'.join(errors))
    return 1
  print(f'{len(parent)} portable source files match {args.parent} → {args.child}')
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
