// Test-only driver for check_navpilot_plp_interop.py. Uses the actual sibling
// PLP package, not a second codec implementation or a phone inference stub.
import 'dart:convert';
import 'dart:io';

import 'package:plp/plp.dart';
import 'package:plp/plp_tcp.dart';

Future<void> main(List<String> args) async {
  final link = await PlpTcpLink.connect(
    host: '127.0.0.1',
    port: int.parse(args[0]),
    hello: const PlpHello(
      link: 'usb',
      maxPayload: 65536,
      payloadCrc: false,
      services: {'core': (1, 1), 'locate': (1, 1)},
    ),
  );
  try {
    if (!link.isConnected ||
        !link.locateAvailable ||
        link.agreed!.maxPayload != 64) {
      throw StateError('Python/Dart hello or payload negotiation failed');
    }
    if (!link.sendGnssFix({
      't_ms': 1000,
      'lat': 13.75,
      'lon': 100.5,
      'h_acc': 3.0,
    })) {
      throw StateError('GNSS send refused');
    }
    final horizon =
        jsonDecode(await File(args[1]).readAsString()) as Map<String, dynamic>;
    if (!link.sendMapHorizon(horizon)) {
      throw StateError('Horizon send refused');
    }
    stdout.writeln('sent');
    // Host confirms decoded receipt before the client closes its byte stream.
    await stdin.first.timeout(const Duration(seconds: 5));
  } finally {
    await link.close();
  }
}
