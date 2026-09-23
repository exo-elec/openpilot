"""Navigation view.

Best of both, and they split cleanly:

- **Nagasware** has the richer presentation -- `domains/navigation/ui/` is
  ~5,600 LOC with a typed `ManeuverType`, drawn maneuver icons, route cards
  and a Material-styled search bar. The maneuver model here is Nagasware's.
- **Routing preferences** Nagasware lacks are added here: `PathType`
  (fastest / shortest / eco / avoid tolls / avoid highways). Nagasware's
  engine assumes one route.

Data comes from `mapd`/`navd` via `navInstruction`, `navRoute` and `mapData`
(plan section 5.2) -- not from Nagasware's Mapbox client, which is a cloud
dependency this device does not need: routing is already on-device through
Valhalla and OSM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from openpilot.selfdrive.ui.qt import Qt, QColor, QPainter, QtWidgets, QWidget


class Maneuver(Enum):
  """Nagasware's ManeuverType, reduced to what navd actually emits."""
  STRAIGHT = "straight"
  SLIGHT_LEFT = "slight left"
  LEFT = "turn left"
  SHARP_LEFT = "sharp left"
  SLIGHT_RIGHT = "slight right"
  RIGHT = "turn right"
  SHARP_RIGHT = "sharp right"
  UTURN = "uturn"
  MERGE = "merge"
  EXIT = "off ramp"
  ROUNDABOUT = "roundabout"
  DESTINATION = "arrive"

  @classmethod
  def parse(cls, text: str) -> Maneuver:
    t = (text or "").strip().lower()
    for m in cls:
      if m.value in t:
        return m
    return cls.STRAIGHT

  @property
  def turn_degrees(self) -> float:
    """Arrow rotation. Drawn rather than iconography, so it scales and needs
    no asset pipeline."""
    return {
      Maneuver.STRAIGHT: 0.0, Maneuver.SLIGHT_LEFT: -35.0, Maneuver.LEFT: -90.0,
      Maneuver.SHARP_LEFT: -135.0, Maneuver.SLIGHT_RIGHT: 35.0,
      Maneuver.RIGHT: 90.0, Maneuver.SHARP_RIGHT: 135.0, Maneuver.UTURN: 180.0,
      Maneuver.MERGE: 25.0, Maneuver.EXIT: 45.0, Maneuver.ROUNDABOUT: 0.0,
      Maneuver.DESTINATION: 0.0,
    }[self]


class PathType(Enum):
  """Routing preferences."""
  FAST = "fastest"
  SHORT = "shortest"
  ECO = "eco"
  NO_TOLL = "avoid_tolls"
  NO_HWY = "avoid_highways"


@dataclass
class Step:
  maneuver: Maneuver = Maneuver.STRAIGHT
  instruction: str = ""
  distance_m: float = 0.0
  road: str = ""


@dataclass
class Route:
  steps: list[Step] = field(default_factory=list)
  distance_m: float = 0.0
  eta_s: float = 0.0
  destination: str = ""
  path_type: PathType = PathType.FAST

  @property
  def active(self) -> bool:
    return bool(self.steps)


def format_distance(metres: float, metric: bool = True) -> str:
  if metric:
    return f"{metres:.0f} m" if metres < 1000 else f"{metres / 1000:.1f} km"
  feet = metres * 3.28084
  return f"{feet:.0f} ft" if feet < 1000 else f"{feet / 5280:.1f} mi"


