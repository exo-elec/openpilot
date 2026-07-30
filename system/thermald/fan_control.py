"""Fan control with hysteresis bands to prevent oscillation.

Implements proportional fan control within overlapping temperature bands
for smooth, stable thermal management.
"""
from enum import IntEnum
from dataclasses import dataclass
from collections import OrderedDict


class ThermalStatus(IntEnum):
    """Thermal status levels."""
    GREEN = 0    # Normal operation - fan off or low
    YELLOW = 1   # Elevated - increase fan
    RED = 2      # High - maximum cooling
    DANGER = 3   # Critical - throttling required


@dataclass
class ThermalBand:
    """Temperature band definition with fan speed range.
    
    Overlapping bands provide hysteresis to prevent rapid
    oscillation between states.
    
    Args:
        min_temp: Lower bound (exit band below this)
        max_temp: Upper bound (enter next band above this)
        min_fan: Fan speed at lower bound (%)
        max_fan: Fan speed at upper bound (%)
    """
    min_temp: float
    max_temp: float
    min_fan: int
    max_fan: int


# Standard thermal bands for RK3588 (ExoPilot 01M)
# Overlapping ranges provide hysteresis:
# - YELLOW starts at 75° but doesn't exit until 80°
# - RED starts at 88° but doesn't exit until 96°
DEFAULT_BANDS = OrderedDict({
    ThermalStatus.GREEN:  ThermalBand(float('-inf'), 80.0, 0, 0),
    ThermalStatus.YELLOW: ThermalBand(75.0, 96.0, 30, 50),
    ThermalStatus.RED:    ThermalBand(88.0, 107.0, 50, 80),
    ThermalStatus.DANGER: ThermalBand(94.0, float('inf'), 100, 100),
})


class FanController:
    """Proportional fan controller with hysteresis.
    
    Uses overlapping temperature bands to prevent rapid fan speed
    changes. Temperature smoothing reduces noise-induced fluctuations.
    
    Args:
        bands: Temperature bands (default: DEFAULT_BANDS)
        temp_tau: Temperature smoothing time constant in seconds
        min_fan_speed: Minimum fan speed when active (%)
    
    Example:
        >>> controller = FanController()
        >>> speed = controller.update(max_temp=78.0, dt=1.0)
        >>> print(f"Fan speed: {speed}%")
        Fan speed: 36%
    """
    
    def __init__(self, bands=None, temp_tau: float = 5.0, min_fan_speed: int = 0):
        self.bands = bands or DEFAULT_BANDS
        self.temp_tau = temp_tau
        self.min_fan_speed = min_fan_speed
        
        self._status = ThermalStatus.GREEN
        self._smoothed_temp = 0.0
        self._current_fan_speed = 0
    
    def _smooth_temperature(self, new_temp: float, dt: float) -> float:
        """Apply first-order low-pass filter to temperature.
        
        Reduces noise-induced fluctuations for smoother fan control.
        """
        if self._smoothed_temp == 0.0:
            return new_temp
        
        alpha = dt / (dt + self.temp_tau)
        return (1.0 - alpha) * self._smoothed_temp + alpha * new_temp
    
    def _update_status(self, temp: float) -> ThermalStatus:
        """Update thermal status with hysteresis.
        
        Only changes status when crossing band boundaries,
        preventing oscillation.
        """
        current_band = self.bands[self._status]
        
        # Check if we should move to a lower band
        if temp < current_band.min_temp and self._status > ThermalStatus.GREEN:
            return ThermalStatus(self._status.value - 1)
        
        # Check if we should move to a higher band
        if temp > current_band.max_temp and self._status < ThermalStatus.DANGER:
            return ThermalStatus(self._status.value + 1)
        
        return self._status
    
    def _calculate_fan_speed(self, temp: float) -> int:
        """Calculate fan speed within current band.
        
        Uses proportional control: fan speed scales linearly
        within the current temperature band.
        """
        band = self.bands[self._status]
        
        if self._status == ThermalStatus.GREEN:
            return self.min_fan_speed
        
        if self._status == ThermalStatus.DANGER:
            return 100
        
        # Proportional control within band
        temp_range = band.max_temp - band.min_temp
        if temp_range <= 0:
            return band.min_fan
        
        temp_ratio = (temp - band.min_temp) / temp_range
        fan_speed = band.min_fan + temp_ratio * (band.max_fan - band.min_fan)
        
        return int(max(0, min(100, fan_speed)))
    
    def update(self, max_temp: float, dt: float) -> int:
        """Update fan controller with new temperature reading.
        
        Args:
            max_temp: Maximum temperature across all zones (Celsius)
            dt: Time since last update (seconds)
        
        Returns:
            Target fan speed (0-100%)
        """
        # Smooth temperature
        self._smoothed_temp = self._smooth_temperature(max_temp, dt)
        
        # Update status with hysteresis
        self._status = self._update_status(self._smoothed_temp)
        
        # Calculate fan speed
        self._current_fan_speed = self._calculate_fan_speed(self._smoothed_temp)
        
        return self._current_fan_speed
    
    @property
    def status(self) -> ThermalStatus:
        """Current thermal status."""
        return self._status
    
    @property
    def smoothed_temperature(self) -> float:
        """Smoothed temperature reading."""
        return self._smoothed_temp
    
    def get_status_name(self) -> str:
        """Get human-readable status name."""
        return self._status.name
    
    def should_throttle(self) -> bool:
        """Check if thermal throttling is required."""
        return self._status == ThermalStatus.DANGER
    
    def should_shutdown(self, critical_temp: float = 100.0) -> bool:
        """Check if emergency shutdown is required.
        
        Args:
            critical_temp: Absolute critical temperature threshold
        """
        return self._smoothed_temp >= critical_temp
