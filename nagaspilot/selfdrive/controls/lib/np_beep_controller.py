#!/usr/bin/env python3
"""
NagasPilot Beep Controller - Audio alert system for enhanced driver feedback
Based on DragonPilot's beepd but integrated into NagasPilot architecture
"""

import os
import time
from cereal import log
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params
from openpilot.common.swaglog import cloudlog

# GPIO pin for beeper (same as DragonPilot)
BEEP_GPIO_PIN = 42

class NpBeepController:
    """
    NagasPilot Beep Controller for audio alerts
    Provides enhanced driver feedback through GPIO-controlled beeper
    """
    
    def __init__(self):
        self.enabled = False
        self.params = Params()
        self.beep_duration = 0.1  # seconds
        self.beep_frequency = 1000  # Hz
        
    def update(self, sm):
        """Update beep controller based on system state"""
        if not self.enabled:
            return
            
        # Check if beeping should be active
        should_beep = self.should_beep(sm)
        
        if should_beep:
            self.trigger_beep()
            
    def should_beep(self, sm):
        """Determine if beeping should be triggered"""
        # Check for conditions that warrant beeping
        if sm.updated['selfdriveState']:
            selfdrive_state = sm['selfdriveState']
            
            # Beep on engagement/disengagement
            if selfdrive_state.getEnabled() and not getattr(self, 'prev_enabled', False):
                return True
            if not selfdrive_state.getEnabled() and getattr(self, 'prev_enabled', False):
                return True
                
            self.prev_enabled = selfdrive_state.getEnabled()
            
        # Check for warning conditions
        if sm.updated['controlsState']:
            controls_state = sm['controlsState']
            if controls_state.getAlertType() in [log.ControlsState.AlertType.warning, log.ControlsState.AlertType.critical]:
                return True
                
        return False
        
    def trigger_beep(self):
        """Trigger a beep sound through GPIO"""
        try:
            # Try to use GPIO beeping (Linux systems)
            if os.path.exists('/sys/class/gpio'):
                self._gpio_beep()
            else:
                # Fallback to console beep (for development/testing)
                self._console_beep()
        except Exception as e:
            cloudlog.warning(f"Beep failed: {e}")
            
    def _gpio_beep(self):
        """GPIO-based beeping for hardware systems"""
        try:
            # Export GPIO pin if not already exported
            gpio_export_path = f'/sys/class/gpio/gpio{BEEP_GPIO_PIN}'
            if not os.path.exists(gpio_export_path):
                with open('/sys/class/gpio/export', 'w') as f:
                    f.write(str(BEEP_GPIO_PIN))
                    
            # Set GPIO as output
            with open(f'{gpio_export_path}/direction', 'w') as f:
                f.write('out')
                
            # Trigger beep
            with open(f'{gpio_export_path}/value', 'w') as f:
                f.write('1')
            time.sleep(self.beep_duration)
            with open(f'{gpio_export_path}/value', 'w') as f:
                f.write('0')
                
        except Exception as e:
            cloudlog.warning(f"GPIO beep failed: {e}")
            raise
            
    def _console_beep(self):
        """Console beep for development systems"""
        try:
            # Use console beep as fallback
            print(f"\a", end='', flush=True)  # ASCII bell character
            time.sleep(self.beep_duration)
        except Exception as e:
            cloudlog.warning(f"Console beep failed: {e}")
            
    def set_enabled(self, enabled):
        """Enable/disable beep controller"""
        if self.enabled != enabled:
            self.enabled = enabled
            cloudlog.info(f"NpBeepController {'enabled' if enabled else 'disabled'}")
            
    def is_enabled(self):
        """Check if beep controller is enabled"""
        return self.enabled
        
    def get_status(self):
        """Get controller status for monitoring"""
        return {
            'enabled': self.enabled,
            'gpio_pin': BEEP_GPIO_PIN,
            'beep_duration': self.beep_duration,
            'beep_frequency': self.beep_frequency
        }

def main():
    """Main function for standalone testing"""
    import cereal.messaging as messaging
    
    cloudlog.info("Starting NpBeepController")
    
    # Create controller
    beep_controller = NpBeepController()
    
    # Check if enabled via parameter
    params = Params()
    if params.get_bool("np_device_beep"):
        beep_controller.set_enabled(True)
        cloudlog.info("NpBeepController enabled via parameter")
    else:
        cloudlog.info("NpBeepController disabled (set np_device_beep to enable)")
        return
        
    # Setup messaging
    pm = messaging.PubMaster([])
    sm = messaging.SubMaster(['selfdriveState', 'controlsState', 'carState'])
    
    while True:
        sm.update()
        beep_controller.update(sm)
        time.sleep(DT_MDL)  # Run at model frequency

if __name__ == "__main__":
    main()
