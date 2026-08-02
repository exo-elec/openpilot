# NGP10 concept

NGP10 is a minimized EOP10 experience for comma 3. It should surpass EDP10 in
portable behavior without carrying EOP10 services that require unavailable
hardware or duplicate upstream estimators.

Design rules:

- integrate through normal openpilot planner, controls, model, UI, and safety paths;
- keep NGP-owned implementations in `nagaspilot/controls/` with `ngp_` names;
- reuse upstream `paramsd` for real-time steering ratio/stiffness learning and persistence;
- keep gateway geometry learning local and persistent when Tesla-format CAN has no verified parameter transport;
- enforce steering with continuous vehicle-model ISO accel/jerk limits plus physical limits;
- treat 2/6/12/24/36 m/s as ranges, not equality triggers;
- add outputs only with tests and retain bench/HIL gates for vehicle authority.

BrownPanda exposes only Tesla party bus 0 and autopilot-party bus 2 to comma.
NGP10 does not select or name the gateway MCU variant. Its pinned OpenDBC
adapter enables the optional converted measurements from their party-bus wire
signature and fails closed when that signature or stream is absent. Unmodified
sunnypilot and dragonpilot remain compatible with BrownPanda vehicle/control
traffic but do not receive the party-bus radar extension.
