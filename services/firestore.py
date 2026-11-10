"""保存処理。本番は Firestore、ローカルとテストはメモリを使う。

transact はユーザーとミッションを同時に読み書きする。経験値の二重加算や
捕獲の二重処理を防ぐため、更新は必ずこれを通す。
fn は (user, mission) を受け取り、変更なしなら None を返す。
"""
import threading
from typing import Callable, Protocol

from .models import Mission, User

TransactFn = Callable[[User, Mission | None], tuple[User, Mission | None] | None]


class Repository(Protocol):
    def create_user(self, user: User) -> None: ...
    def get_user(self, user_id: str) -> User | None: ...
    def find_user_by_transfer_code(self, code_hash: str) -> User | None: ...
    def create_mission(self, mission: Mission) -> None: ...
    def get_mission(self, mission_id: str) -> Mission | None: ...
    def list_missions(self, user_id: str) -> list[Mission]: ...
    def list_push_users(self) -> list[User]: ...
    def transact(self, user_id: str, mission_id: str | None, fn: TransactFn) -> None: ...


class MemoryRepository:
    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._missions: dict[str, Mission] = {}
        self._lock = threading.RLock()

    def create_user(self, user: User) -> None:
        with self._lock:
            self._users[user.id] = user.model_copy(deep=True)

    def get_user(self, user_id: str) -> User | None:
        with self._lock:
            user = self._users.get(user_id)
            return user.model_copy(deep=True) if user else None

    def find_user_by_transfer_code(self, code_hash: str) -> User | None:
        with self._lock:
            user = next((u for u in self._users.values() if u.transfer_code_hash == code_hash), None)
            return user.model_copy(deep=True) if user else None

    def create_mission(self, mission: Mission) -> None:
        with self._lock:
            self._missions[mission.id] = mission.model_copy(deep=True)

    def get_mission(self, mission_id: str) -> Mission | None:
        with self._lock:
            mission = self._missions.get(mission_id)
            return mission.model_copy(deep=True) if mission else None

    def list_missions(self, user_id: str) -> list[Mission]:
        with self._lock:
            found = [m.model_copy(deep=True) for m in self._missions.values() if m.user_id == user_id]
        return sorted(found, key=lambda m: m.created_at)

    def list_push_users(self) -> list[User]:
        with self._lock:
            return [u.model_copy(deep=True) for u in self._users.values() if u.push_enabled]

    def transact(self, user_id: str, mission_id: str | None, fn: TransactFn) -> None:
        with self._lock:
            user = self.get_user(user_id)
            mission = self.get_mission(mission_id) if mission_id else None
            if user is None or (mission_id and mission is None):
                raise KeyError("not found")
            changed = fn(user, mission)
            if changed is None:
                return
            new_user, new_mission = changed
            self._users[user_id] = new_user.model_copy(deep=True)
            if new_mission is not None:
                self._missions[new_mission.id] = new_mission.model_copy(deep=True)


def _dump(model: User | Mission) -> dict:
    data = model.model_dump()
    if isinstance(model, User) and data["last_clear_date"] is not None:
        # Firestore は date 型を保存できない
        data["last_clear_date"] = data["last_clear_date"].isoformat()
    return data


class FirestoreRepository:
    def __init__(self, client) -> None:
        self._client = client
        self._users = client.collection("users")
        self._missions = client.collection("missions")

    def create_user(self, user: User) -> None:
        self._users.document(user.id).set(_dump(user))

    def get_user(self, user_id: str) -> User | None:
        snap = self._users.document(user_id).get()
        return User(**snap.to_dict()) if snap.exists else None

    def find_user_by_transfer_code(self, code_hash: str) -> User | None:
        from google.cloud.firestore_v1.base_query import FieldFilter

        docs = self._users.where(filter=FieldFilter("transfer_code_hash", "==", code_hash)).limit(1).stream()
        return next((User(**d.to_dict()) for d in docs), None)

    def create_mission(self, mission: Mission) -> None:
        self._missions.document(mission.id).set(_dump(mission))

    def get_mission(self, mission_id: str) -> Mission | None:
        snap = self._missions.document(mission_id).get()
        return Mission(**snap.to_dict()) if snap.exists else None

    def list_missions(self, user_id: str) -> list[Mission]:
        # 複合インデックスを増やさないよう、並び替えはこちらで行う
        from google.cloud.firestore_v1.base_query import FieldFilter

        docs = self._missions.where(filter=FieldFilter("user_id", "==", user_id)).stream()
        return sorted((Mission(**d.to_dict()) for d in docs), key=lambda m: m.created_at)

    def list_push_users(self) -> list[User]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        docs = self._users.where(filter=FieldFilter("push_enabled", "==", True)).stream()
        return [User(**d.to_dict()) for d in docs]

    def transact(self, user_id: str, mission_id: str | None, fn: TransactFn) -> None:
        from google.cloud import firestore

        user_ref = self._users.document(user_id)
        mission_ref = self._missions.document(mission_id) if mission_id else None

        @firestore.transactional
        def run(transaction) -> None:
            user_snap = user_ref.get(transaction=transaction)
            mission_snap = mission_ref.get(transaction=transaction) if mission_ref else None
            if not user_snap.exists or (mission_snap is not None and not mission_snap.exists):
                raise KeyError("not found")
            user = User(**user_snap.to_dict())
            mission = Mission(**mission_snap.to_dict()) if mission_snap else None
            changed = fn(user, mission)
            if changed is None:
                return
            new_user, new_mission = changed
            transaction.set(user_ref, _dump(new_user))
            if new_mission is not None:
                transaction.set(self._missions.document(new_mission.id), _dump(new_mission))

        run(self._client.transaction())
