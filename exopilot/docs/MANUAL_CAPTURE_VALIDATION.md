# Manual factory-MPC validation workflow

This is the primary evidence pipeline for the camera-only BYD Atto 3 port.
NagasPilot is validated against synchronized raw behavior from the test car's
factory MPC, rather than treating runtime output enablement as a parity test.

## Ground-truth recording

Record the same drive as one manually synchronized dataset containing:

- unmodified factory-MPC road video;
- raw, timestamped classic CAN from both sides of the camera harness (car side
  and MPC side, corresponding to NagasPilot buses 0 and 2);
- the original CAN channel, arbitration ID, DLC and eight payload bytes;
- vehicle/ignition state and operator annotations for each maneuver; and
- the recording-tool configuration, database version and acquisition clock details.

Do not use private radar CAN-FD or decoded radar tracks. Factory ACC and AEB may
continue using the vehicle's radar internally, but NagasPilot's validation input
is factory-MPC video plus chassis/camera CAN only.

Keep the raw CAN measurement and video immutable. Export derived CSV, frame
images, or clips only as reproducible working artifacts. Record a
SHA-256 hash, duration, start time, channel mapping, frame rate, CAN message
counts, dropped-frame count and clock discontinuities for every source file.

## Required factory-MPC scenarios

Capture a representative factory baseline before testing NagasPilot output:

1. power-off, wake, Ready, Park, Reverse, Neutral, Drive and shutdown;
2. stationary steering center plus known left and right angles;
3. steady speeds suitable for resolving the `0.0713` versus `0.0758` scale;
4. factory lane detection with no lines, one line, two lines, gentle curves and
   deliberate driver steering override;
5. factory LKS off, available, active, temporarily unavailable and fault/retry;
6. stock ACC off, available, active, set-speed changes, standstill and resume;
7. accelerator and brake disengagements; and
8. normal factory `0x32E` behavior. Do not intentionally provoke an AEB event.

## Offline cross-check

Replay the raw CAN chronologically through NagasPilot without transmitting to
the vehicle. For every replay, preserve the correspondence between CAN time and
video frame time.

Compare:

- fingerprint, firmware responses, bus assignment and ignition state;
- every CarState output against the raw signal and visible vehicle event;
- speed scale, steering sign/ratio, pedal thresholds and gear mapping;
- factory `0x1E2`, `0x316` and `0x32E` counters, checksums, cadence and state
  transitions;
- NagasPilot steering/HUD bytes against factory-MPC bytes for equivalent
  inactive, available, active, override and recovery states; and
- optional NagasPilot longitudinal bytes against the factory command factor,
  hold/resume encoding and observed comfort envelope.

Generated commands are evaluated offline even if the checked-out interface is
configured as `noOutput`. That setting controls vehicle transmission; it is not
a software-parity criterion and must not prevent parser, controller, safety or
replay validation.

## Acceptance record

For each dataset, retain a machine-readable comparison report and a short human
review containing:

- source hashes and exact NagasPilot commit;
- video-to-CAN time alignment and measured offset/error;
- expected and observed message/state transitions;
- byte-level mismatches grouped by signal or state;
- resolved values for steering ratio and wheel-speed scale;
- unexplained gaps, dropped samples and test limitations; and
- pass/fail per feature, never only one pass/fail for the whole vehicle.

A feature reaches **recording parity** when the synchronized factory dataset can
be replayed deterministically, decoded state agrees with the raw/video event,
and generated behavior agrees with the accepted factory-MPC semantics or has a
documented safety-motivated difference. Vehicle transmission and closed-course
approval are later, separate decisions.

