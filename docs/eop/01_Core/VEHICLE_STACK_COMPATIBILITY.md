# EOP10 vehicle-stack compatibility

EOP10 can use the forked OpenDBC Tesla protocol library without a Red Panda.
OpenDBC provides CAN definitions, parsers, packers, vehicle parameters, and
vehicle-model helpers; it does not require Panda hardware by itself.

The stock openpilot `selfdrive/car/card.py` path is different. It expects
Panda transport, `pandaStates`, Panda safety configuration, and the standard
car-interface lifecycle. EOP10 intentionally replaced that path with:

```
SocketCAN -> socketd -> can topic -> vehicled -> carState
carControl -> vehicled safety -> sendcan -> socketd -> BrownPanda gateway -> vehicle
```

Therefore:

- Keep `vehicled` as the EOP adapter and safety boundary for now.
- Reuse the forked OpenDBC Tesla CAN definitions, parser, packer, vehicle
  parameters, and vehicle model inside that adapter.
- Keep `socketd` as the transport and BrownPanda gateway as the hardware safety layer.
- Do not restore stock `selfdrive/car/card.py` merely to consume OpenDBC; that
  would reintroduce Panda assumptions and bypass the BrownPanda contract.

The eventual migration target is an EOP `CarInterface` adapter that exposes the
same `carState`, `carParams`, `carOutput`, and `sendcan` boundaries while using
OpenDBC internally. Once that adapter is tested against SocketCAN and the
BrownPanda gateway,
the duplicated Tesla parser/controller code in `vehicled` can be removed. A
full daemon replacement before that point would remove the active safety and
transport integration.

EOP10 and NGP10 pin the same OpenDBC fork commit. The official `v0.2.1` tag
was evaluated as a possible rebase point: it contains Tesla Model 3/Model Y
support, but it predates the OpenDBC safety tree required by BrownPanda.
Therefore it is retained as a release reference only; the shared descendant
is the clean compatible base.

The fork is Python 3.10-compatible for Ubuntu 22.04/ROS 2 Humble. Protocol
imports should still be migrated one module at a time and tested against
captured SocketCAN frames before deleting the local implementation.
