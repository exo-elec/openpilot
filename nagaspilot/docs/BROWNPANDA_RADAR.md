# BrownPanda radar on NGP10

NGP10 is a privileged two-channel consumer of BrownPanda's optional converted
BYD radar stream. The host does not identify an MCU or gateway variant: it
enables radar only from the wire contract. For Tesla Model 3/Y normalization,
NGP10 reads the Continental ARS4-B-compatible stream from logical party bus 0. Stock
openpilot-family Tesla integrations normally expect radar on bus 1, so an
unchanged sunnypilot or dragonpilot installation does not gain this radar path.

Radar-capable BrownPanda hardware converts ten 64-byte BYD MVS4 CAN-FD slots into classical 8-byte Tesla
frames: status `0x401`, then forty A/B pairs `0x410..0x45F`. The final `0x45F`
is the complete-set trigger. This is Tesla's Continental layout, not the
industrial ARS408 protocol.

NGP10 pins the shared
[`exo-electronics/opendbc`](https://github.com/exo-electronics/opendbc) fork's
`master` branch at an exact gitlink. Its Tesla `RadarInterface` uses the
official signal layout while applying the BrownPanda allocation and safety
lifecycle. Tesla fingerprinting enables it only when both `0x401` and `0x45F`
are present as eight-byte frames on party bus 0. The parser accepts only bus-0
frames, requires
a fresh healthy status, full eight-byte frames, complete pairs, matching pair
indexes, all forty slots in one coherent set, and `Tracked`, `Valid`, and
`Meas`. Missing or unhealthy status clears all tracks and reports radar
unavailable; a vehicle-dynamics fault reports a radar fault. No object is
published beside either error.

BYD `ALEAD` and `LAT_RELATED` remain unproven and reserved. BrownPanda puts
neutral values on the Tesla wire, and NGP10 publishes those optional fields as NaN
rather than claiming measured zero. `radard` derives lead acceleration from
lead speed and ego speed with its existing Kalman filter.

This adapter does not repair missing source-frame payload authentication. The
reference BYD port reports offline CRC8/J1850 plus DataID validation, but its
validator and DataID table are absent. BrownPanda currently relies on trusted
ECU-side origin, DLC, freshness, identity lifecycle, and plausibility. Recovery
and reproduction of the original checksum validator remains a HIL gate.
