"""ゲームの流れ（予約、脱出、アイテム、チェックイン、捕獲、現在地の更新）。画面の仕組みには依存しない。"""
import math
import random
import secrets
from datetime import date, datetime, timedelta

from . import devices, game
from .deps import Deps
from .location import bearing_deg, destination, distance_m, round_chase_coord, round_coord
from .models import ClearResult, GeoPoint, Item, Mission, ModeName, SavedApproach, Shelter, User, UserStats, WalkRecord
from .places import PlacesError


class GameError(Exception):
    def __init__(self, code: str, message: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def new_id() -> str:
    """URLに入れる推測できないID。"""
    return secrets.token_urlsafe(24)


def not_found() -> GameError:
    return GameError("not_found", "見つかりませんでした", 404)


def check_rate(deps: Deps, key: str, limit: int, window: timedelta) -> None:
    """外部APIの料金が膨らまないよう、回数を制限する。"""
    if not deps.limiter.allow(key, limit, int(window.total_seconds())):
        raise GameError("too_many_requests", "短い時間に使いすぎています。時間をおいて試してね", 429)


def _spawn_zombie(lat: float, lng: float, km: float = game.ZOMBIE_START_KM) -> tuple[float, float]:
    """目標から指定の距離の位置に、ランダムな方角で出現させる。"""
    return destination(lat, lng, random.uniform(0, 360), km * 1000)


def create_user(
    deps: Deps, nickname: str, lat: float, lng: float, daily_notify_time: str, device_hash: str | None = None
) -> User:
    now = deps.clock()
    home_lat, home_lng = round_coord(lat), round_coord(lng)
    arrival_at = game.first_arrival_at(daily_notify_time, now)
    zombie_lat, zombie_lng = _spawn_zombie(home_lat, home_lng, game.first_zombie_km(arrival_at, now))
    user = User(
        id=new_id(),
        nickname=nickname,
        home_lat=home_lat,
        home_lng=home_lng,
        daily_notify_time=daily_notify_time,
        zombie_lat=zombie_lat,
        zombie_lng=zombie_lng,
        zombie_anchor_at=now,
        target_lat=home_lat,
        target_lng=home_lng,
        arrival_at=arrival_at,
        device_hashes=[device_hash] if device_hash else [],
        created_at=now,
    )
    deps.repo.create_user(user)
    return user


# --- 端末の結びつけ。URLだけでは開けないようにする ---

def _bind_device(user: User, device_hash: str) -> None:
    if device_hash not in user.device_hashes:
        user.device_hashes = (user.device_hashes + [device_hash])[-devices.MAX_DEVICES:]


def claim_device(deps: Deps, user_id: str, device_hash: str) -> None:
    """端末の記録がないユーザー（この仕組みを入れる前の人）を、最初に開いた端末に結びつける。"""

    def apply(user: User, _mission: Mission | None):
        if user.device_hashes:
            return None
        _bind_device(user, device_hash)
        return user, None

    deps.repo.transact(user_id, None, apply)


def issue_transfer_code(deps: Deps, user_id: str) -> str:
    """別の端末へ移るコードを発行する。前に発行したコードは使えなくなる。"""
    code = devices.new_transfer_code()

    def apply(user: User, _mission: Mission | None):
        user.transfer_code_hash = devices.hash_code(code)
        return user, None

    deps.repo.transact(user_id, None, apply)
    return code


def link_device(deps: Deps, code: str, device_hash: str) -> User:
    """引き継ぎコードで、この端末を結びつける。コードは1回で無効になる。"""
    code_hash = devices.hash_code(code)
    wrong_code = GameError("bad_code", "コードが違います。もう一度確かめてね", 404)
    found = deps.repo.find_user_by_transfer_code(code_hash)
    if found is None:
        raise wrong_code
    linked: list[bool] = []

    def apply(user: User, _mission: Mission | None):
        linked.clear()  # 再実行に備える
        # 同時に使われても1台にだけ効くよう、ここで確かめて消す
        if user.transfer_code_hash != code_hash:
            return None
        user.transfer_code_hash = None
        _bind_device(user, device_hash)
        linked.append(True)
        return user, None

    deps.repo.transact(found.id, None, apply)
    if not linked:
        raise wrong_code
    return deps.repo.get_user(found.id)


# --- ゾンビ。ハードでは3体になる。全員が同じ基準時刻と目標を共有する ---

def _mode(user: User) -> game.Mode:
    return game.MODES[user.chase_mode or "normal"]


def zombie_states(user: User, now: datetime) -> list[game.ZombieState]:
    """全員の今の状態。家へ近づく段階は本体だけ。"""
    if not user.chasing:
        return [game.approach_state(
            user.zombie_lat, user.zombie_lng, user.zombie_anchor_at, user.home_lat, user.home_lng, user.arrival_at, now
        )]
    speed = _mode(user).speed_km_per_hour
    points = [(user.zombie_lat, user.zombie_lng)] + [(z.lat, z.lng) for z in user.extra_zombies]
    return [
        game.zombie_state(lat, lng, user.zombie_anchor_at, user.target_lat, user.target_lng, now, speed)
        for lat, lng in points
    ]


def zombie_now(user: User, now: datetime) -> game.ZombieState:
    """最も近いゾンビ。距離の表示、通知、捕獲の判定に使う。"""
    return min(zombie_states(user, now), key=lambda state: state.km)


def zombie_speed_km_per_hour(user: User, now: datetime) -> float:
    """表示用の速さ。家へ向かう間は、時刻にちょうど着く速さになる。"""
    if user.chasing:
        return _mode(user).speed_km_per_hour
    hours = (user.arrival_at - now).total_seconds() / 3600
    return zombie_now(user, now).km / hours if hours > 0 else 0.0


def _reanchor(user: User, states: list[game.ZombieState], kms: list[float], now: datetime) -> None:
    """全員を、今の方角のまま目標から指定の距離に置き直し、基準時刻をそろえる。"""
    points = [game.place_zombie(user.target_lat, user.target_lng, s.lat, s.lng, km) for s, km in zip(states, kms)]
    user.zombie_lat, user.zombie_lng = points[0]
    user.extra_zombies = [GeoPoint(lat=lat, lng=lng) for lat, lng in points[1:]]
    user.zombie_anchor_at = now


def _extra_zombies(user: User, main: game.ZombieState, mode: game.Mode) -> list[game.ZombieState]:
    """ハードの追加のゾンビ。本体と同じ方面（フリーランならシェルターと反対側）から、群れで追ってくる。"""
    if mode.zombies <= 1:
        return []
    main_bearing = bearing_deg(user.target_lat, user.target_lng, main.lat, main.lng)
    # main.km は家までの距離のことがあるので、今の目標（自分）からの距離で測り直す
    main_km = distance_m(user.target_lat, user.target_lng, main.lat, main.lng) / 1000
    extras = []
    for angle, behind_km in game.EXTRA_ZOMBIE_OFFSETS[: mode.zombies - 1]:
        km = min(main_km + behind_km, game.ZOMBIE_START_KM)
        lat, lng = destination(user.target_lat, user.target_lng, (main_bearing + angle) % 360, km * 1000)
        extras.append(game.ZombieState(lat, lng, km))
    return extras


def _zombie_behind(
    target_lat: float, target_lng: float, shelter: Shelter, km: float, spread_deg: float = 0.0
) -> game.ZombieState:
    """自分から見てシェルターと反対側（背後）に置いたゾンビ。spread_deg の分だけ左右にずらす。"""
    behind = bearing_deg(target_lat, target_lng, shelter.lat, shelter.lng) + 180 + random.uniform(-spread_deg, spread_deg)
    lat, lng = destination(target_lat, target_lng, behind % 360, km * 1000)
    return game.ZombieState(lat, lng, km)


def _set_target(
    user: User, lat: float, lng: float, now: datetime, left_home: bool = False, can_chase: bool = False,
    mode: ModeName = "normal", shelter: Shelter | None = None,
) -> None:
    """プレイヤーの最後の位置を更新する。ミッション中に家を出ていれば追跡を始める。離れすぎた場合は初期距離まで詰める。
    追跡しない間は粗く（約70m）、追跡中は捕獲の判定が画面とずれないよう細かく（約7m）丸めて保存する。"""
    states = zombie_states(user, now)
    if not user.chasing:
        user.target_lat, user.target_lng = round_coord(lat), round_coord(lng)
        away_m = distance_m(user.home_lat, user.home_lng, user.target_lat, user.target_lng)
        if not left_home and not (can_chase and away_m >= game.ESCAPE_DISTANCE_M):
            return
        user.target_lat, user.target_lng = round_chase_coord(lat), round_chase_coord(lng)
        user.chasing = True
        user.chase_mode = mode
        main = states[0]
        if shelter is not None:
            # この瞬間のゾンビは家のほぼ真上にいる。そのまま見せると家の位置が分かるので、距離は保って置き直す。
            # 真後ろに固定すると、家からまっすぐ歩いた人ではまた家の上に戻るので、左右にずらす
            km = min(distance_m(main.lat, main.lng, user.target_lat, user.target_lng) / 1000, game.ZOMBIE_START_KM)
            main = _zombie_behind(user.target_lat, user.target_lng, shelter, km, game.CHASE_SPREAD_DEG)
        states = [main] + _extra_zombies(user, main, game.MODES[mode])
    else:
        via = (user.target_lat, user.target_lng)
        user.target_lat, user.target_lng = round_chase_coord(lat), round_chase_coord(lng)
        speed = _mode(user).speed_km_per_hour
        points = [(user.zombie_lat, user.zombie_lng)] + [(z.lat, z.lng) for z in user.extra_zombies]
        states = [
            game.zombie_on_trail(z_lat, z_lng, user.zombie_anchor_at, *via, user.target_lat, user.target_lng, now, speed)
            for z_lat, z_lng in points
        ]
    kms = [min(distance_m(s.lat, s.lng, user.target_lat, user.target_lng) / 1000, game.ZOMBIE_START_KM) for s in states]
    _reanchor(user, states, kms, now)


def _next_arrival(user: User, done_day: date, now: datetime) -> datetime:
    """done_day の到着を終えたあとの、次の到着。1日に2回は来させない。"""
    return game.next_daily_after(user.daily_notify_time, done_day, now)


def _start_next_approach(user: User, now: datetime, consume: bool = True) -> None:
    """追跡を終え、次の時刻に家へ着く動きを始める。
    consume が False（フリーラン）で毎日の時刻より前に終わったなら、今日の到着はそのまま残し、
    毎日のゾンビは走っている間も進んでいたものとして続きから近づく。それ以外は遠くに出直す。"""
    keeps_today = not consume and user.arrival_at > now
    saved = user.saved_approach
    if keeps_today and saved is not None:
        user.zombie_lat, user.zombie_lng = saved.lat, saved.lng
        user.zombie_anchor_at = saved.anchor_at
    else:
        user.zombie_lat, user.zombie_lng = _spawn_zombie(user.home_lat, user.home_lng)
        user.zombie_anchor_at = now
    user.saved_approach = None
    user.extra_zombies = []
    user.target_lat, user.target_lng = user.home_lat, user.home_lng
    user.chasing = False
    user.chase_mode = None
    if not keeps_today:
        user.arrival_at = _next_arrival(user, game.jst_date(user.arrival_at), now)


def _collapse_base(user: User) -> None:
    """捕まったら拠点は振り出しに戻る。崩れたランクは画面の演出のために残す。"""
    rank = game.base_rank(user.base_escaped_m)
    user.lost_base_rank = rank if rank > 1 else None
    user.base_escaped_m = 0


# --- 時間の経過で起きること ---

def _fail_mission(deps: Deps, mission: Mission, now: datetime) -> None:
    """予約したのに脱出を押さないまま時刻が過ぎた。
    毎日の時刻をまたいだフリーランで逃げ切っていたら達成扱い。そうでなければ失敗にする。"""
    runs = [
        m for m in deps.repo.list_missions(mission.user_id)
        if m.kind == "free" and m.escaped_at is not None and m.escaped_at <= mission.depart_at
    ]
    if any(m.status == "escaped" for m in runs):
        return  # その時刻をまたいで走っている最中。逃げ切るか捕まるかが決まってから判定する
    covered = any(m.status == "cleared" and m.ended_at is not None and m.ended_at >= mission.depart_at for m in runs)

    def apply(user: User, current: Mission | None):
        if current is None or current.status != "reserved" or now < current.depart_at:
            return None
        if covered:
            streak = game.next_streak(user.streak, user.last_clear_date, now)
            rank = game.base_rank(user.base_escaped_m)
            current.status = "cleared"
            current.cleared_at = current.ended_at = now
            current.result = ClearResult(
                walked_m=0, mode=current.mode, base_rank_before=rank, base_rank_after=rank,
                base_escaped_m_after=user.base_escaped_m, base_rank_name_after=game.base_rank_name(rank),
                streak=streak, covered_by_free_run=True,
            )
            user.streak = streak
            user.last_clear_date = game.jst_date(now)
            return user, current
        current.status = "failed"
        current.ended_at = now
        user.streak = 0
        user.last_result = "failed"
        return user, current

    deps.repo.transact(mission.user_id, mission.id, apply)


def _game_over_at_home(deps: Deps, user_id: str, now: datetime) -> None:
    """毎日の時刻に家から動いていなければゲームオーバー。"""
    due = [m for m in deps.repo.list_missions(user_id) if m.status == "reserved" and m.depart_at <= now]
    mission_id = due[0].id if due else None

    def apply(user: User, current: Mission | None):
        # 同時アクセスでも二重に処理しないよう、トランザクション内で再確認する
        if user.chasing or now < user.arrival_at:
            return None
        if current is not None:
            current.status = "failed"
        user.streak = 0
        user.capture_count += 1
        user.last_result = "failed" if current is not None else "captured"
        _collapse_base(user)
        _start_next_approach(user, now)
        return user, current

    deps.repo.transact(user_id, mission_id, apply)


def _caught(user: User, now: datetime) -> bool:
    """追いつかれたか。位置が届いている間は、ゾンビが最後の位置に着いたら捕獲する。
    画面を消すなどで途絶えたら、今の位置が届くまで待つ（_set_target が足取りをたどって判定する）。長く届かなければ捕獲する。"""
    if not user.chasing or zombie_now(user, now).km > game.CATCH_KM:
        return False
    silent = now - user.zombie_anchor_at
    return silent <= game.POSITION_FRESH or silent >= game.POSITION_GIVE_UP


def _capture_if_reached(deps: Deps, user_id: str, now: datetime) -> None:
    """どれか1体に追いつかれたら捕獲。向かっていたミッションも失敗にして、後からチェックインできないようにする。"""
    escaped = [m for m in deps.repo.list_missions(user_id) if m.status == "escaped"]
    mission_id = escaped[-1].id if escaped else None

    def apply(user: User, current: Mission | None):
        # 同時アクセスでも二重に捕獲しないよう、トランザクション内で再確認する
        if not _caught(user, now):
            return None
        free_run = current is not None and current.kind == "free"
        # 毎日の時刻より前のフリーランは罰しない。時刻をまたいで捕まったら、毎日のゾンビから逃げられなかったのと同じ
        spared = free_run and now < user.arrival_at
        if current is not None and current.status == "escaped":
            current.status = "failed"
            current.ended_at = now
        _start_next_approach(user, now, consume=not free_run)
        user.capture_count += 1
        if spared:
            # 自分から走りに出た人は罰しない。拠点と連続日数は残し、その回のポイントが入らないだけ
            user.last_result = "run_captured"
        else:
            user.streak = 0
            user.last_result = "captured"
            _collapse_base(user)
        return user, current

    deps.repo.transact(user_id, mission_id, apply)


def _close_orphan_runs(deps: Deps, user_id: str, missions: list[Mission], now: datetime) -> None:
    """追跡が終わっているのに逃走中のまま残ったミッションを閉じる。残っていると、予約もフリーランもできなくなる。
    捕まったわけではないので罰しない。"""
    for mission in missions:
        if mission.status != "escaped" or mission.escaped_at is None or now - mission.escaped_at < game.ORPHAN_RUN_GRACE:
            continue

        def apply(user: User, current: Mission | None):
            if current is None or current.status != "escaped" or user.chasing:
                return None
            current.status = "failed"
            current.ended_at = now
            return user, current

        deps.repo.transact(user_id, mission.id, apply)


def settle_user(deps: Deps, user_id: str) -> User:
    """閉じている間の出来事を反映する。"""
    now = deps.clock()
    user = deps.repo.get_user(user_id)
    if user is None:
        raise not_found()
    all_missions = deps.repo.list_missions(user_id)
    if not user.chasing and now >= user.arrival_at:
        _game_over_at_home(deps, user_id, now)
    for mission in all_missions:
        if mission.status == "reserved" and now >= mission.depart_at:
            _fail_mission(deps, mission, now)
    user = deps.repo.get_user(user_id)
    if _caught(user, now):
        _capture_if_reached(deps, user_id, now)
        user = deps.repo.get_user(user_id)
    if not user.chasing:
        _close_orphan_runs(deps, user_id, all_missions, now)
    return user


def _reserved_mission(deps: Deps, user_id: str, now: datetime) -> Mission | None:
    reserved = [m for m in deps.repo.list_missions(user_id) if m.status == "reserved" and m.depart_at > now]
    return reserved[-1] if reserved else None


def update_position(deps: Deps, user_id: str, lat: float, lng: float, accuracy_m: float | None) -> User:
    """開いている間の現在地を、ゾンビの目標にする。閉じている間は呼ばれない。"""
    user = settle_user(deps, user_id)
    if _accuracy_too_low(accuracy_m):
        return user
    # 動いていなければ書き込まない。追跡中は同じ場所でも受け付ける（立ち止まっている人の位置も「届いている」と分かるように）
    if not user.chasing and (round_coord(lat), round_coord(lng)) == (user.target_lat, user.target_lng):
        return user

    now = deps.clock()
    # シェルターを選んでいない日は追跡を始めない。時刻が来ればゲームオーバーになる
    reserved = _reserved_mission(deps, user_id, now)

    def apply(current: User, _mission: Mission | None):
        _set_target(
            current, lat, lng, now, can_chase=reserved is not None, mode=reserved.mode if reserved else "normal",
            shelter=reserved.shelter if reserved else None,
        )
        return current, None

    deps.repo.transact(user_id, None, apply)
    return settle_user(deps, user_id)


def register_push(deps: Deps, user_id: str, fid: str) -> None:
    if deps.repo.get_user(user_id) is None:
        raise not_found()

    def apply(user: User, _mission: Mission | None):
        user.push_enabled = True
        user.push_fid = fid
        return user, None

    deps.repo.transact(user_id, None, apply)


def change_daily_time(deps: Deps, user_id: str, daily_time: str) -> User:
    """毎日の時刻を変える。今日まだ来ていない到着はそのままにし、翌日から新しい時刻にする。"""
    settle_user(deps, user_id)
    now = deps.clock()
    reserved = _reserved_mission(deps, user_id, now)

    def apply(user: User, mission: Mission | None):
        user.daily_notify_time = daily_time
        # 今日の分は消化済みで、次の到着が明日以降なら、その日の新しい時刻に置き直す
        if not user.chasing and game.jst_date(user.arrival_at) > game.jst_date(now):
            old_arrival = user.arrival_at
            new_arrival = game.daily_at_on(daily_time, game.jst_date(old_arrival))
            state = zombie_now(user, now)
            user.zombie_lat, user.zombie_lng = state.lat, state.lng
            user.zombie_anchor_at = now
            user.arrival_at = new_arrival
            if mission is not None and mission.depart_at == old_arrival:
                mission.depart_at = new_arrival
        return user, mission

    deps.repo.transact(user_id, reserved.id if reserved else None, apply)
    return deps.repo.get_user(user_id)


# --- 予約、脱出、アイテム、チェックイン ---

def create_mission(deps: Deps, user_id: str, place_id: str, mode: ModeName = "normal") -> Mission:
    """次に来る毎日の時刻に向けて、逃げ込むシェルターとモードを決める。時刻は変えられない。"""
    user = settle_user(deps, user_id)
    now = deps.clock()
    if user.chasing:
        raise GameError("chasing", "いまは追われています。先にシェルターへ逃げ込もう", 409)

    missions = deps.repo.list_missions(user_id)
    if any(m.status in ("reserved", "escaped") for m in missions):
        raise GameError("already_reserved", "すでに予約中のミッションがあります", 409)
    # Places の使いすぎによる課金事故を防ぐ
    recent = [m for m in missions if m.kind == "daily" and now - m.created_at < timedelta(days=1)]
    if len(recent) >= game.MAX_MISSIONS_PER_DAY:
        raise GameError("too_many_missions", "今日はこれ以上予約できません", 429)

    shelter = next((s for s in deps.shelters.candidates(user) if s.place_id == place_id), None)
    if shelter is None:
        raise GameError("unknown_shelter", "そのシェルターは選べません", 400)

    mission = Mission(
        id=new_id(),
        user_id=user_id,
        depart_at=user.arrival_at,
        shelter=shelter,
        mode=mode,
        created_at=now,
    )
    deps.repo.create_mission(mission)
    return mission


def start_free_run(
    deps: Deps, user_id: str, place_id: str, mode: ModeName, lat: float, lng: float
) -> Mission:
    """フリーラン。何回でも走れる。ゾンビは背後から追ってくる。
    PC でも3Dの世界に入れるよう、位置の精度は問わない（歩いた距離やアイテムは精度の良い位置でだけ数える）。"""
    user = settle_user(deps, user_id)
    now = deps.clock()
    if user.chasing:
        raise GameError("chasing", "いまは追われています。先にシェルターへ逃げ込もう", 409)
    missions = deps.repo.list_missions(user_id)
    if any(m.status == "escaped" for m in missions):
        raise GameError("already_running", "逃走中のミッションがあります", 409)
    recent = [m for m in missions if m.kind == "free" and now - m.created_at < timedelta(days=1)]
    if len(recent) >= game.MAX_FREE_RUNS_PER_DAY:
        raise GameError("too_many_runs", "今日はたくさん走りました。また明日", 429)

    rank = game.base_rank(user.base_escaped_m)
    shelter = next((s for s in deps.shelters.candidates_near(lat, lng, rank) if s.place_id == place_id), None)
    if shelter is None:
        raise GameError("unknown_shelter", "そのシェルターは選べません", 400)

    mission = Mission(
        id=new_id(),
        user_id=user_id,
        kind="free",
        status="escaped",
        depart_at=now,
        escaped_at=now,
        shelter=shelter,
        mode=mode,
        created_at=now,
    )
    mission.items = _make_items(user, mission, lat, lng)
    deps.repo.create_mission(mission)

    def apply(current: User, _mission: Mission | None):
        if current.chasing:
            return None
        # 毎日のゾンビの動きは預かっておき、走り終えたら続きから近づかせる
        current.saved_approach = SavedApproach(
            lat=current.zombie_lat, lng=current.zombie_lng, anchor_at=current.zombie_anchor_at
        )
        # ゾンビ（ハードは3体）を、シェルターと反対側の背後に置いて追跡を始める
        current.target_lat, current.target_lng = round_chase_coord(lat), round_chase_coord(lng)
        current.chasing = True
        current.chase_mode = mode
        main = _zombie_behind(current.target_lat, current.target_lng, shelter, game.FREE_RUN_ZOMBIE_KM)
        states = [main] + _extra_zombies(current, main, game.MODES[mode])
        _reanchor(current, states, [s.km for s in states], now)
        return current, None

    deps.repo.transact(user_id, None, apply)
    return deps.repo.get_mission(mission.id)


def use_freeze(deps: Deps, user_id: str) -> User:
    """フリーズを1つ使う。次の毎日の時刻にゾンビは来ず、その次の日の時刻に来る。
    ゾンビは遠くに出直す。予約中のミッションはその日に移し、連続日数はその日の分も切らない。"""
    user = settle_user(deps, user_id)
    now = deps.clock()
    if user.freeze_count <= 0:
        raise GameError("no_freeze", "フリーズを持っていません", 409)
    if user.chasing:
        raise GameError("chasing", "いまは追われています。先にシェルターへ逃げ込もう", 409)
    if any(m.status == "escaped" for m in deps.repo.list_missions(user_id)):
        raise GameError("already_running", "逃走中のミッションがあります", 409)
    reserved = _reserved_mission(deps, user_id, now)

    def apply(current: User, mission: Mission | None):
        # 連打や同時の送信で二重に使わないよう、ここで状態を再確認する
        if current.freeze_count <= 0 or current.chasing or now >= current.arrival_at:
            return None
        skipped = current.arrival_at
        skipped_day = game.jst_date(skipped)
        current.freeze_count -= 1
        current.zombie_lat, current.zombie_lng = _spawn_zombie(current.home_lat, current.home_lng)
        current.zombie_anchor_at = now
        current.arrival_at = _next_arrival(current, skipped_day, now)
        # 休んだ日はクリアした日として扱い、連続日数をつなぐ（数は増やさない）
        if game.effective_streak(current.streak, current.last_clear_date, now) > 0:
            current.last_clear_date = max(current.last_clear_date, skipped_day)
        if mission is not None and mission.depart_at == skipped:
            mission.depart_at = current.arrival_at
        return current, mission

    deps.repo.transact(user_id, reserved.id if reserved else None, apply)
    return deps.repo.get_user(user_id)


def change_avatar(deps: Deps, user_id: str, outfit: str, skin: str) -> User:
    if deps.repo.get_user(user_id) is None:
        raise not_found()

    def apply(user: User, _mission: Mission | None):
        user.avatar_outfit = outfit
        user.avatar_skin = skin
        return user, None

    deps.repo.transact(user_id, None, apply)
    return deps.repo.get_user(user_id)


def get_mission(deps: Deps, mission_id: str) -> Mission:
    mission = deps.repo.get_mission(mission_id)
    if mission is None:
        raise not_found()
    settle_user(deps, mission.user_id)
    return deps.repo.get_mission(mission_id)


def _accuracy_too_low(accuracy_m: float | None) -> bool:
    return accuracy_m is not None and accuracy_m > game.MAX_GPS_ACCURACY_M


def _make_items(user: User, mission: Mission, lat: float, lng: float) -> list[Item]:
    """脱出した地点からシェルターまでの道のりにアイテムを置く。家の近くには置かない。"""
    rng = random.Random(mission.id)  # 同じミッションなら同じ配置になる
    planned = game.plan_items(lat, lng, mission.shelter.lat, mission.shelter.lng, rng)
    if mission.kind == "daily":
        # フリーズは毎日の逃走だけ。何回でも走れるフリーランで集められると、毎日の時刻の意味がなくなる
        freeze = game.plan_freeze(lat, lng, mission.shelter.lat, mission.shelter.lng, rng)
        if freeze is not None:
            planned.append(("freeze", *freeze))
    return [
        Item(id=secrets.token_urlsafe(8), kind=kind, lat=item_lat, lng=item_lng)
        for kind, item_lat, item_lng in planned
        if distance_m(user.home_lat, user.home_lng, item_lat, item_lng) >= game.ITEM_HOME_EXCLUSION_M
    ]


def escape(deps: Deps, mission_id: str, lat: float, lng: float, accuracy_m: float | None) -> dict:
    mission = get_mission(deps, mission_id)
    now = deps.clock()
    if mission.status in ("escaped", "cleared"):
        return {"status": "escaped"}
    if mission.status == "failed":
        return {"status": "timeout"}
    # 逃走中のミッションが2つになると、片方が捕まっても残り続けて進行不能になる
    if any(m.status == "escaped" and m.id != mission_id for m in deps.repo.list_missions(mission.user_id)):
        raise GameError("already_running", "フリーラン中は脱出できません。先にシェルターへ逃げ込もう", 409)
    if now < mission.depart_at - game.EARLY_START:
        return {"status": "too_early"}
    if _accuracy_too_low(accuracy_m):
        return {"status": "low_accuracy"}

    # 家にいても押せる。押した瞬間から追われるので、家にとどまれば追いつかれる（フリーランと同じ）。
    # 以前は家から200m離れないと押せず、それまで道の線が出なかったので、どちらへ進むか分からなかった
    user = deps.repo.get_user(mission.user_id)
    shelter = _shelter_from_here(deps, user, mission.shelter, lat, lng)

    def apply(current_user: User, current: Mission | None):
        if current is None or current.status != "reserved":
            return None
        current.status = "escaped"
        current.escaped_at = now
        current.shelter = shelter
        current.items = _make_items(current_user, current, lat, lng)
        _set_target(current_user, lat, lng, now, left_home=True, mode=current.mode, shelter=shelter)
        return current_user, current

    deps.repo.transact(mission.user_id, mission_id, apply)
    return {"status": "escaped", "shelter_changed": shelter is not mission.shelter}


def _shelter_from_here(deps: Deps, user: User, planned: Shelter, lat: float, lng: float) -> Shelter:
    """予約したシェルターは家の周りから選んでいる。時刻に外出先（家から離れた所）にいて、予約したシェルターが
    今いる場所から選べる範囲より遠ければ、今いる場所の近くで、予約したときの距離にいちばん近いシェルターに替える。
    家を出てすぐ脱出した人のシェルターは替えない。見つからなければ予約したまま。"""
    rank = game.base_rank(user.base_escaped_m)
    if distance_m(user.home_lat, user.home_lng, lat, lng) < game.OUTSIDE_FROM_HOME_M:
        return planned
    if distance_m(lat, lng, planned.lat, planned.lng) <= game.search_radius_m(rank):
        return planned
    try:
        nearby = deps.shelters.candidates_near(lat, lng, rank, allow_waypoints=False)
    except PlacesError:
        return planned  # 検索に失敗しても脱出はさせる
    if not nearby:
        return planned
    return min(nearby, key=lambda s: abs(s.distance_m - planned.distance_m))


def collect_item(
    deps: Deps, mission_id: str, item_id: str, lat: float, lng: float, accuracy_m: float | None
) -> dict:
    """アイテムに近づいたら拾う。資材はシェルターに着いたら拠点ポイントになる。"""
    mission = get_mission(deps, mission_id)
    if mission.status != "escaped":
        raise GameError("not_escaped", "逃げている間だけ拾えます", 409)
    item = next((i for i in mission.items if i.id == item_id), None)
    if item is None:
        raise not_found()
    if item.collected:
        return {"status": "already", "kind": item.kind}
    if accuracy_m is not None and accuracy_m > game.ITEM_MAX_ACCURACY_M:
        return {"status": "low_accuracy"}
    distance = distance_m(item.lat, item.lng, lat, lng)
    if distance > game.ITEM_COLLECT_RADIUS_M:
        return {"status": "too_far", "remaining_m": math.ceil(distance - game.ITEM_COLLECT_RADIUS_M)}

    now = deps.clock()

    def apply(user: User, current: Mission | None):
        target = next((i for i in current.items if i.id == item_id), None) if current else None
        # 連打や同時の送信で二重に効かないよう、ここで状態を再確認する
        if current is None or current.status != "escaped" or target is None or target.collected:
            return None
        target.collected = True
        # 前に作ったミッションに残る撃退（repel）は、拾っても何も起きない
        if target.kind == "material":
            current.material_bonus_m += game.MATERIAL_BONUS_M
        elif target.kind == "freeze":
            current.freeze_found = True
        return user, current

    deps.repo.transact(mission.user_id, mission_id, apply)
    user = deps.repo.get_user(mission.user_id)
    return {"status": "collected", "kind": item.kind, "zombie_km": round(zombie_now(user, now).km, 5)}


def walking_route(deps: Deps, mission_id: str, lat: float, lng: float):
    """⑥で今いる場所からシェルターまでの徒歩ルートを返す。位置は保存しない。"""
    mission = get_mission(deps, mission_id)
    if mission.status != "escaped":
        # 家にいる間に呼ばれると家の位置を外部に送ることになるので、脱出後だけ受ける
        raise GameError("not_escaped", "先に脱出してください", 409)
    check_rate(deps, f"route:{mission.user_id}", game.MAX_ROUTES_PER_DAY, timedelta(days=1))
    return deps.routes.walking(lat, lng, mission.shelter.lat, mission.shelter.lng)


def checkin(
    deps: Deps, mission_id: str, lat: float, lng: float, accuracy_m: float | None, walked_m: float | None = None
) -> dict:
    """シェルターに着いたらクリア。walked_m は画面が数えた道のりで、サーバーで妥当な範囲に収める。"""
    mission = get_mission(deps, mission_id)
    if mission.status == "cleared":
        return {"status": "cleared", "result": mission.result}
    if mission.status != "escaped":
        raise GameError("not_escaped", "先に脱出してください", 409)
    if accuracy_m is not None and accuracy_m > game.CHECKIN_MAX_ACCURACY_M:
        return {"status": "low_accuracy"}

    shelter = mission.shelter
    distance = distance_m(shelter.lat, shelter.lng, lat, lng)
    if distance > shelter.checkin_radius_m:
        return {"status": "too_far", "remaining_m": math.ceil(distance - shelter.checkin_radius_m)}

    now = deps.clock()
    # 画面を消して歩いてきた人は、ここで初めて今の位置が届く。途中で追いつかれていなかったかを先に確かめる
    tracked = deps.repo.get_user(mission.user_id)
    if tracked.chasing and now - tracked.zombie_anchor_at > game.POSITION_FRESH:
        update_position(deps, mission.user_id, lat, lng, accuracy_m)
        if deps.repo.get_mission(mission_id).status == "failed":
            return {"status": "captured"}

    def apply(user: User, current: Mission | None):
        # 連打や再送で距離が二重に入らないよう、ここで状態を再確認する
        if current is None or current.status != "escaped":
            return None
        # 歩いた距離の上限の起点。フリーランは始めた時刻、毎日のミッションは脱出できる時刻
        earliest_start = current.escaped_at if current.kind == "free" else current.depart_at - game.EARLY_START
        credited = game.credited_walk_m(walked_m, current.shelter.distance_m, now - earliest_start)
        mode = game.MODES[current.mode]
        mode_bonus = round(credited * (mode.point_rate - 1))
        gained = credited + mode_bonus + current.material_bonus_m
        streak = game.next_streak(user.streak, user.last_clear_date, now)
        rank_before = game.base_rank(user.base_escaped_m)

        user.base_escaped_m += gained
        user.total_walked_m += credited  # 生涯の距離は実際に歩いた分だけ
        rank_after = game.base_rank(user.base_escaped_m)
        user.best_base_rank = max(user.best_base_rank, rank_after)
        user.lost_base_rank = None
        freeze_gained = current.freeze_found and user.freeze_count < game.FREEZE_MAX
        if freeze_gained:
            user.freeze_count += 1
        user.streak = streak
        user.last_clear_date = game.jst_date(now)
        user.last_result = "cleared"
        # 追跡を終える。毎日のミッションならゾンビは遠くに出直し、次は翌日の時刻
        _start_next_approach(user, now, consume=current.kind == "daily")

        current.status = "cleared"
        current.cleared_at = current.ended_at = now
        current.result = ClearResult(
            walked_m=credited,
            mode=current.mode,
            mode_bonus_m=mode_bonus,
            material_bonus_m=current.material_bonus_m,
            freeze_gained=freeze_gained,
            base_points_gained=gained,
            base_rank_before=rank_before,
            base_rank_after=rank_after,
            base_escaped_m_after=user.base_escaped_m,
            base_rank_name_after=game.base_rank_name(rank_after),
            streak=streak,
        )
        return user, current

    deps.repo.transact(mission.user_id, mission_id, apply)
    return {"status": "cleared", "result": deps.repo.get_mission(mission_id).result}


def user_stats(deps: Deps, user_id: str) -> UserStats:
    """成果画面の集計。ミッションの記録から出す。"""
    user = settle_user(deps, user_id)
    now = deps.clock()
    # フリーランで達成扱いになった毎日のミッションは、同じ運動を二重に数えないよう除く
    cleared = [
        m for m in deps.repo.list_missions(user_id)
        if m.status == "cleared" and m.result and m.cleared_at and not m.result.covered_by_free_run
    ]
    week_start = game.jst_date(now) - timedelta(days=game.STATS_WEEK_DAYS - 1)
    history = sorted(cleared, key=lambda m: m.cleared_at, reverse=True)[: game.STATS_HISTORY_LIMIT]
    return UserStats(
        total_walked_m=user.total_walked_m,
        week_walked_m=sum(m.result.walked_m for m in cleared if game.jst_date(m.cleared_at) >= week_start),
        clear_count=len(cleared),
        capture_count=user.capture_count,
        streak=game.effective_streak(user.streak, user.last_clear_date, now),
        base_rank=game.base_rank(user.base_escaped_m),
        best_base_rank=user.best_base_rank,
        history=[WalkRecord(cleared_at=m.cleared_at, shelter_name=m.shelter.name, walked_m=m.result.walked_m) for m in history],
    )
