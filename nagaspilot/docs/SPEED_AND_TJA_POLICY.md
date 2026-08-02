# Speed zones and Traffic Jam Assist

| Zone | Range | Positive acceleration cap | Positive jerk cap |
|---|---:|---:|---:|
| CRAWL | 0–2 m/s | 0.45 m/s² | 0.35 m/s³ |
| WALK | 2–6 m/s | ramps to 0.70 m/s² | ramps to 0.55 m/s³ |
| CITY | 6–12 m/s | ramps to 1.00 m/s² | ramps to 0.80 m/s³ |
| URBAN | 12–24 m/s | ramps to 1.20 m/s² | ramps to 1.20 m/s³ |
| HIGHWAY | 24–36 m/s | ramps to 1.40 m/s² | ramps to 1.50 m/s³ |

`36 m/s` is the policy maximum, not another zone. These values only limit
rising acceleration. Planner-requested braking and hard vehicle safety remain
immediately available.

TJA runs only below 6 m/s with a valid lead. Its desired gap is `4 m + 1.1 s`
of ego speed. A stable lead and oversized gap permits controlled WALK-speed
closing. A new radar track, sudden distance drop, uncertain new lead, gap below
target, or time-to-collision below 3 seconds suppresses positive acceleration.
This targets dense mixed traffic without teaching the controller to accelerate
into motorcycles or cars that cut in.
