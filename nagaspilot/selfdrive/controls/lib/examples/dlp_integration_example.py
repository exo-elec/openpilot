#!/usr/bin/env python3
"""
Example integration of NpDlpController

This example demonstrates how to integrate the unified DLP controller
into existing lateral control systems with minimal changes.
"""

import time
from cereal import car, log
from openpilot.common.realtime import DT_MDL

# Import the DLP controller
from nagaspilot.selfdrive.controls.lib.np_dlp_controller import create_dlp_controller


class ExampleLateralController:
    """
    Example lateral controller showing DLP integration
    """
    
    def __init__(self, CP, params=None):
        """Initialize controller with DLP integration"""
        self.CP = CP
        self.params = params
        
        # Replace existing DesireHelper with DLP controller
        self.dlp_controller = create_dlp_controller(CP, params)
        
        # Keep reference for backward compatibility
        self.desire_helper = self.dlp_controller
        
        print("Example lateral controller initialized with DLP")
    
    def update(self, sm, lateral_active, v_ego, steer_limited_by_controls, 
               desired_curvature, calibrated_pose, curvature_limited):
        """
        Update lateral controller with DLP integration
        
        Args:
            sm: SubMaster with sensor data
            lateral_active: Whether lateral control is active
            v_ego: Vehicle speed (m/s)
            steer_limited_by_controls: Whether steering is limited
            desired_curvature: Target curvature from model
            calibrated_pose: Calibrated vehicle pose
            curvature_limited: Whether curvature is limited
            
        Returns:
            Tuple of (output_steer, angle_steers_des, status)
        """
        
        # Get comprehensive DLP status
        dlp_status = self.dlp_controller.update(sm, lateral_active, v_ego)
        
        # Use mode information for adaptive control
        lateral_gain = self._get_lateral_gain(dlp_status.mode)
        
        # Get desire value (backward compatible)
        desire = dlp_status.desire
        
        # Calculate steering output (simplified example)
        angle_steers_des = self._calculate_desired_angle(
            desired_curvature, v_ego, calibrated_pose
        )
        
        # Apply lateral control with mode-aware tuning
        output_steer = self._apply_lateral_control(
            angle_steers_des, sm['carState'].steeringAngleDeg, 
            lateral_gain, steer_limited_by_controls
        )
        
        # Create status message
        status = {
            'dlp_mode': dlp_status.mode,
            'dlp_confidence': dlp_status.confidence,
            'desire': desire,
            'transition_active': dlp_status.transition_active,
            'lca_active': dlp_status.mode == 'lca'
        }
        
        return output_steer, angle_steers_des, status
    
    def _get_lateral_gain(self, mode):
        """Get lateral gain based on DLP mode"""
        gain_map = {
            'laneful': 1.0,      # Normal gain for laneful
            'laneless': 0.7,     # Reduced gain for laneless
            'lca': 0.8,          # Moderate gain during LCA
            'transition': 0.9    # Slightly reduced during transitions
        }
        return gain_map.get(mode, 1.0)
    
    def _calculate_desired_angle(self, desired_curvature, v_ego, calibrated_pose):
        """Calculate desired steering angle (simplified)"""
        # Simplified calculation - real implementation would use vehicle model
        base_angle = desired_curvature * 180 / 3.14159  # Rough conversion
        speed_factor = max(0.5, min(1.0, v_ego / 30.0))  # Speed-dependent scaling
        return base_angle * speed_factor
    
    def _apply_lateral_control(self, angle_des, angle_current, gain, limited):
        """Apply lateral control with gain adjustment"""
        error = angle_des - angle_current
        
        # Simple proportional control
        output = gain * error * 0.5  # Base gain factor
        
        # Apply limits
        if limited:
            output = max(-0.8, min(0.8, output))
        else:
            output = max(-1.0, min(1.0, output))
        
        return output
    
    def get_dlp_status(self):
        """Get comprehensive DLP status for monitoring"""
        return self.dlp_controller.get_health_status()
    
    def get_dlp_debug(self):
        """Get DLP debug information""" 
        return self.dlp_controller.get_debug_info()


