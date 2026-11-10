import json
import random
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import create_app
from services import devices, game, missions
from services.config import Settings
from services.location import bearing_deg, destination, distance_m
from services.models import Mission
from services.places import FakePlacesBackend, PlacesError, ShelterFinder
from services.routes import RoutesError

from .conftest import HOME

AWAY = {"lat": HOME[0] + 0.01, "lng": HOME[1]}  # 家から北へ約1.1km
NORMAL = game.MODES["normal"]
HARD = game.MODES["hard"]


def _at_shelter(mission: dict) -> dict:
    return {"lat": mission["shelter"]["lat"], "lng": mission["shelter"]["lng"]}


def _near_zombie(user):
    """ゾンビを目標の1km手前へ置く（方角はそのまま）。"""
    user.zombie_lat, user.zombie_lng = game.place_zombie(user.target_lat, user.target_lng, user.zombie_lat, user.zombie_lng, 1.0)
    return user


def _depart(mission: dict) -> datetime:
    return datetime.fromisoformat(mission["depart_at"])


def _escape(client, clock, mission, at=AWAY) -> None:
    """出発の5分前（脱出できる時間帯）まで進めて脱出する。"""
    clock.now = max(clock.now, _depart(mission) - timedelta(minutes=5))
    assert client.post(f"/api/missions/{mission['id']}/escape", json=at).json()["status"] == "escaped"


def _reserve(client, user_id, mode="normal", place_id=None):
    place = place_id or client.get(f"/api/users/{user_id}/shelters").json()[0]["place_id"]
    return client.post(f"/api/users/{user_id}/missions", json={"place_id": place, "mode": mode})


def _checkin(client, mission, walked_m=None):
    body = _at_shelter(mission) | ({"walked_m": walked_m} if walked_m is not None else {})
    return client.post(f"/api/missions/{mission['id']}/checkin", json=body).json()


def _new_user(client, daily_time):
    body = {"nickname": "隊員", "lat": HOME[0], "lng": HOME[1], "daily_notify_time": daily_time}
    return client.post("/api/users", json=body).json()["user_id"]


def _jst(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(game.JST)


# --- 家の座標を外に出さない ---

def test_home_coordinates_are_rounded_when_stored(deps, user_id):
    user = deps.repo.get_user(user_id)
    assert (user.home_lat, user.home_lng) == (35.681, 139.767)


def test_no_response_exposes_home_coordinates(client, user_id, mission, clock):
    _escape(client, clock, mission)
    bodies = [
        client.get(f"/api/users/{user_id}").text,
        client.get(f"/api/missions/{mission['id']}").text,
        client.get(f"/api/users/{user_id}/shelters").text,
        client.get(f"/home/{user_id}").text,
        client.get(f"/reserve/{user_id}").text,
        client.get(f"/results/{user_id}").text,
        client.get(f"/api/users/{user_id}/stats").text,
    ]
    for body in bodies:
        assert "home_lat" not in body and "home_lng" not in body
        assert "target_lat" not in body and "target_lng" not in body
        assert "35.681," not in body and "139.767," not in body


# --- 入力チェック ---

@pytest.mark.parametrize(
    "patch",
    [{"nickname": ""}, {"lat": 91}, {"lng": 181}, {"daily_notify_time": "24:00"}],
)
def test_invalid_user_input_is_rejected(client, patch):
    body = {"nickname": "a", "lat": 35.0, "lng": 139.0, "daily_notify_time": "18:00"}
    assert client.post("/api/users", json=body | patch).status_code == 422


def test_unknown_and_malformed_ids(client):
    assert client.get("/api/users/" + "a" * 32).status_code == 404
    assert client.get("/api/users/short").status_code == 422
    assert client.get("/home/" + "a" * 32).status_code == 404


def test_ids_are_unguessable(user_id):
    assert len(user_id) >= 32


# --- ゾンビの接近と捕獲 ---

def test_zombie_approaches_without_any_reservation(client, user_id, clock):
    assert client.get(f"/api/users/{user_id}").json()["zombie_km"] == pytest.approx(5, abs=0.001)
    clock.advance(hours=3)  # 12時から18時までの半分
    assert client.get(f"/api/users/{user_id}").json()["zombie_km"] == pytest.approx(2.5, abs=0.001)


def test_zombie_reaches_home_at_set_time_and_game_over_if_not_moved(client, user_id, clock):
    clock.advance(hours=5, minutes=59)
    assert client.get(f"/api/users/{user_id}").json()["capture_count"] == 0
    clock.advance(minutes=1)
    body = client.get(f"/api/users/{user_id}").json()
    assert body["capture_count"] == 1 and body["last_result"] == "captured" and body["chasing"] is False
    assert client.get(f"/api/users/{user_id}").json()["capture_count"] == 1


def test_small_move_near_home_is_still_game_over(client, user_id, clock):
    client.post(f"/api/users/{user_id}/position", json={"lat": HOME[0] + 0.001, "lng": HOME[1]})
    clock.advance(hours=6)
    assert client.get(f"/api/users/{user_id}").json()["capture_count"] == 1


def test_leaving_home_without_shelter_is_still_game_over(client, user_id, clock):
    assert client.post(f"/api/users/{user_id}/position", json=AWAY).json()["chasing"] is False
    clock.advance(hours=6)
    assert client.get(f"/api/users/{user_id}").json()["capture_count"] == 1


def test_leaving_home_starts_chase_at_normal_speed(client, user_id, mission, clock):
    clock.advance(minutes=30)
    after = client.post(f"/api/users/{user_id}/position", json=AWAY).json()
    assert after["chasing"] is True and after["chase_mode"] == "normal"
    assert after["zombie_speed_km_per_hour"] == NORMAL.speed_km_per_hour and len(after["zombies"]) == 1
    clock.advance(minutes=15)
    later = client.get(f"/api/users/{user_id}").json()
    assert later["zombie_km"] == pytest.approx(after["zombie_km"] - NORMAL.speed_km_per_hour / 4, abs=0.01)
    assert later["capture_count"] == 0


def test_capture_resets_distance_and_streak_once(client, deps, user_id, clock):
    clock.advance(hours=73)
    body = client.get(f"/api/users/{user_id}").json()
    assert body["capture_count"] == 1 and body["zombie_km"] == pytest.approx(5, abs=0.001) and body["last_result"] == "captured"
    assert client.get(f"/api/users/{user_id}").json()["capture_count"] == 1


def test_concurrent_views_capture_only_once(deps, user_id, clock):
    clock.advance(hours=73)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: missions.settle_user(deps, user_id), range(40)))
    assert deps.repo.get_user(user_id).capture_count == 1


# --- 毎日の時刻（習慣化のため、予約では変えられない） ---

def test_reservation_uses_daily_time_and_cannot_change_it(client, user_id, clock):
    # 時刻を送っても無視され、次に来る毎日の時刻になる
    place = client.get(f"/api/users/{user_id}/shelters").json()[0]["place_id"]
    body = {"place_id": place, "depart_at": "2026-11-06T13:00:00+09:00"}
    mission = client.post(f"/api/users/{user_id}/missions", json=body).json()
    assert _jst(mission["depart_at"]) == datetime(2026, 11, 6, 18, 0, tzinfo=game.JST)
    user = client.get(f"/api/users/{user_id}").json()
    assert user["arrival_at"] == mission["depart_at"]
    # 予約しても、ゾンビの来る速さは変わらない（12時から18時で5km）
    assert user["zombie_speed_km_per_hour"] == pytest.approx(5 / 6, abs=0.001)


def test_reserve_page_has_no_time_field(client, user_id):
    text = client.get(f"/reserve/{user_id}").text
    assert 'type="time"' not in text and "ゾンビが来る時刻" in text and 'name="mode"' in text


def test_after_game_over_next_arrival_is_tomorrow(client, user_id, clock):
    clock.advance(hours=6)
    user = client.get(f"/api/users/{user_id}").json()
    assert _jst(user["arrival_at"]) == datetime(2026, 11, 7, 18, 0, tzinfo=game.JST)


def test_clearing_skips_todays_daily_time(client, user_id, mission, clock):
    _escape(client, clock, mission)
    _checkin(client, mission)
    user = client.get(f"/api/users/{user_id}").json()
    assert _jst(user["arrival_at"]) == datetime(2026, 11, 7, 18, 0, tzinfo=game.JST)
    clock.advance(hours=6)  # 同じ日の深夜まで進めても、もう来ない
    assert client.get(f"/api/users/{user_id}").json()["capture_count"] == 0


def test_time_change_keeps_todays_arrival_and_applies_tomorrow(client, user_id, clock):
    response = client.put(f"/api/users/{user_id}/daily-time", json={"daily_notify_time": "20:00"})
    assert response.status_code == 200
    # 今日の18時はそのまま。直前に時刻を変えて逃れることはできない
    assert _jst(response.json()["arrival_at"]) == datetime(2026, 11, 6, 18, 0, tzinfo=game.JST)
    clock.advance(hours=6)  # 18時に家にいたのでゲームオーバー
    user = client.get(f"/api/users/{user_id}").json()
    assert user["capture_count"] == 1
    # 同じ日の20時には来ず、明日の20時に来る
    assert _jst(user["arrival_at"]) == datetime(2026, 11, 7, 20, 0, tzinfo=game.JST)


def test_time_change_after_todays_clear_moves_tomorrow_only(client, user_id, mission, clock):
    _escape(client, clock, mission)
    _checkin(client, mission)
    tomorrow = _reserve(client, user_id).json()
    user = client.put(f"/api/users/{user_id}/daily-time", json={"daily_notify_time": "21:00"}).json()
    # 今日もう一度来ることはなく、明日の21時になる。予約した明日のミッションも合わせて動く
    assert _jst(user["arrival_at"]) == datetime(2026, 11, 7, 21, 0, tzinfo=game.JST)
    assert _jst(client.get(f"/api/missions/{tomorrow['id']}").json()["depart_at"]) == datetime(2026, 11, 7, 21, 0, tzinfo=game.JST)
    clock.now = datetime(2026, 11, 6, 21, 30, tzinfo=game.JST)
    assert client.get(f"/api/users/{user_id}").json()["capture_count"] == 0


