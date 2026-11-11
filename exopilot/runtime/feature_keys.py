"""Parameter names at the product boundary; portable policies do not read Params."""
NGP_KEYS = {"mode": "ngp_lon_drive_mode", "accel": "ngp_lon_accel_profile", "personality": "LongitudinalPersonality",
            "gap": "ngp_lon_adaptive_gap"}
EOP_KEYS = {"mode": "DriveMode", "accel": "AccelerationProfile", "personality": "LongitudinalPersonality",
            "gap": "AdaptiveGapEnabled"}

# EOP-origin features retain EOP names even when their pure core runs in NGP.
EOP_MAP_KEYS = {"mtsc": "MTSCEnabled", "mslc": "MSLCEnabled", "tlsc": "TLSCEnabled",
                "ddsc": "DDSCEnabled", "rcd": "RCDEnabled", "offsets": "SharedSLCOffsets"}
EOP_LEGACY_KEYS = {value: f"ngp_lon_{name}" for name, value in EOP_MAP_KEYS.items() if name != "offsets"}
EOP_LEGACY_KEYS.update({"SharedSLCOffsets": "ngp_lon_slc_offsets", "PathdNudgesEnabled": "ngp_pathd_nudges"})


class OriginParams:
  """Read canonical origin keys first, then legacy persisted keys without writing settings."""
  def __init__(self, params):
    self.params = params

  def get(self, key):
    canonical = next((name for name, legacy in EOP_LEGACY_KEYS.items() if legacy == key), key)
    value = self.params.get(canonical)
    if value is None and canonical in EOP_LEGACY_KEYS:
      value = self.params.get(EOP_LEGACY_KEYS[canonical])
    return value

  def get_text(self, key):
    raw = self.get(key)
    return raw.decode("utf-8") if isinstance(raw, bytes) else (raw or "")

  def get_bool(self, key):
    if key not in EOP_LEGACY_KEYS and key not in EOP_LEGACY_KEYS.values():
      return self.params.get_bool(key)
    return self.get(key) in (True, b"1", "1")


def migrate_origin_params(params):
  """Preserve saved aliases before manager initialization writes canonical defaults."""
  for canonical, legacy in EOP_LEGACY_KEYS.items():
    if params.get(canonical) is None:
      value = params.get(legacy)
      if value is not None:
        params.put(canonical, value)
