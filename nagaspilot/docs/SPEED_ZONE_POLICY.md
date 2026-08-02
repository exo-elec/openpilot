# Speed zones and Traffic Jam Assist

EDP10, NGP10, and EOP10 use one range contract:

| Zone | Range | Positive acceleration cap | Positive jerk cap |
|---|---:|---:|---:|
| CRAWL | 0–2 m/s | 0.45 m/s² | 0.35 m/s³ |
| WALK | 2–6 m/s | ramps to 0.70 m/s² | ramps to 0.55 m/s³ |
| CITY | 6–12 m/s | ramps to 1.00 m/s² | ramps to 0.80 m/s³ |
| URBAN | 12–24 m/s | ramps to 1.20 m/s² | ramps to 1.20 m/s³ |
| HIGHWAY | 24–36 m/s | ramps to 1.40 m/s² | ramps to 1.50 m/s³ |

MAX_SPEED_MPS=36 is a policy clamp, not a zone. The profile only limits
positive acceleration and rising jerk. It never delays braking.

Traffic Jam Assist is implemented in selfdrive/controls/lib/dp_tja.py on
EDP10. Below 6 m/s, it uses lead distance, relative speed, model confidence,
and radar track continuity. Stable oversized gaps permit controlled closing.
A cut-in, sudden distance drop, short time-to-collision, or undersized gap
suppresses positive acceleration immediately.

The canonical constants live in nagaspilot/speed_zones.py. OpenDBC safety
cannot import that package; its continuous vehicle-model ISO steering check is
kept in sync by tests and review rather than Python imports.