def test_time_change_rejects_invalid_time(client, user_id):
    assert client.put(f"/api/users/{user_id}/daily-time", json={"daily_notify_time": "25:00"}).status_code == 422


# --- 予約 ---

def test_reservation_rules(client, user_id):
    assert _reserve(client, user_id, place_id="not-a-candidate").json()["error"]["code"] == "unknown_shelter"
    assert _reserve(client, user_id, mode="extreme").status_code == 422
    assert _reserve(client, user_id).status_code == 201
    assert _reserve(client, user_id).json()["error"]["code"] == "already_reserved"


def test_reservation_while_chased_is_rejected(client, user_id, mission, clock):
    _escape(client, clock, mission)
    assert _reserve(client, user_id).json()["error"]["code"] == "chasing"


def test_daily_mission_limit(client, deps, user_id):
    def finish(mission_id):
        deps.repo.transact(user_id, mission_id, lambda u, m: (u, m.model_copy(update={"status": "failed"})))

    for _ in range(game.MAX_MISSIONS_PER_DAY):
        created = _reserve(client, user_id)
        assert created.status_code == 201
        finish(created.json()["id"])
    assert _reserve(client, user_id).status_code == 429


@pytest.mark.parametrize("daily_time,expected", [("13:00", False), ("19:00", True)])
def test_night_warning(client, daily_time, expected):
    user_id = _new_user(client, daily_time)
    assert _reserve(client, user_id).json()["night_warning"] is expected


def test_shelters_have_short_address(client, user_id):
    shelters = client.get(f"/api/users/{user_id}/shelters").json()
    assert shelters and all(s["address"].startswith("開発用の町") for s in shelters)


def test_short_address_drops_country_and_postal_code():
    from services.places import _short_address

    assert _short_address("日本、〒150-0041 東京都渋谷区神南２丁目") == "東京都渋谷区神南２丁目"
    assert _short_address("渋谷区神南２丁目") == "渋谷区神南２丁目"
    assert _short_address(None) == ""


def test_shelter_pickers_have_map_without_home(client, user_id):
    for url in (f"/reserve/{user_id}", f"/run/{user_id}"):
        text = client.get(url).text
        assert 'id="shelter-map"' in text and "/static/shelter-map.js?v=" in text


def test_welcome_page_offers_resume(client):
    text = client.get("/").text
    assert 'id="resume"' in text and "/static/welcome.js?v=" in text


def test_shelters_exclude_places_too_close_and_grow_with_base_rank(client, deps, user_id):
    near = client.get(f"/api/users/{user_id}/shelters").json()
    assert near and all(game.MIN_SHELTER_DISTANCE_M <= s["distance_m"] <= 500 for s in near)
    deps.repo.transact(user_id, None, lambda u, _m: (u.model_copy(update={"base_escaped_m": 10_000}), None))
    assert len(client.get(f"/api/users/{user_id}/shelters").json()) > len(near)


# --- 脱出 ---

def test_escape_outcomes(client, user_id, mission, clock):
    url = f"/api/missions/{mission['id']}/escape"
    assert client.post(url, json=AWAY).json()["status"] == "too_early"
    clock.now = _depart(mission) - timedelta(minutes=5)
    assert client.post(url, json=AWAY | {"accuracy_m": 500}).json()["status"] == "low_accuracy"
    assert client.post(url, json=AWAY).json()["status"] == "escaped"
    assert client.post(url, json=AWAY).json()["status"] == "escaped"


def test_escape_far_from_home_switches_to_a_shelter_near_here(client, deps, user_id, mission, clock):
    # 時刻に外出先（家から約5.5km）にいる。予約したシェルターは家の近くなので遠すぎる
    outside = {"lat": HOME[0] + 0.05, "lng": HOME[1]}
    clock.now = _depart(mission) - timedelta(minutes=5)
    body = client.post(f"/api/missions/{mission['id']}/escape", json=outside).json()
    assert body == {"status": "escaped", "shelter_changed": True}
    shelter = client.get(f"/api/missions/{mission['id']}").json()["shelter"]
    assert (shelter["lat"], shelter["lng"]) != (mission["shelter"]["lat"], mission["shelter"]["lng"])
    rank = game.base_rank(deps.repo.get_user(user_id).base_escaped_m)
    here_m = distance_m(outside["lat"], outside["lng"], shelter["lat"], shelter["lng"])
    assert here_m <= game.search_radius_m(rank) and shelter["distance_m"] == pytest.approx(here_m, abs=2)
    # 替えたシェルターでチェックインできる
    clock.advance(minutes=10)
    assert client.post(f"/api/missions/{mission['id']}/checkin", json={"lat": shelter["lat"], "lng": shelter["lng"]}).json()["status"] == "cleared"


def test_escape_just_outside_home_keeps_reserved_shelter(client, mission, clock):
    clock.now = _depart(mission) - timedelta(minutes=5)
    body = client.post(f"/api/missions/{mission['id']}/escape", json=AWAY).json()
    assert body == {"status": "escaped", "shelter_changed": False}
    assert client.get(f"/api/missions/{mission['id']}").json()["shelter"] == mission["shelter"]


def test_escape_at_home_starts_chase_right_away(client, user_id, mission, clock):
    # フリーランと同じく、家にいても押した瞬間に逃走が始まる
    clock.now = _depart(mission) - timedelta(minutes=5)
    body = client.post(f"/api/missions/{mission['id']}/escape", json={"lat": HOME[0], "lng": HOME[1]}).json()
    assert body["status"] == "escaped"
    assert client.get(f"/api/users/{user_id}").json()["chasing"] is True
    # 家の近くにはアイテムを置かない
    items = client.get(f"/api/missions/{mission['id']}").json()["items"]
    assert all(distance_m(HOME[0], HOME[1], i["lat"], i["lng"]) >= game.ITEM_HOME_EXCLUSION_M for i in items)
    # 徒歩ルートもすぐ取れる
    route = client.post(f"/api/missions/{mission['id']}/route", json={"lat": HOME[0], "lng": HOME[1]})
    assert route.status_code == 200 and route.json()["points"]


def test_staying_home_after_escape_gets_caught(client, user_id, mission, clock):
    clock.now = _depart(mission) - timedelta(minutes=5)
    client.post(f"/api/missions/{mission['id']}/escape", json={"lat": HOME[0], "lng": HOME[1]})
    clock.advance(hours=3)  # 家から動かないまま
    user = client.get(f"/api/users/{user_id}").json()
    assert user["capture_count"] == 1 and user["last_result"] == "captured"


def test_staying_home_at_depart_time_fails_once(client, deps, user_id, mission, clock):
    clock.now = _depart(mission)
    assert client.post(f"/api/missions/{mission['id']}/escape", json=AWAY).json()["status"] == "timeout"
    first = client.get(f"/api/users/{user_id}").json()
    assert first["last_result"] == "failed" and first["capture_count"] == 1
    assert first["zombie_km"] == pytest.approx(game.ZOMBIE_START_KM, abs=0.001)
    assert client.get(f"/api/users/{user_id}").json()["capture_count"] == 1


def test_failed_mission_allows_new_reservation_for_tomorrow(client, user_id, mission, clock):
    clock.now = _depart(mission) + timedelta(minutes=30)
    response = _reserve(client, user_id)
    assert response.status_code == 201
    assert _jst(response.json()["depart_at"]) == datetime(2026, 11, 7, 18, 0, tzinfo=game.JST)


# --- チェックイン ---

def test_checkin_requires_escape(client, mission):
    response = client.post(f"/api/missions/{mission['id']}/checkin", json=_at_shelter(mission))
    assert response.status_code == 409


def test_checkin_too_far_reports_remaining(client, mission, clock):
    _escape(client, clock, mission)
    body = client.post(f"/api/missions/{mission['id']}/checkin", json=AWAY).json()
    assert body["status"] == "too_far" and body["remaining_m"] > 0


def test_checkin_needs_a_precise_position(client, mission, clock):
    _escape(client, clock, mission)
    clock.advance(minutes=3)
    url = f"/api/missions/{mission['id']}/checkin"
    # 精度の誤差のぶん手前でゴールにならないよう、脱出（100m）より厳しく見る
    assert client.post(url, json=_at_shelter(mission) | {"accuracy_m": 60}).json()["status"] == "low_accuracy"
    assert client.post(url, json=_at_shelter(mission) | {"accuracy_m": 30}).json()["status"] == "cleared"


def test_checkin_area_ends_at_the_radius(client, mission, clock):
    _escape(client, clock, mission)
    clock.advance(minutes=3)
    shelter = mission["shelter"]
    url = f"/api/missions/{mission['id']}/checkin"
    outside = destination(shelter["lat"], shelter["lng"], 0, shelter["checkin_radius_m"] + 5)
    inside = destination(shelter["lat"], shelter["lng"], 0, shelter["checkin_radius_m"] - 5)
    assert client.post(url, json={"lat": outside[0], "lng": outside[1]}).json()["status"] == "too_far"
    assert client.post(url, json={"lat": inside[0], "lng": inside[1]}).json()["status"] == "cleared"


def test_checkin_clears_and_adds_walked_distance(client, user_id, mission, clock):
    _escape(client, clock, mission)
    clock.advance(minutes=3)  # 追いつかれる前に着く
    result = _checkin(client, mission, walked_m=1234)["result"]
    assert result["walked_m"] == 1234 and result["streak"] == 1 and result["mode"] == "normal"
    assert result["base_points_gained"] == 1234 and result["mode_bonus_m"] == 0
    assert result["base_escaped_m_after"] == 1234 and result["base_rank_before"] == 1 and result["base_rank_after"] == 2
    assert result["base_rank_name_after"] == "小屋"
    user = client.get(f"/api/users/{user_id}").json()
    assert user["base_escaped_m"] == 1234 and user["total_walked_m"] == 1234 and user["base_rank"] == 2
    assert user["last_result"] == "cleared" and user["next_rank_m"] == 3000


