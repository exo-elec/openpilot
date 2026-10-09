"""tripd keeps its rolling 7-day stats in one registered JSON param, not in unregistered per-date keys."""
import json
from datetime import datetime, timedelta

from openpilot.common.params import Params
from openpilot.selfdrive.tripd.tripd import DailyStats, TripD


def _today(offset=0):
  return (datetime.now().date() - timedelta(days=offset)).strftime("%Y-%m-%d")


def test_daily_stats_round_trip_and_prune():
  params = Params()
  params.put("EOPTripDailyStats", json.dumps({_today(): [12.5, 600.0, 300.0, 2], _today(30): [1.0, 1.0, 1.0, 1]}))
  trip = TripD()
  trip._load_daily_stats()
  today = trip.daily_stats[_today()]
  assert (today.distance, today.onroad_time, today.engaged_time, today.drives) == (12.5, 600.0, 300.0, 2)
  assert _today(30) not in trip.daily_stats and len(trip.daily_stats) == 7

  trip.daily_stats[_today()] = DailyStats(date=_today(), distance=20.0, onroad_time=1.0, engaged_time=1.0, drives=3)
  trip.daily_stats[_today(30)] = DailyStats(date=_today(30), distance=9.0)
  trip._save_daily_stats()
  saved = json.loads(params.get("EOPTripDailyStats"))
  assert saved[_today()] == [20.0, 1.0, 1.0, 3] and _today(30) not in saved


def test_garbage_in_the_param_is_ignored():
  Params().put("EOPTripDailyStats", "not json")
  trip = TripD()
  trip._load_daily_stats()
  assert all(s.distance == 0.0 for s in trip.daily_stats.values())
