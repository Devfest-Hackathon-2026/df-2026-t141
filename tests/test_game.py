import random
from datetime import date, datetime, timedelta

import pytest

from services import game
from services.game import JST
from services.location import bearing_deg, destination, distance_m

T0 = datetime(2026, 11, 5, 12, 0, tzinfo=JST)


TARGET = (35.681, 139.767)
START = destination(*TARGET, 90, 5000)


def _state(now, speed=6.0):
    return game.zombie_state(*START, T0, *TARGET, now, speed)


def test_zombie_chases_at_given_speed():
    assert _state(T0).km == pytest.approx(5, abs=0.001)
    middle = _state(T0 + timedelta(minutes=25))
    assert middle.km == pytest.approx(2.5, abs=0.001)
    assert distance_m(middle.lat, middle.lng, *TARGET) == pytest.approx(2500, abs=5)
    assert _state(T0 + timedelta(minutes=51)).km == 0
    assert (_state(T0 + timedelta(days=30)).lat, _state(T0 + timedelta(days=30)).lng) == TARGET


NEXT = destination(*TARGET, 270, 1000)  # 最後に見えた位置から、さらに1km先


def _trail(now):
    return game.zombie_on_trail(*START, T0, *TARGET, *NEXT, now, 6.0)


def test_trail_is_the_normal_chase_until_the_last_seen_position():
    now = T0 + timedelta(minutes=25)
    state = _trail(now)
    assert (state.lat, state.lng) == (_state(now).lat, _state(now).lng)
    assert state.km == pytest.approx(distance_m(state.lat, state.lng, *NEXT) / 1000, abs=0.001)


def test_trail_continues_past_the_last_seen_position():
    state = _trail(T0 + timedelta(minutes=55))  # 5.5km進んだので、最後に見えた位置から500m先
    assert state.km == pytest.approx(0.5, abs=0.01)
    assert distance_m(state.lat, state.lng, *TARGET) == pytest.approx(500, abs=10)


def test_trail_catches_someone_who_did_not_get_far_enough():
    state = _trail(T0 + timedelta(minutes=61))
    assert state.km == 0 and (state.lat, state.lng) == NEXT
    same_place = game.zombie_on_trail(*START, T0, *TARGET, *TARGET, T0 + timedelta(minutes=51), 6.0)
    assert same_place.km == 0


def test_approach_reaches_home_exactly_at_arrival():
    arrival = T0 + timedelta(hours=4)
    assert game.approach_state(*START, T0, *TARGET, arrival, T0).km == pytest.approx(5, abs=0.01)
    assert game.approach_state(*START, T0, *TARGET, arrival, T0 + timedelta(hours=2)).km == pytest.approx(2.5, abs=0.01)
    assert game.approach_state(*START, T0, *TARGET, arrival, arrival).km == 0


def test_modes_balance_against_walking_speed():
    # 徒歩は時速5km前後。ノーマルでも普通に歩くだけではじわじわ追いつかれ、ハードは走らないと逃げ切れない
    assert 5 < game.MODES["normal"].speed_km_per_hour < game.MODES["hard"].speed_km_per_hour
    assert game.MODES["hard"].zombies == 3 and game.MODES["hard"].point_rate == 1.5
    assert _state(T0 + timedelta(minutes=30), speed=4.0).km == pytest.approx(3, abs=0.001)


def test_next_daily_after_never_returns_the_same_day():
    evening = datetime(2026, 11, 5, 18, 5, tzinfo=JST)
    # 今日の18時を終えたあとに21時へ変えても、今日の21時ではなく明日の21時
    assert game.next_daily_after("21:00", evening.date(), evening) == datetime(2026, 11, 6, 21, 0, tzinfo=JST)
    # 何日も開かなかったときは、過去の日ではなく次に来る時刻
    later = datetime(2026, 11, 9, 10, 0, tzinfo=JST)
    assert game.next_daily_after("18:00", date(2026, 11, 5), later) == datetime(2026, 11, 9, 18, 0, tzinfo=JST)


def test_plan_items_are_materials_near_the_route():
    start, end = (35.70, 139.70), (35.70, 139.712)  # 東へ約1.08km
    items = game.plan_items(*start, *end, random.Random(1))
    assert len(items) == 3 and [kind for kind, _lat, _lng in items] == ["material"] * 3
    total = distance_m(*start, *end)
    for _kind, lat, lng in items:
        detour = distance_m(*start, lat, lng) + distance_m(lat, lng, *end) - total
        assert detour <= 2 * game.ITEM_LATERAL_M
    short = game.plan_items(35.70, 139.70, 35.70, 139.7011, random.Random(1))  # 約100m
    assert len(short) == game.ITEM_MIN


