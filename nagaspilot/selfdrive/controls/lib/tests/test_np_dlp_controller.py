#!/usr/bin/env python3
"""
Comprehensive test suite for NpDlpController - Unified Dynamic Lane Profile
"""

import unittest
import numpy as np
import time
from unittest.mock import Mock, MagicMock, patch
from dataclasses import dataclass

from cereal import log, custom
from openpilot.common.realtime import DT_MDL
from openpilot.common.params import Params

from nagaspilot.selfdrive.controls.lib.np_dlp_controller import (
    NpDlpController, DLPMode, LCAMode, DLPStatus, calculate_transition_progress
)


class TestDLPModeManagement(unittest.TestCase):
    """Test DLP mode management and transitions"""
    
    def setUp(self):
        self.mock_cp = Mock()
        self.mock_params = Mock()
        self.controller = NpDlpController(self.mock_cp, self.mock_params)
        self.controller.set_enabled(True)
    
    def test_initial_state(self):
        """Test initial controller state"""
        self.assertEqual(self.controller.current_mode, DLPMode.LANEFUL)
        self.assertEqual(self.controller.current_lca_mode, LCAMode.NUDGE)
        self.assertEqual(self.controller.desire, log.Desire.none)
        self.assertTrue(self.controller.enabled)
    
    def test_mode_transitions(self):
        """Test smooth transitions between modes"""
        # Start in laneful mode
        self.assertEqual(self.controller.current_mode, DLPMode.LANEFUL)
        self.assertEqual(self.controller.mode_transition_progress, 1.0)
        
        # Simulate conditions that trigger laneless
        sm = self._create_mock_submaster(lane_confidence=0.2, v_ego=15.0)
        
        # Update multiple times to see transition
        for i in range(10):
            status = self.controller.update(sm, True, 15.0, True, False)
            
        # Should transition to laneless
        self.assertEqual(self.controller.current_mode, DLPMode.LANELESS)
        self.assertGreater(self.controller.mode_transition_progress, 0.5)
    
    def test_lca_mode_management(self):
        """Test LCA mode management"""
        # Test different LCA modes
        for mode_name in ["OFF", "NUDGE", "TIMED", "ADAPTIVE"]:
            self.controller.set_lca_mode(mode_name)
            self.assertEqual(self.controller.current_lca_mode.name, mode_name)
    
    def test_mode_hysteresis(self):
        """Test mode switching hysteresis to prevent chattering"""
        # Create conditions that oscillate around threshold
        sm = self._create_mock_submaster(lane_confidence=0.65, v_ego=20.0)
        
        # Multiple updates with slightly changing conditions
        for i in range(20):
            # Slightly vary conditions
            confidence = 0.65 + (i % 3 - 1) * 0.05
            sm = self._create_mock_submaster(lane_confidence=confidence, v_ego=20.0)
            status = self.controller.update(sm, True, 20.0, True, False)
        
        # Should not have excessive mode transitions
        self.assertLess(self.controller.mode_transitions, 5)  # Should be stable


class TestLanelessFunctionality(unittest.TestCase):
    """Test laneless mode functionality"""
    
    def setUp(self):
        self.mock_cp = Mock()
        self.mock_params = Mock()
        self.controller = NpDlpController(self.mock_cp, self.mock_params)
        self.controller.set_enabled(True)
    
    def test_laneless_activation(self):
        """Test laneless mode activation"""
        # Create poor lane conditions
        sm = self._create_mock_submaster(lane_confidence=0.2, v_ego=15.0)
        
        status = self.controller.update(sm, True, 15.0, True, False)
        
        # Should activate laneless mode
        self.assertEqual(status.mode, DLPMode.LANELESS)
        self.assertTrue(status.active)
        self.assertGreater(status.confidence, 0.5)
    
    def test_laneless_safety_parameters(self):
        """Test safety parameters in laneless mode"""
        # Force laneless mode
        sm = self._create_mock_submaster(lane_confidence=0.1, v_ego=15.0)
        
        for i in range(5):
            status = self.controller.update(sm, True, 15.0, True, False)
        
        # Should have reduced lateral acceleration
        self.assertEqual(status.mode, DLPMode.LANELESS)
        # Lateral acceleration should be reduced (factor 0.7)
        self.assertLess(status.lateral_accel, self.controller.max_lateral_accel * 0.8)
    
    def test_laneless_to_laneful_transition(self):
        """Test transition from laneless back to laneful"""
        # Start in laneless mode
        sm_poor = self._create_mock_submaster(lane_confidence=0.2, v_ego=15.0)
        self.controller.update(sm_poor, True, 15.0, True, False)
        self.assertEqual(self.controller.current_mode, DLPMode.LANELESS)
        
        # Improve conditions
        sm_good = self._create_mock_submaster(lane_confidence=0.9, v_ego=25.0)
        
        # Multiple updates to see transition
        for i in range(10):
            status = self.controller.update(sm_good, True, 25.0, True, False)
        
        # Should transition back to laneful
        self.assertEqual(self.controller.current_mode, DLPMode.LANEFUL)


