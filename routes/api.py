"""A担当と共有するAPI。形式の説明は docs/api.md。"""
from datetime import timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field

from services import devices, game, missions
from services.deps import Deps
from services.models import (
    ClearResult, ItemKind, Mission, MissionPublic, ModeName, OutfitColor, Shelter, SkinColor, User, UserPublic,
    UserStats, ZombiePublic,
)

router = APIRouter(prefix="/api")

IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{20,64}$")]
ItemIdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{6,32}$")]
TimeOfDay = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


def _deps(request: Request) -> Deps:
    return request.app.state.deps


def base_url(request: Request) -> str:
    return _deps(request).settings.public_base_url or str(request.base_url).rstrip("/")


def _client_ip(request: Request) -> str:
    """接続元。Cloud Run は実際の接続元を X-Forwarded-For の末尾に付ける（先頭は送り手が自由に書ける）。"""
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def _limit_searches(deps: Deps, user_id: str) -> None:
    missions.check_rate(deps, f"search:{user_id}", game.MAX_SHELTER_SEARCHES_PER_DAY, timedelta(days=1))


# --- この端末で開いてよいか。URLを知っているだけでは開けない ---

def _device_hash(request: Request) -> str:
    """この端末の鍵のハッシュ。鍵がなければ作り、応答の Cookie で渡す（app.py）。"""
    key = request.cookies.get(devices.COOKIE_NAME)
    if not key:
        key = getattr(request.state, "device_key", None) or devices.new_key()
        request.state.device_key = key
    return devices.hash_secret(key)


def authorize(request: Request, user: User) -> None:
    deps = _deps(request)
    device = _device_hash(request)
    if not user.device_hashes:
        missions.claim_device(deps, user.id, device)
        user = deps.repo.get_user(user.id)
    if device not in user.device_hashes:
        raise missions.GameError("other_device", "この端末ではまだ開けません。引き継ぎコードを入力してください", 403)


def owned_user_id(request: Request, user_id: IdPath) -> str:
    user = _deps(request).repo.get_user(user_id)
    if user is None:
        raise missions.not_found()
    authorize(request, user)
    return user_id


def owned_mission_id(request: Request, mission_id: IdPath) -> str:
    deps = _deps(request)
    mission = deps.repo.get_mission(mission_id)
    user = deps.repo.get_user(mission.user_id) if mission else None
    if user is None:
        raise missions.not_found()
    authorize(request, user)
    return mission_id


OwnedUser = Annotated[str, Depends(owned_user_id)]
OwnedMission = Annotated[str, Depends(owned_mission_id)]


def user_public(request: Request, user: User) -> UserPublic:
    deps = _deps(request)
    now = deps.clock()
    base = base_url(request)
    rank = game.base_rank(user.base_escaped_m)
    states = missions.zombie_states(user, now)
    zombie = min(states, key=lambda state: state.km)

    def coord(value: float) -> float | None:
        # 家へ近づいている間の座標は、2回取るだけで家の位置が分かってしまう
        return round(value, 6) if user.chasing else None

    return UserPublic(
        nickname=user.nickname,
        avatar_outfit=user.avatar_outfit,
        avatar_skin=user.avatar_skin,
        base_rank=rank,
        base_rank_name=game.base_rank_name(rank),
        base_escaped_m=user.base_escaped_m,
        next_rank_m=game.next_rank_m(user.base_escaped_m),
        lost_base_rank=user.lost_base_rank,
        lost_base_rank_name=game.base_rank_name(user.lost_base_rank) if user.lost_base_rank else None,
        total_walked_m=user.total_walked_m,
        streak=game.effective_streak(user.streak, user.last_clear_date, now),
        freeze_count=user.freeze_count,
        zombie_km=round(zombie.km, 5),
        zombie_lat=coord(zombie.lat),
        zombie_lng=coord(zombie.lng),
        zombie_speed_km_per_hour=missions.zombie_speed_km_per_hour(user, now),
        zombies=[ZombiePublic(lat=coord(s.lat), lng=coord(s.lng), km=round(s.km, 5)) for s in states],
        arrival_at=user.arrival_at,
        chasing=user.chasing,
        chase_mode=user.chase_mode,
        capture_count=user.capture_count,
        last_result=user.last_result,
        daily_notify_time=user.daily_notify_time,
        home_url=f"{base}/home/{user.id}",
    )