def format_eta(seconds: float) -> str:
  if seconds <= 0:
    return "--"
  mins = int(seconds // 60)
  return f"{mins} min" if mins < 60 else f"{mins // 60} h {mins % 60:02d}"


class ManeuverIcon(QWidget):
  """Drawn arrow, rotated by maneuver."""

  def __init__(self, size: int = 72, parent=None):
    super().__init__(parent)
    self.maneuver = Maneuver.STRAIGHT
    self.setFixedSize(size, size)

  def set_maneuver(self, m: Maneuver) -> None:
    self.maneuver = m
    self.update()

  def paintEvent(self, event):
    p = QPainter(self)
    p.setRenderHint(QPainter.Antialiasing)
    c = self.rect().center()
    p.translate(c)
    p.rotate(self.maneuver.turn_degrees)
    r = min(self.width(), self.height()) // 2 - 6
    pen = p.pen()
    pen.setColor(QColor(255, 255, 255))
    pen.setWidth(6)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.drawLine(0, r, 0, -r + 8)
    p.drawLine(0, -r, -8, -r + 12)
    p.drawLine(0, -r, 8, -r + 12)


class NextManeuverCard(QWidget):
  """The turn-by-turn card: icon, distance, instruction."""

  def __init__(self, parent=None):
    super().__init__(parent)
    self.setObjectName("maneuverCard")
    self.route = Route()

    lay = QtWidgets.QHBoxLayout(self)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(18)

    self.icon = ManeuverIcon(72, self)
    lay.addWidget(self.icon, 0, Qt.AlignVCenter)

    text = QtWidgets.QVBoxLayout()
    self.distance = QtWidgets.QLabel("--")
    self.distance.setObjectName("navDistance")
    self.instruction = QtWidgets.QLabel("No route")
    self.instruction.setObjectName("navInstruction")
    self.instruction.setWordWrap(True)
    text.addWidget(self.distance)
    text.addWidget(self.instruction)
    lay.addLayout(text, 1)

    self.eta = QtWidgets.QLabel("")
    self.eta.setObjectName("navEta")
    lay.addWidget(self.eta, 0, Qt.AlignVCenter)

  def set_route(self, route: Route, metric: bool = True) -> None:
    self.route = route
    if not route.active:
      self.distance.setText("--")
      self.instruction.setText("No route")
      self.eta.setText("")
      return
    step = route.steps[0]
    self.icon.set_maneuver(step.maneuver)
    self.distance.setText(format_distance(step.distance_m, metric))
    self.instruction.setText(step.instruction or step.road or step.maneuver.value)
    self.eta.setText(f"{format_eta(route.eta_s)}\n{format_distance(route.distance_m, metric)}")


class RouteOverview(QWidget):
  """Remaining steps, as a scrolling list of cards."""

  def __init__(self, parent=None):
    super().__init__(parent)
    lay = QtWidgets.QVBoxLayout(self)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    self._lay = lay
    self._rows: list[QWidget] = []

  def set_route(self, route: Route, metric: bool = True) -> None:
    for w in self._rows:
      w.setParent(None)
      w.deleteLater()
    self._rows.clear()
    for step in route.steps[1:6]:
      row = QWidget()
      row.setObjectName("routeStep")
      h = QtWidgets.QHBoxLayout(row)
      h.setContentsMargins(12, 8, 12, 8)
      icon = ManeuverIcon(40, row)
      icon.set_maneuver(step.maneuver)
      h.addWidget(icon)
      label = QtWidgets.QLabel(
        f"{format_distance(step.distance_m, metric)}   {step.instruction or step.road}")
      label.setObjectName("routeStepText")
      h.addWidget(label, 1)
      self._lay.addWidget(row)
      self._rows.append(row)
    self._lay.addStretch(1)


class NavigationView(QWidget):
  """Turn-by-turn card over a route overview."""

  def __init__(self, parent=None):
    super().__init__(parent)
    self.setObjectName("navRoot")
    lay = QtWidgets.QVBoxLayout(self)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.setSpacing(12)

    self.card = NextManeuverCard(self)
    lay.addWidget(self.card)
    self.overview = RouteOverview(self)
    lay.addWidget(self.overview, 1)

  def set_route(self, route: Route, metric: bool = True) -> None:
    self.card.set_route(route, metric)
    self.overview.set_route(route, metric)


def route_from_nav(instruction, nav_route, metric: bool = True) -> Route:
  """Build a Route from navInstruction / navRoute readers.

  Tolerant by construction: navd may publish either, neither, or a partially
  filled message, and a nav view that raises on a missing field takes the
  whole UI down with it.
  """
  route = Route()
  if instruction is not None:
    step = Step(
      maneuver=Maneuver.parse(str(getattr(instruction, "maneuverType", "") or "")),
      instruction=str(getattr(instruction, "maneuverPrimaryText", "") or ""),
      distance_m=float(getattr(instruction, "maneuverDistance", 0.0) or 0.0),
      road=str(getattr(instruction, "maneuverSecondaryText", "") or ""),
    )
    if step.instruction or step.distance_m:
      route.steps.append(step)
    route.distance_m = float(getattr(instruction, "distanceRemaining", 0.0) or 0.0)
    route.eta_s = float(getattr(instruction, "timeRemaining", 0.0) or 0.0)
  if nav_route is not None:
    route.destination = str(getattr(nav_route, "destinationName", "") or "")
  return route