class TestLCAFunctionality(unittest.TestCase):
    """Test Lane Change Assistance functionality"""
    
    def setUp(self):
        self.mock_cp = Mock()
        self.mock_params = Mock()
        self.controller = NpDlpController(self.mock_cp, self.mock_params)
        self.controller.set_enabled(True)
    
    def test_lca_basic_activation(self):
        """Test basic LCA activation"""
        # Create lane change conditions
        sm = self._create_mock_submaster(
            left_blinker=True,
            right_blinker=False,
            v_ego=25.0,
            steering_torque=1.0
        )
        
        status = self.controller.update(sm, True, 25.0, True, False)
        
        # Should detect lane change request
        self.assertEqual(status.mode, DLPMode.LCA)
        self.assertIn(status.desire, [log.Desire.laneChangeLeft, log.Desire.laneChangeRight])
    
    def test_lca_blindspot_detection(self):
        """Test LCA blind spot detection"""
        # Create lane change with blind spot
        sm = self._create_mock_submaster(
            left_blinker=True,
            right_blinker=False,
            v_ego=25.0,
            left_blindspot=True
        )
        
        status = self.controller.update(sm, True, 25.0, True, False)
        
        # Should detect blind spot
        self.assertFalse(status.blindspot_clear)
        # Should not complete lane change
        self.assertNotEqual(status.desire, log.Desire.laneChangeLeft)
    
    def test_lca_speed_thresholds(self):
        """Test LCA speed thresholds"""
        # Below minimum speed
        sm = self._create_mock_submaster(
            left_blinker=True,
            v_ego=10.0  # Below 20 mph threshold
        )
        
        status = self.controller.update(sm, True, 10.0, True, False)
        
        # Should not activate LCA below minimum speed
        self.assertNotEqual(status.mode, DLPMode.LCA)
    
    def test_lca_timed_mode(self):
        """Test LCA timed mode"""
        self.controller.set_lca_mode("TIMED")
        self.controller.lca_delay = 1.0  # 1 second delay
        
        # Start lane change
        sm = self._create_mock_submaster(
            left_blinker=True,
            v_ego=25.0,
            steering_torque=0.0  # No steering input
        )
        
        # First update should start timer
        status = self.controller.update(sm, True, 25.0, True, False)
        self.assertEqual(status.mode, DLPMode.LCA)
        
        # Wait for timer to complete
        for i in range(int(1.0 / DT_MDL) + 5):  # Wait 1 second + buffer
            status = self.controller.update(sm, True, 25.0, True, False)
        
        # Should complete after timer
        self.assertEqual(status.desire, log.Desire.laneChangeLeft)


class TestPerformanceAndReliability(unittest.TestCase):
    """Test performance characteristics and reliability"""
    
    def setUp(self):
        self.mock_cp = Mock()
        self.mock_params = Mock()
        self.controller = NpDlpController(self.mock_cp, self.mock_params)
        self.controller.set_enabled(True)
    
    def test_performance_characteristics(self):
        """Test performance characteristics"""
        sm = self._create_mock_submaster(lane_confidence=0.8, v_ego=25.0)
        
        # Measure update time
        start_time = time.time()
        for i in range(100):  # 100 updates
            status = self.controller.update(sm, True, 25.0, True, False)
        end_time = time.time()
        
        avg_time = (end_time - start_time) / 100
        print(f"Average update time: {avg_time*1000:.3f}ms")
        
        # Should be very fast
        self.assertLess(avg_time, 0.001)  # Less than 1ms
    
    def test_memory_efficiency(self):
        """Test memory usage efficiency"""
        # Create controller and run many updates
        sm = self._create_mock_submaster(lane_confidence=0.8, v_ego=25.0)
        
        # Run many updates to check for memory leaks
        for i in range(1000):
            status = self.controller.update(sm, True, 25.0, True, False)
        
        # Check that metrics don't grow unbounded
        self.assertLess(self.controller.mode_transitions, 50)  # Should be stable
        self.assertLess(len(self.controller.mode_history), 101)  # Limited history
    
    def test_error_handling(self):
        """Test error handling and graceful degradation"""
        # Test with invalid data
        sm_invalid = Mock()
        sm_invalid.valid = {}  # No valid data
        
        status = self.controller.update(sm_invalid, True, 25.0, True, False)
        
        # Should handle gracefully
        self.assertEqual(status.mode, DLPMode.LANEFUL)
        self.assertEqual(status.confidence, 0.0)
        self.assertFalse(status.active)
    
    def test_concurrent_mode_operation(self):
        """Test concurrent operation of multiple modes"""
        # Create complex scenario with mode switching
        scenarios = [
            {"lane_confidence": 0.9, "v_ego": 30.0, "expected": DLPMode.LANEFUL},
            {"lane_confidence": 0.2, "v_ego": 15.0, "expected": DLPMode.LANELESS},
            {"left_blinker": True, "v_ego": 25.0, "expected": DLPMode.LCA},
        ]
        
        for scenario in scenarios:
            sm = self._create_mock_submaster(**scenario)
            status = self.controller.update(sm, True, scenario["v_ego"], True, False)
            
            self.assertEqual(status.mode, scenario["expected"])


