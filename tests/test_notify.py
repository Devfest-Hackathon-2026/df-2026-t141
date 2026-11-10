from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import create_app
from services import game
from services.config import Settings
from services.notify import _LEVEL_MESSAGES, FcmSender, run_notifications

BASE = "https://x.example"
CONFIG = dict(
    project="demo", notify_secret="s3cret", firebase_api_key="a", firebase_app_id="b", firebase_sender_id="c", firebase_vapid_key="d"
)


class FakeSender:
    def __init__(self, registered=True):
        self.sent, self.registered = [], registered

    def send(self, fid, title, body, link):
        self.sent.append((fid, title, body, link))
        return self.registered


@pytest.fixture
def push_deps(deps):
    return replace(deps, push=FakeSender(), settings=Settings(**CONFIG))


@pytest.fixture
def push_client(push_deps):
    return TestClient(create_app(push_deps))


@pytest.fixture
def push_user(push_client):
    body = {"nickname": "隊員", "lat": 35.681, "lng": 139.767, "daily_notify_time": "18:00"}
    user_id = push_client.post("/api/users", json=body).json()["user_id"]
    assert push_client.post(f"/api/users/{user_id}/push", json={"fid": "fid-1"}).status_code == 204
    return user_id


def _go_to(clock, hour):
    clock.now = clock.now.replace(hour=hour)


def test_no_message_when_zombie_is_far(push_deps, push_user):
    assert run_notifications(push_deps, BASE) == {"sent": 0, "failed": 0}


def test_each_threshold_is_sent_once(push_deps, push_user, clock):
    clock.advance(hours=3)  # 2.5km。15時
    assert run_notifications(push_deps, BASE)["sent"] == 1
    assert run_notifications(push_deps, BASE)["sent"] == 0
    fid, title, body, link = push_deps.push.sent[0]
    assert fid == "fid-1" and link == f"{BASE}/home/{push_user}" and "3km" in body
    clock.advance(hours=2)  # 約0.83km。17時
    assert run_notifications(push_deps, BASE)["sent"] == 1
    assert "1km" in push_deps.push.sent[1][2]


def test_message_has_no_location(push_deps, push_user, clock):
    clock.advance(days=2)
    run_notifications(push_deps, BASE)
    _, title, body, _ = push_deps.push.sent[0]
    assert push_deps.push.sent
    assert "35.6" not in title + body and "139.7" not in title + body


def test_quiet_hours_delay_message(push_deps, push_user, clock):
    clock.advance(days=2)
    _go_to(clock, 23)
    assert run_notifications(push_deps, BASE)["sent"] == 0
    assert push_deps.push.sent == []
    clock.advance(hours=9)  # 翌8時
    assert run_notifications(push_deps, BASE)["sent"] == 1


def test_capture_notice_is_sent_once(push_deps, push_user, clock):
    clock.advance(hours=75)
    _go_to(clock, 15)
    assert run_notifications(push_deps, BASE)["sent"] == 1
    assert "追いつかれた" in push_deps.push.sent[0][1]
    assert run_notifications(push_deps, BASE)["sent"] == 0


def test_moving_away_rearms_notification(push_client, push_deps, push_user, clock):
    place = push_client.get(f"/api/users/{push_user}/shelters").json()[0]["place_id"]
    depart = (clock.now + timedelta(hours=5)).isoformat()
    assert push_client.post(f"/api/users/{push_user}/missions", json={"depart_at": depart, "place_id": place}).status_code == 201
    clock.advance(hours=3)
    assert run_notifications(push_deps, BASE)["sent"] == 1
    level = push_deps.repo.get_user(push_user).notified_level
    assert level >= 1
    far = {"lat": 34.7, "lng": 135.5}
    assert push_client.post(f"/api/users/{push_user}/position", json=far).status_code == 200
    run_notifications(push_deps, BASE)
    assert push_deps.repo.get_user(push_user).notified_level == 0


