"""Parameter names at the product boundary; portable policies do not read Params."""
NGP_KEYS = {"mode": "ngp_lon_drive_mode", "accel": "ngp_lon_accel_profile", "personality": "LongitudinalPersonality",
            "gap": "ngp_lon_adaptive_gap"}
EOP_KEYS = {"mode": "EOPDriveMode", "accel": "EOPAccelerationProfile", "personality": "LongitudinalPersonality",
            "gap": "EOPAdaptiveGapEnabled"}
