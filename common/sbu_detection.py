"""Host-side SBU detection for RK3588 platforms without comma.ai Panda.

Mirrors FrogPilot panda/board/drivers/harness.h logic:
  - Sample SBU1 / SBU2 via ADC/GPIO/shell command
  - Threshold = VDD / 2
  - Publish orientation at 8 Hz

Usage:
    from common.sbu_detection import SBUDetector, ADCSource

    det = SBUDetector(
        source=ADCSource("/sys/.../in_voltage0_raw", "/sys/.../in_voltage1_raw", scale=0.805),
        threshold_mv=1650,
    )
    status, sbu1, sbu2 = det.tick()  # ("normal", "flipped", or "nc")
"""

import json
import os
import subprocess
import time
from abc import ABC, abstractmethod


class SBUSource(ABC):
    """Abstract SBU voltage reader."""

    @abstractmethod
    def read(self) -> tuple[int | None, int | None]:
        """Return (sbu1_voltage_mV, sbu2_voltage_mV) or (None, None) on error."""


class ADCSource(SBUSource):
    """Read SBU voltages from Linux IIO ADC sysfs."""

    def __init__(self, sbu1_path: str, sbu2_path: str, scale: float = 1.0) -> None:
        self._sbu1_path = sbu1_path
        self._sbu2_path = sbu2_path
        self._scale = scale

    def _read_channel(self, path: str) -> int | None:
        try:
            with open(path) as f:
                raw = int(f.read().strip())
            return int(raw * self._scale)
        except (OSError, ValueError):
            return None

    def read(self) -> tuple[int | None, int | None]:
        return self._read_channel(self._sbu1_path), self._read_channel(self._sbu2_path)


class GPIOSource(SBUSource):
    """Read SBU as digital GPIO (weak pull-up vs GND)."""

    def __init__(self, sbu1_gpio: int, sbu2_gpio: int, vdd_mv: int = 3300) -> None:
        self._sbu1_path = f"/sys/class/gpio/gpio{sbu1_gpio}/value"
        self._sbu2_path = f"/sys/class/gpio/gpio{sbu2_gpio}/value"
        self._vdd_mv = vdd_mv

    def _read_pin(self, path: str) -> int | None:
        try:
            with open(path) as f:
                val = int(f.read().strip())
            return self._vdd_mv if val else 0
        except (OSError, ValueError):
            return None

    def read(self) -> tuple[int | None, int | None]:
        return self._read_pin(self._sbu1_path), self._read_pin(self._sbu2_path)


class ShellCmdSource(SBUSource):
    """Run external command that prints JSON: {\"sbu1_mV\": 3300, \"sbu2_mV\": 0}"""

    def __init__(self, command: str, timeout_sec: float = 1.0) -> None:
        self._command = command
        self._timeout = timeout_sec

    def read(self) -> tuple[int | None, int | None]:
        try:
            result = subprocess.run(
                self._command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
            data = json.loads(result.stdout)
            return int(data.get("sbu1_mV", 0)), int(data.get("sbu2_mV", 0))
        except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, ValueError):
            return None, None


class MockSource(SBUSource):
    """Fixed/mock values for simulation and CI."""

    def __init__(self, sbu1_mv: int, sbu2_mv: int) -> None:
        self._sbu1 = sbu1_mv
        self._sbu2 = sbu2_mv

    def read(self) -> tuple[int | None, int | None]:
        return self._sbu1, self._sbu2


class SemanticCANMapper:
    """Map application-layer semantic names to physical CAN interfaces.

    ADAS naming (openpilot/visionpilot):
      canmpc  → main processor camera (Bosch ADAS camera bus)
      canpwrt → powertrain bus

    Usage:
        mapper = SemanticCANMapper({"canmpc": "can0", "canpwrt": "can1"})
        physical = mapper.resolve("canmpc", orientation="normal")  # → "can0"
        physical = mapper.resolve("canmpc", orientation="flipped") # → "can1"
    """

    def __init__(self, normal_map: dict):
        self.normal_map = normal_map
        self.flipped_map = {k: self._swap(v) for k, v in normal_map.items()}

    @staticmethod
    def _swap(iface: str) -> str:
        return "can1" if iface == "can0" else "can0" if iface == "can1" else iface

    def resolve(self, semantic: str, orientation: str = "normal") -> str:
        if orientation == "flipped":
            return self.flipped_map.get(semantic, semantic)
        return self.normal_map.get(semantic, semantic)