def test_free_run_does_not_repeat_approach_notice(push_client, push_deps, push_user, clock):
    _at(clock, 17, 45)  # 毎日のゾンビは約200m先
    assert run_notifications(push_deps, BASE)["sent"] == 1
    start = {"lat": 35.687, "lng": 139.771}
    url = f"/api/users/{push_user}"
    place = push_client.post(f"{url}/shelters/near", json=start).json()[0]
    run = push_client.post(f"{url}/runs", json={"place_id": place["place_id"]} | start).json()
    assert run_notifications(push_deps, BASE)["sent"] == 0  # 背後400mのゾンビの距離で数え直さない
    clock.advance(minutes=2)
    at_shelter = {"lat": place["lat"], "lng": place["lng"], "accuracy_m": 10}
    assert push_client.post(f"/api/missions/{run['id']}/checkin", json=at_shelter).json()["status"] == "cleared"
    assert run_notifications(push_deps, BASE)["sent"] == 0  # 毎日のゾンビに戻っても、同じ通知を送らない


def _at(clock, hour, minute=0):
    clock.now = clock.now.replace(hour=hour, minute=minute)


def _reserve(push_client, user_id):
    place = push_client.get(f"/api/users/{user_id}/shelters").json()[0]["place_id"]
    return push_client.post(f"/api/users/{user_id}/missions", json={"place_id": place}).json()


def test_notices_when_escape_opens_and_when_time_runs_out(push_client, push_deps, push_user, clock):
    _reserve(push_client, push_user)
    _at(clock, 17, 45)
    run_notifications(push_deps, BASE)  # 距離の通知はここで済ませる
    before = len(push_deps.push.sent)
    _at(clock, 17, 50)
    assert run_notifications(push_deps, BASE)["sent"] == 1
    assert "脱出できるようになった" in push_deps.push.sent[before][1]
    assert run_notifications(push_deps, BASE)["sent"] == 0
    _at(clock, 18, 0)
    assert run_notifications(push_deps, BASE)["sent"] == 1
    assert "追いつかれた" in push_deps.push.sent[before + 1][1]
    assert run_notifications(push_deps, BASE)["sent"] == 0


def test_notice_without_shelter_asks_to_choose_one(push_deps, push_user, clock):
    _at(clock, 17, 50)
    assert run_notifications(push_deps, BASE)["sent"] == 1
    assert "シェルターを選んで" in push_deps.push.sent[-1][2]


def test_escape_opens_notice_ignores_quiet_hours(push_client, push_deps, clock):
    body = {"nickname": "早起き", "lat": 35.681, "lng": 139.767, "daily_notify_time": "06:30"}
    user_id = push_client.post("/api/users", json=body).json()["user_id"]
    push_client.post(f"/api/users/{user_id}/push", json={"fid": "fid-early"})
    _reserve(push_client, user_id)
    clock.advance(days=1)
    _at(clock, 6, 20)
    assert game.is_quiet_hours(clock.now)
    assert run_notifications(push_deps, BASE)["sent"] == 1
    assert "脱出できるようになった" in push_deps.push.sent[-1][1]
    _at(clock, 6, 30)
    assert run_notifications(push_deps, BASE)["sent"] == 0  # ゲームオーバーの通知は静かな時間には送らない


def test_no_arrival_or_approach_notice_while_chased(push_client, push_deps, push_user, clock):
    mission = _reserve(push_client, push_user)
    _at(clock, 17, 55)
    assert push_client.post(f"/api/missions/{mission['id']}/escape", json={"lat": 35.691, "lng": 139.767}).json()["status"] == "escaped"
    assert run_notifications(push_deps, BASE)["sent"] == 0


def test_approach_message_does_not_tell_to_run_before_escape_opens():
    assert "今すぐ" not in _LEVEL_MESSAGES[3][1] and "10分前" in _LEVEL_MESSAGES[3][1]


