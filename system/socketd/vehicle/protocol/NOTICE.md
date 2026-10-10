# Protocol provenance

Tesla-party CAN packing/parsing, state decoding, control limits, vehicle model,
and Kalman helpers are adapted from OpenDBC commit
6c0fbcd4d7c91c3b3225042473159eed53db7db6 under the included MIT license.
Only the BrownPanda-facing Tesla party protocol is retained; multi-brand vehicle
dispatch, firmware querying, radar interfaces and hardware drivers are excluded.
The runtime package and the preserved car schema are now EOP-owned.
NGP10 continues to use OpenDBC independently.
