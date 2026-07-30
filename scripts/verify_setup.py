#!/usr/bin/env python3
"""Verify EOP10 setup for RTK GPS and audio pipeline."""

import sys
import os

def check_file(path, desc):
  if os.path.exists(path):
    print(f"✅ {desc}: {path}")
    return True
  print(f"❌ {desc} MISSING: {path}")
  return False

def check_module(module_name, desc):
  try:
    __import__(module_name)
    print(f"✅ {desc}: {module_name}")
    return True
  except ImportError as e:
    print(f"❌ {desc} NOT AVAILABLE: {module_name} ({e})")
    return False

def main():
  print("=" * 70)
  print("EOP10 Setup Verification")
  print("=" * 70)

  all_ok = True

  # RTK GPS
  print("\n📡 NTRIP RTK GPS:")
  all_ok &= check_file("third_party/pygnssutils/src/pygnssutils/__init__.py", "pygnssutils module")
  all_ok &= check_file("third_party/pygnssutils/src/pygnssutils/gnssntripclient.py", "GNSSNTRIPClient")
  all_ok &= check_file("system/ubloxd/ntripd.py", "ntripd daemon")
  all_ok &= check_file("system/ubloxd/pigeond.py", "pigeond daemon")

  # Audio pipeline
  print("\n🔊 Audio Pipeline (Piper TTS + adaptive loudness):")
  all_ok &= check_file("selfdrive/soundd/soundd.py", "soundd (TTS + alert tones)")
  all_ok &= check_file("selfdrive/soundd/piper_tts.py", "piper_tts (20-language Piper)")
  all_ok &= check_file("system/spkd/spkd.py", "spkd (I2S speaker output)")
  all_ok &= check_file("system/micd/micd.py", "micd (SPL metering)")

  # Process config
  print("\n⚙️  Process Configuration:")
  with open("system/manager/process_config.py", "r") as f:
    content = f.read()
    for daemon in ["ntripd", "spkd", "soundd", "micd"]:
      if f'"{daemon}"' in content:
        print(f"✅ {daemon} registered in process_config.py")
      else:
        print(f"❌ {daemon} NOT registered in process_config.py")
        all_ok = False

  # Parameters
  print("\n🔧 Parameters:")
  with open("common/params_keys.h", "r") as f:
    content = f.read()
    for param in ["EOPRTKEnabled", "EOPNTRIPEnabled", "EOPNTRIPCaster", "EOPNTRIPMount",
                  "EOPNavVoiceEnabled", "EOPTTSVoice", "EOPLanguage", "EOPMicAdaptiveLoudness"]:
      if param in content:
        print(f"✅ {param} defined")
      else:
        print(f"❌ {param} NOT defined")
        all_ok = False

  print("\n" + "=" * 70)
  if all_ok:
    print("✅ All checks passed!")
    print("\nNext steps:")
    print("1. Install pygnssutils: pip install -e third_party/pygnssutils")
    print("2. Configure RTK: params put EOPNTRIPMount 'YOUR-MOUNTPOINT'")
    print("3. Enable RTK: params put_bool EOPRTKEnabled true")
    return 0
  print("❌ Some checks failed. Please review the output above.")
  return 1

if __name__ == "__main__":
  sys.exit(main())