def test_checkin_without_measurement_counts_straight_distance(client, mission, clock):
    _escape(client, clock, mission)
    clock.advance(minutes=3)
    assert _checkin(client, mission)["result"]["walked_m"] == mission["shelter"]["distance_m"]


def test_checkin_caps_unrealistic_distance_by_time(client, mission, clock):
    _escape(client, clock, mission)  # 出発の5分前。数え始めは出発の10分前
    result = _checkin(client, mission, walked_m=50_000)["result"]
    assert result["walked_m"] == 1250  # 5分を時速15kmで歩いた距離


def test_checkin_rejects_invalid_walked_distance(client, mission, clock):
    _escape(client, clock, mission)
    response = client.post(f"/api/missions/{mission['id']}/checkin", json=_at_shelter(mission) | {"walked_m": -1})
    assert response.status_code == 422


def test_repeated_checkin_adds_distance_once(client, user_id, mission, clock):
    _escape(client, clock, mission)
    clock.advance(minutes=3)
    first = _checkin(client, mission, walked_m=900)["result"]
    assert _checkin(client, mission, walked_m=900)["result"] == first
    assert client.get(f"/api/users/{user_id}").json()["total_walked_m"] == first["walked_m"]


def test_concurrent_checkins_add_distance_once(client, deps, user_id, mission, clock):
    _escape(client, clock, mission)
    position = _at_shelter(mission)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: missions.checkin(deps, mission["id"], position["lat"], position["lng"], None), range(20)))
    walked = deps.repo.get_mission(mission["id"]).result.walked_m
    assert deps.repo.get_user(user_id).base_escaped_m == walked
    assert all(r["status"] == "cleared" for r in results)


def test_streak_grows_on_next_day(client, user_id, mission, clock):
    _escape(client, clock, mission)
    _checkin(client, mission)
    second = _reserve(client, user_id).json()
    _escape(client, clock, second)  # 翌日の18時の5分前
    assert _checkin(client, second)["result"]["streak"] == 2


# --- モード ---

def test_hard_mode_has_three_fast_zombies(client, deps, user_id, clock):
    mission = _reserve(client, user_id, mode="hard").json()
    assert mission["mode"] == "hard"
    _escape(client, clock, mission)
    user = client.get(f"/api/users/{user_id}").json()
    assert user["chase_mode"] == "hard" and user["zombie_speed_km_per_hour"] == HARD.speed_km_per_hour
    assert len(user["zombies"]) == HARD.zombies
    stored = deps.repo.get_user(user_id)
    # 追加の2体は本体と同じ方面から、本体の少し後ろに群れで来る
    target = (stored.target_lat, stored.target_lng)
    main_bearing = bearing_deg(*target, stored.zombie_lat, stored.zombie_lng)
    main_m = distance_m(stored.zombie_lat, stored.zombie_lng, *target)
    for zombie, (angle, behind_km) in zip(stored.extra_zombies, game.EXTRA_ZOMBIE_OFFSETS):
        gap = (bearing_deg(*target, zombie.lat, zombie.lng) - main_bearing + 180) % 360 - 180
        assert gap == pytest.approx(angle, abs=1)
        assert distance_m(zombie.lat, zombie.lng, *target) == pytest.approx(main_m + behind_km * 1000, abs=10)
    # 最も近い1体が zombie_* になる
    assert user["zombie_km"] == pytest.approx(min(z["km"] for z in user["zombies"]), abs=1e-5)


def test_hard_mode_any_zombie_captures(client, deps, user_id, clock):
    mission = _reserve(client, user_id, mode="hard").json()
    _escape(client, clock, mission)

    def put_extra_next_to_target(user, _mission):
        user.extra_zombies[0].lat, user.extra_zombies[0].lng = user.target_lat + 0.0005, user.target_lng
        return user, None

    deps.repo.transact(user_id, None, put_extra_next_to_target)
    clock.advance(seconds=30)  # 約55mを時速9kmなら30秒かからない
    user = client.get(f"/api/users/{user_id}").json()
    assert user["capture_count"] == 1 and user["chasing"] is False and user["zombies"] and len(user["zombies"]) == 1
    assert client.get(f"/api/missions/{mission['id']}").json()["status"] == "failed"


def test_hard_mode_multiplies_base_points_but_not_walked_distance(client, user_id, clock):
    mission = _reserve(client, user_id, mode="hard").json()
    _escape(client, clock, mission)
    clock.advance(minutes=3)
    result = _checkin(client, mission, walked_m=1000)["result"]
    assert result["walked_m"] == 1000 and result["mode_bonus_m"] == 500 and result["base_points_gained"] == 1500
    user = client.get(f"/api/users/{user_id}").json()
    assert user["base_escaped_m"] == 1500 and user["total_walked_m"] == 1000


# --- アイテム ---

def _items(client, mission):
    return client.get(f"/api/missions/{mission['id']}").json()["items"]


def _collect(client, mission, item, at=None, **extra):
    position = at or {"lat": item["lat"], "lng": item["lng"]}
    return client.post(f"/api/missions/{mission['id']}/items/{item['id']}/collect", json=position | extra)


def test_items_appear_on_escape_between_escape_point_and_shelter(client, mission, clock):
    assert _items(client, mission) == []
    _escape(client, clock, mission)
    items = _items(client, mission)
    materials = [i for i in items if i["kind"] == "material"]
    assert game.ITEM_MIN <= len(materials) <= game.ITEM_MAX
    assert len(items) - len(materials) <= 1 and all(i["kind"] in ("material", "freeze") for i in items)
    shelter = mission["shelter"]
    route_m = distance_m(AWAY["lat"], AWAY["lng"], shelter["lat"], shelter["lng"])
    for item in items:
        via = distance_m(AWAY["lat"], AWAY["lng"], item["lat"], item["lng"]) + distance_m(item["lat"], item["lng"], shelter["lat"], shelter["lng"])
        assert via <= route_m + 2 * game.ITEM_LATERAL_M + 1  # 直線からほとんど外れない
        assert item["collected"] is False


def test_items_are_never_placed_near_home(client, mission, clock):
    # 家をはさんで反対側から脱出すると、直線の途中が家の近くを通る
    south = {"lat": HOME[0] - 0.003, "lng": HOME[1]}
    _escape(client, clock, mission, at=south)
    items = _items(client, mission)
    assert len(items) < 4  # 家の近くに落ちる分は置かない
    assert all(distance_m(HOME[0], HOME[1], i["lat"], i["lng"]) >= game.ITEM_HOME_EXCLUSION_M for i in items)


# --- フリーズ（ゾンビを1日止める） ---

@pytest.fixture
def always_freeze(monkeypatch):
    monkeypatch.setattr(game, "FREEZE_ITEM_CHANCE", 1.0)


def _gain_freeze(client, clock, mission):
    _escape(client, clock, mission)
    freeze = next(i for i in _items(client, mission) if i["kind"] == "freeze")
    assert _collect(client, mission, freeze).json()["kind"] == "freeze"
    clock.advance(minutes=10)
    return _checkin(client, mission)["result"]


def test_freeze_is_kept_only_after_reaching_shelter(client, user_id, mission, clock, always_freeze):
    _escape(client, clock, mission)
    freeze = next(i for i in _items(client, mission) if i["kind"] == "freeze")
    _collect(client, mission, freeze)
    assert client.get(f"/api/missions/{mission['id']}").json()["freeze_found"] is True
    assert client.get(f"/api/users/{user_id}").json()["freeze_count"] == 0  # 着くまではたまらない
    clock.advance(minutes=10)
    assert _checkin(client, mission)["result"]["freeze_gained"] is True
    assert client.get(f"/api/users/{user_id}").json()["freeze_count"] == 1


def test_freeze_appears_about_every_two_or_three_daily_runs():
    hits = sum(game.plan_freeze(35.70, 139.70, 35.70, 139.71, random.Random(seed)) is not None for seed in range(1000))
    assert 300 <= hits <= 500


def test_free_run_never_has_freeze(client, user_id, always_freeze):
    run = _run(client, user_id).json()
    assert all(i["kind"] != "freeze" for i in run["items"])


def test_using_freeze_skips_next_arrival_and_keeps_streak(client, deps, user_id, mission, clock, always_freeze):
    _gain_freeze(client, clock, mission)  # 今日（6日）クリア。次の到着は7日18時
    before = client.get(f"/api/users/{user_id}").json()
    assert before["streak"] == 1 and before["freeze_count"] == 1
    tomorrow = datetime.fromisoformat(before["arrival_at"])
    second = _reserve(client, user_id).json()
    assert datetime.fromisoformat(second["depart_at"]) == tomorrow

    user = client.post(f"/api/users/{user_id}/freeze").json()
    assert user["freeze_count"] == 0
    assert datetime.fromisoformat(user["arrival_at"]) == tomorrow + timedelta(days=1)
    assert user["zombie_km"] == pytest.approx(game.ZOMBIE_START_KM, abs=0.001)
    # 予約していたミッションも、その次の日に移る
    moved = client.get(f"/api/missions/{second['id']}").json()
    assert datetime.fromisoformat(moved["depart_at"]) == tomorrow + timedelta(days=1)

    # 7日は何も起きず、8日にクリアすると連続日数がつながる
    clock.now = tomorrow + timedelta(hours=1)
    assert client.get(f"/api/users/{user_id}").json()["capture_count"] == 0
    _escape(client, clock, moved)
    clock.advance(minutes=10)
    assert _checkin(client, moved)["result"]["streak"] == 2


def test_freeze_needs_stock_and_not_while_running(client, user_id, mission, clock, always_freeze):
    assert client.post(f"/api/users/{user_id}/freeze").json()["error"]["code"] == "no_freeze"
    _gain_freeze(client, clock, mission)
    _run(client, user_id)  # フリーラン中は使えない
    assert client.post(f"/api/users/{user_id}/freeze").status_code == 409


def test_freeze_stock_is_capped(client, deps, user_id, mission, clock, always_freeze):
    deps.repo.transact(user_id, None, lambda u, _m: (u.model_copy(update={"freeze_count": game.FREEZE_MAX}), None))
    assert _gain_freeze(client, clock, mission)["freeze_gained"] is False
    assert client.get(f"/api/users/{user_id}").json()["freeze_count"] == game.FREEZE_MAX


