"""閉じている間の通知。定期実行で距離を確認し、段階を超えたら1回だけ送る。

通知の文面に位置情報は入れない。
"""
import logging
from typing import Protocol

from . import game

logger = logging.getLogger(__name__)

_LEVEL_MESSAGES = {
    1: ("ゾンビが近づいている", "距離が3kmを切った。逃げる時間を決めよう。"),
    2: ("ゾンビがすぐそこまで来ている", "距離が1kmを切った。シェルターは決めた？"),
    3: ("ゾンビが近くまで来た", "距離が300mを切った。時刻の10分前から脱出できる。準備しておこう。"),
}
_CAPTURE_MESSAGE = ("ゾンビに追いつかれた", "拠点が崩壊した。テントからリベンジだ。")
_RUN_CAPTURE_MESSAGE = ("フリーランは失敗", "ゾンビに追いつかれた。拠点は無事だ。また走ろう。")
# 毎日の時刻に合わせた知らせ。本人が決めた時刻の知らせなので、深夜でも送る
_ESCAPE_OPENS_MESSAGE = ("脱出できるようになった", "あと10分でゾンビが家に着く。家を出て、脱出を押そう。")
_NO_SHELTER_MESSAGE = ("あと10分でゾンビが来る", "シェルターを選んで、家を出よう。")


class PushSender(Protocol):
    def send(self, fid: str, title: str, body: str, link: str) -> bool:
        """送信する。端末が登録解除済みなら False を返す。"""
        ...


class FcmSender:
    def __init__(self, project: str) -> None:
        import firebase_admin

        try:
            firebase_admin.get_app()
        except ValueError:
            firebase_admin.initialize_app(options={"projectId": project})

    def send(self, fid: str, title: str, body: str, link: str) -> bool:
        from firebase_admin import messaging

        message = messaging.Message(
            fid=fid,
            webpush=messaging.WebpushConfig(
                notification=messaging.WebpushNotification(title=title, body=body),
                fcm_options=messaging.WebpushFCMOptions(link=link),
            ),
        )
        try:
            messaging.send(message)
        except (messaging.UnregisteredError, messaging.SenderIdMismatchError):
            return False
        return True


def _seen_stage(user) -> int:
    """今の到着について、時刻の通知をどの段階まで送ったか。"""
    return user.notified_arrival_stage if user.notified_arrival_at == user.arrival_at else 0


def _nothing_due(user, now) -> bool:
    """手元のデータだけで「送るものがない」と分かるか。到着前で追われてもいなければ判定できる。
    定期実行のたびに全員のミッションを読まないようにする。"""
    from .missions import zombie_now  # 循環インポートを避ける

    if user.chasing or now >= user.arrival_at or user.notified_capture_count < user.capture_count:
        return False
    return (
        game.arrival_stage(user.arrival_at, now) <= _seen_stage(user)
        and game.notify_level(zombie_now(user, now).km) == user.notified_level
    )


def _notify_user(deps, user_id: str, base_url: str, now) -> bool:
    from .missions import _reserved_mission, settle_user, zombie_now  # 循環インポートを避ける

    user = settle_user(deps, user_id)
    level = game.notify_level(zombie_now(user, now).km)
    quiet = game.is_quiet_hours(now)
    stage = 0 if user.chasing else game.arrival_stage(user.arrival_at, now)
    reserved = stage > 0 and _reserved_mission(deps, user_id, now) is not None
    outgoing: list[tuple[str, str]] = []

    def apply(current, _mission):
        outgoing.clear()
        if not current.push_enabled or not current.push_fid:
            return None
        changed = False
        if current.notified_capture_count < current.capture_count and not quiet:
            outgoing.append(_RUN_CAPTURE_MESSAGE if current.last_result == "run_captured" else _CAPTURE_MESSAGE)
            current.notified_capture_count = current.capture_count
            changed = True
        if stage > _seen_stage(current):
            current.notified_arrival_at = current.arrival_at
            current.notified_arrival_stage = stage
            changed = True
            if not outgoing:
                outgoing.append(_ESCAPE_OPENS_MESSAGE if reserved else _NO_SHELTER_MESSAGE)
        # フリーランのゾンビの距離で下げると、走り終えて毎日のゾンビに戻ったときに同じ通知が重なる
        if level < current.notified_level and current.saved_approach is None:
            # 引き離したら、また近づいたときに通知できるようにする
            current.notified_level = level
            changed = True
        elif level > current.notified_level and not quiet and not current.chasing:
            current.notified_level = level
            if not outgoing:
                outgoing.append(_LEVEL_MESSAGES[level])
            changed = True
        return (current, None) if changed else None

    # 先に記録してから送る。再実行されても同じ通知を二重に送らない
    deps.repo.transact(user_id, None, apply)
    if not outgoing:
        return False

    title, body = outgoing[0]
    fid = deps.repo.get_user(user_id).push_fid
    if deps.push.send(fid, title, body, f"{base_url}/home/{user_id}"):
        return True

    def disable(current, _mission):
        current.push_enabled = False
        current.push_fid = None
        return current, None

    deps.repo.transact(user_id, None, disable)
    return False


def run_notifications(deps, base_url: str) -> dict:
    now = deps.clock()
    sent = failed = 0
    for user in deps.repo.list_push_users():
        if _nothing_due(user, now):
            continue
        try:
            sent += 1 if _notify_user(deps, user.id, base_url, now) else 0
        except Exception as exc:  # 1人の失敗で他の人の通知を止めない
            failed += 1
            logger.error("notify failed: %s", type(exc).__name__)
    return {"sent": sent, "failed": failed}