def create_mock_submaster():
    """Create mock SubMaster for testing"""
    class MockSubMaster:
        def __init__(self):
            self.valid = {'modelV2': True, 'carState': True}
            
            # Mock modelV2
            self.model_v2 = Mock()
            self.model_v2.laneLines = [
                Mock(prob=0.8), Mock(prob=0.9), 
                Mock(prob=0.85), Mock(prob=0.7)
            ]
            self.model_v2.roadEdges = [
                Mock(std=0.2), Mock(std=0.3)
            ]
            
            # Mock carState
            self.carstate = Mock()
            self.carstate.vEgo = 25.0
            self.carstate.steeringAngleDeg = 0.0
            self.carstate.steeringTorque = 0.0
            self.carstate.steeringPressed = False
            self.carstate.leftBlinker = False
            self.carstate.rightBlinker = False
            self.carstate.leftBlindspot = False
            self.carstate.rightBlindspot = False
            self.carstate.standstill = False
        
        def __getitem__(self, key):
            if key == 'modelV2':
                return self.model_v2
            elif key == 'carState':
                return self.carstate
            return None
    
    return MockSubMaster()


def demonstrate_basic_integration():
    """Demonstrate basic DLP integration"""
    print("=== Basic DLP Integration Demo ===")
    
    # Create car parameters
    CP = car.CarParams()
    
    # Create example controller
    controller = ExampleLateralController(CP)
    
    # Create mock data
    sm = create_mock_submaster()
    
    # Simulate normal driving
    print("\n1. Normal laneful driving:")
    for i in range(5):
        steer, angle, status = controller.update(
            sm, True, 25.0, False, 0.01, None, False
        )
        print(f"  Update {i+1}: mode={status['dlp_mode']}, "
              f"confidence={status['dlp_confidence']:.2f}, "
              f"desire={status['desire']}")
    
    # Simulate lane change
    print("\n2. Lane change scenario:")
    sm.carstate.leftBlinker = True
    
    for i in range(10):
        if i == 3:  # Apply steering after a few cycles
            sm.carstate.steeringPressed = True
            sm.carstate.steeringTorque = 1.0
        
        steer, angle, status = controller.update(
            sm, True, 25.0, False, 0.01, None, False
        )
        
        print(f"  LCA {i+1}: mode={status['dlp_mode']}, "
              f"lca_active={status['lca_active']}, "
              f"desire={status['desire']}")
    
    # Return to normal
    sm.carstate.leftBlinker = False
    sm.carstate.steeringPressed = False
    sm.carstate.steeringTorque = 0.0
    
    print("\n3. Return to normal driving:")
    for i in range(5):
        steer, angle, status = controller.update(
            sm, True, 25.0, False, 0.01, None, False
        )
        print(f"  Update {i+1}: mode={status['dlp_mode']}, "
              f"confidence={status['dlp_confidence']:.2f}")
    
    # Show final status
    print(f"\n4. Final DLP Status:")
    health = controller.get_dlp_status()
    for key, value in health.items():
        if isinstance(value, dict):
            print(f"  {key}:")
            for k, v in value.items():
                print(f"    {k}: {v}")
        else:
            print(f"  {key}: {value}")


def demonstrate_mode_switching():
    """Demonstrate automatic mode switching"""
    print("\n\n=== Mode Switching Demo ===")
    
    CP = car.CarParams()
    controller = ExampleLateralController(CP)
    sm = create_mock_submaster()
    
    print("\n1. Good lane conditions (laneful mode):")
    for i in range(5):
        status = controller.update(sm, True, 20.0, False, 0.005, None, False)[2]
        print(f"  Cycle {i+1}: {status['dlp_mode']} (confidence: {status['dlp_confidence']:.2f})")
    
    print("\n2. Poor lane conditions (switching to laneless):")
    # Degrade lane conditions
    sm.model_v2.laneLines[1].prob = 0.2  # Poor left lane
    sm.model_v2.laneLines[2].prob = 0.25  # Poor right lane
    sm.model_v2.roadEdges[0].std = 0.1   # Good left edge
    sm.model_v2.roadEdges[1].std = 0.15  # Good right edge
    
    for i in range(10):
        status = controller.update(sm, True, 15.0, False, 0.005, None, False)[2]
        print(f"  Cycle {i+1}: {status['dlp_mode']} (confidence: {status['dlp_confidence']:.2f})")
        if status['transition_active']:
            print(f"    -> Transition in progress")
    
    print("\n3. Lane conditions improve (back to laneful):")
    # Restore good lane conditions
    sm.model_v2.laneLines[1].prob = 0.85  # Good left lane
    sm.model_v2.laneLines[2].prob = 0.8   # Good right lane
    
    for i in range(10):
        status = controller.update(sm, True, 25.0, False, 0.005, None, False)[2]
        print(f"  Cycle {i+1}: {status['dlp_mode']} (confidence: {status['dlp_confidence']:.2f})")