def test_material_counts_only_when_reaching_shelter(client, user_id, mission, clock):
    _escape(client, clock, mission)
    material = next(i for i in _items(client, mission) if i["kind"] == "material")
    assert _collect(client, mission, material).json()["kind"] == "material"
    assert client.get(f"/api/missions/{mission['id']}").json()["material_bonus_m"] == game.MATERIAL_BONUS_M
    assert client.get(f"/api/users/{user_id}").json()["base_escaped_m"] == 0  # 着くまでは入らない
    clock.advance(minutes=3)
    result = _checkin(client, mission, walked_m=800)["result"]
    assert result["material_bonus_m"] == game.MATERIAL_BONUS_M and result["base_points_gained"] == 800 + game.MATERIAL_BONUS_M
    user = client.get(f"/api/users/{user_id}").json()
    assert user["base_escaped_m"] == 1000 and user["total_walked_m"] == 800


def test_collect_rules(client, user_id, mission, clock):
    _escape(client, clock, mission)
    item = _items(client, mission)[0]
    far = {"lat": item["lat"] + 0.002, "lng": item["lng"]}
    body = _collect(client, mission, item, at=far).json()
    assert body["status"] == "too_far" and body["remaining_m"] > 0
    assert _collect(client, mission, item, accuracy_m=80).json()["status"] == "low_accuracy"
    assert _collect(client, mission, item).json()["status"] == "collected"
    assert _collect(client, mission, item).json()["status"] == "already"
    assert client.post(f"/api/missions/{mission['id']}/items/unknown-item/collect", json=AWAY).status_code == 404
    assert client.post(f"/api/missions/{mission['id']}/items/x/collect", json=AWAY).status_code == 422


def test_collect_requires_active_escape(client, deps, user_id, mission, clock):
    _escape(client, clock, mission)
    item = _items(client, mission)[0]
    clock.advance(minutes=3)
    _checkin(client, mission)
    assert _collect(client, mission, item).status_code == 409


def test_concurrent_collects_apply_once(client, deps, user_id, mission, clock):
    _escape(client, clock, mission)
    material = next(i for i in _items(client, mission) if i["kind"] == "material")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: missions.collect_item(deps, mission["id"], material["id"], material["lat"], material["lng"], None), range(10)))
    assert deps.repo.get_mission(mission["id"]).material_bonus_m == game.MATERIAL_BONUS_M


# --- フリーラン（何回でも走れる） ---

START = {"lat": HOME[0] + 0.006, "lng": HOME[1] + 0.004}  # 家から北東へ約770m


def _near_shelters(client, user_id, at=START):
    return client.post(f"/api/users/{user_id}/shelters/near", json=at).json()


def _run(client, user_id, mode="normal", at=START, place_id=None):
    place = place_id or _near_shelters(client, user_id, at)[0]["place_id"]
    return client.post(f"/api/users/{user_id}/runs", json={"place_id": place, "mode": mode} | at)


def test_near_shelters_are_around_current_position(client, user_id):
    shelters = _near_shelters(client, user_id)
    assert shelters
    for shelter in shelters:
        # 中心は約100m単位に丸める
        center = (round(START["lat"], 3), round(START["lng"], 3))
        assert distance_m(*center, shelter["lat"], shelter["lng"]) == pytest.approx(shelter["distance_m"], abs=1)


def test_free_run_starts_chase_right_away_with_zombie_behind(client, deps, user_id, clock):
    response = _run(client, user_id)
    assert response.status_code == 201
    run = response.json()
    assert run["kind"] == "free" and run["status"] == "escaped" and run["items"]
    user = client.get(f"/api/users/{user_id}").json()
    assert user["chasing"] is True and user["chase_mode"] == "normal" and len(user["zombies"]) == 1
    assert user["zombie_km"] == pytest.approx(game.FREE_RUN_ZOMBIE_KM, abs=0.01)
    # ゾンビはシェルターと反対側（背後）にいる
    stored = deps.repo.get_user(user_id)
    shelter = run["shelter"]
    to_shelter = distance_m(stored.target_lat, stored.target_lng, shelter["lat"], shelter["lng"])
    zombie_to_shelter = distance_m(user["zombie_lat"], user["zombie_lng"], shelter["lat"], shelter["lng"])
    assert zombie_to_shelter > to_shelter


def test_free_run_hard_has_three_zombies(client, user_id):
    _run(client, user_id, mode="hard")
    user = client.get(f"/api/users/{user_id}").json()
    assert len(user["zombies"]) == HARD.zombies and user["zombie_speed_km_per_hour"] == HARD.speed_km_per_hour


def test_free_run_can_be_repeated_any_number_of_times(client, user_id, clock):
    # 400m後ろの時速6kmのゾンビは4分で追いつくので、3分で着く
    for walked in (400, 550, 700):
        run = _run(client, user_id).json()
        clock.advance(minutes=3)
        assert _checkin(client, run, walked_m=walked)["result"]["walked_m"] == walked
        clock.advance(minutes=10)
    user = client.get(f"/api/users/{user_id}").json()
    assert user["total_walked_m"] == 1650 and user["base_escaped_m"] == 1650


def test_free_run_rules(client, user_id, clock):
    assert _run(client, user_id, place_id="not-a-candidate").json()["error"]["code"] == "unknown_shelter"
    assert _run(client, user_id).status_code == 201
    assert _run(client, user_id).json()["error"]["code"] == "chasing"


def test_free_run_walk_cap_starts_at_run_start(client, user_id, clock):
    run = _run(client, user_id).json()
    clock.advance(minutes=3)
    assert _checkin(client, run, walked_m=50_000)["result"]["walked_m"] == 750  # 3分を時速15km


def test_free_run_capture_keeps_base_and_streak(client, deps, user_id, mission, clock):
    _set_base(deps, user_id, 3200)
    _escape(client, clock, mission)
    clock.advance(minutes=3)
    _checkin(client, mission)  # 連続1日
    clock.now = datetime(2026, 11, 7, 9, 0, tzinfo=game.JST)
    run = _run(client, user_id).json()
    clock.advance(hours=2)  # シェルターに着かないまま追いつかれる
    user = client.get(f"/api/users/{user_id}").json()
    assert user["capture_count"] == 1 and user["last_result"] == "run_captured"
    assert user["base_escaped_m"] > 3200 and user["lost_base_rank"] is None and user["streak"] == 1
    assert client.get(f"/api/missions/{run['id']}").json()["status"] == "failed"
    assert "拠点は無事" in client.get(f"/home/{user_id}").text


def test_free_run_before_daily_time_keeps_todays_arrival(client, user_id, clock):
    run = _run(client, user_id).json()
    clock.advance(minutes=3)
    _checkin(client, run)
    user = client.get(f"/api/users/{user_id}").json()
    # 毎日の時刻のゾンビは必ず来る。今日の18時はそのまま
    assert _jst(user["arrival_at"]) == datetime(2026, 11, 6, 18, 0, tzinfo=game.JST)
    assert user["zombie_km"] == pytest.approx(5 * (1 - 3 / 360), abs=0.01)  # 走っていた3分の分も近づいている
    clock.now = datetime(2026, 11, 6, 18, 0, tzinfo=game.JST)
    assert client.get(f"/api/users/{user_id}").json()["capture_count"] == 1  # 家にいればゲームオーバー


def test_free_run_does_not_reset_the_daily_zombie(client, user_id, clock):
    clock.advance(hours=3)  # 15時。12時に5km先を出て18時に着くので、残り2.5km
    run = _run(client, user_id).json()
    clock.advance(minutes=3)
    assert _checkin(client, run)["status"] == "cleared"
    user = client.get(f"/api/users/{user_id}").json()
    assert user["zombie_km"] == pytest.approx(5 * (1 - 183 / 360), abs=0.01)
    assert _jst(user["arrival_at"]) == datetime(2026, 11, 6, 18, 0, tzinfo=game.JST)


def test_free_run_capture_keeps_the_daily_zombie_coming(client, user_id, clock):
    clock.advance(hours=3)
    _run(client, user_id)
    clock.advance(minutes=61)  # 位置が途絶えたまま60分を超えて捕まった。毎日の時刻より前なので拠点は無事
    user = client.get(f"/api/users/{user_id}").json()
    assert user["last_result"] == "run_captured"
    assert user["zombie_km"] == pytest.approx(5 * (1 - 241 / 360), abs=0.01)


def test_free_run_across_daily_time_respawns_zombie_far_away(client, user_id, clock):
    clock.now = datetime(2026, 11, 6, 17, 58, tzinfo=game.JST)
    run = _run(client, user_id).json()
    clock.advance(minutes=3)  # 18時をまたいだ。今日の分は終わり
    assert _checkin(client, run)["status"] == "cleared"
    user = client.get(f"/api/users/{user_id}").json()
    assert user["zombie_km"] == pytest.approx(game.ZOMBIE_START_KM, abs=0.01)
    assert _jst(user["arrival_at"]) == datetime(2026, 11, 7, 18, 0, tzinfo=game.JST)


def test_free_run_across_daily_time_counts_as_daily(client, deps, user_id, mission, clock):
    clock.now = datetime(2026, 11, 6, 17, 40, tzinfo=game.JST)
    here = {"lat": HOME[0] + 0.03, "lng": HOME[1]}  # 遠くから走り始める
    run = _run(client, user_id, at=here).json()
    # 18時をまたいで走り続ける（2分ごとに約270m＝時速8km。時速6kmのゾンビには追いつかれない）
    while clock.now < datetime(2026, 11, 6, 18, 15, tzinfo=game.JST):
        clock.advance(minutes=2)
        here = {"lat": here["lat"] + 0.0024, "lng": here["lng"]}
        client.post(f"/api/users/{user_id}/position", json=here)
    # 逃げ切るか捕まるかが決まるまでは、達成にも失敗にもしない
    assert client.get(f"/api/missions/{mission['id']}").json()["status"] == "reserved"
    user = client.get(f"/api/users/{user_id}").json()
    assert user["capture_count"] == 0 and user["chasing"] is True
    _checkin(client, run)
    daily = client.get(f"/api/missions/{mission['id']}").json()
    assert daily["status"] == "cleared"
    assert daily["result"]["covered_by_free_run"] is True and daily["result"]["walked_m"] == 0
    user = client.get(f"/api/users/{user_id}").json()
    assert user["streak"] == 1
    # 今日の分は終わったので、次は翌日
    assert _jst(user["arrival_at"]) == datetime(2026, 11, 7, 18, 0, tzinfo=game.JST)
    stats = client.get(f"/api/users/{user_id}/stats").json()
    assert stats["clear_count"] == 1  # 達成扱いの分は二重に数えない


