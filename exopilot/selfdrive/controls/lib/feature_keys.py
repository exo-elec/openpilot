"""Param names read at the runtime boundary; portable policies do not read Params."""
LONGITUDINAL_KEYS = {"mode": "DriveMode", "accel": "AccelerationProfile", "personality": "LongitudinalPersonality",
                     "gap": "AdaptiveGapEnabled"}

MAP_SPEED_KEYS = {"mtsc": "MTSCEnabled", "mslc": "MSLCEnabled", "tlsc": "TLSCEnabled",
                  "ddsc": "DDSCEnabled", "rcd": "RCDEnabled", "offsets": "SharedSLCOffsets"}
