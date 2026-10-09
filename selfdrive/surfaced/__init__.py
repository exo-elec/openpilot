"""
surfaced - Surface Perception Daemon

Unified road surface monitoring combining:
- Real-time: IMU shock detection + stereo depth analysis
- Predictive: GPS-based surface history lookup

Publishes surfaceStatus at 20Hz for consumers:
- SQSC (real-time speed control)
- gridd (costmap enhancement)
- pathd (trajectory planning)

Architecture:
  IMU + Stereo + GPS History ──► surfaced ──► surfaceStatus
                                              ├──► SQSC
                                              ├──► gridd
                                              └──► pathd
"""

__version__ = "1.0.0"