def test_free_run_notification_says_base_is_safe(client, deps, user_id, clock):
    from services.notify import _RUN_CAPTURE_MESSAGE

    assert "拠点は無事" in _RUN_CAPTURE_MESSAGE[1]


# --- アバター ---

def test_avatar_defaults_and_change(client, user_id):
    user = client.get(f"/api/users/{user_id}").json()
    assert (user["avatar_outfit"], user["avatar_skin"]) == (game.DEFAULT_OUTFIT, game.DEFAULT_SKIN)
    changed = client.put(f"/api/users/{user_id}/avatar", json={"outfit": "red", "skin": "dark"}).json()
    assert (changed["avatar_outfit"], changed["avatar_skin"]) == ("red", "dark")


@pytest.mark.parametrize("body", [{"outfit": "gold", "skin": "fair"}, {"outfit": "red", "skin": "green"}, {"outfit": "red"}])
def test_avatar_rejects_unknown_colors(client, user_id, body):
    assert client.put(f"/api/users/{user_id}/avatar", json=body).status_code == 422


def test_avatar_choices_match_game_palette():
    from typing import get_args

    from services.models import OutfitColor, SkinColor

    assert set(get_args(OutfitColor)) == set(game.AVATAR_OUTFITS)
    assert set(get_args(SkinColor)) == set(game.AVATAR_SKINS)


def test_home_shows_free_run_and_avatar(client, user_id):
    text = client.get(f"/home/{user_id}").text
    assert "フリーラン" in text and f"/run/{user_id}" in text
    assert 'id="avatar-card"' in text and 'data-kind="outfit"' in text and 'aria-checked="true"' in text


def test_home_during_free_run_links_back_to_map(client, user_id):
    run = _run(client, user_id).json()
    text = client.get(f"/home/{user_id}").text
    assert "外のマップに戻る" in text and run["id"] in text and "フリーラン中" in text


def test_run_page_renders_and_redirects_while_running(client, user_id):
    text = client.get(f"/run/{user_id}").text
    assert 'id="run-form"' in text and 'name="mode"' in text and "/static/run.js" in text
    run = _run(client, user_id).json()
    text = client.get(f"/run/{user_id}").text
    assert "外のマップに戻る" in text and run["id"] in text and "run.js" not in text
    assert client.get("/run/" + "a" * 32).status_code == 404


def test_escape_page_has_avatar_palette(client, mission):
    text = client.get(f"/escape?mission={mission['id']}").text
    assert 'id="avatar-palette"' in text and "lime" in text


# --- 画面 ---

def test_pages_render(client, user_id, mission):
    home = client.get(f"/home/{user_id}")
    assert home.status_code == 200 and mission["shelter"]["name"] in home.text
    assert client.get(f"/reserve/{user_id}").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get(f"/escape?mission={mission['id']}").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_escape_page_renders_mission_id(client, mission):
    text = client.get(f"/escape?mission={mission['id']}").text
    assert mission["id"] in text and "/static/escape.js" in text


def test_escape_page_escapes_mission_parameter(client):
    assert "<script>alert" not in client.get("/escape", params={"mission": "<script>alert(1)</script>"}).text


def test_reserve_page_while_chased_shows_notice(client, user_id, mission, clock):
    _escape(client, clock, mission)
    text = client.get(f"/reserve/{user_id}").text
    assert "いまは追われています" in text and "reserve.js" not in text


def test_home_has_daily_time_form(client, user_id):
    text = client.get(f"/home/{user_id}").text
    assert 'id="daily-time-form"' in text and 'value="18:00"' in text and "明日から" in text


def test_route_requires_escape(client, mission):
    response = client.post(f"/api/missions/{mission['id']}/route", json=AWAY)
    assert response.status_code == 409 and response.json()["error"]["code"] == "not_escaped"


def test_route_returns_points_to_shelter(client, mission, clock):
    _escape(client, clock, mission)
    body = client.post(f"/api/missions/{mission['id']}/route", json=AWAY).json()
    assert len(body["points"]) >= 2 and body["distance_m"] > 0 and body["duration_min"] >= 1
    assert body["points"][-1] == {"lat": mission["shelter"]["lat"], "lng": mission["shelter"]["lng"]}


def test_route_failure_returns_message(client, deps, mission, clock):
    _escape(client, clock, mission)

    class Broken:
        def walking(self, *_args):
            raise RoutesError("徒歩ルートを取得できませんでした")

    deps.routes = Broken()
    response = client.post(f"/api/missions/{mission['id']}/route", json=AWAY)
    assert response.status_code == 502 and response.json()["error"]["code"] == "routes_unavailable"


def test_nickname_is_html_escaped(client):
    body = {"nickname": "<script>x", "lat": 35.0, "lng": 139.0, "daily_notify_time": "18:00"}
    user_id = client.post("/api/users", json=body).json()["user_id"]
    text = client.get(f"/home/{user_id}").text
    assert "<script>x" not in text and "&lt;script&gt;x" in text


def test_security_headers(client):
    headers = client.get("/health").headers
    assert headers["referrer-policy"] == "strict-origin" and headers["x-robots-tag"] == "noindex"


def test_pages_link_versioned_assets(client, user_id):
    assert "/static/reserve.js?v=" in client.get(f"/reserve/{user_id}").text
    assert "/static/style.css?v=" in client.get("/").text


def test_static_files_are_revalidated(client):
    assert client.get("/static/style.css").headers["cache-control"] == "no-cache"


