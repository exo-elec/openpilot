# visionpilot Topics 07–12 Integration Guide

Step-by-step instructions for closing architectural gaps between visionpilot and Autoware reference patterns: diagnostic graph FTA, vehicle command gate refactoring, speed-scheduled filtering, multi-source arbitration, behavior-velocity pluginlib, and trajectory-follower dynamic model switching.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Topic 07 — Diagnostic Graph FTA](#2-topic-07--diagnostic-graph-fta)
3. [Topic 08 — Vehicle Command Gate Refactor](#3-topic-08--vehicle-command-gate-refactor)
4. [Topic 09 — Speed-Scheduled Filter](#4-topic-09--speed-scheduled-filter)
5. [Topic 10 — Multi-Source Control Arbitration](#5-topic-10--multi-source-control-arbitration)
6. [Topic 11 — Behavior-Velocity Pluginlib](#6-topic-11--behavior-velocity-pluginlib)
7. [Topic 12 — Trajectory Follower Dynamic Switching](#7-topic-12--trajectory-follower-dynamic-switching)
8. [Build & Verify](#8-build--verify)

---

## 1. Prerequisites

- ROS2 Humble workspace sourced:
  ```bash
  source /opt/ros/humble/setup.bash
  ```
- visionpilot workspace at `/home/admin/pilot/visionpilot/`
- `evp_msgs` package builds cleanly:
  ```bash
  cd /home/admin/pilot/visionpilot && colcon build --packages-select evp_msgs
  ```

---

## 2. Topic 07 — Diagnostic Graph FTA

### Goal
Replace the current JSON-based diagnostic aggregation with a proper Fault-Tree Analysis (FTA) DAG so system-level faults can be traced back to root causes.

### Current State (visionpilot)

**File:** `src/system/diagnostic_graph/diagnostic_graph/diagnostic_graph_node.py`

- Subscribes to `/diagnostics` (`DiagnosticArray`)
- Stores entries in a flat `dict` keyed by `status.name`
- Publishes JSON aggregate on `/system/diagnostics/aggregated`
- No causal links, no root-cause tracing

### Step 1: Define the FTA node schema

Create a new message to represent nodes in the diagnostic graph:

**File:** `src/evp_msgs/msg/DiagnosticGraphNode.msg`

```msg
# A single node in the diagnostic fault-tree graph
string id          # Unique node identifier (e.g., "camera.front.left")
string label       # Human-readable label
string[] parents   # IDs of parent nodes this node feeds into
uint8 level        # OK=0, WARN=1, ERROR=2, STALE=3
string message     # Status message
```

**File:** `src/evp_msgs/msg/DiagnosticGraph.msg`

```msg
# Full diagnostic graph snapshot
std_msgs/Header header
DiagnosticGraphNode[] nodes
string[] roots     # IDs of top-level fault nodes
```

Rebuild `evp_msgs`:

```bash
cd /home/admin/pilot/visionpilot && colcon build --packages-select evp_msgs
```

### Step 2: Create the FTA engine

**File:** `src/system/diagnostic_graph/diagnostic_graph/fta_engine.py`

```python
"""Fault-Tree Analysis engine.

Builds a DAG from node definitions.  Propagates fault levels bottom-up:
  - If any child is ERROR, parent becomes ERROR.
  - If any child is WARN and no child is ERROR, parent becomes WARN.
  - Otherwise parent is OK.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Set


@dataclass
class FTANode:
    id: str
    label: str
    level: int = 0
    message: str = ""
    parents: List[str] = field(default_factory=list)
    children: List[str] = field(default_factory=list)


class FTAEngine:
    OK = 0
    WARN = 1
    ERROR = 2
    STALE = 3

    def __init__(self):
        self._nodes: Dict[str, FTANode] = {}

    def register_node(self, node_id: str, label: str, parents: List[str]):
        node = FTANode(id=node_id, label=label, parents=parents)
        self._nodes[node_id] = node
        for p in parents:
            if p not in self._nodes:
                self._nodes[p] = FTANode(id=p, label=p)
            self._nodes[p].children.append(node_id)

    def update_leaf(self, node_id: str, level: int, message: str = ""):
        if node_id not in self._nodes:
            self.register_node(node_id, node_id, [])
        self._nodes[node_id].level = level
        self._nodes[node_id].message = message

    def evaluate(self) -> Dict[str, FTANode]:
        """Propagate levels bottom-up."""
        # Find leaf nodes
        leaves = [n for n in self._nodes.values() if not n.children]
        visited = set()

        def _propagate(node_id: str) -> int:
            if node_id in visited:
                return self._nodes[node_id].level
            node = self._nodes[node_id]
            if not node.children:
                visited.add(node_id)
                return node.level
            child_levels = [_propagate(c) for c in node.children]
            if any(l == self.ERROR for l in child_levels):
                node.level = self.ERROR
            elif any(l == self.WARN for l in child_levels):
                node.level = self.WARN
            elif any(l == self.STALE for l in child_levels):
                node.level = self.STALE
            else:
                node.level = self.OK
            visited.add(node_id)
            return node.level

        for leaf in leaves:
            _propagate(leaf.id)
        return self._nodes.copy()

    def get_roots(self) -> List[str]:
        """Top-level nodes with no parents."""
        return [n.id for n in self._nodes.values() if not n.parents]

    def get_root_causes(self, root_id: str) -> List[str]:
        """DFS from root to find deepest ERROR nodes."""
        causes = []
        stack = [root_id]
        while stack:
            nid = stack.pop()
            node = self._nodes[nid]
            if node.level == self.ERROR and not any(
                self._nodes[c].level == self.ERROR for c in node.children
            ):
                causes.append(nid)
            else:
                stack.extend(node.children)
        return causes
```

### Step 3: Wire FTA into the diagnostic graph node

**File:** `src/system/diagnostic_graph/diagnostic_graph/diagnostic_graph_node.py`

Replace the flat dict with the FTA engine:

```python
from .fta_engine import FTAEngine

class DiagnosticGraphNode(Node):
    def __init__(self):
        # ... existing init ...
        self._fta = FTAEngine()
        self._load_topology()

    def _load_topology(self):
        """Load static fault-tree topology from param/YAML."""
        # Example hard-coded topology; load from YAML in production
        self._fta.register_node("camera.front.left", "Front Left Camera", ["perception.camera"])
        self._fta.register_node("camera.front.right", "Front Right Camera", ["perception.camera"])
        self._fta.register_node("perception.camera", "Camera Perception", ["perception"])
        self._fta.register_node("perception", "Perception System", ["autonomy"])
        # ... etc

    def _on_diagnostics(self, msg: DiagnosticArray):
        for status in msg.status:
            level = status.level
            self._fta.update_leaf(status.name, level, status.message)

    def _publish_aggregated(self):
        nodes = self._fta.evaluate()
        # Build DiagnosticGraph message
        graph_msg = DiagnosticGraph()
        graph_msg.header.stamp = self.get_clock().now().to_msg()
        for nid, n in nodes.items():
            node_msg = DiagnosticGraphNode()
            node_msg.id = nid
            node_msg.label = n.label
            node_msg.parents = n.parents
            node_msg.level = n.level
            node_msg.message = n.message
            graph_msg.nodes.append(node_msg)
        graph_msg.roots = self._fta.get_roots()
        self._graph_pub.publish(graph_msg)

        # Root-cause analysis
        for root in graph_msg.roots:
            causes = self._fta.get_root_causes(root)
            if causes:
                self.get_logger().error(f"Root causes for {root}: {causes}")
```

Add publisher in `__init__`:

```python
self._graph_pub = self.create_publisher(
    DiagnosticGraph,
    '/system/diagnostics/graph',
    qos
)
```

---

## 3. Topic 08 — Vehicle Command Gate Refactor

### Goal
Fix the schema mismatch between `AckermannControlCommand` (flat) and gate code (nested `.lateral`/`.longitudinal`), add proper emergency handling, and integrate joystick commands.

### Current State (visionpilot)

**File:** `src/control/vehicle_cmd_gate/vehicle_cmd_gate/vehicle_cmd_gate_node.py`

Bugs identified:
1. `cmd.lateral.steering_tire_angle_rad` — `AckermannControlCommand` is flat; no `.lateral` sub-message exists.
2. `cmd.longitudinal.accel_mps2eration_mps2` — typo and non-existent field.
3. `EMERGENCY` mode falls through to `_create_zero_command()` instead of hard decel.
4. Joystick commands (`Twist`) are received but never used.

### Step 1: Fix the schema to match the flat message

**File:** `src/control/vehicle_cmd_gate/vehicle_cmd_gate/vehicle_cmd_gate_node.py`

Replace `_create_zero_command` and `_create_emergency_command`:

```python
def _create_zero_command(self) -> AckermannControlCommand:
    cmd = AckermannControlCommand()
    cmd.stamp = self.get_clock().now().to_msg()
    cmd.steering_tire_angle = 0.0
    cmd.steering_tire_rotation_rate = 0.0
    cmd.acceleration = 0.0
    cmd.speed = 0.0
    cmd.jerk = 0.0
    return cmd

def _create_emergency_command(self) -> AckermannControlCommand:
    """Hard emergency decel."""
    cmd = self._create_zero_command()
    cmd.acceleration = -5.0  # m/s² hard brake
    return cmd
```

Fix `_on_external_command`:

```python
def _on_external_command(self, msg: AckermannControlCommand):
    max_speed = self.get_parameter('max_external_speed').value
    # Flat message: clamp speed directly
    if msg.speed > max_speed:
        msg.speed = max_speed
    self._external_command = msg
    self._external_control_active = True
```

### Step 2: Fix `_process` to handle EMERGENCY and joystick

```python
def _process(self):
    if self.mode_manager.current_mode == OperationMode.AUTO:
        cmd = self._current_command

    elif self.mode_manager.current_mode == OperationMode.EXTERNAL:
        cmd = self._external_command if self._external_control_active else self._create_zero_command()

    elif self.mode_manager.current_mode == OperationMode.MANUAL:
        cmd = self._create_zero_command()

    elif self.mode_manager.current_mode == OperationMode.EMERGENCY:
        cmd = self._create_emergency_command()

    elif self.mode_manager.current_mode == OperationMode.STOP:
        cmd = self._create_emergency_command()

    else:
        cmd = self._create_zero_command()

    # Apply speed-scheduled filter (Topic 09)
    # cmd = self._filter.apply(cmd, current_speed)

    self.pub_output.publish(cmd)
    self._commands_processed += 1
    self._publish_mode_report()
```

### Step 3: Integrate joystick into command selection

Add joystick → `AckermannControlCommand` conversion:

```python
def _on_joy(self, msg: Joy):
    if not self.get_parameter('joy_enabled').value:
        self._joy_enabled = False
        return

    if len(msg.axes) >= 4:
        cmd = AckermannControlCommand()
        cmd.stamp = self.get_clock().now().to_msg()
        cmd.steering_tire_angle = msg.axes[3] * 0.5  # max 0.5 rad
        cmd.steering_tire_rotation_rate = 0.0
        cmd.speed = msg.axes[1] * 5.0  # max 5 m/s
        cmd.acceleration = 0.0
        cmd.jerk = 0.0
        self._joy_command = cmd
        self._joy_enabled = True
```

Then in `_process`, add joystick as a source:

```python
elif self.mode_manager.current_mode == OperationMode.MANUAL and self._joy_enabled:
    cmd = self._joy_command
```

> **Note:** `MANUAL` mode can be repurposed as "joystick test mode" or add a dedicated `JOYSTICK` mode to `OperationMode` if needed.

---

## 4. Topic 09 — Speed-Scheduled Filter

### Goal
Add a `VehicleCmdFilter` that limits longitudinal/lateral commands based on 1-D interpolation over current vehicle speed (Autoware pattern).

### Reference (Autoware)

**File:** `autoware_universe/control/autoware_vehicle_cmd_gate/src/vehicle_cmd_filter.cpp`

Key pattern:
- `reference_speed_points`: sorted speed breakpoints `[0.0, 5.0, 10.0, 20.0, 40.0]` m/s
- `lon_acc_lim_for_lon_vel`: max longitudinal accel per speed point
- `lon_jerk_lim_for_lon_acc`: max longitudinal jerk per speed point
- `lat_acc_lim_for_steer_cmd`: max lateral accel per speed point
- Interpolation: zero-order hold outside range, linear inside.

### Step 1: Create `VehicleCmdFilter`

**File:** `src/control/vehicle_cmd_gate/vehicle_cmd_gate/vehicle_cmd_filter.py`

```python
"""Speed-scheduled command filter."""

import numpy as np
from evp_msgs.msg import AckermannControlCommand


class VehicleCmdFilter:
    def __init__(self):
        self.reference_speed_points = np.array([0.0, 5.0, 10.0, 20.0, 40.0])
        self.lon_acc_lim = np.array([1.5, 1.5, 1.2, 0.8, 0.5])
        self.lon_jerk_lim = np.array([3.0, 3.0, 2.5, 2.0, 1.5])
        self.lat_acc_lim = np.array([2.0, 2.0, 1.8, 1.5, 1.2])
        self.steer_lim = np.array([0.7, 0.6, 0.5, 0.4, 0.35])
        self.steer_rate_lim = np.array([0.5, 0.4, 0.35, 0.3, 0.25])
        self._current_speed = 0.0
        self._prev_cmd = AckermannControlCommand()

    def set_current_speed(self, v: float):
        self._current_speed = abs(v)

    def set_prev_cmd(self, cmd: AckermannControlCommand):
        self._prev_cmd = cmd

    def _interpolate(self, limits: np.ndarray) -> float:
        v = self._current_speed
        ref = self.reference_speed_points
        if v <= ref[0]:
            return float(limits[0])
        if v >= ref[-1]:
            return float(limits[-1])
        return float(np.interp(v, ref, limits))

    def limit_longitudinal(self, cmd: AckermannControlCommand, dt: float):
        acc_lim = self._interpolate(self.lon_acc_lim)
        jerk_lim = self._interpolate(self.lon_jerk_lim)

        # Limit acceleration magnitude
        cmd.acceleration = max(min(cmd.acceleration, acc_lim), -acc_lim)

        # Limit jerk (change in acceleration)
        if dt > 0:
            acc_delta = cmd.acceleration - self._prev_cmd.acceleration
            max_acc_delta = jerk_lim * dt
            acc_delta = max(min(acc_delta, max_acc_delta), -max_acc_delta)
            cmd.acceleration = self._prev_cmd.acceleration + acc_delta

        # Limit speed change via accel
        speed_delta = cmd.speed - self._prev_cmd.speed
        max_speed_delta = acc_lim * dt
        speed_delta = max(min(speed_delta, max_speed_delta), -max_speed_delta)
        cmd.speed = max(0.0, self._prev_cmd.speed + speed_delta)

    def limit_lateral(self, cmd: AckermannControlCommand, dt: float):
        steer_lim = self._interpolate(self.steer_lim)
        steer_rate_lim = self._interpolate(self.steer_rate_lim)

        # Limit steering angle
        cmd.steering_tire_angle = max(min(cmd.steering_tire_angle, steer_lim), -steer_lim)

        # Limit steering rate
        if dt > 0:
            steer_delta = cmd.steering_tire_angle - self._prev_cmd.steering_tire_angle
            max_steer_delta = steer_rate_lim * dt
            steer_delta = max(min(steer_delta, max_steer_delta), -max_steer_delta)
            cmd.steering_tire_angle = self._prev_cmd.steering_tire_angle + steer_delta

    def filter_all(self, cmd: AckermannControlCommand, dt: float):
        self.limit_longitudinal(cmd, dt)
        self.limit_lateral(cmd, dt)
        self._prev_cmd = cmd
```

### Step 2: Wire filter into `vehicle_cmd_gate_node.py`

```python
from .vehicle_cmd_filter import VehicleCmdFilter

class VehicleCmdGateNode(Node):
    def __init__(self):
        # ... after mode_manager init ...
        self._filter = VehicleCmdFilter()
        # Subscribe to current speed
        self.create_subscription(
            VelocityReport, '/vehicle/status/velocity_report',
            self._on_velocity, 10
        )
        self._current_speed = 0.0

    def _on_velocity(self, msg: VelocityReport):
        self._current_speed = msg.longitudinal_velocity
        self._filter.set_current_speed(msg.longitudinal_velocity)

    def _process(self):
        # ... mode selection ...
        cmd = self._filter.filter_all(cmd, dt=0.01)
        self.pub_output.publish(cmd)
```

---

## 5. Topic 10 — Multi-Source Control Arbitration

### Goal
Unify `external_cmd_selector` and `vehicle_cmd_gate` mode arbitration so there is a single, consistent priority scheme across the control pipeline.

### Current State

- `external_cmd_selector`: Priority = Remote > Joystick > Autonomous
- `vehicle_cmd_gate`: Modes = STOP / MANUAL / AUTO / EXTERNAL / EMERGENCY

These two nodes do not agree on priorities.  `external_cmd_selector` publishes to `/control/vehicle_controller/selected_cmd`, but `vehicle_cmd_gate` reads from `/control/vehicle_controller/validated_cmd`.

### Step 1: Define unified arbitration rules

Priority (highest to lowest):
1. **EMERGENCY** — MRM / AEB hard brake (always wins)
2. **STOP** — Zero velocity, zero steering
3. **EXTERNAL** — Remote teleop
4. **AUTO** — Normal autonomous control
5. **MANUAL** — Driver / joystick (fallback)

### Step 2: Merge arbitration into `vehicle_cmd_gate`

**File:** `src/control/vehicle_cmd_gate/vehicle_cmd_gate/vehicle_cmd_gate_node.py`

Subscribe directly to the selector output and MRM topics:

```python
# In __init__:
self.sub_selected = self.create_subscription(
    AckermannControlCommand,
    '/control/vehicle_controller/selected_cmd',
    self._on_selected_command,
    qos_reliable
)

self.sub_mrm = self.create_subscription(
    Bool,
    '/control/mrm/active',
    self._on_mrm,
    10
)

self._mrm_active = False
self._selected_command = self._create_zero_command()

# Callbacks:
def _on_selected_command(self, msg: AckermannControlCommand):
    self._selected_command = msg

def _on_mrm(self, msg: Bool):
    self._mrm_active = msg.data
    if self._mrm_active:
        self.mode_manager.request_transition(OperationMode.EMERGENCY, reason="MRM activated")
```

Update `_process` to use unified arbitration:

```python
def _process(self):
    if self._mrm_active:
        cmd = self._create_emergency_command()
    elif self.mode_manager.current_mode == OperationMode.EMERGENCY:
        cmd = self._create_emergency_command()
    elif self.mode_manager.current_mode == OperationMode.STOP:
        cmd = self._create_zero_command()
    elif self.mode_manager.current_mode == OperationMode.EXTERNAL:
        cmd = self._external_command if self._external_control_active else self._create_zero_command()
    elif self.mode_manager.current_mode == OperationMode.AUTO:
        cmd = self._selected_command
    elif self.mode_manager.current_mode == OperationMode.MANUAL:
        cmd = self._joy_command if self._joy_enabled else self._create_zero_command()
    else:
        cmd = self._create_zero_command()

    # Apply speed-scheduled filter
    cmd = self._filter.filter_all(cmd, dt=0.01)
    self.pub_output.publish(cmd)
```

### Step 3: Deprecate or simplify `external_cmd_selector`

If `vehicle_cmd_gate` now handles all arbitration, `external_cmd_selector` can be reduced to a thin command-source multiplexer (no safety decisions) or removed entirely and its logic folded into the gate.

---

## 6. Topic 11 — Behavior-Velocity Pluginlib

### Goal
Refactor the monolithic `velocity_planner` into a plugin-based architecture where scene modules (crosswalk, intersection, traffic light) are dynamically loaded, like Autoware's `behavior_velocity_planner`.

### Current State (visionpilot)

**File:** `src/planning/velocity_planner/velocity_planner/velocity_planner_node.py`

- Hard-coded modules: `CrosswalkModule`, `IntersectionModule`
- No dynamic loading; adding a module requires editing the node.

### Step 1: Define the scene module interface

**File:** `src/planning/velocity_planner/velocity_planner/scene_module_interface.py`

```python
"""Plugin interface for behavior-velocity scene modules."""

from abc import ABC, abstractmethod
from typing import List, Optional
from evp_msgs.msg import Trajectory, TrajectoryPoint


class SceneModuleInterface(ABC):
    """Base class for velocity-limiting scene modules."""

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @abstractmethod
    def init(self, node_params: dict) -> bool:
        """Initialize with node-level parameters."""
        pass

    @abstractmethod
    def update(self, ego_state, trajectory: Trajectory) -> Optional[float]:
        """Return a speed limit (m/s) or None if no limit applies."""
        pass

    @abstractmethod
    def get_debug_markers(self):
        """Return visualization markers for RViz."""
        pass
```

### Step 2: Refactor existing modules to implement the interface

**File:** `src/planning/velocity_planner/velocity_planner/crosswalk_module.py`

```python
from .scene_module_interface import SceneModuleInterface

class CrosswalkModule(SceneModuleInterface):
    @property
    def name(self) -> str:
        return "crosswalk"

    def init(self, node_params: dict) -> bool:
        self._stop_margin = node_params.get("crosswalk_stop_margin", 2.0)
        return True

    def update(self, ego_state, trajectory: Trajectory) -> Optional[float]:
        # ... existing logic ...
        pass

    def get_debug_markers(self):
        return []
```

Do the same for `intersection_module.py`.

### Step 3: Create the plugin manager

**File:** `src/planning/velocity_planner/velocity_planner/plugin_manager.py`

```python
"""Dynamically loads and manages scene modules."""

import importlib
import pkgutil
from typing import Dict, List, Type
from .scene_module_interface import SceneModuleInterface


class PluginManager:
    def __init__(self):
        self._modules: Dict[str, SceneModuleInterface] = {}
        self._registry: Dict[str, Type[SceneModuleInterface]] = {}

    def discover_modules(self, package_name: str = "velocity_planner.modules"):
        """Auto-discover modules in a package."""
        try:
            package = importlib.import_module(package_name)
            for _, modname, ispkg in pkgutil.iter_modules(package.__path__):
                if ispkg:
                    continue
                try:
                    mod = importlib.import_module(f"{package_name}.{modname}")
                    for attr in dir(mod):
                        cls = getattr(mod, attr)
                        if (isinstance(cls, type) and
                            issubclass(cls, SceneModuleInterface) and
                            cls is not SceneModuleInterface):
                            self._registry[cls().name] = cls
                except Exception as e:
                    print(f"Failed to load {modname}: {e}")
        except ImportError:
            pass

    def load_module(self, name: str, params: dict) -> bool:
        if name not in self._registry:
            return False
        instance = self._registry[name]()
        if instance.init(params):
            self._modules[name] = instance
            return True
        return False

    def update_all(self, ego_state, trajectory: Trajectory) -> List[float]:
        limits = []
        for mod in self._modules.values():
            limit = mod.update(ego_state, trajectory)
            if limit is not None:
                limits.append(limit)
        return limits
```

### Step 4: Update `velocity_planner_node.py`

```python
from .plugin_manager import PluginManager

class VelocityPlannerNode(Node):
    def __init__(self):
        # ...
        self._plugin_mgr = PluginManager()
        self._plugin_mgr.discover_modules()
        enabled_modules = self.get_parameter('enabled_modules').value  # e.g. ["crosswalk", "intersection"]
        for name in enabled_modules:
            if self._plugin_mgr.load_module(name, self._get_params()):
                self.get_logger().info(f"Loaded scene module: {name}")
            else:
                self.get_logger().warn(f"Failed to load scene module: {name}")

    def _plan(self, trajectory: Trajectory):
        limits = self._plugin_mgr.update_all(self._ego_state, trajectory)
        if limits:
            min_limit = min(limits)
            self._apply_speed_limit(trajectory, min_limit)
```

---

## 7. Topic 12 — Trajectory Follower Dynamic Switching

### Goal
Allow the trajectory follower to switch between lateral controllers (MPC vs Pure Pursuit) and longitudinal controllers (PID vs MPC) at runtime based on speed or confidence.

### Current State (visionpilot)

**File:** `src/control/trajectory_follower/trajectory_follower/trajectory_follower_node.py`

- Computes lateral error, yaw error, velocity error
- Publishes errors for downstream controllers
- No controller selection logic

### Step 1: Define controller interface

**File:** `src/control/trajectory_follower/trajectory_follower/controller_interface.py`

```python
"""Interface for lateral/longitudinal controllers."""

from abc import ABC, abstractmethod
from evp_msgs.msg import AckermannControlCommand


class LateralController(ABC):
    @abstractmethod
    def compute(self, lateral_error, yaw_error, curvature, v_ego) -> float:
        """Return steering tire angle (rad)."""
        pass

    @abstractmethod
    def get_name(self) -> str:
        pass


class LongitudinalController(ABC):
    @abstractmethod
    def compute(self, vel_error, acc_error, dt) -> tuple[float, float]:
        """Return (accel, speed)."""
        pass

    @abstractmethod
    def get_name(self) -> str:
        pass
```

### Step 2: Implement MPC and Pure Pursuit variants

**File:** `src/control/trajectory_follower/trajectory_follower/controllers/mpc_lateral.py`

```python
from ..controller_interface import LateralController

class MPCLateralController(LateralController):
    def __init__(self, horizon=10, dt=0.1):
        self.horizon = horizon
        self.dt = dt

    def get_name(self) -> str:
        return "mpc_lateral"

    def compute(self, lateral_error, yaw_error, curvature, v_ego) -> float:
        # Simplified MPC: weighted sum of errors
        k_lat = 0.5
        k_yaw = 1.0
        k_curv = 0.3
        steer = -(k_lat * lateral_error + k_yaw * yaw_error + k_curv * curvature)
        steer = max(min(steer, 0.7), -0.7)
        return steer
```

**File:** `src/control/trajectory_follower/trajectory_follower/controllers/pure_pursuit.py`

```python
import math
from ..controller_interface import LateralController

class PurePursuitController(LateralController):
    def __init__(self, wheelbase=2.8, look_ahead_gain=0.5):
        self.wheelbase = wheelbase
        self.look_ahead_gain = look_ahead_gain

    def get_name(self) -> str:
        return "pure_pursuit"

    def compute(self, lateral_error, yaw_error, curvature, v_ego) -> float:
        look_ahead = self.look_ahead_gain * max(v_ego, 1.0)
        alpha = yaw_error + math.atan2(lateral_error, look_ahead)
        steer = math.atan2(2.0 * self.wheelbase * math.sin(alpha), look_ahead)
        steer = max(min(steer, 0.7), -0.7)
        return steer
```

### Step 3: Add dynamic switching logic

**File:** `src/control/trajectory_follower/trajectory_follower/trajectory_follower_node.py`

```python
from .controllers.mpc_lateral import MPCLateralController
from .controllers.pure_pursuit import PurePursuitController

class TrajectoryFollowerNode(Node):
    def __init__(self):
        # ... existing init ...
        self._mpc_lat = MPCLateralController()
        self._pp_lat = PurePursuitController()
        self._current_lat_controller = self._mpc_lat

        self.declare_parameter('switch_speed_mps', 5.0)
        self._switch_speed = self.get_parameter('switch_speed_mps').value

    def _select_controller(self, v_ego: float):
        """Switch to Pure Pursuit at low speed, MPC at high speed."""
        if v_ego < self._switch_speed:
            if self._current_lat_controller != self._pp_lat:
                self.get_logger().info("Switching to Pure Pursuit (low speed)")
                self._current_lat_controller = self._pp_lat
        else:
            if self._current_lat_controller != self._mpc_lat:
                self.get_logger().info("Switching to MPC (high speed)")
                self._current_lat_controller = self._mpc_lat

    def _update(self):
        if self._pose is None or self._trajectory is None:
            return

        ego_x = self._pose.pose.position.x
        ego_y = self._pose.pose.position.y
        ego_yaw = yaw_from_quaternion(self._pose.pose.orientation)

        lat_err, yaw_err, vel_err, idx = self._find_closest(ego_x, ego_y, ego_yaw)
        if lat_err is None:
            return

        v_ego = self._estimate_velocity()
        self._select_controller(v_ego)

        # Get curvature at closest point
        curvature = 0.0
        if idx < len(self._trajectory.points) - 1:
            pt1 = self._trajectory.points[idx]
            pt2 = self._trajectory.points[idx + 1]
            dx = pt2.pose.position.x - pt1.pose.position.x
            dy = pt2.pose.position.y - pt1.pose.position.y
            ds = math.hypot(dx, dy)
            if ds > 0.01:
                dtheta = yaw_from_quaternion(pt2.pose.orientation) - yaw_from_quaternion(pt1.pose.orientation)
                curvature = dtheta / ds

        steer = self._current_lat_controller.compute(lat_err, yaw_err, curvature, v_ego)

        # Publish selected controller name for monitoring
        self._ctrl_name_pub.publish(String(data=self._current_lat_controller.get_name()))

        # ... publish errors ...
```

---

## 8. Build & Verify

### 8.1 Build modified packages

```bash
cd /home/admin/pilot/visionpilot

colcon build --packages-select evp_msgs
source install/setup.bash

colcon build --packages-select \
  diagnostic_graph \
  vehicle_cmd_gate \
  trajectory_follower \
  velocity_planner
```

### 8.2 Run tests

```bash
colcon test --packages-select vehicle_cmd_gate
colcon test-result --verbose
```

### 8.3 Runtime verification checklist

| Check | How |
|-------|-----|
| Diagnostic graph FTA | Publish a fake `/diagnostics` with `camera.front.left=ERROR`; verify `/system/diagnostics/graph` shows `perception.camera=ERROR` and `autonomy=ERROR` |
| Vehicle cmd gate schema fix | Launch gate, send `AckermannControlCommand` with `speed=10`; verify no AttributeError on `.lateral`/`.longitudinal` |
| Speed-scheduled filter | Send speed=35 m/s, request accel=2.0; verify output accel ≤ 0.5 m/s² (interp from table) |
| Multi-source arbitration | Activate MRM (`/control/mrm/active = True`); verify gate outputs hard decel regardless of mode |
| Behavior-velocity pluginlib | Add a new module implementing `SceneModuleInterface`; verify it loads without editing the planner node |
| Trajectory follower switching | Drive below 5 m/s; verify logs "Switching to Pure Pursuit"; accelerate above 5 m/s; verify "Switching to MPC" |

---

## Appendix: File Inventory

| File | Action |
|------|--------|
| `src/evp_msgs/msg/DiagnosticGraphNode.msg` | **Create** |
| `src/evp_msgs/msg/DiagnosticGraph.msg` | **Create** |
| `src/system/diagnostic_graph/diagnostic_graph/fta_engine.py` | **Create** |
| `src/system/diagnostic_graph/diagnostic_graph/diagnostic_graph_node.py` | Modify — integrate FTA engine |
| `src/control/vehicle_cmd_gate/vehicle_cmd_gate/vehicle_cmd_filter.py` | **Create** |
| `src/control/vehicle_cmd_gate/vehicle_cmd_gate/vehicle_cmd_gate_node.py` | Modify — fix schema, add filter, unify arbitration |
| `src/control/vehicle_cmd_gate/vehicle_cmd_gate/operation_modes.py` | Modify — add `JOYSTICK` mode if needed |
| `src/planning/velocity_planner/velocity_planner/scene_module_interface.py` | **Create** |
| `src/planning/velocity_planner/velocity_planner/plugin_manager.py` | **Create** |
| `src/planning/velocity_planner/velocity_planner/crosswalk_module.py` | Modify — implement interface |
| `src/planning/velocity_planner/velocity_planner/intersection_module.py` | Modify — implement interface |
| `src/planning/velocity_planner/velocity_planner/velocity_planner_node.py` | Modify — use plugin manager |
| `src/control/trajectory_follower/trajectory_follower/controller_interface.py` | **Create** |
| `src/control/trajectory_follower/trajectory_follower/controllers/mpc_lateral.py` | **Create** |
| `src/control/trajectory_follower/trajectory_follower/controllers/pure_pursuit.py` | **Create** |
| `src/control/trajectory_follower/trajectory_follower/trajectory_follower_node.py` | Modify — dynamic switching |
