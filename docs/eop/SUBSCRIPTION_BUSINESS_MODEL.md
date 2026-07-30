# ExoPilot + NavPilot Subscription Business Model

> **Strategy**: Open-source the device (ExoPilot). Monetize via NavPilot mobile app subscriptions.  
> **Hardware**: RK3588 (LubanCat-5) / RK3576 (RongPin) running Ubuntu 22.04

---

## Philosophy

The **device-side ADAS stack is fully open source** and works offline forever. Anyone can build, modify, and flash it.

The **NavPilot mobile app is the monetization layer**. It provides cloud-connected features, AI assistance, and seamless mobile integration that are worth paying for monthly.

> "The car computer is free. The phone companion that makes it smarter is premium."

---

## Subscription Tiers

> **Rule**: Safety and real-time processing stay on the device. AI, maps, and rich UI stay on the phone.

### 🆓 FREE — $0/month
**For**: Everyone who builds or buys an ExoPilot device.

| Feature | Runs On | Detail |
|---------|---------|--------|
| **ADAS Core** | Device | Lane keeping, adaptive cruise, emergency braking |
| **Offline Navigation** | Device | OSM + Valhalla turn-by-turn (download maps once) |
| **Local DVR** | Device | Impact detection, parking mode, loop recording |
| **Basic Voice** | Device | Alert tones + TTS warnings ("BRAKE!", "Turn right") |
| **Bluetooth Pairing** | Device | Pair phone for hands-free |

**Why free is generous**: We want adoption. Free tier is genuinely useful — not a crippled demo. Users get a fully functional driver-assistance system without ever paying.

---

### 🔷 BASIC — ~$3-5/month
**For**: Car enthusiasts who want data.

| Feature | Runs On | Detail |
|---------|---------|--------|
| **Everything in FREE** | Device | |
| **Live Telemetry Dashboard** | **Phone** | Real-time OBD2 gauges: RPM, coolant temp, battery voltage, fuel level, boost pressure |
| **Trip Sync** | **Phone** | Every drive synced to NavPilot with distance, time, engagement ratio |
| **Route Replay** | **Phone** | Replay any past drive with speed, acceleration, and ADAS state graphs |
| **Driving Score** | **Phone** | Efficiency rating, braking smoothness, cornering G-force analysis |
| **Vehicle Health** | **Phone** | DTC code reading, maintenance reminders, battery health tracking |

**Value proposition**: "Know your car better than the dealership does."

---

### ⭐ PREMIUM — ~$8-12/month
**For**: Power users who want the full AI co-pilot experience.

| Feature | Runs On | Detail |
|---------|---------|--------|
| **Everything in BASIC** | **Phone** | |
| **"Hey Pilot" AI Voice** | Device + **Phone** | Wake word on device → cloud AI processing on phone |
| **Voice Navigation** | **Phone** | "Take me home", "Find the nearest charger", "Avoid highways" |
| **AI Routing** | **Phone** | Gemini-powered route suggestions: "Scenic route", "Fastest with charging stops" |
| **Live Traffic** | **Phone** | Real-time congestion, accidents, ETA updates |
| **Cloud Backup** | **Phone** | Automatic upload of incident clips + trip logs to your cloud storage |
| **Remote Camera** | **Phone** | View road camera live from NavPilot (WiFi hotspot required) |
| **Live Location Share** | **Phone** | Family can see your ETA and location during drives |
| **Emergency Alert** | Device + **Phone** | Auto-SMS emergency contacts on impact detection with location |
| **Fleet Mode** | **Phone** | One NavPilot account managing multiple ExoPilot vehicles |
| **Cross-Device Sync** | **Phone** | Favorites, home/work, settings sync across phones |

**Value proposition**: "Your car finally understands what you say." 

> **Why the phone?** Modern smartphones have Neural Engines, 120Hz screens, always-on 5G, and access to GPT-4/Gemini. The RK3588/RK3576 is a safety computer — we don't waste its NPU on chatbots. We use it for not crashing.

---

## Feature Deep Dive: "Hey Pilot"

### How It Works

```
Driver: "Hey Pilot, take me home"

┌──────────────────────────────┐  BLE JSON-RPC  ┌────────────┐  navDestination  ┌──────┐
│  NavPilot (phone)            │───────────────►│ subscribed │────────────────►│ navd │
│ (wake + STT + intent on app) │  navigate:home │ (dispatch) │                 │      │
└──────────────────────────────┘                └────────────┘                 └──────┘
                                                      │
                                                      ▼ ttsRequest
                                                 soundd: "Navigating home"
```
*(voiced → NavPilot bridge not yet implemented — open TODO)*

### Supported Commands (PREMIUM)

