"""One-time migration of saved params from the old `EOP` prefix to the unprefixed names.

Params are plain files under Params().get_param_path(); the old names are no longer
registered, so they cannot be read through the Params API. The migration therefore renames
the stored files directly, before the manager reads anything. Values carry over; if the new
name already exists the stale old file is dropped.

This replaces the earlier migration that moved fork params *to* `EOP<Feature><Param>`
(e.g. QuietMode -> EOPQuietMode); those names are unprefixed again.
"""
import os

from exopilot.param_names import FORK_KEYS, OLD_PREFIX


def migrate_renamed_params(param_dir: str) -> list[str]:
  """Rename stored `EOP<name>` params to `<name>`. Returns the old names that were moved."""
  migrated = []
  for name in sorted(FORK_KEYS):
    old = OLD_PREFIX + name
    src, dst = os.path.join(param_dir, old), os.path.join(param_dir, name)
    if not os.path.lexists(src):
      continue
    if os.path.lexists(dst):
      os.remove(src)
    else:
      os.replace(src, dst)
      migrated.append(old)
  return migrated
