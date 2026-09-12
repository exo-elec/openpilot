"""First-run onboarding.

From VisionPilot's `widgets/onboarding/` -- Nagasware has no onboarding at
all, so there is nothing to weigh here. Replaces openpilot's
qt/offroad/onboarding.cc.

Terms acceptance and training completion are recorded in Params, so the flow
is resumable: a device powered off mid-training restarts at the page it
reached, not at the beginning.
"""

from __future__ import annotations

from openpilot.selfdrive.ui.eop.qt import QtWidgets, QWidget, Signal

TERMS_VERSION = "2"
TRAINING_VERSION = "1"


class _Page(QWidget):
  def __init__(self, title: str, body: str, parent=None):
    super().__init__(parent)
    lay = QtWidgets.QVBoxLayout(self)
    lay.setContentsMargins(80, 50, 80, 40)
    lay.setSpacing(20)
    h = QtWidgets.QLabel(title)
    h.setObjectName("onboardTitle")
    lay.addWidget(h)
    t = QtWidgets.QLabel(body)
    t.setObjectName("onboardBody")
    t.setWordWrap(True)
    lay.addWidget(t, 1)
    self.buttons = QtWidgets.QHBoxLayout()
    lay.addLayout(self.buttons)


class WelcomePage(_Page):
  continued = Signal()

  def __init__(self, parent=None):
    super().__init__(
      "ExoPilot 02M",
      "Driver assistance. You remain responsible for the vehicle at all " +
      "times, must keep your hands on the wheel and your eyes on the road.",
      parent)
    b = QtWidgets.QPushButton("Begin")
    b.clicked.connect(self.continued.emit)
    self.buttons.addStretch(1)
    self.buttons.addWidget(b)


class TermsPage(_Page):
  accepted = Signal()
  declined = Signal()

  def __init__(self, parent=None):
    super().__init__(
      "Terms",
      "This is alpha software for research use. It is not a substitute for " +
      "an attentive driver and may disengage without warning at any time.\n\n" +
      "By accepting you confirm you understand the system's limits and accept " +
      "responsibility for supervising it.",
      parent)
    decline = QtWidgets.QPushButton("Decline")
    decline.clicked.connect(self.declined.emit)
    accept = QtWidgets.QPushButton("Accept")
    accept.setObjectName("primaryButton")
    accept.clicked.connect(self.accepted.emit)
    self.buttons.addWidget(decline)
    self.buttons.addStretch(1)
    self.buttons.addWidget(accept)


class TrainingPage(_Page):
  """Stepped through rather than one wall of text, so the driver has actually
  read each point before the next appears."""

  completed = Signal()

  STEPS = [
    ("Engaging", "Set cruise control to engage. The band around the screen " +
                 "turns green when the system is steering."),
    ("Taking over", "Turn the wheel, brake, or press cancel to take over. " +
                    "The system releases immediately."),
    ("Blind spot", "A band on the side of the screen means a vehicle is " +
                   "alongside. Signalling opens that camera full screen."),
    ("Limits", "The system does not see everything. It can miss stopped " +
               "vehicles, cross traffic and roadworks."),
  ]

  def __init__(self, parent=None):
    super().__init__(self.STEPS[0][0], self.STEPS[0][1], parent)
    self._i = 0
    self._next = QtWidgets.QPushButton("Next")
    self._next.setObjectName("primaryButton")
    self._next.clicked.connect(self._advance)
    self.buttons.addStretch(1)
    self.buttons.addWidget(self._next)
    self._title = self.findChild(QtWidgets.QLabel, "onboardTitle")
    self._body = self.findChild(QtWidgets.QLabel, "onboardBody")

  def _advance(self) -> None:
    self._i += 1
    if self._i >= len(self.STEPS):
      self.completed.emit()
      return
    title, body = self.STEPS[self._i]
    self._title.setText(title)
    self._body.setText(body)
    if self._i == len(self.STEPS) - 1:
      self._next.setText("Done")


class OnboardingView(QtWidgets.QStackedWidget):
  """Welcome -> terms -> training. Emits `finished` when all are done."""

  finished = Signal()

  def __init__(self, store=None, parent=None):
    super().__init__(parent)
    self.setObjectName("onboardRoot")
    # A ParamStore, like every other view takes -- not a raw Params. The two
    # were mixed here, which meant this was the one view that could not be
    # handed the store the rest of the UI already shares.
    self._store = store

    self.welcome = WelcomePage(self)
    self.terms = TermsPage(self)
    self.training = TrainingPage(self)
    for w in (self.welcome, self.terms, self.training):
      self.addWidget(w)

    self.welcome.continued.connect(lambda: self.setCurrentWidget(self.terms))
    self.terms.accepted.connect(self._accept_terms)
    self.terms.declined.connect(lambda: self.setCurrentWidget(self.welcome))
    self.training.completed.connect(self._finish_training)

    self.setCurrentWidget(self._resume_at())

  # ---- persistence ------------------------------------------------------

  def _get(self, key: str) -> str:
    return self._store.get_text(key) if self._store is not None else ""

  def _put(self, key: str, value: str) -> None:
    if self._store is not None:
      self._store.put_text(key, value)

  def _resume_at(self) -> QWidget:
    """Where to open. Terms first, then training.

    Training is also where "Review Training Guide" lands, so a device that
    has already finished onboarding still opens here when it is shown again
    on purpose rather than on first run.
    """
    if self._get("HasAcceptedTerms") != TERMS_VERSION:
      return self.welcome
    return self.training

  def completed(self) -> bool:
    return (self._get("HasAcceptedTerms") == TERMS_VERSION
            and self._get("CompletedTrainingVersion") == TRAINING_VERSION)

  def _accept_terms(self) -> None:
    self._put("HasAcceptedTerms", TERMS_VERSION)
    self.setCurrentWidget(self.training)

  def _finish_training(self) -> None:
    self._put("CompletedTrainingVersion", TRAINING_VERSION)
    self.finished.emit()