def test_dev_user_route_works_locally(client):
    response = client.get("/dev/new-user", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/home/")


def test_mission_public_does_not_expose_user_id_field(mission):
    assert "user_id" not in json.dumps(mission)
    assert Mission.model_fields.get("user_id") is not None


# --- ゾンビは最後の位置を追う ---

NEAR = {"lat": HOME[0] + 0.002, "lng": HOME[1]}


def _distance_to(body: dict, lat: float, lng: float) -> float:
    return distance_m(body["zombie_lat"], body["zombie_lng"], lat, lng) / 1000


def test_zombie_distance_matches_stored_position(client, deps, user_id):
    body = client.get(f"/api/users/{user_id}").json()
    stored = deps.repo.get_user(user_id)
    assert distance_m(stored.zombie_lat, stored.zombie_lng, *HOME) / 1000 == pytest.approx(body["zombie_km"], abs=0.01)


def test_zombie_targets_latest_position(client, deps, user_id, mission, clock):
    clock.advance(minutes=30)
    client.post(f"/api/users/{user_id}/position", json=NEAR)  # ここから追跡が始まる
    clock.advance(minutes=5)
    before = client.get(f"/api/users/{user_id}").json()
    farther = {"lat": NEAR["lat"] + 0.002, "lng": NEAR["lng"]}
    after = client.post(f"/api/users/{user_id}/position", json=farther).json()
    # 更新の瞬間にゾンビは瞬間移動しない
    assert after["zombie_lat"] == pytest.approx(before["zombie_lat"], abs=1e-4)
    assert after["zombie_lng"] == pytest.approx(before["zombie_lng"], abs=1e-4)
    assert _distance_to(after, farther["lat"], farther["lng"]) == pytest.approx(after["zombie_km"], abs=0.01)
    clock.advance(minutes=15)
    later = client.get(f"/api/users/{user_id}").json()
    assert later["zombie_km"] == pytest.approx(after["zombie_km"] - NORMAL.speed_km_per_hour / 4, abs=0.01)
    assert _distance_to(later, farther["lat"], farther["lng"]) == pytest.approx(later["zombie_km"], abs=0.01)


def test_closed_app_chases_last_position_until_capture(client, user_id, clock):
    client.post(f"/api/users/{user_id}/position", json=NEAR)
    clock.advance(hours=100)
    assert client.get(f"/api/users/{user_id}").json()["capture_count"] == 1


def test_running_away_is_capped_at_start_distance(client, user_id, mission):
    far = client.post(f"/api/users/{user_id}/position", json={"lat": 34.7, "lng": 135.5}).json()
    assert far["zombie_km"] == pytest.approx(game.ZOMBIE_START_KM, abs=0.01)
    assert _distance_to(far, 34.7, 135.5) == pytest.approx(game.ZOMBIE_START_KM, abs=0.01)


def test_walking_into_zombie_leads_to_capture(client, deps, user_id, mission, clock):
    stored = deps.repo.get_user(user_id)
    lat, lng = destination(stored.zombie_lat, stored.zombie_lng, 0, 30)  # ゾンビの30m手前
    after = client.post(f"/api/users/{user_id}/position", json={"lat": lat, "lng": lng}).json()
    assert after["zombie_km"] < 0.1
    clock.advance(hours=2)
    assert client.get(f"/api/users/{user_id}").json()["capture_count"] == 1


def test_position_is_stored_rounded(client, deps, user_id):
    client.post(f"/api/users/{user_id}/position", json={"lat": 35.69123, "lng": 139.70123})
    user = deps.repo.get_user(user_id)
    assert (user.target_lat, user.target_lng) == (35.691, 139.701)
    assert (user.home_lat, user.home_lng) == (35.681, 139.767)


def test_chase_target_is_stored_finely(client, deps, user_id, mission, clock):
    # 追跡中は、捕獲の判定が画面とずれないよう約7m単位で保存する
    _escape(client, clock, mission)
    client.post(f"/api/users/{user_id}/position", json={"lat": 35.69123, "lng": 139.70123})
    user = deps.repo.get_user(user_id)
    assert user.chasing
    assert (user.target_lat, user.target_lng) == (35.6912, 139.7012)
    assert (user.home_lat, user.home_lng) == (35.681, 139.767)


def test_free_run_target_is_stored_finely(client, deps, user_id):
    near = client.post(f"/api/users/{user_id}/shelters/near", json={"lat": 35.69123, "lng": 139.77123}).json()
    client.post(f"/api/users/{user_id}/runs", json={"place_id": near[0]["place_id"], "lat": 35.69123, "lng": 139.77123})
    user = deps.repo.get_user(user_id)
    assert (user.target_lat, user.target_lng) == (35.6912, 139.7712)


def test_low_accuracy_position_is_ignored(client, deps, user_id):
    client.post(f"/api/users/{user_id}/position", json=NEAR | {"accuracy_m": 500})
    assert deps.repo.get_user(user_id).target_lat == 35.681


def test_unchanged_position_writes_nothing(client, deps, user_id, clock):
    anchor = deps.repo.get_user(user_id).zombie_anchor_at
    clock.advance(minutes=30)
    client.post(f"/api/users/{user_id}/position", json={"lat": 35.68101, "lng": 139.76701})
    assert deps.repo.get_user(user_id).zombie_anchor_at == anchor


def test_clear_respawns_zombie_far_away(client, deps, user_id, mission, clock):
    clock.advance(minutes=1)
    deps.repo.transact(user_id, None, lambda u, _m: (_near_zombie(u), None))
    _escape(client, clock, mission)
    assert client.get(f"/api/users/{user_id}").json()["zombie_km"] < 2
    _checkin(client, mission)
    user = client.get(f"/api/users/{user_id}").json()
    stored = deps.repo.get_user(user_id)
    assert stored.chasing is False and stored.extra_zombies == [] and stored.chase_mode is None
    # クリア後は遠くへ出直す。接近通知がすぐ鳴らないようにする
    assert user["zombie_km"] == pytest.approx(game.ZOMBIE_START_KM, abs=0.01)
    assert distance_m(stored.zombie_lat, stored.zombie_lng, stored.home_lat, stored.home_lng) / 1000 == pytest.approx(game.ZOMBIE_START_KM, abs=0.01)


def test_home_page_shows_base_without_map(client, user_id):
    text = client.get(f"/home/{user_id}").text
    assert 'id="base-scene"' in text and "テント" in text and f"/results/{user_id}" in text
    assert "/static/base3d.js" in text
    assert 'id="map"' not in text and "target" not in text


def test_escape_page_loads_outside_3d(client, mission):
    assert "/static/map3d.js" in client.get(f"/escape?mission={mission['id']}").text


def test_three_js_is_served_compressed(client):
    response = client.get("/static/vendor/three/three.core.js", headers={"Accept-Encoding": "gzip"})
    assert response.status_code == 200 and response.headers["content-encoding"] == "gzip"
    assert client.get("/static/vendor/three/LICENSE").status_code == 200


# --- 拠点は捕まったら振り出しに戻る ---

def _set_base(deps, user_id, meters):
    deps.repo.transact(user_id, None, lambda u, _m: (u.model_copy(update={"base_escaped_m": meters, "best_base_rank": game.base_rank(meters)}), None))


def test_staying_home_collapses_base(client, deps, user_id, clock):
    _set_base(deps, user_id, 7500)
    clock.advance(hours=6)
    user = client.get(f"/api/users/{user_id}").json()
    assert user["base_rank"] == 1 and user["base_escaped_m"] == 0
    assert user["lost_base_rank"] == 4 and user["lost_base_rank_name"] == "見張り台つきの砦"
    assert "見張り台つきの砦が崩壊" in client.get(f"/home/{user_id}").text


def test_capture_during_chase_collapses_base_and_fails_mission(client, deps, user_id, mission, clock):
    _set_base(deps, user_id, 3200)
    _escape(client, clock, mission)
    clock.advance(hours=3)  # シェルターに着かないまま追いつかれる
    user = client.get(f"/api/users/{user_id}").json()
    assert user["capture_count"] == 1 and user["base_escaped_m"] == 0 and user["lost_base_rank"] == 3
    assert client.get(f"/api/missions/{mission['id']}").json()["status"] == "failed"
    # 捕まった後にシェルターへ着いても、距離は戻らない
    assert client.post(f"/api/missions/{mission['id']}/checkin", json=_at_shelter(mission)).status_code == 409
    assert deps.repo.get_user(user_id).total_walked_m == 0


def test_missed_escape_after_leaving_home_keeps_base(client, deps, user_id, mission, clock):
    _set_base(deps, user_id, 3200)
    clock.now = _depart(mission) - timedelta(minutes=30)
    # 家は出たが脱出を押していない。ゾンビは5km先から追うので、期限切れまでは追いつかれない
    client.post(f"/api/users/{user_id}/position", json={"lat": 34.7, "lng": 135.5})
    clock.now = _depart(mission) + timedelta(minutes=15)  # 脱出の期限切れ
    assert client.get(f"/api/missions/{mission['id']}").json()["status"] == "failed"
    user = client.get(f"/api/users/{user_id}").json()
    assert user["base_escaped_m"] == 3200 and user["streak"] == 0 and user["lost_base_rank"] is None


def test_clear_after_collapse_clears_lost_rank_and_keeps_best(client, deps, user_id, clock):
    _set_base(deps, user_id, 7500)
    clock.advance(hours=6)  # 崩壊
    mission = _reserve(client, user_id).json()
    _escape(client, clock, mission)
    clock.advance(minutes=3)
    _checkin(client, mission, walked_m=600)
    user = client.get(f"/api/users/{user_id}").json()
    assert user["lost_base_rank"] is None and user["base_escaped_m"] == 600 and user["total_walked_m"] == 600
    assert client.get(f"/api/users/{user_id}/stats").json()["best_base_rank"] == 4


# --- 成果 ---

def test_stats_sum_walks_by_week_in_japan_time(client, deps, user_id, clock):
    shelter = client.get(f"/api/users/{user_id}/shelters").json()[0]
    for walked in (700, 900):
        mission = _reserve(client, user_id, place_id=shelter["place_id"]).json()
        _escape(client, clock, mission)
        clock.advance(minutes=3)
        _checkin(client, mission, walked_m=walked)
        clock.advance(days=4)
    stats = client.get(f"/api/users/{user_id}/stats").json()
    assert stats["total_walked_m"] == 1600 and stats["clear_count"] == 2
    # 1回目は8日前なので直近7日に入らない
    assert stats["week_walked_m"] == 900
    assert [r["walked_m"] for r in stats["history"]] == [900, 700]
    assert stats["history"][0]["shelter_name"] == shelter["name"]


def test_stats_for_new_user_are_empty(client, user_id):
    stats = client.get(f"/api/users/{user_id}/stats").json()
    assert stats["total_walked_m"] == 0 and stats["history"] == [] and stats["best_base_rank"] == 1


def test_results_page_renders(client, user_id, mission, clock):
    _escape(client, clock, mission)
    clock.advance(minutes=3)
    _checkin(client, mission, walked_m=1500)
    text = client.get(f"/results/{user_id}").text
    assert "1.50" in text and mission["shelter"]["name"] in text and "1,500m" in text
    assert client.get("/results/" + "a" * 32).status_code == 404


def test_dev_base_route_sets_rank_locally(client, user_id):
    assert client.get(f"/dev/base/{user_id}?m=155000", follow_redirects=False).status_code == 303
    assert client.get(f"/api/users/{user_id}").json()["base_rank"] == 10
    assert client.get("/dev/base/" + "a" * 32 + "?m=1").status_code == 404


def test_home_hides_push_button_when_unconfigured(client, user_id):
    assert "push-enable" not in client.get(f"/home/{user_id}").text


def test_manifest_opens_own_home(client, user_id):
    response = client.get(f"/manifest/{user_id}.webmanifest")
    assert response.json()["start_url"] == f"/home/{user_id}" and response.json()["display"] == "standalone"
    assert client.get("/manifest/" + "a" * 32 + ".webmanifest").status_code == 404


# --- 画面を消して位置が途絶えたとき ---

def _far_mission(client, deps, user_id):
    """歩いて数分かかるシェルターで予約する。探索範囲が広がるよう、拠点を強くしておく。"""
    _set_base(deps, user_id, 13_000)
    shelters = client.get(f"/api/users/{user_id}/shelters").json()
    place = max(shelters, key=lambda s: s["distance_m"])["place_id"]
    return _reserve(client, user_id, place_id=place).json()


def _on_the_way(mission, meters):
    """家からシェルターへ向かう直線上の点。"""
    shelter = mission["shelter"]
    lat, lng = destination(*HOME, bearing_deg(*HOME, shelter["lat"], shelter["lng"]), meters)
    return {"lat": lat, "lng": lng}


def _capture_count(client, user_id):
    return client.get(f"/api/users/{user_id}").json()["capture_count"]


def test_walking_with_screen_off_still_reaches_shelter(client, deps, user_id, clock):
    mission = _far_mission(client, deps, user_id)
    _escape(client, clock, mission, at=_on_the_way(mission, 250))
    clock.advance(minutes=4, seconds=30)  # 画面を消したまま、時速6kmほどで残りを歩いた
    assert _capture_count(client, user_id) == 0  # 位置が届かない間は捕まえない
    assert _checkin(client, mission, walked_m=430)["status"] == "cleared"
    user = client.get(f"/api/users/{user_id}").json()
    assert user["capture_count"] == 0 and user["base_escaped_m"] > 13_000


def test_standing_still_with_screen_off_is_caught_when_position_returns(client, deps, user_id, clock):
    mission = _far_mission(client, deps, user_id)
    spot = _on_the_way(mission, 250)
    _escape(client, clock, mission, at=spot)
    clock.advance(minutes=10)
    assert _capture_count(client, user_id) == 0
    after = client.post(f"/api/users/{user_id}/position", json=spot).json()
    assert after["capture_count"] == 1 and after["chasing"] is False and after["base_escaped_m"] == 0
    assert client.get(f"/api/missions/{mission['id']}").json()["status"] == "failed"


def test_too_slow_with_screen_off_is_caught_at_checkin(client, deps, user_id, mission, clock):
    _escape(client, clock, mission, at=_on_the_way(mission, 250))
    clock.advance(minutes=6)  # 残り約100mに6分かかった
    assert _checkin(client, mission) == {"status": "captured", "remaining_m": None, "result": None}
    assert client.get(f"/api/missions/{mission['id']}").json()["status"] == "failed"


def test_long_silence_counts_as_capture(client, deps, user_id, mission, clock):
    _set_base(deps, user_id, 3200)
    _escape(client, clock, mission)
    clock.advance(minutes=30)
    assert _capture_count(client, user_id) == 0
    clock.advance(minutes=31)
    user = client.get(f"/api/users/{user_id}").json()
    assert user["capture_count"] == 1 and user["base_escaped_m"] == 0


def test_unchanged_position_while_chasing_counts_as_tracked(client, deps, user_id, mission, clock):
    _escape(client, clock, mission)
    clock.advance(seconds=30)
    client.post(f"/api/users/{user_id}/position", json=AWAY)
    assert deps.repo.get_user(user_id).zombie_anchor_at == clock.now


# --- フリーランで毎日の時刻を逃れられない ---

AT_HOME = {"lat": HOME[0], "lng": HOME[1]}


def test_free_run_just_before_daily_time_does_not_dodge_it(client, deps, user_id, mission, clock):
    _set_base(deps, user_id, 13_000)
    clock.now = _depart(mission) - timedelta(minutes=1)
    assert _run(client, user_id, at=AT_HOME).status_code == 201
    clock.advance(minutes=6)  # 家で放置した。ゾンビは4分で着く
    user = client.post(f"/api/users/{user_id}/position", json=AT_HOME).json()
    assert user["capture_count"] == 1 and user["base_escaped_m"] == 0 and user["streak"] == 0
    assert user["last_result"] == "captured"
    daily = client.get(f"/api/missions/{mission['id']}").json()
    assert daily["status"] == "failed" and daily["result"] is None


def test_free_run_left_alone_across_daily_time_collapses_base(client, deps, user_id, mission, clock):
    _set_base(deps, user_id, 13_000)
    clock.now = _depart(mission) - timedelta(minutes=1)
    _run(client, user_id, at=AT_HOME)
    clock.advance(minutes=30)
    assert _capture_count(client, user_id) == 0  # 位置が途絶えている間は判定を待つ
    clock.advance(minutes=31)
    user = client.get(f"/api/users/{user_id}").json()
    assert user["capture_count"] == 1 and user["base_escaped_m"] == 0


# --- 逃走中のミッションが2つにならない ---

def test_cannot_escape_during_free_run(client, user_id, mission, clock):
    clock.now = _depart(mission) - timedelta(minutes=8)
    _run(client, user_id)
    response = client.post(f"/api/missions/{mission['id']}/escape", json=AWAY)
    assert response.status_code == 409 and response.json()["error"]["code"] == "already_running"
    assert client.get(f"/api/missions/{mission['id']}").json()["status"] == "reserved"


def test_stuck_run_is_closed_without_penalty(client, deps, user_id, mission, clock):
    def stuck(user, current):
        current.status, current.escaped_at = "escaped", clock.now
        return user, current

    deps.repo.transact(user_id, mission["id"], stuck)
    clock.advance(minutes=2)
    assert client.get(f"/api/missions/{mission['id']}").json()["status"] == "failed"
    assert _capture_count(client, user_id) == 0
    assert _reserve(client, user_id).status_code == 201


def test_home_does_not_open_escape_during_free_run(client, user_id, mission, clock):
    clock.now = _depart(mission) - timedelta(minutes=8)
    _run(client, user_id)
    text = client.get(f"/home/{user_id}").text
    assert '"depart_at": null' in text and "ゾンビから逃げる" not in text


# --- 脱出は時刻まで。予約していても、過ぎたらゲームオーバー ---

def test_can_escape_just_before_depart_time(client, deps, user_id, mission, clock):
    _set_base(deps, user_id, 3200)
    clock.now = _depart(mission) - timedelta(seconds=1)
    assert client.post(f"/api/missions/{mission['id']}/escape", json=AWAY).json()["status"] == "escaped"
    user = client.get(f"/api/users/{user_id}").json()
    assert user["base_escaped_m"] == 3200 and user["capture_count"] == 0 and user["chasing"] is True


def test_cannot_escape_after_depart_time(client, deps, user_id, mission, clock):
    _set_base(deps, user_id, 3200)
    clock.now = _depart(mission) + timedelta(seconds=1)
    assert client.post(f"/api/missions/{mission['id']}/escape", json=AWAY).json()["status"] == "timeout"
    user = client.get(f"/api/users/{user_id}").json()
    assert user["capture_count"] == 1 and user["base_escaped_m"] == 0 and user["chasing"] is False
    body = client.get(f"/api/missions/{mission['id']}").json()
    assert body["status"] == "failed" and "deadline_at" not in body


def test_escape_page_has_foldable_hud(client, mission):
    text = client.get(f"/escape?mission={mission['id']}").text
    assert 'id="hud-fold"' in text
    assert 'id="pocket"' not in text and 'id="pocket-mode"' not in text


# --- 外部APIの回数制限 ---

NEW_USER = {"nickname": "隊員", "lat": 35.0, "lng": 139.0, "daily_notify_time": "18:00"}


def test_new_users_are_limited_per_ip(client, monkeypatch):
    monkeypatch.setattr(game, "MAX_USERS_PER_IP_PER_HOUR", 2)
    headers = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8"}
    for _ in range(2):
        assert client.post("/api/users", json=NEW_USER, headers=headers).status_code == 201
    blocked = client.post("/api/users", json=NEW_USER, headers=headers)
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "too_many_requests"
    # 先頭は送り手が自由に書ける。書き換えても逃れられない
    assert client.post("/api/users", json=NEW_USER, headers={"X-Forwarded-For": "9.9.9.9, 5.6.7.8"}).status_code == 429
    assert client.post("/api/users", json=NEW_USER, headers={"X-Forwarded-For": "5.6.7.9"}).status_code == 201


def test_shelter_search_is_limited_per_user(client, user_id, monkeypatch):
    monkeypatch.setattr(game, "MAX_SHELTER_SEARCHES_PER_DAY", 2)
    url = f"/api/users/{user_id}/shelters"
    assert client.get(url).status_code == 200
    assert client.post(f"{url}/near", json=START).status_code == 200
    assert client.get(url).status_code == 429
    assert client.post(f"{url}/near", json=START).status_code == 429


def test_route_is_limited_per_user(client, user_id, monkeypatch):
    monkeypatch.setattr(game, "MAX_ROUTES_PER_DAY", 1)
    run = _run(client, user_id).json()
    url = f"/api/missions/{run['id']}/route"
    assert client.post(url, json=START).status_code == 200
    assert client.post(url, json=START).status_code == 429


# --- 近くに公園がなくても遊べる ---

class _NoParks(FakePlacesBackend):
    def search_parks(self, *_args):
        return []


class _NothingNearby(_NoParks):
    def search_landmarks(self, *_args):
        return []


class _LandmarkSearchBroken(_NoParks):
    def search_landmarks(self, *_args):
        raise PlacesError("公園の検索に失敗しました")


def test_no_parks_falls_back_to_landmarks(client, deps, user_id):
    deps.shelters = ShelterFinder(_NoParks())
    shelters = client.get(f"/api/users/{user_id}/shelters").json()
    assert shelters and all(s["kind"] == "landmark" for s in shelters)
    assert all(game.MIN_SHELTER_DISTANCE_M <= s["distance_m"] <= game.FALLBACK_SEARCH_RADIUS_M for s in shelters)
    assert _reserve(client, user_id, place_id=shelters[0]["place_id"]).status_code == 201


def test_nothing_nearby_offers_waypoints_that_can_be_cleared(client, deps, user_id, clock):
    deps.shelters = ShelterFinder(_NothingNearby())
    shelters = client.get(f"/api/users/{user_id}/shelters").json()
    assert len(shelters) == game.WAYPOINT_COUNT and all(s["kind"] == "waypoint" for s in shelters)
    assert all(game.MIN_SHELTER_DISTANCE_M <= s["distance_m"] <= game.BASE_SEARCH_RADIUS_M for s in shelters)
    assert client.get(f"/api/users/{user_id}/shelters").json() == shelters  # 同じ候補を作り直せる
    assert not any(f"{HOME[0]:.3f}" in s["place_id"] or f"{HOME[1]:.3f}" in s["place_id"] for s in shelters)  # 家の座標をIDに入れない
    mission = _reserve(client, user_id, place_id=shelters[0]["place_id"]).json()
    assert mission["shelter"]["kind"] == "waypoint"
    _escape(client, clock, mission)
    clock.advance(minutes=3)
    assert _checkin(client, mission)["status"] == "cleared"


def test_landmark_search_failure_still_offers_waypoints(client, deps, user_id):
    deps.shelters = ShelterFinder(_LandmarkSearchBroken())
    shelters = client.get(f"/api/users/{user_id}/shelters").json()
    assert shelters and all(s["kind"] == "waypoint" for s in shelters)


def test_waypoints_do_not_replace_reserved_shelter_when_outside(client, deps, mission, clock):
    deps.shelters = ShelterFinder(_NothingNearby())
    outside = {"lat": HOME[0] + 0.05, "lng": HOME[1]}
    clock.now = _depart(mission) - timedelta(minutes=5)
    body = client.post(f"/api/missions/{mission['id']}/escape", json=outside).json()
    assert body == {"status": "escaped", "shelter_changed": False}


def test_old_missions_without_shelter_kind_are_parks(client, mission):
    assert mission["shelter"]["kind"] == "park"


# --- 登録の直後にゲームオーバーにしない ---

def test_registering_at_the_last_moment_starts_close_and_still_comes_today(client, clock):
    clock.now = datetime(2026, 11, 6, 17, 59, 30, tzinfo=game.JST)
    user_id = _new_user(client, "18:00")
    user = client.get(f"/api/users/{user_id}").json()
    assert _jst(user["arrival_at"]) == datetime(2026, 11, 6, 18, 3, 30, tzinfo=game.JST)  # 400mを時速6kmで4分
    assert user["zombie_km"] == pytest.approx(game.FREE_RUN_ZOMBIE_KM, abs=0.001)
    assert user["zombie_speed_km_per_hour"] == pytest.approx(NORMAL.speed_km_per_hour, abs=0.01)
    clock.advance(minutes=3)
    assert _capture_count(client, user_id) == 0
    clock.advance(minutes=1, seconds=1)  # シェルターを選ばなければ、いつもどおりゲームオーバー
    user = client.get(f"/api/users/{user_id}").json()
    assert user["capture_count"] == 1
    # 2回目からは毎日の時刻に来る
    assert _jst(user["arrival_at"]) == datetime(2026, 11, 7, 18, 0, tzinfo=game.JST)


def test_last_moment_signup_can_reserve_and_escape(client, clock):
    clock.now = datetime(2026, 11, 6, 17, 59, 30, tzinfo=game.JST)
    user_id = _new_user(client, "18:00")
    mission = _reserve(client, user_id).json()
    assert _jst(mission["depart_at"]) == datetime(2026, 11, 6, 18, 3, 30, tzinfo=game.JST)
    clock.advance(minutes=2)
    assert client.post(f"/api/missions/{mission['id']}/escape", json=AWAY).json()["status"] == "escaped"


@pytest.mark.parametrize(
    "registered_at,zombie_km",
    [((17, 30), 3.0), ((17, 50), 1.0), ((12, 0), game.ZOMBIE_START_KM)],
)
def test_first_zombie_starts_where_it_arrives_on_time_at_normal_speed(client, clock, registered_at, zombie_km):
    clock.now = datetime(2026, 11, 6, *registered_at, tzinfo=game.JST)
    user = client.get(f"/api/users/{_new_user(client, '18:00')}").json()
    assert _jst(user["arrival_at"]) == datetime(2026, 11, 6, 18, 0, tzinfo=game.JST)
    assert user["zombie_km"] == pytest.approx(zombie_km, abs=0.001)


# --- モードの説明 ---

def test_mode_text_does_not_tell_how_fast_to_go(client, user_id):
    for url in (f"/reserve/{user_id}", f"/run/{user_id}"):
        text = client.get(url).text
        assert "歩けば逃げ切れる" not in text and "小走り" not in text
        assert "拠点ポイント1.5倍" in text


# --- 拠点の画面 ---

def test_home_shows_base_first_and_avatar_behind_a_button(client, user_id):
    text = client.get(f"/home/{user_id}").text
    assert text.index('class="card base"') < text.index('class="card free-run"') < text.index('class="card daily-mission"')
    assert 'id="avatar-open"' in text and 'aria-controls="avatar-card"' in text
    assert f'id="avatar-card" data-user="{user_id}" hidden' in text


# --- ゾンビの座標から家の位置が分からない ---

def test_approaching_zombie_coordinates_are_not_exposed(client, user_id, mission):
    bodies = [client.get(f"/api/users/{user_id}").json(), client.post(f"/api/users/{user_id}/position", json={"lat": HOME[0], "lng": HOME[1]}).json()]
    for body in bodies:
        assert body["chasing"] is False and body["zombie_km"] > 0
        assert body["zombie_lat"] is None and body["zombie_lng"] is None
        assert all(z["lat"] is None and z["lng"] is None for z in body["zombies"])


def test_chase_shows_coordinates_but_not_on_top_of_home(client, deps, user_id, clock, monkeypatch):
    # シェルターへまっすぐ歩いた人でも、ゾンビが家の上に戻らない（ずらす角度をいちばん大きくして確かめる）
    monkeypatch.setattr("services.missions.random.uniform", lambda _low, high: high)
    mission = _far_mission(client, deps, user_id)
    spot = _on_the_way(mission, 250)
    _escape(client, clock, mission, at=spot)
    body = client.get(f"/api/users/{user_id}").json()
    assert body["chasing"] is True and body["zombie_lat"] is not None
    assert distance_m(body["zombie_lat"], body["zombie_lng"], *HOME) > 150
    # 背後にいる（シェルターまでの距離が、自分より遠い）
    shelter = mission["shelter"]
    assert distance_m(body["zombie_lat"], body["zombie_lng"], shelter["lat"], shelter["lng"]) > distance_m(spot["lat"], spot["lng"], shelter["lat"], shelter["lng"])


# --- 端末に結びつける。URLだけでは開けない ---

@pytest.fixture
def other_device(deps):
    """同じサーバーを、Cookie を持たない別の端末から開く。"""
    return TestClient(create_app(deps))


def test_url_alone_cannot_open_the_api(other_device, user_id, mission):
    responses = [
        other_device.get(f"/api/users/{user_id}"),
        other_device.get(f"/api/users/{user_id}/shelters"),
        other_device.post(f"/api/users/{user_id}/freeze"),
        other_device.post(f"/api/users/{user_id}/transfer-code"),
        other_device.get(f"/api/missions/{mission['id']}"),
        other_device.post(f"/api/missions/{mission['id']}/escape", json=AWAY),
    ]
    for response in responses:
        assert response.status_code == 403 and response.json()["error"]["code"] == "other_device"


def test_url_alone_goes_to_the_welcome_page(other_device, user_id, mission):
    for url in (f"/home/{user_id}", f"/reserve/{user_id}", f"/run/{user_id}", f"/results/{user_id}", f"/escape?mission={mission['id']}"):
        response = other_device.get(url, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/?locked=1"
    welcome = other_device.get(f"/home/{user_id}")
    assert 'id="start"' in welcome.text and 'href="/link"' in welcome.text
    assert "隊員の拠点" not in welcome.text and mission["shelter"]["name"] not in welcome.text


def test_owner_device_opens_everything(client, user_id, mission):
    assert client.get(f"/api/users/{user_id}").status_code == 200
    assert client.get(f"/api/missions/{mission['id']}").status_code == 200
    assert 'id="link-form"' not in client.get(f"/home/{user_id}").text


def test_transfer_code_moves_to_another_device(client, other_device, user_id):
    code = client.post(f"/api/users/{user_id}/transfer-code").json()["code"]
    assert len(code) == 14 and code.count("-") == 2
    linked = other_device.post("/api/devices/link", json={"code": code.lower().replace("-", " ")})
    assert linked.status_code == 200 and linked.json()["home_url"].endswith(f"/home/{user_id}")
    assert other_device.get(f"/api/users/{user_id}").status_code == 200
    assert client.get(f"/api/users/{user_id}").status_code == 200  # 元の端末もそのまま使える


def test_transfer_code_works_once(client, deps, other_device, user_id):
    code = client.post(f"/api/users/{user_id}/transfer-code").json()["code"]
    assert other_device.post("/api/devices/link", json={"code": code}).status_code == 200
    third = TestClient(create_app(deps))
    response = third.post("/api/devices/link", json={"code": code})
    assert response.status_code == 404 and response.json()["error"]["code"] == "bad_code"


def test_reissuing_invalidates_the_old_code(client, other_device, user_id):
    old = client.post(f"/api/users/{user_id}/transfer-code").json()["code"]
    new = client.post(f"/api/users/{user_id}/transfer-code").json()["code"]
    assert other_device.post("/api/devices/link", json={"code": old}).status_code == 404
    assert other_device.post("/api/devices/link", json={"code": new}).status_code == 200


def test_transfer_code_is_stored_only_as_a_hash(client, deps, user_id):
    code = client.post(f"/api/users/{user_id}/transfer-code").json()["code"]
    stored = deps.repo.get_user(user_id)
    assert stored.transfer_code_hash and code not in stored.model_dump_json()
    assert client.cookies.get(devices.COOKIE_NAME) not in stored.device_hashes


def test_wrong_codes_are_limited_per_ip(other_device, monkeypatch):
    monkeypatch.setattr(game, "MAX_LINK_TRIES_PER_IP_PER_HOUR", 2)
    for _ in range(2):
        assert other_device.post("/api/devices/link", json={"code": "WRONG"}).status_code == 404
    assert other_device.post("/api/devices/link", json={"code": "WRONG"}).status_code == 429


def test_devices_are_capped_and_oldest_is_dropped(client, deps, user_id):
    first = deps.repo.get_user(user_id).device_hashes[0]
    for _ in range(devices.MAX_DEVICES):
        code = client.post(f"/api/users/{user_id}/transfer-code").json()["code"]
        assert TestClient(create_app(deps)).post("/api/devices/link", json={"code": code}).status_code == 200
    hashes = deps.repo.get_user(user_id).device_hashes
    assert len(hashes) == devices.MAX_DEVICES and first not in hashes


def test_existing_user_is_bound_to_the_first_device(client, deps, other_device):
    user = missions.create_user(deps, "前からの隊員", HOME[0], HOME[1], "18:00")  # 端末の記録がない
    assert client.get(f"/home/{user.id}").status_code == 200
    assert len(deps.repo.get_user(user.id).device_hashes) == 1
    assert other_device.get(f"/home/{user.id}", follow_redirects=False).status_code == 303


def test_device_cookie_flags(deps):
    local = TestClient(create_app(deps)).post("/api/users", json=NEW_USER).headers["set-cookie"].lower()
    assert "httponly" in local and "samesite=lax" in local and "secure" not in local
    production = TestClient(create_app(replace(deps, settings=Settings(app_env="production"))))
    assert "secure" in production.post("/api/users", json=NEW_USER).headers["set-cookie"].lower()


def test_transfer_is_reachable_but_out_of_the_way(client, user_id):
    # 新しい端末はトップから入れる。デモの邪魔にならないよう、トップは小さな文字のリンク、拠点では一番下に畳んでおく
    assert 'id="link-form"' in client.get("/link").text
    welcome = client.get("/").text
    assert 'class="transfer-link muted small" href="/link"' in welcome and "引き継ぎコードをお持ちの方" in welcome
    text = client.get(f"/home/{user_id}").text
    assert '<details class="transfer">' in text and 'id="transfer-issue"' in text
    assert text.index('class="card settings"') < text.index('class="transfer"')