def demonstrate_backward_compatibility():
    """Demonstrate backward compatibility with existing code"""
    print("\n\n=== Backward Compatibility Demo ===")
    
    from nagaspilot.selfdrive.controls.lib.np_dlp_controller import DesireHelperWrapper
    
    CP = car.CarParams()
    
    # Create DLP controller
    dlp_controller = create_dlp_controller(CP)
    
    # Wrap for backward compatibility
    desire_helper = DesireHelperWrapper(dlp_controller)
    
    # Create mock carstate (existing interface)
    class MockCarState:
        def __init__(self):
            self.vEgo = 25.0
            self.leftBlinker = False
            self.rightBlinker = False
            self.steeringPressed = False
            self.steeringTorque = 0.0
            self.leftBlindspot = False
            self.rightBlindspot = False
    
    carstate = MockCarState()
    
    print("\n1. Using existing DesireHelper interface:")
    
    # Normal driving (existing code pattern)
    for i in range(3):
        desire = desire_helper.update(carstate, True, 0.02, False, False)
        print(f"  Cycle {i+1}: desire={desire}, "
              f"LCA state={desire_helper.lane_change_state}")
    
    # Lane change (existing code pattern)
    carstate.leftBlinker = True
    desire = desire_helper.update(carstate, True, 0.02, False, False)
    print(f"  With blinker: desire={desire}, "
          f"LCA state={desire_helper.lane_change_state}")
    
    # Apply steering (existing code pattern)
    carstate.steeringPressed = True
    carstate.steeringTorque = 1.0
    desire = desire_helper.update(carstate, True, 0.02, False, False)
    print(f"  With steering: desire={desire}, "
          f"LCA state={desire_helper.lane_change_state}")
    
    print("\n2. Underlying DLP controller status:")
    debug_info = dlp_controller.get_debug_info()
    print(f"  Current mode: {debug_info['current_mode']}")
    print(f"  LCA state: {debug_info['lca_state']['state']}")
    print(f"  Frame count: {debug_info['performance']['frame_count']}")


def demonstrate_advanced_features():
    """Demonstrate advanced DLP features"""
    print("\n\n=== Advanced Features Demo ===")
    
    CP = car.CarParams()
    controller = ExampleLateralController(CP)
    sm = create_mock_submaster()
    
    print("\n1. Health monitoring:")
    health = controller.get_dlp_status()
    print(f"  Overall health: {health['current_mode']}")
    print(f"  Mode switches: {health['mode_switch_count']}")
    print(f"  Total frames: {health['frame_count']}")
    
    print("\n2. Debug information:")
    debug = controller.get_dlp_debug()
    print(f"  Configuration: {debug['configuration']}")
    print(f"  Performance: {debug['performance']}")
    
    print("\n3. Adaptive control based on mode:")
    modes_to_test = ['laneful', 'laneless', 'lca']
    
    for mode in modes_to_test:
        # Simulate different modes by manually setting (for demo)
        controller.dlp_controller.current_mode = mode
        
        # Get lateral gain for this mode
        gain = controller._get_lateral_gain(mode)
        print(f"  {mode.upper()} mode: lateral gain = {gain}")
    
    print("\n4. Performance metrics:")
    import time
    start_time = time.time()
    
    # Run multiple updates
    for i in range(100):
        controller.update(sm, True, 25.0, False, 0.01, None, False)
    
    end_time = time.time()
    print(f"  100 updates in {end_time - start_time:.3f}s")
    print(f"  Average: {(end_time - start_time)/100*1000:.1f}ms per update")


if __name__ == "__main__":
    # Import Mock class here to avoid issues
    from unittest.mock import Mock
    
    print("NagasPilot DLP Controller Integration Examples")
    print("=" * 50)
    
    # Run all demonstrations
    demonstrate_basic_integration()
    demonstrate_mode_switching()
    demonstrate_backward_compatibility()
    demonstrate_advanced_features()
    
    print("\n" + "=" * 50)
    print("All demonstrations completed successfully!")
    print("\nKey benefits of DLP Controller:")
    print("- Single unified controller for all lane modes")
    print("- Drop-in replacement for DesireHelper")
    print("- Automatic mode switching based on conditions")
    print("- Comprehensive monitoring and debugging")
    print("- Production-ready with error handling")
    print("- Follows NagasPilot np_* controller patterns")