class SBUDetector:
    """FrogPilot-compatible USB-C harness orientation detector."""

    STATUS_NC = "nc"
    STATUS_NORMAL = "normal"
    STATUS_FLIPPED = "flipped"

    def __init__(
        self,
        source: SBUSource,
        threshold_mv: int = 1650,
        invert_logic: bool = False,
    ):
        self.source = source
        self.threshold_mv = threshold_mv
        self.invert_logic = invert_logic
        self.sbu1_mv = 0
        self.sbu2_mv = 0
        self.status = self.STATUS_NC

    def tick(self) -> tuple[str, int, int]:
        """Detect orientation. Returns (status, sbu1_mV, sbu2_mV)."""
        sbu1, sbu2 = self.source.read()
        if sbu1 is None or sbu2 is None:
            sbu1, sbu2 = self.sbu1_mv, self.sbu2_mv
            self.status = self.STATUS_NC
        else:
            self.sbu1_mv, self.sbu2_mv = sbu1, sbu2
            self.status = self._detect_orientation(sbu1, sbu2)
        return self.status, self.sbu1_mv, self.sbu2_mv

    def _detect_orientation(self, sbu1: int, sbu2: int) -> str:
        if sbu1 >= self.threshold_mv and sbu2 >= self.threshold_mv:
            return self.STATUS_NC
        s1, s2 = (sbu2, sbu1) if self.invert_logic else (sbu1, sbu2)
        if s1 < s2:
            return self.STATUS_FLIPPED
        return self.STATUS_NORMAL


def create_source_from_env() -> SBUSource | None:
    """Create SBU source from environment variables (convenience)."""
    source_type = os.environ.get("SBU_SOURCE", "mock")
    if source_type == "adc":
        p1 = os.environ.get("SBU1_ADC_PATH", "")
        p2 = os.environ.get("SBU2_ADC_PATH", "")
        scale = float(os.environ.get("SBU_ADC_SCALE", "1.0"))
        if p1 and p2:
            return ADCSource(p1, p2, scale)
    elif source_type == "gpio":
        g1 = int(os.environ.get("SBU1_GPIO", "-1"))
        g2 = int(os.environ.get("SBU2_GPIO", "-1"))
        vdd = int(os.environ.get("SBU_VDD_MV", "3300"))
        if g1 >= 0 and g2 >= 0:
            return GPIOSource(g1, g2, vdd)
    elif source_type == "shell_cmd":
        cmd = os.environ.get("SBU_SHELL_CMD", "")
        if cmd:
            return ShellCmdSource(cmd)
    elif source_type == "mock":
        m1 = int(os.environ.get("SBU_MOCK_SBU1_MV", "3300"))
        m2 = int(os.environ.get("SBU_MOCK_SBU2_MV", "0"))
        return MockSource(m1, m2)
    return None


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="SBU orientation detector")
    parser.add_argument("--source", default="mock", choices=["adc", "gpio", "shell_cmd", "mock"])
    parser.add_argument("--sbu1-adc", default="")
    parser.add_argument("--sbu2-adc", default="")
    parser.add_argument("--adc-scale", type=float, default=1.0)
    parser.add_argument("--sbu1-gpio", type=int, default=-1)
    parser.add_argument("--sbu2-gpio", type=int, default=-1)
    parser.add_argument("--shell-cmd", default="")
    parser.add_argument("--mock-sbu1", type=int, default=3300)
    parser.add_argument("--mock-sbu2", type=int, default=0)
    parser.add_argument("--threshold", type=int, default=1650)
    parser.add_argument("--invert", action="store_true")
    parser.add_argument("--rate", type=float, default=8.0)
    args = parser.parse_args()

    if args.source == "adc":
        src = ADCSource(args.sbu1_adc, args.sbu2_adc, args.adc_scale)
    elif args.source == "gpio":
        src = GPIOSource(args.sbu1_gpio, args.sbu2_gpio)
    elif args.source == "shell_cmd":
        src = ShellCmdSource(args.shell_cmd)
    else:
        src = MockSource(args.mock_sbu1, args.mock_sbu2)

    det = SBUDetector(src, threshold_mv=args.threshold, invert_logic=args.invert)
    period = 1.0 / args.rate

    print(f"SBU detector: source={args.source} threshold={args.threshold}mV rate={args.rate}Hz")
    try:
        while True:
            status, s1, s2 = det.tick()
            print(f"SBU1={s1:4d}mV SBU2={s2:4d}mV → {status}")
            time.sleep(period)
    except KeyboardInterrupt:
        pass
