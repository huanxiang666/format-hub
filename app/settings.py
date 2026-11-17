"""Strict public runtime settings; saved values take precedence over environment defaults."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading

from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    workers: int = Field(default=2, ge=1, le=32)
    timeout_seconds: int = Field(default=600, ge=30, le=86400)
    cad_timeout_seconds: int = Field(default=3600, ge=30, le=86400)
    media_threads: int = Field(default=2, ge=1, le=16)
    memory_mb: int = Field(default=2048, ge=512, le=32768)
    max_file_mb: int = Field(default=200, ge=1, le=2048)
    max_files: int = Field(default=30, ge=1, le=100)
    queue_limit: int = Field(default=40, ge=1, le=500)
    retention_hours: int = Field(default=24, ge=1, le=720)
    job_mb: int = Field(default=1024, ge=128, le=16384)
    storage_mb: int = Field(default=10240, ge=1024, le=1048576)
    min_free_mb: int = Field(default=512, ge=128, le=65536)


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    values: dict[str, int]
    revision: int = Field(ge=0)


class SavedSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    values: Settings
    revision: int = Field(ge=0)


ENV_KEYS = {'timeout_seconds': 'TIMEOUT', 'cad_timeout_seconds': 'CAD_TIMEOUT', 'min_free_mb': 'MIN_FREE_MB'}
DESCRIPTIONS = {
    'workers': ('同时转换任务数', '个', '调低后等待运行任务结束；调高立即开放后续任务。'),
    'timeout_seconds': ('单任务时限', '秒', '非 CAD 新任务的最长运行时间。'),
    'cad_timeout_seconds': ('CAD 单任务时限', '秒', '每个 CAD 新任务独立计时，默认一小时。'),
    'media_threads': ('媒体转换线程', '个', '每个新媒体任务使用的引擎线程数。'),
    'memory_mb': ('单任务内存预算', 'MB', '新任务进程的内存上限。'),
    'max_file_mb': ('单文件上传上限', 'MB', '单个输入文件允许的最大容量。'),
    'max_files': ('每批文件上限', '个', '一次提交允许的输入文件数量。'),
    'queue_limit': ('等待队列容量', '个', '与并发任务数合计形成接收容量；降低不丢弃已接收任务。'),
    'retention_hours': ('任务保留时间', '小时', '结束任务达到保留时间后自动清理。'),
    'job_mb': ('单任务磁盘预算', 'MB', '新任务目录的容量预算。'),
    'storage_mb': ('全部任务磁盘预算', 'MB', '上传和转换目录占用的总容量限制。'),
    'min_free_mb': ('磁盘剩余空间下限', 'MB', '磁盘低于此剩余空间时停止接收或转换。'),
}


def fields():
    result = []
    for key, field in Settings.model_fields.items():
        bounds = {attr: getattr(item, attr) for item in field.metadata for attr in ('ge', 'le') if hasattr(item, attr)}
        label, unit, description = DESCRIPTIONS[key]
        result.append({'key': key, 'label': label, 'min': bounds['ge'], 'max': bounds['le'], 'unit': unit, 'description': description})
    return result


def environment_defaults():
    values = Settings().model_dump()
    for key in values:
        name = 'FORMAT_HUB_' + ENV_KEYS.get(key, key.upper())
        if name in os.environ:
            values[key] = int(os.environ[name])
    return Settings.model_validate(values)


class RevisionConflict(Exception):
    pass


class SettingsStore:
    def __init__(self, directory: Path, defaults: Settings):
        self.path = directory / 'settings.json'
        self.lock = threading.RLock()
        if self.path.exists():
            saved = SavedSettings.model_validate_json(self.path.read_text(encoding='utf-8'))
            self.values, self.revision = saved.values, saved.revision
            os.chmod(self.path, 0o600)
        else:
            self.values, self.revision = defaults, 0

    def update(self, update: SettingsUpdate):
        with self.lock:
            if update.revision != self.revision:
                raise RevisionConflict()
            candidate = Settings.model_validate({**self.values.model_dump(), **update.values})
            saved = SavedSettings(values=candidate, revision=self.revision + 1)
            fd, name = tempfile.mkstemp(prefix='.settings-', suffix='.tmp', dir=self.path.parent)
            temporary = Path(name)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    os.chmod(temporary, 0o600)
                    stream.write(saved.model_dump_json())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            self.values, self.revision = candidate, saved.revision
            return candidate