def mission_public(request: Request, mission: Mission) -> MissionPublic:
    base = base_url(request)
    return MissionPublic(
        id=mission.id,
        status=mission.status,
        depart_at=mission.depart_at,
        shelter=mission.shelter,
        kind=mission.kind,
        mode=mission.mode,
        items=mission.items,
        material_bonus_m=mission.material_bonus_m,
        freeze_found=mission.freeze_found,
        escape_url=f"{base}/escape?mission={mission.id}",
        home_url=f"{base}/home/{mission.user_id}",
        night_warning=game.is_dark(mission.depart_at),
        result=mission.result,
    )


class CreateUserBody(BaseModel):
    nickname: str = Field(min_length=1, max_length=20)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    daily_notify_time: TimeOfDay


class CreatedUser(BaseModel):
    user_id: str
    user: UserPublic


class CreateMissionBody(BaseModel):
    """時刻は選べない。次に来る毎日の時刻に向けた予約になる。"""

    place_id: str = Field(min_length=1, max_length=300)
    mode: ModeName = "normal"


class DailyTimeBody(BaseModel):
    daily_notify_time: TimeOfDay


class RunBody(BaseModel):
    """フリーラン。今いる場所の近くのシェルターへ向かう。"""

    place_id: str = Field(min_length=1, max_length=300)
    mode: ModeName = "normal"
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class NearBody(BaseModel):
    """今いる場所。URLに載せるとリクエストのログに残るので、本文で受ける。"""

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class AvatarBody(BaseModel):
    outfit: OutfitColor
    skin: SkinColor


class PositionBody(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)


class CheckinBody(PositionBody):
    walked_m: float | None = Field(default=None, ge=0, le=100_000)  # 画面が数えた道のり


class PushBody(BaseModel):
    fid: str = Field(min_length=1, max_length=4096)


class TransferCode(BaseModel):
    code: str


class LinkBody(BaseModel):
    code: str = Field(min_length=1, max_length=40)


class LinkedDevice(BaseModel):
    home_url: str


class EscapeResponse(BaseModel):
    status: Literal["escaped", "too_early", "timeout", "low_accuracy"]
    shelter_changed: bool | None = None  # 脱出できたときだけ。外出先で脱出し、今いる場所の近くのシェルターに替えたら true


class RoutePoint(BaseModel):
    lat: float
    lng: float


class RouteResponse(BaseModel):
    points: list[RoutePoint]
    distance_m: int
    duration_min: int


class CollectResponse(BaseModel):
    status: Literal["collected", "already", "too_far", "low_accuracy"]
    kind: ItemKind | None = None
    remaining_m: int | None = None
    zombie_km: float | None = None


class CheckinResponse(BaseModel):
    status: Literal["cleared", "too_far", "low_accuracy", "captured"]
    remaining_m: int | None = None
    result: ClearResult | None = None


@router.post("/users", status_code=201)
def create_user(request: Request, body: CreateUserBody) -> CreatedUser:
    deps = _deps(request)
    missions.check_rate(deps, f"new-user:{_client_ip(request)}", game.MAX_USERS_PER_IP_PER_HOUR, timedelta(hours=1))
    user = missions.create_user(deps, body.nickname, body.lat, body.lng, body.daily_notify_time, _device_hash(request))
    return CreatedUser(user_id=user.id, user=user_public(request, user))


@router.post("/users/{user_id}/transfer-code", status_code=201)
def issue_transfer_code(request: Request, user_id: OwnedUser) -> TransferCode:
    """別の端末へ移るコードを発行する。前に発行したコードは使えなくなる。"""
    return TransferCode(code=missions.issue_transfer_code(_deps(request), user_id))


@router.post("/devices/link")
def link_device(request: Request, body: LinkBody) -> LinkedDevice:
    """引き継ぎコードで、この端末を結びつける。"""
    deps = _deps(request)
    missions.check_rate(deps, f"link:{_client_ip(request)}", game.MAX_LINK_TRIES_PER_IP_PER_HOUR, timedelta(hours=1))
    user = missions.link_device(deps, body.code, _device_hash(request))
    return LinkedDevice(home_url=f"{base_url(request)}/home/{user.id}")


@router.get("/users/{user_id}")
def get_user(request: Request, user_id: OwnedUser) -> UserPublic:
    return user_public(request, missions.settle_user(_deps(request), user_id))


