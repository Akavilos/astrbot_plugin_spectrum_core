"""根据实际生活事件判断承诺是否可执行，生成共同作息背景。"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo


def intent_ready(store, life, row, now):
    """结合事件替代、期限、回家条件与留守承诺判断约定是否仍可执行。"""
    if row is None:
        return False
    meta = json.loads(row["meta"] or "{}")
    related_id = meta.get("related_id")
    if related_id is not None:
        event = store.shared_life_event(related_id)
        if event is not None and event["superseded_by"] is not None:
            store.set_intent_status(row["id"], "cancelled")
            return False
    expiry = meta.get("expires_at")
    if expiry is not None and now > expiry:
        store.set_intent_status(row["id"], "cancelled")
        return False
    if meta.get("trigger", "time") == "after_return" or meta.get("movement") == "go":
        returned = json.loads(store.get_state("global", "last_outing_return", "{}"))
        if meta.get("movement") == "go" or returned.get("returned_at", 0) < row["created_ts"]:
            stays = [r for r in store.shared_life_events(life.current_life_id(), limit=128)
                     if r["kind"] == "commitment" and r["movement"] == "stay"
                     and (r["valid_until"] is None or now <= r["valid_until"])]
            if stays and not life.get_owner():
                store.set_intent_status(row["id"], "cancelled")
            if meta.get("trigger") == "after_return" or stays:
                return False
    return row["status"] == "waiting" and row["not_before"] <= now


def runtime_facts(store, timezone="Asia/Shanghai"):
    """依据实际睡眠状态与上次睡眠记录生成作息背景。"""
    tz = ZoneInfo(timezone)
    def stamp(value):
        return datetime.fromtimestamp(value, tz).strftime("%m月%d日 %H:%M")
    if store.is_asleep():
        sleep = store.get_sleep()
        fact = f"我从{stamp(sleep['start'])}开始睡觉，目前尚未醒来。"
    else:
        sleep = json.loads(store.get_state("global", "last_sleep", "{}"))
        fact = f"我实际在{stamp(sleep['start'])}至{stamp(sleep['end'])}睡过，目前清醒。" if sleep else "我目前清醒。"
    return ("【我实际经历的作息】\n" + fact
            + "\n我以前的疲惫或未眠描述属于当时，不能据此抹掉这段睡眠。"
              "这些事实供我判断自己的状态，并不要求我向别人报备。")