def test_users_with_nothing_due_skip_reading_missions(push_deps, push_user, monkeypatch):
    calls = []
    original = push_deps.repo.list_missions
    monkeypatch.setattr(push_deps.repo, "list_missions", lambda user_id: calls.append(user_id) or original(user_id))
    assert run_notifications(push_deps, BASE) == {"sent": 0, "failed": 0}
    assert calls == []


def test_unregistered_device_is_disabled(push_deps, push_user, clock):
    push_deps.push.registered = False
    clock.advance(days=2)
    _go_to(clock, 12)
    assert run_notifications(push_deps, BASE)["sent"] == 0
    user = push_deps.repo.get_user(push_user)
    assert user.push_enabled is False and user.push_fid is None
    assert push_deps.repo.list_push_users() == []


def test_one_failure_does_not_stop_others(push_client, push_deps, push_user, clock):
    body = {"nickname": "二人目", "lat": 35.0, "lng": 139.0, "daily_notify_time": "18:00"}
    second = push_client.post("/api/users", json=body).json()["user_id"]
    push_client.post(f"/api/users/{second}/push", json={"fid": "fid-2"})
    clock.advance(days=2)
    _go_to(clock, 12)
    original = push_deps.push.send

    def flaky(fid, title, text, link):
        if fid == "fid-1":
            raise RuntimeError("boom")
        return original(fid, title, text, link)

    push_deps.push.send = flaky
    assert run_notifications(push_deps, BASE) == {"sent": 1, "failed": 1}


def test_users_without_push_are_skipped(deps, push_deps, push_client, clock):
    body = {"nickname": "通知なし", "lat": 35.0, "lng": 139.0, "daily_notify_time": "18:00"}
    push_client.post("/api/users", json=body)
    clock.advance(days=2)
    assert run_notifications(push_deps, BASE)["sent"] == 0


# --- 定期実行の入口 ---

def test_endpoint_requires_secret(push_client, push_user):
    assert push_client.post("/internal/notify").status_code == 404
    assert push_client.post("/internal/notify", headers={"X-Notify-Secret": "wrong"}).status_code == 404
    assert push_client.post("/internal/notify", headers={"X-Notify-Secret": "s3cret"}).json() == {"sent": 0, "failed": 0}


def test_endpoint_is_hidden_without_config(client):
    assert client.post("/internal/notify", headers={"X-Notify-Secret": ""}).status_code == 404


def test_push_ui_and_service_worker_when_configured(push_client, push_user):
    text = push_client.get(f"/home/{push_user}").text
    assert 'id="push-enable"' in text and "/static/push.js" in text
    assert "s3cret" not in text
    worker = push_client.get("/firebase-messaging-sw.js")
    assert worker.headers["content-type"].startswith("text/javascript")
    assert '"projectId": "demo"' in worker.text and "s3cret" not in worker.text and "13.1.0" in worker.text


def test_service_worker_hidden_without_config(client):
    assert client.get("/firebase-messaging-sw.js").status_code == 404


# --- FCM（SDKの型は本物を使い、送信だけ偽物にする）---

def test_fcm_message_shape(monkeypatch):
    import firebase_admin
    from firebase_admin import messaging

    monkeypatch.setattr(firebase_admin, "get_app", lambda: object())
    captured = {}
    monkeypatch.setattr(messaging, "send", lambda message: captured.setdefault("message", message))
    assert FcmSender("demo").send("fid-1", "題", "本文", "https://x.example/home/u") is True
    message = captured["message"]
    assert message.fid == "fid-1" and message.token is None
    assert message.webpush.fcm_options.link == "https://x.example/home/u"
    assert message.webpush.notification.title == "題"


def test_fcm_unregistered_returns_false(monkeypatch):
    import firebase_admin
    from firebase_admin import messaging

    monkeypatch.setattr(firebase_admin, "get_app", lambda: object())

    def raise_unregistered(_message):
        raise messaging.UnregisteredError("gone")

    monkeypatch.setattr(messaging, "send", raise_unregistered)
    assert FcmSender("demo").send("f", "t", "b", "https://x.example") is False
