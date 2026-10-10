"""通过框架注册表读取可选棱镜的本轮关系与情绪信号。
未安装或未返回信号时提供空结果，不直接访问棱镜数据库。"""


from __future__ import annotations


def peek_signals(umo: str = ""):
    try:
        from astrbot.core.star.star import star_map
        for meta in list(star_map.values()):
            if getattr(meta, "name", "") != "astrbot_plugin_prism":
                continue
            cls = getattr(meta, "star_cls", None)
            fn = getattr(cls, "last_relation_signals", None) if cls else None
            if callable(fn):
                r = fn(umo=umo) if umo else fn()
                return dict(r) if r else None
    except Exception:
        return None
    return None


def peek_emotion(umo: str = ""):
    try:
        from astrbot.core.star.star import star_map
        for meta in list(star_map.values()):
            if getattr(meta, "name", "") != "astrbot_plugin_prism":
                continue
            cls = getattr(meta, "star_cls", None)
            fn = getattr(cls, "last_emotion_valence", None) if cls else None
            if callable(fn):
                r = fn(umo=umo) if umo else fn()
                return float(r) if r is not None else None
    except Exception:
        return None
    return None
