"""PLP, the Pilot Link Protocol (v5): the link between a pilot device (comma / ExoPilot) and a phone, over BLE, SPP or the USB cable.

PLP replaces NCP (NavPilot Companion Protocol, v3/v4.x), which grew message by message from a navigation link into a vehicle, perception, auth, voice and map link.
The two share no wire format; a connection starts as NCP v4.1 and moves to PLP after the hello (`hello.negotiate`). `registry.json` is the single list of messages;
`registry.v4_to_v5` maps an old type number onto the new (service, message) pair. Spec: nagaspilot/docs/PLP_V5_SPEC.md. Standard library only.
"""
