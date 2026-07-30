#!/usr/bin/env python3
"""
Unified Costmap Generation - Using centralized HAL.

All compute goes through InferenceD HAL (GPU/CPU auto-selection).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from openpilot.common.swaglog import cloudlog

# Centralized HAL - ONLY way to access hardware
from openpilot.system.inferenced import InferenceClient, BackendType


@dataclass
class GridObject:
    """Object in BEV grid."""
    x: float
    y: float
    class_id: int
    confidence: float


@dataclass
class CostmapConfig:
    """Costmap configuration."""
    grid_h: int = 128
    grid_w: int = 128
    resolution: float = 0.25
    safety_margin: float = 1.0


class CostmapGenerator:
    """
    Costmap generator using centralized HAL.
    
    All GPU/CPU compute goes through InferenceClient.
    """
    
    def __init__(self, client: InferenceClient, config: CostmapConfig | None = None):
        """
        Initialize costmap generator.
        
        Args:
            client: InferenceClient from HAL
            config: Costmap configuration
        """
        self.client = client
        self.config = config or CostmapConfig()
        
        # Get best compute backend (GPU > CPU)
        try:
            self.backend = client.acl()
            cloudlog.info("CostmapGenerator: Using GPU backend")
        except RuntimeError:
            cloudlog.warning("CostmapGenerator: GPU not available, using CPU numpy fallback")
            self.backend = None
    
    def generate(
        self,
        objects: list[GridObject],
        vehicle_speed: float = 0.0
    ) -> np.ndarray:
        """
        Generate costmap from objects.
        
        Args:
            objects: List of detected objects
            vehicle_speed: Current vehicle speed for adaptive margins
            
        Returns:
            Costmap array [H, W]
        """
        if not objects:
            return np.zeros((self.config.grid_h, self.config.grid_w), dtype=np.float32)
        
        # Adaptive safety margin based on speed
        safety_margin = self.config.safety_margin * (1.0 + vehicle_speed / 20.0)
        
        # Prepare inputs
        num_objects = len(objects)
        obj_array = np.array([[o.x, o.y] for o in objects], dtype=np.float32).flatten()
        class_array = np.array([o.class_id for o in objects], dtype=np.int32)
        score_array = np.array([o.confidence for o in objects], dtype=np.float32)
        
        inputs = {
            'objects': obj_array,
            'classes': class_array,
            'scores': score_array,
            'grid_h': self.config.grid_h,
            'grid_w': self.config.grid_w,
            'resolution': self.config.resolution,
            'num_objects': num_objects,
            'safety_margin': safety_margin,
        }
        
        # Run through HAL (GPU) if available
        if self.backend is not None:
            result = self.backend.infer('generate_costmap', inputs)
            if result.success:
                costmap = result.outputs.get('costmap')
                if costmap is not None:
                    return costmap.reshape((self.config.grid_h, self.config.grid_w))

        # CPU numpy fallback
        return self._generate_cpu(objects, safety_margin)
    
    def _generate_cpu(
        self,
        objects: list[GridObject],
        safety_margin: float
    ) -> np.ndarray:
        """CPU fallback using NumPy."""
        costmap = np.zeros((self.config.grid_h, self.config.grid_w), dtype=np.float32)
        
        for gy in range(self.config.grid_h):
            for gx in range(self.config.grid_w):
                cell_x = (gx - self.config.grid_w / 2) * self.config.resolution
                cell_y = (gy - self.config.grid_h / 2) * self.config.resolution
                
                cost = 0.0
                for obj in objects:
                    dx = cell_x - obj.x
                    dy = cell_y - obj.y
                    dist = np.sqrt(dx * dx + dy * dy)
                    
                    class_cost = 1.0 if obj.class_id < 3 else (2.0 if obj.class_id < 5 else 0.5)
                    
                    if dist < safety_margin:
                        cost = max(cost, class_cost * obj.confidence)
                    elif dist < safety_margin * 2.0:
                        cost = max(cost, class_cost * obj.confidence * 0.5)
                
                costmap[gy, gx] = cost
        
        return costmap
