"""接入未唤醒的群消息观测，不把旁听消息转成框架唤醒。"""
from __future__ import annotations
from typing import Any
import weakref
from astrbot.api.event import AstrMessageEvent
from astrbot.api.event.filter import CustomFilter
from astrbot.api.platform import MessageType

_ACTIVE_PLUGIN_REF: weakref.ReferenceType | None = None

def set_active_plugin(plugin: Any) -> None:
    global _ACTIVE_PLUGIN_REF
    _ACTIVE_PLUGIN_REF = weakref.ref(plugin) if plugin is not None else None

def get_active_plugin() -> Any:
    if _ACTIVE_PLUGIN_REF is None:
        return None
    return _ACTIVE_PLUGIN_REF()

class PassiveInterjectFilter(CustomFilter):
    def __init__(self, raise_error: bool = True, plugin=None, **kwargs) -> None:
        super().__init__(raise_error=raise_error, **kwargs)
        self._plugin_ref = weakref.ref(plugin) if plugin is not None else None

    def _get_plugin(self):
        if self._plugin_ref is not None:
            return self._plugin_ref()
        return get_active_plugin()

    def filter(self, event: AstrMessageEvent, cfg: Any) -> bool:
        plugin = self._get_plugin()
        if not plugin:
            return False


        if not plugin._cfg("enabled", False):
            return False


        try:
            if event.get_message_type() != MessageType.GROUP_MESSAGE:
                return False
        except:
            return False


        is_at_me = False
        if hasattr(event, "is_at_me"):
            value = event.is_at_me
            is_at_me = value() if callable(value) else bool(value)
        elif event.get_extra("_is_waked", False):
            is_at_me = True

        if is_at_me:
            return False


        if hasattr(plugin, "on_message_eavesdrop_sync"):
            plugin.on_message_eavesdrop_sync(event)


        return False
