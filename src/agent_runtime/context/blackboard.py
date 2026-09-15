"""
Blackboard 管理器

纯存储层，不做语义理解。
所有语义操作（总结、摘要、提取 KV）由 Lead 负责。
"""

from typing import Dict, Any, List, Optional
from datetime import datetime

from agent_contracts.logging import get_logger
from ..core.interfaces import PersistenceBackend
from ..core.protocol import BLACKBOARD_UPDATED
from ..core.types import Event
from ..runtime.event_dispatcher import EventDispatcher

logger = get_logger(__name__)


class BlackboardManager:
    """Blackboard 存储管理器.

    All runtime writes must go through append_history/add_summary/update_kv so
    persistence and blackboard.updated events stay consistent.
    """

    def __init__(
        self,
        persistence: Optional[PersistenceBackend] = None,
        event_bus: Optional[EventDispatcher] = None,
    ):
        self._cache: Dict[str, Any] = {}
        self._persistence = persistence
        self._event_bus = event_bus

    # --- 核心 CRUD ---

    async def create(self, conversation_id: str) -> Dict[str, Any]:
        """创建新的 Blackboard"""
        logger.debug("Blackboard 创建", conversation_id=conversation_id)
        blackboard = {
            "id": f"bb_{conversation_id}",
            "conversation_id": conversation_id,
            "raw_history": [],
            "structured_summaries": [],
            "kv_state": {},
            "version": 0,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
        }
        await self._persist(blackboard)
        return blackboard

    async def get(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """获取 Blackboard（优先缓存，其次持久化）"""
        if conversation_id in self._cache:
            return self._normalize(conversation_id, self._cache[conversation_id])
        if self._persistence:
            try:
                bb = await self._persistence.load_blackboard(conversation_id)
                if bb:
                    normalized = self._normalize(conversation_id, bb)
                    self._cache[conversation_id] = normalized
                    return normalized
            except Exception:
                logger.warning("Blackboard 加载失败", conversation_id=conversation_id, exc_info=True)
        return None

    # --- 写操作 ---

    async def append_history(self, conversation_id: str, entry: Dict[str, Any]) -> Dict[str, Any]:
        """追加原始历史条目"""
        logger.debug(
            "Blackboard 追加历史", conversation_id=conversation_id, entry_type=entry.get("type")
        )
        blackboard = await self.get(conversation_id)
        if not blackboard:
            blackboard = await self.create(conversation_id)

        blackboard["raw_history"].append(
            {
                **entry,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        blackboard["version"] += 1
        blackboard["updated_at"] = datetime.utcnow().isoformat()

        await self._persist(blackboard)
        return blackboard

    async def add_summary(self, conversation_id: str, summary: Dict[str, Any]) -> Dict[str, Any]:
        """添加结构化摘要（由 Lead 生成内容，此处只存储）"""

        logger.info("Blackboard 添加摘要", conversation_id=conversation_id)

        blackboard = await self.get(conversation_id)

        if not blackboard:
            raise ValueError(f"Blackboard not found for {conversation_id}")

        blackboard["structured_summaries"].append(
            {
                **summary,
                "created_at": datetime.utcnow().isoformat(),
            }
        )
        blackboard["version"] += 1
        blackboard["updated_at"] = datetime.utcnow().isoformat()

        await self._persist(blackboard)

        return blackboard

    async def update_kv(self, conversation_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """更新键值状态"""
        logger.debug(
            "Blackboard 更新 KV", conversation_id=conversation_id, keys=list(updates.keys())
        )
        blackboard = await self.get(conversation_id)

        if not blackboard:
            raise ValueError(f"Blackboard not found for {conversation_id}")

        blackboard["kv_state"].update(updates)
        blackboard["version"] += 1
        blackboard["updated_at"] = datetime.utcnow().isoformat()

        await self._persist(blackboard)
        return blackboard

    # --- 读操作 ---

    async def get_raw_history(
        self, conversation_id: str, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """获取原始历史"""
        blackboard = await self.get(conversation_id)
        if not blackboard:
            return []
        history = blackboard["raw_history"]
        if limit:
            return history[-limit:]
        return history

    async def get_all_summaries(self, conversation_id: str) -> List[Dict[str, Any]]:
        """获取所有结构化摘要"""
        blackboard = await self.get(conversation_id)
        if not blackboard:
            return []
        return blackboard["structured_summaries"]

    async def get_kv(self, conversation_id: str, key: Optional[str] = None):
        """获取键值状态"""
        blackboard = await self.get(conversation_id)
        if not blackboard:
            return {} if key is None else None
        kv = blackboard["kv_state"]
        if key:
            return kv.get(key)
        return kv

    async def get_version(self, conversation_id: str) -> int:
        """获取当前版本号"""
        blackboard = await self.get(conversation_id)
        return blackboard["version"] if blackboard else 0

    # --- 持久化 ---

    async def _persist(self, blackboard: Dict[str, Any]):
        """持久化到存储 + 更新缓存"""
        blackboard = self._normalize(str(blackboard.get("conversation_id") or ""), blackboard)
        self._cache[blackboard["conversation_id"]] = blackboard
        if self._persistence:
            try:
                await self._persistence.save_blackboard(blackboard["conversation_id"], blackboard)
            except Exception as e:
                logger.error(
                    "Blackboard 持久化失败",
                    conversation_id=blackboard["conversation_id"],
                    error=str(e),
                )
        if self._event_bus:
            await self._event_bus.publish(
                Event(
                    type=BLACKBOARD_UPDATED,
                    payload={
                        "conversation_id": blackboard["conversation_id"],
                        "version": blackboard.get("version", 0),
                        "updated_at": blackboard.get("updated_at"),
                    },
                    source="blackboard",
                    channel="internal",
                )
            )

    @staticmethod
    def _normalize(conversation_id: str, blackboard: Dict[str, Any] | None) -> Dict[str, Any]:
        """Normalize persisted/legacy blackboard payloads to the runtime shape.

        Older records stored only raw_history/kv_state/version under
        conversation.extra.blackboard. The runtime always needs conversation_id
        for cache keys and persistence, so hydrate missing fields here instead
        of letting legacy data crash the scheduler.
        """
        data = dict(blackboard or {})
        cid = str(data.get("conversation_id") or conversation_id or "")
        now = datetime.utcnow().isoformat()
        data["conversation_id"] = cid
        data.setdefault("id", f"bb_{cid}")
        if not isinstance(data.get("raw_history"), list):
            data["raw_history"] = []
        if not isinstance(data.get("structured_summaries"), list):
            data["structured_summaries"] = []
        if not isinstance(data.get("kv_state"), dict):
            data["kv_state"] = {}
        try:
            data["version"] = int(data.get("version") or 0)
        except (TypeError, ValueError):
            data["version"] = 0
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        return data