| Command | Action |
|---------|--------|
| "Hey Pilot, take me home" | Navigate to saved home address |
| "Hey Pilot, find a gas station" | Search + navigate to nearest fuel |
| "Hey Pilot, show telemetry" | NavPilot opens OBD dashboard |
| "Hey Pilot, snapshot" | Save current camera frame to gallery |
| "Hey Pilot, I'm tired" | Enable max safety: tighter following, louder alerts |
| "Hey Pilot, record this" | Bookmark current location + save clip |
| "Hey Pilot, call for help" | Trigger emergency alert with GPS location |
| "Hey Pilot, what's the weather ahead?" | NavPilot queries AI + GPS → spoken weather forecast |
| "Hey Pilot, find me a scenic route to the beach" | NavPilot AI (Gemini) plans route → sends to device |
| "Hey Pilot, summarize my last trip" | NavPilot AI analyzes trip data → spoken summary |

### Architecture: Thin Device, Smart Phone

```
┌─────────────────────────────────────────────────────────────────┐
│  EXOPILOT DEVICE (RK3588/RK3576)                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │ Safety-CRT  │  │ Perception  │  │ Basic Nav   │             │
│  │ controlsd   │  │ modeld      │  │ navd/Valh   │             │
│  │ CAN control │  │ stereo/yolo │  │ offline OSM │             │
│  └─────────────┘  └─────────────┘  └─────────────┘             │
│  Real-time deterministic — NO internet required                 │
└─────────────────────────────────────────────────────────────────┘
                              ↑↓ BLE SPP (20 Hz)
┌─────────────────────────────────────────────────────────────────┐
│  NAVPILOT (Phone)                                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │ Cloud AI    │  │ Live Maps   │  │ Responsive UI           │ │
│  │ Gemini API  │  │ Traffic/ETA │  │ 60fps gestures, charts  │ │
│  │ ChatGPT     │  │ OSM + AI    │  │ rich animations         │ │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘ │
│  Internet-connected, GPU-accelerated, always up-to-date         │
└─────────────────────────────────────────────────────────────────┘
```

**Device never runs AI models that require internet.**  
**Phone never runs safety-critical code.**  
Separation of concerns = safety + performance.

### Hardware Requirements
- **RK3588 (ExoPilot 01M)**: Wake word standby only (no mic). NavPilot handles voice via phone mic + cloud STT.
- **RK3576 (ExoPilot 02M)**: On-device wake word (Hailo-8). After wake, audio streams to NavPilot for cloud AI processing. Best of both worlds: privacy (no always-on cloud) + intelligence (cloud LLM after wake).

---

## Anti-Sharing & Anti-Clone Architecture

### Problem
Since the device code is open source, a determined user could:
1. Patch `subscribed.py` to skip verification
2. Share one NavPilot login across multiple cars
3. Build a clone device with copied OS image

### Mitigations

| Attack | Defense |
|--------|---------|
| **Skip verification patch** | Public key is in source → easy to patch. **But**: NavPilot app is closed-source Flutter. Recompiling the device is easier than replicating the mobile app + backend. Most users will just pay. |
| **Account sharing** | Token contains **eMMC CID** (unique per chip). A token issued for Device A will NOT validate on Device B. One subscription = one physical car. |
| **OS image clone** | eMMC CID is hardware-bound. Clone SD card → same image, different CID → token invalid → falls back to FREE tier. |
| **Build from source** | Community builds show "Community Edition" watermark. Official builds are signed at factory. Premium feel comes from the seamless NavPilot integration, not the device alone. |

**Reality check**: Android is open source. Google still makes billions from Google Play Services. Same model.

---

## Revenue Model

| Tier | Price | Target % | Est. ARPU |
|------|-------|----------|-----------|
| FREE | $0 | 60% of users | $0 |
| BASIC | $3.99/mo | 25% of users | ~$12/mo across base |
| PREMIUM | $9.99/mo | 15% of users | ~$15/mo across base |

**Break-even**: ~500 paying subscribers covers one developer full-time.  
**Scale**: 10,000 devices → $150K/mo potential at these conversion rates.

---

## Implementation Checklist

### Device-side (open source)
- [x] `subscribed` daemon — subscription state + hardware auth
- [x] `rk_device_id.py` — eMMC CID / RK OTP fingerprinting
- [x] Feature-gate params — `EOPFeatureTelemetry`, `EOPFeatureAiVoice`, etc.
- [x] JSON-RPC over BLE — remote commands (`takeSnapshot`, `getMessage`, etc.)
- [ ] voiced → NavPilot bridge — send voice intents via BLE
- [ ] UI watermark — show "Community" vs "Official" vs tier badge

### NavPilot-side (closed source)
- [ ] In-app purchase (Apple IAP / Google Play Billing / Stripe)
- [ ] Subscription backend — issue signed tokens bound to device CID
- [ ] "Hey Pilot" wake word trigger (phone mic fallback for RK3588)
- [ ] Voice command parser → JSON-RPC dispatch to device
- [ ] Telemetry dashboard — consume OBD2 data from device
- [ ] Cloud sync engine — upload queue drain, settings sync
- [ ] Fleet view — multiple vehicles on one account

---

## Key Message for Users

> **"ExoPilot is free forever. NavPilot makes it smarter."**
>
> Your car computer runs open-source software that keeps you safe on every drive.  
> The optional NavPilot app on your phone adds AI voice control, live traffic,  
> detailed telemetry, and cloud backup — for less than a cup of coffee per month.
