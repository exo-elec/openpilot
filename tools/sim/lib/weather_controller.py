"""CARLA weather controller using native WeatherParameters presets.

Maps EOPSimWeather param names directly to carla.WeatherParameters.
No custom dataclasses — just native CARLA weather presets.

Usage:
  params put EOPSimWeather HardRainNoon
  # The bridge applies the matching carla.WeatherParameters on next tick.
"""
from __future__ import annotations

from openpilot.common.params import Params


def _build_presets() -> dict:
  """Build weather preset dict from carla.WeatherParameters.

  Returns an empty dict if carla is not installed (non-sim environments).
  """
  try:
    import carla
    return {
      "clear_day": carla.WeatherParameters.ClearNoon,
      "clear_night": carla.WeatherParameters.ClearNight,
      "rain": carla.WeatherParameters(
        cloudiness=80.0,
        precipitation=60.0,
        precipitation_deposits=60.0,
        wind_intensity=20.0,
        sun_azimuth_angle=0.0,
        sun_altitude_angle=30.0,
        fog_density=10.0,
        wetness=60.0,
      ),
      "heavy_rain": carla.WeatherParameters.HardRainNoon,
      "fog": carla.WeatherParameters(
        cloudiness=50.0,
        precipitation=0.0,
        precipitation_deposits=0.0,
        wind_intensity=5.0,
        sun_azimuth_angle=0.0,
        sun_altitude_angle=20.0,
        fog_density=80.0,
        fog_distance=10.0,
        wetness=20.0,
      ),
      "overcast": carla.WeatherParameters.CloudyNoon,
    }
  except ImportError:
    return {}


# carla.WeatherParameters objects keyed by EOP weather name.
WEATHER_PRESETS: dict = _build_presets()


class WeatherController:
  """Applies CARLA native weather presets param-driven."""

  def __init__(self):
    self.params = Params()
    self._applied_name: str | None = None

  def _read_param(self) -> str | None:
    name = self.params.get("EOPSimWeather") or ""
    name = name.lower().strip()
    if not name or name not in WEATHER_PRESETS:
      return None
    return name

  def update(self, carla_world) -> bool:
    """Apply weather preset if param has changed. Returns True if changed."""
    import carla
    preset_name = self._read_param()
    if preset_name is None:
      if self._applied_name is not None:
        carla_world.set_weather(carla.WeatherParameters.ClearNoon)
        self._applied_name = None
        print("WeatherController: reset to ClearNoon")
        return True
      return False

    if preset_name == self._applied_name:
      return False

    weather = WEATHER_PRESETS[preset_name]
    carla_world.set_weather(weather)
    self._applied_name = preset_name
    print(f"WeatherController: applied '{preset_name}'")
    return True
