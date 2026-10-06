"""Classes representing input messages from Sigevent emitters"""
from datetime import datetime
from enum import StrEnum
import re
from typing import Optional

from pydantic import BaseModel, ConfigDict


class EventLevel(StrEnum):
    """
    Enum representing Sigevent "level". This is synonymous with
    traditional logging levels
    """
    ERROR = 'ERROR'
    WARN = 'WARN'
    INFO = 'INFO'
    DEBUG = 'DEBUG'

    def __le__(self, other):
        level_map = {
            EventLevel.ERROR: 4,
            EventLevel.WARN: 3,
            EventLevel.INFO: 2,
            EventLevel.DEBUG: 1
        }
        return level_map[self] <= level_map[other]


class EventMessage(BaseModel):
    """
    The Sigevent input message; the primary message format for Sigevent
    """
    model_config = ConfigDict(frozen=True)

    collection_name: str
    category: str
    subject: str
    description: str
    granule_name: Optional[str] = None
    event_level: EventLevel
    source_name: str
    executor: str
    timestamp: Optional[datetime] = None

    def __repr__(self):
        """
        Convert Sigevent object to a string
        """
        return f'EventMessage(' \
               f'event_level={self.event_level}, ' \
               f'subject={self.subject}, ' \
               f'description={self.description}, ' \
               f'collection_name={self.collection_name}, ' \
               f'granule_name={self.granule_name}, ' \
               f'category={self.category}, ' \
               f'source_name={self.source_name}, ' \
               f'executor={self.executor}, ' \
               f'timestamp={self.timestamp}' \
               f')'


MAX_SHORT_SOURCE_CHARS = 80

_ENVIRONMENT_TAG = re.compile(r'\[([^\[\]\s]+)\]$')


def short_source(source_name: str) -> str:
    """
    source_name updated to display in a subjct line.
    """
    if source_name.startswith('arn:'):
        short = source_name.rsplit(':', 1)[-1]
    else:
        short = source_name

    if len(short) > MAX_SHORT_SOURCE_CHARS:
        short = short[:MAX_SHORT_SOURCE_CHARS - 3] + '...'

    return short


def source_environment(source_name: str) -> str:
    """
    The deployment environment an event came from, or '' when it is untagged.

    CC sets SIGEVENT_SOURCE_ENV s.t. its events arrive as 
    e.g. SAFE_unpack.py[cc:cnsld-cumulus-prod]; legacy
    sets nothing, so an untagged source is how legacy is identified.
    """
    match = _ENVIRONMENT_TAG.search(source_name)
    return match.group(1) if match else ''


def strip_environment(source_name: str) -> str:
    """
    source_name without its trailing environment tag, if it has one
    """
    return _ENVIRONMENT_TAG.sub('', source_name)