class TestRealWorldScenarios(unittest.TestCase):
    """Test real-world driving scenarios"""
    
    def setUp(self):
        self.mock_cp = Mock()
        self.mock_params = Mock()
        self.controller = NpDlpController(self.mock_cp, self.mock_params)
        self.controller.set_enabled(True)
    
    def test_highway_construction_zone(self):
        """Test highway construction zone scenario"""
        # Simulate construction zone conditions
        sm = self._create_mock_submaster(
            lane_confidence=0.3,  # Poor lane detection
            lane_width=2.0,       # Narrow lanes
            v_ego=25.0            # Highway speed
        )
        
        # Multiple updates to see behavior
        for i in range(10):
            status = self.controller.update(sm, True, 25.0, True, False)
        
        # Should use laneless mode for better performance
        self.assertEqual(status.mode, DLPMode.LANELESS)
        self.assertGreater(status.confidence, 0.5)
        print(f"Construction zone: mode={status.mode}, confidence={status.confidence:.2f}")
    
    def test_snowy_conditions(self):
        """Test snowy/winter road conditions"""
        # Simulate snowy conditions
        sm = self._create_mock_submaster(
            lane_confidence=0.2,  # Snow covering lane lines
            v_ego=15.0,           # Reduced speed
            weather_factor=0.6    # Poor weather
        )
        
        status = self.controller.update(sm, True, 15.0, True, False)
        
        # Should activate laneless mode
        self.assertEqual(status.mode, DLPMode.LANELESS)
        self.assertTrue(status.active)
        print(f"Snowy conditions: mode={status.mode}, confidence={status.confidence:.2f}")
    
    def test_rural_road_conditions(self):
        """Test rural road with faded markings"""
        # Simulate rural road
        sm = self._create_mock_submaster(
            lane_confidence=0.4,  # Faded markings
            lane_width=2.2,       # Narrow road
            v_ego=18.0            # Rural speed
        )
        
        for i in range(5):
            status = self.controller.update(sm, True, 18.0, True, False)
        
        # Should handle gracefully with laneless
        self.assertIn(status.mode, [DLPMode.LANEFUL, DLPMode.LANELESS])
        print(f"Rural road: mode={status.mode}, confidence={status.confidence:.2f}")
    
    def test_emergency_lane_change(self):
        """Test emergency lane change scenario"""
        # Start in normal conditions
        sm_normal = self._create_mock_submaster(lane_confidence=0.8, v_ego=25.0)
        self.controller.update(sm_normal, True, 25.0, True, False)
        
        # Simulate emergency (sudden obstacle)
        sm_emergency = self._create_mock_submaster(
            left_blinker=True,
            v_ego=25.0,
            steering_torque=2.0,  # Strong steering input
            left_blindspot=False  # Clear path
        )
        
        status = self.controller.update(sm_emergency, True, 25.0, True, False)
        
        # Should respond to emergency input
        self.assertEqual(status.mode, DLPMode.LCA)
        self.assertEqual(status.desire, log.Desire.laneChangeLeft)
        print(f"Emergency lane change: mode={status.mode}, desire={status.desire}")


