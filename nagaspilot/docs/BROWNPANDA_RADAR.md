# Radar ownership

BrownPanda synthetic radar decoding and transmission are removed. Its BYD
blind-spot signals remain presence-only CAN flags, with no fabricated distance
or velocity. Genuine upstream vehicle interfaces retain their standard support.

NGP10 owns portable control policies and the vehicle gateway contract. It does
not run Exopilot-specific UART/BLE/WiFi sensor daemons. EOP10 introduces forward
UART radar3d and surrounding radar4d. Surround BLE supplies tracked 3D objects;
optional WiFi supplies point clouds. Radar2d is a ground-plane BSD compatibility
view, not a different sensor or a claim that BLE hardware has only two dimensions.