@router.post("/users/{user_id}/position")
def update_position(request: Request, user_id: OwnedUser, body: PositionBody) -> UserPublic:
    user = missions.update_position(_deps(request), user_id, body.lat, body.lng, body.accuracy_m)
    return user_public(request, user)


@router.put("/users/{user_id}/daily-time")
def change_daily_time(request: Request, user_id: OwnedUser, body: DailyTimeBody) -> UserPublic:
    """毎日の時刻を変える。今日まだ来ていない到着はそのままで、翌日から新しい時刻になる。"""
    return user_public(request, missions.change_daily_time(_deps(request), user_id, body.daily_notify_time))


@router.post("/users/{user_id}/freeze")
def use_freeze(request: Request, user_id: OwnedUser) -> UserPublic:
    """フリーズを1つ使い、次の毎日の時刻にゾンビが来ないようにする。"""
    return user_public(request, missions.use_freeze(_deps(request), user_id))


@router.post("/users/{user_id}/push", status_code=204)
def register_push(request: Request, user_id: OwnedUser, body: PushBody) -> None:
    missions.register_push(_deps(request), user_id, body.fid)


@router.get("/users/{user_id}/shelters")
def get_shelters(request: Request, user_id: OwnedUser) -> list[Shelter]:
    deps = _deps(request)
    user = missions.settle_user(deps, user_id)
    _limit_searches(deps, user_id)
    return deps.shelters.candidates(user)


@router.post("/users/{user_id}/shelters/near")
def get_shelters_near(request: Request, user_id: OwnedUser, body: NearBody) -> list[Shelter]:
    """今いる場所の近くのシェルター（フリーラン用）。"""
    deps = _deps(request)
    user = missions.settle_user(deps, user_id)
    _limit_searches(deps, user_id)
    return deps.shelters.candidates_near(body.lat, body.lng, game.base_rank(user.base_escaped_m))


@router.post("/users/{user_id}/runs", status_code=201)
def start_free_run(request: Request, user_id: OwnedUser, body: RunBody) -> MissionPublic:
    mission = missions.start_free_run(_deps(request), user_id, body.place_id, body.mode, body.lat, body.lng)
    return mission_public(request, mission)


@router.put("/users/{user_id}/avatar")
def change_avatar(request: Request, user_id: OwnedUser, body: AvatarBody) -> UserPublic:
    return user_public(request, missions.change_avatar(_deps(request), user_id, body.outfit, body.skin))


@router.get("/users/{user_id}/stats")
def get_stats(request: Request, user_id: OwnedUser) -> UserStats:
    return missions.user_stats(_deps(request), user_id)


@router.post("/users/{user_id}/missions", status_code=201)
def create_mission(request: Request, user_id: OwnedUser, body: CreateMissionBody) -> MissionPublic:
    mission = missions.create_mission(_deps(request), user_id, body.place_id, body.mode)
    return mission_public(request, mission)


@router.get("/missions/{mission_id}")
def get_mission(request: Request, mission_id: OwnedMission) -> MissionPublic:
    return mission_public(request, missions.get_mission(_deps(request), mission_id))


@router.post("/missions/{mission_id}/escape")
def escape(request: Request, mission_id: OwnedMission, body: PositionBody) -> EscapeResponse:
    return EscapeResponse(**missions.escape(_deps(request), mission_id, body.lat, body.lng, body.accuracy_m))


@router.post("/missions/{mission_id}/route")
def walking_route(request: Request, mission_id: OwnedMission, body: PositionBody) -> RouteResponse:
    route = missions.walking_route(_deps(request), mission_id, body.lat, body.lng)
    return RouteResponse(
        points=[RoutePoint(lat=lat, lng=lng) for lat, lng in route.points],
        distance_m=route.distance_m,
        duration_min=max(1, round(route.duration_s / 60)),
    )


@router.post("/missions/{mission_id}/items/{item_id}/collect")
def collect_item(request: Request, mission_id: OwnedMission, item_id: ItemIdPath, body: PositionBody) -> CollectResponse:
    result = missions.collect_item(_deps(request), mission_id, item_id, body.lat, body.lng, body.accuracy_m)
    return CollectResponse(**result)


@router.post("/missions/{mission_id}/checkin")
def checkin(request: Request, mission_id: OwnedMission, body: CheckinBody) -> CheckinResponse:
    result = missions.checkin(_deps(request), mission_id, body.lat, body.lng, body.accuracy_m, body.walked_m)
    return CheckinResponse(**result)