def test_next_daily_at_rolls_to_tomorrow_when_passed():
    assert game.next_daily_at("18:00", T0) == T0.replace(hour=18)
    assert game.next_daily_at("09:00", T0) == (T0 + timedelta(days=1)).replace(hour=9)


def test_zombie_ignores_clock_going_backwards():
    assert _state(T0 - timedelta(hours=1)).km == pytest.approx(5, abs=0.001)


def test_place_zombie_keeps_direction_and_sets_distance():
    lat, lng = game.place_zombie(*TARGET, *START, 2.0)
    assert distance_m(lat, lng, *TARGET) == pytest.approx(2000, abs=1)
    assert bearing_deg(*TARGET, lat, lng) == pytest.approx(90, abs=0.5)


@pytest.mark.parametrize("km,level", [(5, 0), (3.01, 0), (3, 1), (1, 2), (0.5, 2), (0.3, 3), (0, 3)])
def test_notify_levels(km, level):
    assert game.notify_level(km) == level


@pytest.mark.parametrize("hour,quiet", [(6, True), (7, False), (21, False), (22, True), (0, True)])
def test_quiet_hours(hour, quiet):
    assert game.is_quiet_hours(datetime(2026, 11, 5, hour, 0, tzinfo=JST)) is quiet


@pytest.mark.parametrize("escaped_m,rank", [
    (0, 1), (999, 1), (1000, 2), (2999, 2), (3000, 3), (7000, 4), (13000, 5), (22999, 5), (23000, 6),
    (100000, 9), (155000, 10), (999999, 10),
])
def test_base_rank_thresholds(escaped_m, rank):
    assert game.base_rank(escaped_m) == rank


def test_base_rank_names_and_next_rank():
    assert game.base_rank_name(1) == "テント" and game.base_rank_name(5) == "要塞"
    assert game.base_rank_name(10) == "灯台の城塞都市" and len(game.BASE_RANKS) == 10
    assert game.next_rank_m(0) == 1000
    assert game.next_rank_m(2500) == 3000
    assert game.next_rank_m(10000) == 13000
    assert game.next_rank_m(155000) is None


def test_credited_walk_trusts_reported_distance_within_bounds():
    assert game.credited_walk_m(1200, 800, timedelta(minutes=30)) == 1200


def test_credited_walk_gives_at_least_straight_distance():
    # 画面を閉じて歩いた人や、計測できなかった人にも直線距離は認める
    assert game.credited_walk_m(None, 800, timedelta(minutes=30)) == 800
    assert game.credited_walk_m(100, 800, timedelta(minutes=30)) == 800


def test_credited_walk_is_capped_by_time():
    # 10分で時速15kmなら2500mが上限。乗り物や書き換えは認めない
    assert game.credited_walk_m(50_000, 800, timedelta(minutes=10)) == 2500
    assert game.credited_walk_m(None, 800, timedelta(minutes=1)) == 250
    assert game.credited_walk_m(1000, 800, timedelta(minutes=-5)) == 0


def test_search_radius_grows_and_caps():
    assert game.search_radius_m(1) == 500
    assert game.search_radius_m(3) == 1000
    assert game.search_radius_m(100) == game.MAX_SEARCH_RADIUS_M


def test_streak_rules_use_japan_date():
    # 日本時間の 23:30 → 翌 00:30 は「翌日」扱いになる
    night = datetime(2026, 11, 5, 23, 30, tzinfo=JST)
    assert game.next_streak(3, night.date(), night + timedelta(hours=1)) == 4
    assert game.next_streak(3, night.date(), night + timedelta(minutes=10)) == 3
    assert game.next_streak(3, night.date(), night + timedelta(days=2)) == 1
    assert game.next_streak(0, None, night) == 1


def test_streak_date_converts_utc_to_jst():
    utc_moment = datetime.fromisoformat("2026-11-05T15:30:00+00:00")  # JSTでは11/6 00:30
    assert game.jst_date(utc_moment) == date(2026, 11, 6)


def test_effective_streak_expires_after_a_missed_day():
    assert game.effective_streak(5, date(2026, 11, 4), T0) == 5
    assert game.effective_streak(5, date(2026, 11, 3), T0) == 0
    assert game.effective_streak(5, None, T0) == 0


@pytest.mark.parametrize(
    "height,width,expected",
    [(None, None, 80), (20, 30, 50), (160, 120, 80), (300, 200, 100), (2000, 900, 100), (None, 500, 80)],
)
def test_checkin_radius_bounds(height, width, expected):
    assert game.checkin_radius_m(height, width) == expected


@pytest.mark.parametrize("hour,dark", [(4, True), (5, False), (17, False), (18, True), (23, True)])
def test_is_dark(hour, dark):
    assert game.is_dark(datetime(2026, 11, 5, hour, 0, tzinfo=JST)) is dark
