# EOP10 vehicle-stack compatibility

EOP10 can use the forked OpenDBC Tesla protocol library without a Red Panda.
OpenDBC provides CAN definitions, parsers, packers, vehicle parameters, and
vehicle-model helpers; it does not require Panda hardware by itself.

The stock openpilot `selfdrive/car/card.py` path is different. It expects
Panda transport, `pandaStates`, Panda safety configuration, and the standard
car-interface lifecycle. EOP10 intentionally replaced that path with:

```
SocketCAN -> socketd/OpenDBC adapter -> carState
carControl -> socketd safety -> sendcan -> BrownPanda gateway -> vehicle
```

Therefore:

- `socketd` owns the vehicle adapter lifecycle and transport boundary.
- Reuse the shared OpenDBC Tesla CAN definitions, parser, packer, vehicle
  parameters, and radar interface from the v0.2.1-based fork.
- Keep BrownPanda as the final hardware safety layer; ALCC policy remains in
  socketd/controls, not OpenDBC.
- Do not restore stock `selfdrive/car/card.py` merely to consume OpenDBC; that
  would reintroduce Panda assumptions and bypass the BrownPanda contract.

The active daemon is `socketd` (`system/socketd/socketd.py`) and exposes the
same `carState`, `carParams`, `carOutput`, and `sendcan` boundaries. The
former `selfdrive/vehicled/` package has been renamed in place to
`system/socketd/vehicle/` and runs as a thread inside the `socketd` process;
there is no separate `vehicled` daemon or process entry anymore.

EOP10 and NGP10 pin the same OpenDBC fork commit. The official `v0.2.1` tag
was evaluated as a possible rebase point: it contains Tesla Model 3/Model Y
support, but it predates the OpenDBC safety tree required by BrownPanda.
Therefore it is retained as a release reference only; the shared descendant
is the clean compatible base.

The fork is Python 3.10-compatible for Ubuntu 22.04/ROS 2 Humble.
