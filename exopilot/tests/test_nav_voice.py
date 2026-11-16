from exopilot.selfdrive.navigation.nav_voice import Instruction, NavVoice, spoken_distance


def ins(d, text="Turn right onto Main Street", kind="turn", remaining=5000.0):
  return Instruction(True, text, kind, d, remaining)


def test_each_stage_once_per_maneuver():
  v = NavVoice()
  said = [v.update(ins(d)) for d in (900, 480, 470, 150, 140, 60, 50, 10)]
  assert said == [None, "In 500 meters, turn right onto Main Street", None, "In 150 meters, turn right onto Main Street", None,
                  "Turn right onto Main Street", None, None]


def test_next_maneuver_starts_fresh_and_route_loss_resets():
  v = NavVoice()
  assert v.update(ins(300)) is not None
  assert v.update(ins(300, "Turn left onto Oak Road")) == "In 300 meters, turn left onto Oak Road"
  assert v.update(Instruction(False)) is None
  assert v.update(ins(300, "Turn left onto Oak Road")) is not None


def test_arrival_once():
  v = NavVoice()
  assert v.update(ins(40, "", "arrive", 40)) == "You have arrived at your destination"
  assert v.update(ins(30, "", "arrive", 30)) is None


def test_distances_spoken_in_units():
  assert spoken_distance(480, True) == "500 meters"
  assert spoken_distance(2000, True) == "2 kilometers"
  assert spoken_distance(150, False) == "500 feet"
  assert spoken_distance(3218.7, False) == "2 miles"