class TestBackwardCompatibility(unittest.TestCase):
    """Test backward compatibility with existing systems"""
    
    def setUp(self):
        self.mock_cp = Mock()
        self.mock_params = Mock()
        self.controller = NpDlpController(self.mock_cp, self.mock_params)
        self.controller.set_enabled(True)
    
    def test_desire_helper_compatibility(self):
        """Test compatibility with existing DesireHelper interface"""
        # Test that get_desire() works like old DesireHelper
        sm = self._create_mock_submaster(lane_confidence=0.8, v_ego=25.0)
        
        status = self.controller.update(sm, True, 25.0, True, False)
        desire = self.controller.get_desire()
        
        # Should return valid desire value
        self.assertIn(desire, [log.Desire.none, log.Desire.laneChangeLeft, log.Desire.laneChangeRight])
        self.assertEqual(desire, status.desire)  # Consistency check
    
    def test_status_compatibility(self):
        """Test status object compatibility"""
        sm = self._create_mock_submaster(lane_confidence=0.8, v_ego=25.0)
        
        status = self.controller.update(sm, True, 25.0, True, False)
        health = self.controller.get_health_status()
        debug = self.controller.get_debug_info()
        
        # All should return valid data structures
        self.assertIsInstance(status, DLPStatus)
        self.assertIsInstance(health, dict)
        self.assertIsInstance(debug, dict)
        
        # Should have expected keys
        self.assertIn("current_mode", health)
        self.assertIn("confidence", health)
        self.assertIn("mode_transitions", debug)
    
    def test_parameter_compatibility(self):
        """Test parameter system compatibility"""
        # Test parameter loading
        self.controller.set_enabled(False)
        self.assertFalse(self.controller.enabled)
        
        self.controller.set_enabled(True)
        self.assertTrue(self.controller.enabled)
        
        # Test LCA mode setting
        self.controller.set_lca_mode("TIMED")
        self.assertEqual(self.controller.current_lca_mode, LCAMode.TIMED)


# Helper functions for testing
def create_mock_submaster(lane_confidence=0.8, v_ego=25.0, left_blinker=False, right_blinker=False,
                         steering_torque=0.0, left_blindspot=False, right_blindspot=False,
                         lane_width=3.5, weather_factor=1.0, road_curvature=0.0):
    """Create a mock SubMaster for testing"""
    
    class MockLaneLine:
        def __init__(self, prob=0.8):
            self.prob = prob
            self.x = [0, 10, 20, 30, 40]
            self.y = [0, 0.1, 0.2, 0.3, 0.4]
    
    class MockRoadEdge:
        def __init__(self, prob=0.7):
            self.prob = prob
            self.x = [0, 10, 20, 30, 40]
            self.y = [-0.5, -0.4, -0.3, -0.2, -0.1]
    
    class MockCarState:
        def __init__(self, v_ego=25.0, left_blinker=False, right_blinker=False,
                    steering_torque=0.0, left_blindspot=False, right_blindspot=False):
            self.vEgo = v_ego
            self.leftBlinker = left_blinker
            self.rightBlinker = right_blinker
            self.steeringPressed = abs(steering_torque) > 0.1
            self.steeringTorque = steering_torque
            self.leftBlindspot = left_blindspot
            self.rightBlindspot = right_blindspot
            self.brakePressed = False
            self.standstill = False
    
    class MockModelV2:
        def __init__(self, lane_confidence=0.8, lane_width=3.5):
            # Create lane lines with specified confidence
            self.laneLines = [
                MockLaneLine(lane_confidence - 0.1),  # Far left
                MockLaneLine(lane_confidence),        # Left
                MockLaneLine(lane_confidence),        # Right
                MockLaneLine(lane_confidence - 0.1)   # Far right
            ]
            self.roadEdges = [
                MockRoadEdge(lane_confidence - 0.2),  # Left edge
                MockRoadEdge(lane_confidence - 0.2)   # Right edge
            ]
            self.position = Mock()
            self.position.x = [0, 10, 20, 30, 40]
            self.orientationRate = Mock()
            self.orientationRate.z = [road_curvature] * 5
            self.meta = Mock()
            self.meta.laneChangeState = 0
            self.meta.laneChangeDirection = 0
    
    class MockWeather:
        def __init__(self, factor=1.0):
            self.precipitation = 0.0 if factor > 0.9 else 0.5
            self.visibility = 1000.0 if factor > 0.7 else 500.0
    
    class MockSubMaster:
        def __init__(self, lane_confidence=0.8, v_ego=25.0, left_blinker=False, 
                    right_blinker=False, steering_torque=0.0, left_blindspot=False, 
                    right_blindspot=False, lane_width=3.5, weather_factor=1.0, 
                    road_curvature=0.0):
            self.valid = {
                'carState': True,
                'modelV2': True,
                'liveParameters': True,
                'weather': True
            }
            self.carState = MockCarState(v_ego, left_blinker, right_blinker, 
                                       steering_torque, left_blindspot, right_blindspot)
            self.modelV2 = MockModelV2(lane_confidence, lane_width)
            self.liveParameters = Mock()
            self.liveParameters.angleOffsetDeg = 0.0
            self.weather = MockWeather(weather_factor)
        
        def __getitem__(self, key):
            return getattr(self, key, None)
    
    return MockSubMaster(lane_confidence, v_ego, left_blinker, right_blinker,
                        steering_torque, left_blindspot, right_blindspot,
                        lane_width, weather_factor, road_curvature)


if __name__ == '__main__':
    unittest.main()