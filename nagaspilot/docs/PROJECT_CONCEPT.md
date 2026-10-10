# NGP10 concept

NGP10 is a minimized EOP10 experience for comma 3. It should surpass EDP10 in
portable behavior without carrying EOP10 services that require unavailable
hardware or duplicate upstream estimators.

Design rules:

- integrate through normal openpilot planner, controls, model, UI, and safety paths;
- keep NGP-owned implementations in `nagaspilot/controls/` with `ngp_` names;
- reuse upstream `paramsd` for real-time steering ratio/stiffness learning and persistence;
- enforce steering with continuous vehicle-model ISO accel/jerk limits plus physical limits;
- treat 2/6/12/24/36 m/s as ranges, not equality triggers;
- add outputs only with tests and retain bench/HIL gates for vehicle authority.

NGP10 uses comma's normal card/OpenDBC vehicle path. BrownPanda gateway and
vehicled/socketd integration belong to EOP10. NGP10's radar2d layer is portable
vehicle blind-spot presence and ground-plane geometry only, without Exopilot HAL,
UART, BLE or WiFi drivers. EOP10 introduces radar3d and radar4d hardware producers.
