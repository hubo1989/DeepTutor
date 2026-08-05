"""
Task ID Manager - Assigns unique IDs to each background task
"""

import asyncio
from datetime import datetime, timedelta
import logging
import threading
from typing import Optional
import uuid

logger = logging.getLogger(__name__)


class TaskIDManager:
    """Singleton class for managing task IDs"""

    _instance: Optional["TaskIDManager"] = None
    _lock = threading.Lock()
    _task_ids: dict[str, str] = {}  # task_key -> task_id
    _task_metadata: dict[str, dict] = {}  # task_id -> metadata
    _active_async_tasks: dict[str, set[asyncio.Task]] = {}

    @classmethod
    def get_instance(cls) -> "TaskIDManager":
        """Get singleton instance"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def generate_task_id(
        self,
        task_type: str,
        task_key: str,
        *,
        owner_id: str | None = None,
    ) -> str:
        """
        Generate unique ID for task

        Args:
            task_type: Task type (e.g., 'kb_init', 'kb_upload', 'question_gen', 'solve', 'research')
            task_key: Task unique identifier (e.g., knowledge base name, question ID, etc.)
            owner_id: Immutable account owner used by realtime authorization.

        Returns:
            Task ID (format: {task_type}_{timestamp}_{uuid})
        """
        with self._lock:
            # If task already exists, return existing ID
            if task_key in self._task_ids:
                task_id = self._task_ids[task_key]
                metadata = self._task_metadata.get(task_id)
                existing_owner = str((metadata or {}).get("owner_id") or "")
                if owner_id and existing_owner and existing_owner != owner_id:
                    raise PermissionError("Task key is already owned by another user")
                if owner_id and metadata is not None and not existing_owner:
                    metadata["owner_id"] = owner_id
                return task_id

            # Generate new ID
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = str(uuid.uuid4())[:8]
            task_id = f"{task_type}_{timestamp}_{unique_id}"

            # Save mapping and metadata
            self._task_ids[task_key] = task_id
            metadata = {
                "task_type": task_type,
                "task_key": task_key,
                "created_at": datetime.now().isoformat(),
                "status": "running",
            }
            if owner_id:
                metadata["owner_id"] = owner_id
            self._task_metadata[task_id] = metadata

            return task_id

    def get_task_id(self, task_key: str) -> str | None:
        """Get task ID"""
        with self._lock:
            return self._task_ids.get(task_key)

    def update_task_status(self, task_id: str, status: str, **kwargs):
        """Update task status"""
        with self._lock:
            if task_id in self._task_metadata:
                self._task_metadata[task_id]["status"] = status
                self._task_metadata[task_id].update(kwargs)
                if status in ["completed", "error", "cancelled"]:
                    self._task_metadata[task_id]["finished_at"] = datetime.now().isoformat()

    def get_task_metadata(self, task_id: str) -> dict | None:
        """Get task metadata"""
        with self._lock:
            metadata = self._task_metadata.get(task_id)
            return metadata.copy() if metadata is not None else None

    def register_current_task(self, task_id: str, owner_id: str) -> asyncio.Task | None:
        """Bind a Starlette background coroutine to its immutable owner id."""
        task = asyncio.current_task()
        if task is None:
            return None
        with self._lock:
            if task_id in self._task_metadata:
                metadata = self._task_metadata[task_id]
                existing_owner = str(metadata.get("owner_id") or "")
                if existing_owner and existing_owner != owner_id:
                    raise PermissionError("Task is owned by another user")
                metadata["owner_id"] = owner_id
            self._active_async_tasks.setdefault(owner_id, set()).add(task)
        return task

    def unregister_current_task(self, owner_id: str, task: asyncio.Task | None) -> None:
        if task is None:
            return
        with self._lock:
            tasks = self._active_async_tasks.get(owner_id)
            if tasks is None:
                return
            tasks.discard(task)
            if not tasks:
                self._active_async_tasks.pop(owner_id, None)

    async def cancel_all_for_owner(self, owner_id: str) -> int:
        """Cancel and await all tracked background work for one account."""
        with self._lock:
            tasks = tuple(
                task
                for task in self._active_async_tasks.get(owner_id, set())
                if not task.done() and task is not asyncio.current_task()
            )
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    def cleanup_old_tasks(self, max_age_hours: int = 24):
        """Clean up old tasks (completed tasks older than specified hours)"""
        with self._lock:
            cutoff = datetime.now() - timedelta(hours=max_age_hours)

            to_remove = []
            for task_id, metadata in self._task_metadata.items():
                if metadata.get("status") in ["completed", "error", "cancelled"]:
                    finished_at = metadata.get("finished_at")
                    if finished_at:
                        try:
                            finished_time = datetime.fromisoformat(finished_at)
                            if finished_time < cutoff:
                                to_remove.append(task_id)
                        except Exception:
                            logger.warning("Failed to parse finished_at for task %s", task_id)

            for task_id in to_remove:
                metadata = self._task_metadata.pop(task_id, {})
                task_key = metadata.get("task_key")
                if task_key:
                    self._task_ids.pop(task_key, None)
