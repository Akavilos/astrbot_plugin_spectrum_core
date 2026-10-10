"""光谱SQLite持久化入口，管理往来、共同生活、人物、认知和后台任务。
提供读写锁、世界版本、受众查询、世界书激活及可选向量索引访问。"""


from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

from . import memory as _memory
from . import recall as _recall
from . import vectors as _vectors
from . import worldbook as _worldbook

try:
    from astrbot.api import logger
except Exception:
    import logging

    logger = logging.getLogger("spectrum")


GLOBAL_UMO = "global"

_SCHEMA = """
 
-- 持久化表与索引定义；字段名称供存储接口及既有数据读取使用。
CREATE TABLE IF NOT EXISTS entries (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    umo     TEXT    NOT NULL,
    ts      REAL    NOT NULL,
    kind    TEXT    NOT NULL,           
    text    TEXT    NOT NULL,
    meta    TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_entries_umo_ts ON entries(umo, ts);
CREATE INDEX IF NOT EXISTS idx_entries_kind_ts ON entries(kind, ts);




























CREATE TABLE IF NOT EXISTS lives (
    life_id       TEXT PRIMARY KEY,            
    kind          TEXT NOT NULL,               
    started_at    REAL NOT NULL,
    ended_at      REAL,                        
    owner_session TEXT NOT NULL DEFAULT '',    
    with_who      TEXT NOT NULL DEFAULT '',    
    title         TEXT NOT NULL DEFAULT '',    
    summary       TEXT NOT NULL DEFAULT '',    
    title_pending INTEGER NOT NULL DEFAULT 0,  
    meta          TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_lives_started ON lives(started_at DESC);

CREATE TABLE IF NOT EXISTS pending (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    umo         TEXT    NOT NULL,
    due_ts      REAL    NOT NULL,
    content     TEXT    NOT NULL,
    created_ts  REAL    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'waiting',    
    origin      TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pending_status_due ON pending(status, due_ts);

CREATE TABLE IF NOT EXISTS pending_life_events (
    pending_id INTEGER PRIMARY KEY,
    events TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_communication (
    pending_id INTEGER PRIMARY KEY,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    umo   TEXT NOT NULL,
    key   TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (umo, key)
);


CREATE TABLE IF NOT EXISTS intents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    umo         TEXT NOT NULL,
    kind        TEXT NOT NULL,      
    summary     TEXT NOT NULL,
    not_before  REAL NOT NULL,      
    status      TEXT NOT NULL DEFAULT 'waiting',   
    created_ts  REAL NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_intents_due
    ON intents(status, not_before);





CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    umo         TEXT NOT NULL,
    text        TEXT NOT NULL,
    importance  REAL NOT NULL DEFAULT 0.5,
    ts          REAL NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}',


    source_entry_ids TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_memories_umo ON memories(umo, importance DESC);




CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    text,
    content='memories',
    content_rowid='id',
    tokenize='trigram'
);


CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
  INSERT INTO memories_fts(memories_fts, rowid, text) VALUES('delete', old.id, old.text);
  INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
END;















CREATE TABLE IF NOT EXISTS persons (
    person_id   TEXT PRIMARY KEY,               
    platform    TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',       
    impression  TEXT NOT NULL DEFAULT '',       
    affinity    REAL NOT NULL DEFAULT 0.5,      
    trust       REAL NOT NULL DEFAULT 0.5,      
    first_seen  REAL NOT NULL DEFAULT 0,
    last_seen   REAL NOT NULL DEFAULT 0,
    meta        TEXT NOT NULL DEFAULT '{}'
);



CREATE TABLE IF NOT EXISTS relationship_events (
    person_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    umo TEXT NOT NULL,
    ts REAL NOT NULL,
    signals TEXT NOT NULL,
    before_affinity REAL NOT NULL,
    after_affinity REAL NOT NULL,
    before_trust REAL NOT NULL,
    after_trust REAL NOT NULL,
    PRIMARY KEY (person_id, event_key)
);

CREATE TABLE IF NOT EXISTS person_aliases (
    person_id TEXT NOT NULL,
    alias     TEXT NOT NULL,
    umo       TEXT NOT NULL DEFAULT '',
    last_seen REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (person_id, alias)
);



CREATE TABLE IF NOT EXISTS person_memories (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id  TEXT NOT NULL,
    text       TEXT NOT NULL,
    origin_umo TEXT NOT NULL DEFAULT '',           
    scope      TEXT NOT NULL DEFAULT 'public',     
    importance REAL NOT NULL DEFAULT 0.5,
    ts         REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_person_memories
    ON person_memories(person_id, importance DESC);


CREATE VIRTUAL TABLE IF NOT EXISTS person_memories_fts USING fts5(
    text, content='person_memories', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS person_memories_ai AFTER INSERT ON person_memories BEGIN
  INSERT INTO person_memories_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS person_memories_ad AFTER DELETE ON person_memories BEGIN
  INSERT INTO person_memories_fts(person_memories_fts, rowid, text)
  VALUES('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS person_memories_au AFTER UPDATE ON person_memories BEGIN
  INSERT INTO person_memories_fts(person_memories_fts, rowid, text)
  VALUES('delete', old.id, old.text);
  INSERT INTO person_memories_fts(rowid, text) VALUES (new.id, new.text);
END;


CREATE TABLE IF NOT EXISTS person_sessions (
    person_id TEXT NOT NULL,
    umo       TEXT NOT NULL,
    last_seen REAL NOT NULL DEFAULT 0,
    msg_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (person_id, umo)
);












CREATE TABLE IF NOT EXISTS storylets (
    sid          TEXT PRIMARY KEY,                     
    kind         TEXT NOT NULL DEFAULT 'action',       
    enabled      INTEGER NOT NULL DEFAULT 1,
    scope        TEXT NOT NULL DEFAULT 'global',       
    scene_id     TEXT NOT NULL DEFAULT '',             
    got_scene_id TEXT NOT NULL DEFAULT '',             
    ending_name  TEXT NOT NULL DEFAULT '',             
    narrative    TEXT NOT NULL DEFAULT '[]',           
    condition    TEXT NOT NULL DEFAULT '{}',           
    depends_on   TEXT NOT NULL DEFAULT '[]',           
    k_turns      INTEGER NOT NULL DEFAULT 0,           
    priority     INTEGER NOT NULL DEFAULT 0,           
    weight       REAL    NOT NULL DEFAULT 1.0,         
    effects      TEXT NOT NULL DEFAULT '{}',           
    eval_annot   TEXT NOT NULL DEFAULT '{}',           
    meta         TEXT NOT NULL DEFAULT '{}',
    created_at   REAL NOT NULL DEFAULT 0,
    updated_at   REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_storylets_scene ON storylets(scene_id, enabled);













CREATE TABLE IF NOT EXISTS director_fires (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL    NOT NULL,
    sid        TEXT    NOT NULL DEFAULT '',             
    kind       TEXT    NOT NULL DEFAULT 'storylet',     
    umo        TEXT    NOT NULL DEFAULT '',
    life_id    TEXT    NOT NULL DEFAULT '',
    turn_seq   INTEGER NOT NULL DEFAULT 0,              
    narr_index INTEGER NOT NULL DEFAULT 0,              
    delivered  INTEGER NOT NULL DEFAULT 0,              
    narrative  TEXT    NOT NULL DEFAULT '',             
    meta       TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_director_fires_sid ON director_fires(sid, ts);
CREATE INDEX IF NOT EXISTS idx_director_fires_seq ON director_fires(turn_seq);





















CREATE TABLE IF NOT EXISTS relation_history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    umo       TEXT NOT NULL,
    person_id TEXT NOT NULL DEFAULT '',    
    affinity  REAL,
    trust     REAL,
    valence   REAL,                        
    meta      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_relation_history_umo ON relation_history(umo, ts);
CREATE INDEX IF NOT EXISTS idx_relation_history_ts ON relation_history(ts);






















CREATE TABLE IF NOT EXISTS drama_threads (
    tid            TEXT PRIMARY KEY,
    title          TEXT NOT NULL DEFAULT '',            
    state          TEXT NOT NULL DEFAULT 'planted',     
    weight         REAL NOT NULL DEFAULT 1.0,
    opened_at      REAL NOT NULL DEFAULT 0,
    closed_at      REAL NOT NULL DEFAULT 0,
    paid_act       INTEGER NOT NULL DEFAULT 0,          
    meta           TEXT NOT NULL DEFAULT '{}',
    element        TEXT NOT NULL DEFAULT '',            
    visibility     TEXT NOT NULL DEFAULT '',            
    scope          TEXT NOT NULL DEFAULT '',            
    kind           TEXT NOT NULL DEFAULT '',            
    payoff_form    TEXT NOT NULL DEFAULT '',            
    planned_payoff TEXT NOT NULL DEFAULT '',            
    trigger_spec   TEXT NOT NULL DEFAULT '{}',          
    open_beat      INTEGER NOT NULL DEFAULT 0,          
    due_beats      INTEGER NOT NULL DEFAULT 0,          
    touch_n        INTEGER NOT NULL DEFAULT 0,          
    last_touch_at  REAL NOT NULL DEFAULT 0,
    abandoned_at   REAL NOT NULL DEFAULT 0,
    abandon_reason TEXT NOT NULL DEFAULT ''
);









CREATE TABLE IF NOT EXISTS tool_notes (
    tool_name  TEXT PRIMARY KEY,
    note       TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL DEFAULT 0
);














CREATE TABLE IF NOT EXISTS worldbook (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    title                  TEXT NOT NULL DEFAULT '',    
    text                   TEXT NOT NULL DEFAULT '',    
    keys                   TEXT NOT NULL DEFAULT '',    
    secondary              TEXT NOT NULL DEFAULT '',    
    selective              INTEGER NOT NULL DEFAULT 0,  
    selective_logic        TEXT NOT NULL DEFAULT 'and_any',
    constant               INTEGER NOT NULL DEFAULT 0,  
    order_value            INTEGER NOT NULL DEFAULT 100,  
    case_sensitive         INTEGER NOT NULL DEFAULT 0,
    match_whole_words      INTEGER NOT NULL DEFAULT 0,
    use_regex              INTEGER NOT NULL DEFAULT 0,
    probability            INTEGER NOT NULL DEFAULT 100,  
    group_name             TEXT NOT NULL DEFAULT '',    
    group_weight           REAL NOT NULL DEFAULT 1.0,
    use_group_scoring      INTEGER NOT NULL DEFAULT 0,  
    prioritize             INTEGER NOT NULL DEFAULT 0,  
    vectorized             INTEGER NOT NULL DEFAULT 0,  
    ignore_budget          INTEGER NOT NULL DEFAULT 0,  
    sticky                 INTEGER NOT NULL DEFAULT 0,  
    cooldown               INTEGER NOT NULL DEFAULT 0,  
    delay                  INTEGER NOT NULL DEFAULT 0,  
    scan_depth             INTEGER NOT NULL DEFAULT 0,  
    exclude_recursion      INTEGER NOT NULL DEFAULT 0,
    prevent_recursion      INTEGER NOT NULL DEFAULT 0,
    delay_until_recursion  INTEGER NOT NULL DEFAULT 0,
    position               TEXT NOT NULL DEFAULT 'l3',   
    enabled                INTEGER NOT NULL DEFAULT 1,
    created_at             REAL NOT NULL DEFAULT 0,
    updated_at             REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_worldbook_on ON worldbook(enabled, order_value);



CREATE VIRTUAL TABLE IF NOT EXISTS worldbook_fts USING fts5(
    text, content='worldbook', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS worldbook_ai AFTER INSERT ON worldbook BEGIN
  INSERT INTO worldbook_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS worldbook_ad AFTER DELETE ON worldbook BEGIN
  INSERT INTO worldbook_fts(worldbook_fts, rowid, text)
  VALUES('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS worldbook_au AFTER UPDATE ON worldbook BEGIN
  INSERT INTO worldbook_fts(worldbook_fts, rowid, text)
  VALUES('delete', old.id, old.text);
  INSERT INTO worldbook_fts(rowid, text) VALUES (new.id, new.text);
END;






CREATE TABLE IF NOT EXISTS worldbook_state (
    umo            TEXT NOT NULL,
    entry_id       INTEGER NOT NULL,
    last_fired_seq INTEGER NOT NULL DEFAULT 0,   
    sticky_until   INTEGER NOT NULL DEFAULT 0,   
    cooldown_until INTEGER NOT NULL DEFAULT 0,   
    PRIMARY KEY (umo, entry_id)
);


CREATE TABLE IF NOT EXISTS shared_life_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,
    subject         TEXT NOT NULL,
    text            TEXT NOT NULL,
    state           TEXT NOT NULL,
    basis           TEXT NOT NULL,
    movement        TEXT NOT NULL DEFAULT '',
    valid_until     REAL,
    source_umo      TEXT NOT NULL,
    source_entry_id INTEGER NOT NULL,
    life_id         TEXT NOT NULL,
    ts              REAL NOT NULL,
    related_id      INTEGER,
    superseded_by   INTEGER,
    UNIQUE(kind, subject, source_entry_id, basis)
);
CREATE INDEX IF NOT EXISTS idx_shared_life_events_active
    ON shared_life_events(state, kind, ts DESC);

"""


_CFG_GET = None


def set_cfg_getter(fn) -> None:
    global _CFG_GET
    _CFG_GET = fn


def _cfg(key: str, default):
    try:
        if _CFG_GET is None:
            return default
        v = _CFG_GET(key, default)
        return default if v is None else v
    except Exception:
        return default


def recall_pattern_cap() -> int:
    try:
        return max(4, int(_cfg("memory_recall_patterns", 24)))
    except Exception:
        return 24


_VEC_PENDING_SQL = {
    "entries": "SELECT id, text FROM entries "
               "WHERE kind IN ('received','delivered') ORDER BY id",
}


class Store:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()


        self._vec_sidecars = {
            "memories": _vectors.VecSidecar(db_path, "memories"),
            "person_memories": _vectors.VecSidecar(db_path, "person_memories"),


            "entries": _vectors.VecSidecar(db_path, "entries"),


            "worldbook": _vectors.VecSidecar(db_path, "worldbook"),
        }
        self._db = sqlite3.connect(str(db_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(_SCHEMA)

            self._db.execute("UPDATE pending SET status='uncertain' WHERE status='sending'")
            self._db.commit()
        self._migrate_fts_trigram()
        self._migrate_entries_life_id()
        self._migrate_entries_audience()
        self._migrate_memories_source()
        self._migrate_lives_title_pending()
        self._migrate_drama_threads_v2()
        self._migrate_drama_threads_act()
        self._migrate_relation_history_person_id()
        self._check_director_tables()

    def _check_director_tables(self) -> None:
        expected = {
            "storylets": {
                "sid", "kind", "enabled", "scope", "scene_id", "got_scene_id",
                "ending_name", "narrative", "condition", "depends_on", "k_turns",
                "priority", "weight", "effects", "eval_annot", "meta",
                "created_at", "updated_at",
            },
            "director_fires": {
                "id", "ts", "sid", "kind", "umo", "life_id", "turn_seq",
                "narr_index", "delivered", "narrative", "meta",
            },
            "drama_threads": {
                "tid", "title", "state", "weight", "opened_at", "closed_at", "meta",

                "paid_act",

                "element", "visibility", "scope", "kind", "payoff_form",
                "planned_payoff", "trigger_spec",
                "open_beat", "due_beats",
                "touch_n", "last_touch_at", "abandoned_at", "abandon_reason",
            },
            "relation_history": {
                "id", "ts", "umo", "affinity", "trust", "valence", "meta",

                "person_id",
            },
        }
        try:
            with self._lock:
                bad = []
                for table, want in expected.items():
                    cols = {r[1] for r in
                            self._db.execute(f"PRAGMA table_info({table})")}
                    if not cols:
                        logger.warning("[spectrum·导演] 表 %s 没建出来（schema 没生效？）",
                                       table)
                        bad.append(table)
                        continue
                    missing, extra = want - cols, cols - want
                    if missing or extra:
                        logger.warning(
                            "[spectrum·导演·形状不符] %s 缺 %s / 多 %s —— "
                            "多半是**同名的旧表被保留了**，读写会按错误的形状走",
                            table, sorted(missing) or "无", sorted(extra) or "无")
                        bad.append(table)
                    else:
                        logger.info("[spectrum·导演] 表 %s 形状正确（%d 列）",
                                    table, len(cols))
                if not bad:
                    logger.info("[spectrum·导演] 三张表就绪")
        except Exception:
            logger.exception("[spectrum·导演] 表形状检查失败（不影响启动）")

    def _migrate_lives_title_pending(self) -> None:
        try:
            with self._lock:
                cols = [r[1] for r in self._db.execute("PRAGMA table_info(lives)")]
                if not cols:
                    return
                if "title_pending" in cols:
                    return
                logger.info("[spectrum·生活线] 给 lives 加 title_pending 列…")
                self._db.execute(
                    "ALTER TABLE lives ADD COLUMN title_pending INTEGER NOT NULL DEFAULT 0")

                self._db.execute(
                    "UPDATE lives SET title_pending=1 "
                    "WHERE ended_at IS NOT NULL AND (title IS NULL OR title='')")
                self._db.commit()
        except Exception:
            logger.exception("[spectrum·生活线] lives.title_pending 迁移失败（不影响启动）")


    _THREAD_V2_COLUMNS: tuple[tuple[str, str], ...] = (
        ("element", "TEXT NOT NULL DEFAULT ''"),
        ("visibility", "TEXT NOT NULL DEFAULT ''"),
        ("scope", "TEXT NOT NULL DEFAULT ''"),
        ("kind", "TEXT NOT NULL DEFAULT ''"),
        ("payoff_form", "TEXT NOT NULL DEFAULT ''"),
        ("planned_payoff", "TEXT NOT NULL DEFAULT ''"),
        ("trigger_spec", "TEXT NOT NULL DEFAULT '{}'"),
        ("open_beat", "INTEGER NOT NULL DEFAULT 0"),
        ("due_beats", "INTEGER NOT NULL DEFAULT 0"),
        ("touch_n", "INTEGER NOT NULL DEFAULT 0"),
        ("last_touch_at", "REAL NOT NULL DEFAULT 0"),
        ("abandoned_at", "REAL NOT NULL DEFAULT 0"),
        ("abandon_reason", "TEXT NOT NULL DEFAULT ''"),
    )

    def _migrate_drama_threads_v2(self) -> None:
        try:
            with self._lock:
                cols = [r[1] for r in
                        self._db.execute("PRAGMA table_info(drama_threads)")]
                if not cols:
                    return
                todo = [(n, t) for n, t in self._THREAD_V2_COLUMNS if n not in cols]
                if todo:
                    logger.info("[spectrum·导演] 叙事线表升级：补 %d 列 %s",
                                len(todo), [n for n, _t in todo])
                    for name, decl in todo:
                        self._db.execute(
                            f"ALTER TABLE drama_threads ADD COLUMN {name} {decl}")

                for old, new in (("open", "planted"), ("closed", "paid")):
                    cur = self._db.execute(
                        "UPDATE drama_threads SET state=? WHERE state=?", (new, old))
                    if cur.rowcount:
                        logger.info("[spectrum·导演] 叙事线状态翻译：%s → %s（%d 行）",
                                    old, new, cur.rowcount)
                self._db.commit()
        except Exception:
            logger.exception("[spectrum·导演] 叙事线表迁移失败（不影响启动）")

    def _migrate_drama_threads_act(self) -> None:
        try:
            with self._lock:
                cols = [r[1] for r in
                        self._db.execute("PRAGMA table_info(drama_threads)")]
                if not cols:
                    return
                if "paid_act" not in cols:
                    logger.info("[spectrum·导演] 叙事线表加幕锚点 paid_act…")
                    self._db.execute(
                        "ALTER TABLE drama_threads ADD COLUMN"
                        " paid_act INTEGER NOT NULL DEFAULT 0")
                cur = self._db.execute(
                    "UPDATE drama_threads SET paid_act=1 WHERE state='paid' AND paid_act=0")
                if cur.rowcount:
                    logger.info("[spectrum·导演] 已回收的线归入第 1 幕：%d 行", cur.rowcount)
                self._db.commit()
        except Exception:
            logger.exception("[spectrum·导演] 叙事线幕锚点迁移失败（不影响启动）")

    def _migrate_relation_history_person_id(self) -> None:
        try:
            with self._lock:
                cols = [r[1] for r in
                        self._db.execute("PRAGMA table_info(relation_history)")]
                if not cols:
                    return
                if "person_id" not in cols:
                    logger.info("[spectrum·导演] 关系量时间线加 person_id 列…")
                    self._db.execute(
                        "ALTER TABLE relation_history ADD COLUMN"
                        " person_id TEXT NOT NULL DEFAULT ''")
                self._db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_relation_history_person"
                    " ON relation_history(person_id, ts)")
                self._db.commit()
        except Exception:
            logger.exception("[spectrum·导演] 关系量时间线迁移失败（不影响启动）")

    def _migrate_entries_life_id(self) -> None:
        try:
            with self._lock:
                cols = [r[1] for r in self._db.execute("PRAGMA table_info(entries)")]
                if "life_id" in cols:
                    return
                logger.info("[spectrum·生活线] 给 entries 加 life_id 列…")
                self._db.execute(
                    "ALTER TABLE entries ADD COLUMN life_id TEXT NOT NULL DEFAULT ''")
                self._db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_entries_life ON entries(life_id, ts)")
                self._db.commit()
        except Exception:
            logger.exception("[spectrum·生活线] entries.life_id 迁移失败（不影响启动）")

    def _migrate_entries_audience(self) -> None:
        try:
            with self._lock:
                cols = [r[1] for r in self._db.execute("PRAGMA table_info(entries)")]
                if "audience" in cols:
                    return
                logger.info("[spectrum·受众] 给 entries 加 audience 列…")
                self._db.execute(
                    "ALTER TABLE entries ADD COLUMN audience TEXT NOT NULL DEFAULT ''")
                self._db.execute(
                    "CREATE INDEX IF NOT EXISTS idx_entries_aud ON entries(audience)")
                self._db.commit()
        except Exception:
            logger.exception("[spectrum·受众] entries.audience 迁移失败（不影响启动）")

    def _migrate_memories_source(self) -> None:
        try:
            with self._lock:
                cols = [r[1] for r in self._db.execute("PRAGMA table_info(memories)")]
                if "source_entry_ids" in cols:
                    return
                logger.info("[spectrum·L3+] 给 memories 加 source_entry_ids 列…")
                self._db.execute(
                    "ALTER TABLE memories ADD COLUMN source_entry_ids TEXT NOT NULL DEFAULT ''")
                self._db.commit()
        except Exception:
            logger.exception("[spectrum·L3+] memories.source_entry_ids 迁移失败（不影响启动）")

    def close(self) -> None:
        with self._lock:
            try:
                self._db.close()
            except Exception:
                pass

    def _migrate_fts_trigram(self) -> None:
        try:
            with self._lock:
                row = self._db.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='memories_fts'"
                ).fetchone()
                if not row or ("trigram" in (row[0] or "")):
                    return
                logger.info("[spectrum·L3+] 检测到旧版 unicode61 FTS 表，迁移到 trigram…")
                self._db.execute("DROP TRIGGER IF EXISTS memories_ai")
                self._db.execute("DROP TRIGGER IF EXISTS memories_ad")
                self._db.execute("DROP TRIGGER IF EXISTS memories_au")
                self._db.execute("DROP TABLE IF EXISTS memories_fts")
                self._db.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
                    "text, content='memories', content_rowid='id', tokenize='trigram')"
                )

                self._db.execute(
                    "INSERT INTO memories_fts(rowid, text) SELECT id, text FROM memories"
                )
                self._db.execute(
                    "CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories "
                    "BEGIN INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text); END;"
                )
                self._db.execute(
                    "CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories "
                    "BEGIN INSERT INTO memories_fts(memories_fts, rowid, text) "
                    "VALUES('delete', old.id, old.text); END;"
                )
                self._db.execute(
                    "CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories "
                    "BEGIN INSERT INTO memories_fts(memories_fts, rowid, text) "
                    "VALUES('delete', old.id, old.text); "
                    "INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text); END;"
                )
                self._db.commit()
                logger.info("[spectrum·L3+] FTS 表已迁移到 trigram")
        except Exception:
            logger.exception("[spectrum·L3+] FTS trigram 迁移失败，保留原表")


    def add_entry(
        self,
        umo: str,
        kind: str,
        text: str,
        ts: float | None = None,
        meta: dict | None = None,
        life_id: str = "",
        audience=None,
    ) -> int:
        if not text:
            return -1


        if kind in ("script", "think", "delivered", "observed"):
            from .parse import strip_all_tags
            text = strip_all_tags(text)
            if not text:
                return -1


        from . import audience as _aud
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO entries(umo, ts, kind, text, meta, life_id, audience) "
                "VALUES(?,?,?,?,?,?,?)",
                (umo, ts if ts is not None else time.time(), kind, text,
                 json.dumps(meta or {}, ensure_ascii=False), life_id or "",
                 _aud.render(audience)),
            )
            self._db.commit()
            return int(cur.lastrowid or -1)

    def record_shared_life_event(
        self, event: dict, *, source_umo: str, source_entry_id: int,
        life_id: str, ts: float,
    ) -> int:
        from .shared_life import normalize_event
        item = normalize_event(event)
        related_id = item["related_id"]
        with self._lock:
            source_kind = {"script": "script", "reply": "delivered", "received": "received"}[item["basis"]]
            source = self._db.execute(
                "SELECT id FROM entries WHERE id=? AND umo=? AND kind=?",
                (int(source_entry_id), source_umo, source_kind),
            ).fetchone()
            if source is None:
                raise ValueError("生活事件没有匹配的真实来源记录")
            existing = self._db.execute(
                "SELECT id FROM shared_life_events WHERE kind=? AND subject=? "
                "AND source_entry_id=? AND basis=?",
                (item["kind"], item["subject"], int(source_entry_id),
                 item["basis"]),
            ).fetchone()
            if existing is not None:
                return int(existing["id"])
            if related_id is not None:
                prior = self._db.execute(
                    "SELECT kind, source_umo, superseded_by "
                    "FROM shared_life_events WHERE id=?",
                    (related_id,),
                ).fetchone()
                if prior is None or ((prior["kind"] == "commitment") != (item["kind"] == "commitment")):
                    raise ValueError("生活事件引用了不存在或不同类型的旧记录")
                if (item["kind"] == "commitment"
                        and prior["source_umo"] != source_umo):
                    raise ValueError("承诺只能在原会话向对方更新")
                if prior["superseded_by"] is not None:
                    raise ValueError("生活事件已被另一轮更新")
            with self._db:
                cur = self._db.execute(
                    "INSERT INTO shared_life_events"
                    "(kind, subject, text, state, basis, movement, valid_until, "
                    "source_umo, source_entry_id, life_id, ts, related_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item["kind"], item["subject"], item["text"], item["state"],
                     item["basis"], item["movement"], item["valid_until"],
                     source_umo, int(source_entry_id), life_id, float(ts),
                     related_id),
                )
                eid = int(cur.lastrowid)
                if related_id is not None:
                    updated = self._db.execute(
                        "UPDATE shared_life_events SET superseded_by=? "
                        "WHERE id=? AND superseded_by IS NULL",
                        (eid, related_id),
                    )
                    if updated.rowcount != 1:
                        raise ValueError("生活事件并发更新冲突")
            return eid

    def shared_life_event(self, event_id: int):
        with self._lock:
            return self._db.execute(
                "SELECT * FROM shared_life_events WHERE id=?", (int(event_id),)
            ).fetchone()

    def cross_send_receipts(self, umo: str, ts: float):
        with self._lock:
            rows = self._db.execute(
                "SELECT id,text,meta FROM entries WHERE umo=? AND kind='delivered' "
                "AND ts>? ORDER BY ts", (umo, float(ts))
            ).fetchall()
        return [row for row in rows
                if json.loads(row["meta"] or "{}").get("cross_send_from")]

    def shared_life_events(self, life_id: str, *, limit: int = 16, query: str = ""):
        """经历属于同一个人，不随生活段清空；有效承诺、未结束经历及相关旧事分配独立位置。"""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM shared_life_events WHERE superseded_by IS NULL "
                "AND ((kind IN ('physical','knowledge') AND "
                "(life_id=? OR (state='active' AND (valid_until IS NULL OR valid_until>?)))) "
                "OR (kind='commitment' AND state='active')) "
                "ORDER BY CASE WHEN kind='commitment' THEN 0 WHEN state='active' THEN 1 ELSE 2 END, ts DESC, id DESC LIMIT ?",
                (life_id, time.time(), max(128, int(limit))),
            ).fetchall()
            relevant = []
            from .experience import topic_keys
            keys = topic_keys(query, recall_pattern_cap()) if query else []
            if keys:
                matches = self._db.execute(
                    "SELECT * FROM shared_life_events WHERE superseded_by IS NULL AND (" +
                    " OR ".join("(subject LIKE ? OR text LIKE ?)" for _ in keys) +
                    ") ORDER BY ts DESC, id DESC LIMIT 128",
                    [value for key in keys for value in ("%" + key + "%", "%" + key + "%")],
                ).fetchall()
                matches = [r for r in matches if r['kind'] != 'commitment' or r['state'] == 'active']
                matches.sort(key=lambda r: sum(len(k) for k in keys if k in r['subject'] + r['text']), reverse=True)
                relevant = matches[:max(1, limit // 3)]
            commitments = [r for r in rows if r['kind'] == 'commitment']
            selected = {}
            for row in commitments + relevant + rows:
                if len(selected) >= limit:
                    break
                selected[row['id']] = row
            return sorted(selected.values(), key=lambda r: (r['ts'], r['id']), reverse=True)

    def shared_event_chain(self, event_id: int):
        """沿旧事件引用还原同一经历的发生和认知更新，保留各自时间。"""
        with self._lock:
            rows = []
            row = self.shared_life_event(event_id)
            while row is not None:
                rows.append(row)
                row = self.shared_life_event(row['related_id']) if row['related_id'] else None
            return list(reversed(rows))

    def recall_cross_session(self, umo: str, query: str, *, limit: int = 2,
                             budget: int = 1200, radius: int = 2):
        """按人物或话题召回其他会话原话；邻接片段始终限定在同一个来源会话。"""
        if not query.strip() or limit <= 0 or budget <= 0:
            return []
        from .person import unambiguous_aliases
        aliases = unambiguous_aliases(self.all_person_aliases(), umo.split(':', 1)[0])
        people = {pid for alias, pid in aliases if len(alias) >= 2 and alias.casefold() in query.casefold()}
        named = set()
        for pid in people:
            named.update(r['umo'] for r in self.list_person_sessions(pid))
            named.add(pid.split(':', 1)[0] + ':FriendMessage:' + pid.split(':', 1)[1])
        from .experience import topic_keys
        keys = topic_keys(query, recall_pattern_cap())
        conditions = ["text LIKE ?" for _ in keys] + ["umo=?" for _ in named]
        if not conditions:
            return []
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM entries WHERE umo<>? AND kind IN ('received','delivered') AND (" +
                " OR ".join(conditions) + ") ORDER BY ts DESC,id DESC LIMIT 160",
                [umo] + ['%' + k + '%' for k in keys] + sorted(named),
            ).fetchall()
            # 主题反复出现不降低其价值；人物点名也可唤起那个人的往来。
            scored = []
            for row in rows:
                hits = [k for k in keys if k in row['text'].casefold()]
                if not hits and row['umo'] not in named:
                    continue
                score = sum(len(k) ** 2 for k in hits) + (4 if row['umo'] in named else 0)
                if row['kind'] == 'received':
                    score += 0.5
                scored.append((score, row['ts'], row['id'], row))
            scored.sort(key=lambda item: item[:3], reverse=True)
            out, covered = [], set()
            for _, _, _, anchor in scored:
                if len(out) >= limit:
                    break
                if anchor['id'] in covered:
                    continue
                params = (anchor['umo'], anchor['id'], radius)
                before = self._db.execute("SELECT * FROM entries WHERE umo=? AND id<? AND kind IN ('received','delivered') ORDER BY id DESC LIMIT ?", params).fetchall()
                after = self._db.execute("SELECT * FROM entries WHERE umo=? AND id>? AND kind IN ('received','delivered') ORDER BY id LIMIT ?", params).fetchall()
                segment = list(reversed(before)) + [anchor] + list(after)
                lo, hi, cut = _recall.pick_segment([r['text'] for r in segment], len(before), budget, radius)
                items = [dict(r) for r in segment[lo:hi]]
                if len(items) == 1 and len(items[0]['text']) > budget:
                    items[0]['text'] = items[0]['text'][:budget]
                for item in items:
                    item['sender'] = str(self.parse_meta(item['meta']).get('sender') or '')
                covered.update(r['id'] for r in items)
                out.append({'source_umo': anchor['umo'], 'anchor_id': anchor['id'], 'truncated': cut,
                            'items': items})
            return out

    def observed_rows(self, *, limit: int = 2) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT id, ts, text, audience FROM entries WHERE kind='observed' "
                "ORDER BY ts DESC, id DESC LIMIT ?", (int(limit),)).fetchall()

    def upsert_observed(self, text: str, *, umo: str, life_id: str = "",
                        audience=None) -> int:
        t = (text or "").strip()
        if not t:
            return -1
        key = _memory.normalize_fact(t) or t
        with self._lock:
            sql = "SELECT id, text FROM entries WHERE kind='observed'"
            params: list = []
            if life_id:
                sql += " AND life_id=?"
                params.append(life_id)
            for r in self._db.execute(sql, params).fetchall():
                if (_memory.normalize_fact(r["text"]) or r["text"]) == key:
                    self._db.execute("UPDATE entries SET ts=? WHERE id=?",
                                     (time.time(), int(r["id"])))
                    self._db.commit()
                    return int(r["id"])
        return self.add_entry(umo, "observed", t, life_id=life_id, audience=audience)

    def delete_observed(self, mode: str, *, keep: int = 3) -> int:
        order = ("SELECT id FROM entries WHERE kind='observed' "
                 "ORDER BY ts DESC, id DESC")
        with self._lock:
            if mode == "all":
                cur = self._db.execute(
                    "DELETE FROM entries WHERE kind='observed'")
            elif mode == "recent":
                cur = self._db.execute(
                    "DELETE FROM entries WHERE id IN (" + order + " LIMIT ?)",
                    (int(keep),))
            elif mode == "history":
                cur = self._db.execute(
                    "DELETE FROM entries WHERE kind='observed' AND id NOT IN ("
                    + order + " LIMIT ?)", (int(keep),))
            else:
                raise ValueError("未知的删除模式：%r" % (mode,))
            self._db.commit()
            return int(cur.rowcount or 0)

    def update_entry_text(self, eid: int, text: str) -> int:
        with self._lock:
            cur = self._db.execute("UPDATE entries SET text=? WHERE id=?", (text, eid))
            self._db.commit()


        self._drop_vectors("entries", [int(eid)])
        return cur.rowcount

    def last_write_ts(self, umo: str) -> float | None:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM state WHERE umo=? AND key='last_write_ts'",
                (umo,),
            ).fetchone()
        if not row:
            return None
        try:
            return float(row["value"])
        except (TypeError, ValueError):
            return None

    def set_last_write_ts(self, umo: str, ts: float) -> None:
        self.set_state(umo, "last_write_ts", repr(ts))


    def get_continuity_struct(self, umo: str) -> dict:
        raw = self.get_state(umo, "continuity", "") or ""
        if not raw:
            return {}
        try:
            d = json.loads(raw)
            if isinstance(d, dict):
                return d
        except Exception:
            pass
        return {"state": raw}

    def set_continuity_struct(self, umo: str, d: dict) -> None:
        self.set_state(umo, "continuity",
                       json.dumps(d, ensure_ascii=False) if d else "")

    def get_continuity(self, umo: str) -> str:
        d = self.get_continuity_struct(umo)
        if not d:
            return ""
        parts = []
        if d.get("state"):
            parts.append(str(d["state"]))


        if d.get("facts"):
            parts.append("【这些是真发生过的】\n"
                         + "\n".join(f"· {x}" for x in d["facts"]))
        if d.get("views"):

            parts.append("【这些只是我当时以为的，不一定是真的】\n"
                         + "\n".join(f"· {x}" for x in d["views"]))
        return "\n\n".join(parts)

    def set_continuity(self, umo: str, text: str) -> None:
        self.set_continuity_struct(umo, {"state": text} if (text or "").strip() else {})


    def get_willingness(self, umo: str) -> float:
        try:
            return float(self.get_state(umo, "willingness", "0.0"))
        except (TypeError, ValueError):
            return 0.0

    def set_willingness(self, umo: str, score: float) -> None:
        self.set_state(umo, "willingness", repr(score))


    def last_summary_ts(self, umo: str) -> float:
        try:
            return float(self.get_state(umo, "last_summary_ts", "0.0"))
        except (TypeError, ValueError):
            return 0.0

    def set_last_summary_ts(self, umo: str, ts: float) -> None:
        self.set_state(umo, "last_summary_ts", repr(ts))

    def get_volatile_changes(self, umo: str) -> list[str]:
        raw = self.get_state(umo, "volatile_changes", "[]")
        try:
            return json.loads(raw)
        except Exception:
            return []

    def add_volatile_change(self, umo: str, text: str) -> None:
        changes = self.get_volatile_changes(umo)
        changes.append(text)

        if len(changes) > 10:
            changes = changes[-10:]
        self.set_state(umo, "volatile_changes", json.dumps(changes, ensure_ascii=False))

    def clear_volatile_changes(self, umo: str, consumed: list[str] | None = None) -> None:
        with self._lock:
            changes = [] if consumed is None else self.get_volatile_changes(umo)
            for text in consumed or []:
                if text in changes:
                    changes.remove(text)
            self.set_state(umo, "volatile_changes", json.dumps(changes, ensure_ascii=False))

    def recent_exchange(self, umo: str, turns: int = 12) -> list[sqlite3.Row]:
        with self._lock:
            floor = self._db.execute(
                "SELECT MIN(ts) FROM (SELECT ts FROM entries "
                "WHERE umo=? AND kind='received' ORDER BY ts DESC, id DESC LIMIT ?)",
                (umo, turns),
            ).fetchone()[0]
            if floor is None:
                floor = self._db.execute(
                    "SELECT MIN(ts) FROM (SELECT ts FROM entries WHERE umo=? AND kind='delivered' "
                    "ORDER BY ts DESC,id DESC LIMIT ?)", (umo, turns)).fetchone()[0]
            return self._db.execute(
                "SELECT id, kind, text, meta FROM entries "
                "WHERE umo=? AND kind IN ('received','delivered') AND ts >= ? "
                "ORDER BY ts DESC, id DESC",
                (umo, floor),
            ).fetchall()


    _PLACEHOLDER_SENDERS = ("某人", "对方", "群友", "")

    @staticmethod
    def parse_meta(raw) -> dict:
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str) or not raw.strip():
            return {}
        try:
            d = json.loads(raw)
        except Exception:
            return {}
        return d if isinstance(d, dict) else {}

    def recent_senders(self, umo: str, limit: int = 60) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT meta FROM entries WHERE umo=? AND kind='received' "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                (umo, limit),
            ).fetchall()
        names: list[str] = []
        for r in rows:
            sender = str(self.parse_meta(r["meta"]).get("sender") or "").strip()
            if sender in self._PLACEHOLDER_SENDERS or sender in names:
                continue
            names.append(sender)
        return names

    def known_sessions(self, exclude: str | None = None) -> list[dict]:
        out: list[dict] = []
        for umo in self.list_umos():
            if exclude and umo == exclude:
                continue
            if "GroupMessage" in umo:
                g_name = self.get_state(umo, "group_name")
                out.append({
                    "session": umo,
                    "kind": "group",
                    "label": g_name or umo.split(":")[-1],
                })
            else:
                out.append({
                    "session": umo,
                    "kind": "private",
                    "label": self.last_sender_of(umo) or umo.split(":")[-1],
                })
        return out

    def last_sender_of(self, umo: str) -> str:
        with self._lock:
            row = self._db.execute(
                "SELECT meta FROM entries WHERE umo=? AND kind='received' "
                "ORDER BY ts DESC, id DESC LIMIT 1",
                (umo,),
            ).fetchone()
        if not row:
            return ""
        sender = str(self.parse_meta(row["meta"]).get("sender") or "").strip()
        return "" if sender in self._PLACEHOLDER_SENDERS else sender

    def recent_script_rows(self, umo: str, limit: int = 3,
                           viewer: str | None = None, life_id: str = "") -> list[tuple[int, str]]:
        from . import audience as _aud
        sql = "SELECT id, text FROM entries WHERE umo=? AND kind='script' "
        args: list = [umo]
        if life_id:
            sql += "AND life_id=? "
            args.append(life_id)
        if viewer is not None:
            sql += f"AND {_aud.sql_admit()} "
            args.append(_aud.sql_param(viewer))
        sql += "ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, tuple(args)).fetchall()
        return [(int(r["id"]), r["text"]) for r in reversed(rows)]

    def recent_script_rows_by_life(self, life_id: str, limit: int = 3,
                                   viewer: str | None = None) -> list[tuple[int, str]]:
        if not life_id:
            return []
        from . import audience as _aud
        sql = ("SELECT id, text FROM entries WHERE life_id=? AND kind='script' ")
        args: list = [life_id]
        if viewer is not None:
            sql += f"AND {_aud.sql_admit()} "
            args.append(_aud.sql_param(viewer))
        sql += "ORDER BY ts DESC, id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, tuple(args)).fetchall()
        return [(int(r["id"]), r["text"]) for r in reversed(rows)]

    def recent_script_window_ids(self, umo: str = "", life_id: str = "",
                                 limit: int = 3) -> list[int]:
        col, val = ("life_id", life_id) if life_id else ("umo", umo)
        if not val:
            return []
        scope = " AND umo=?" if life_id and umo else ""
        args = (val, umo, limit) if scope else (val, limit)
        with self._lock:
            rows = self._db.execute(
                f"SELECT id FROM entries WHERE {col}=? AND kind='script'{scope} "
                "ORDER BY ts DESC, id DESC LIMIT ?",
                args,
            ).fetchall()
        return [int(r["id"]) for r in reversed(rows)]

    def recent_scripts(self, umo: str, limit: int = 3,
                       viewer: str | None = None, life_id: str = "") -> list[str]:
        return [t for _, t in self.recent_script_rows(umo, limit=limit, viewer=viewer, life_id=life_id)]

    def recent_scripts_by_life(self, life_id: str, limit: int = 3,
                               viewer: str | None = None) -> list[str]:
        return [t for _, t in
                self.recent_script_rows_by_life(life_id, limit=limit, viewer=viewer)]

    def get_scripts_since(self, umo: str, ts: float) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT text FROM entries WHERE umo=? AND kind='script' AND ts > ? "
                "ORDER BY ts ASC",
                (umo, ts),
            ).fetchall()
        return [r["text"] for r in rows]


    def add_pending(self, umo: str, due_ts: float, content: str, origin: str = "", *,
                    communication: dict | None = None, events: list[dict] | None = None) -> int:
        from .shared_life import normalize_event
        staged = [event for event in events or [] if event.get("basis") == "reply"]
        for event in staged:
            normalize_event(event)
        events_json = json.dumps(staged, ensure_ascii=False) if staged else None
        communication_json = json.dumps(communication, ensure_ascii=False) if communication else None
        with self._lock, self._db:
            cur = self._db.execute(
                "INSERT INTO pending(umo, due_ts, content, created_ts, status, origin) "
                "VALUES(?,?,?,?, 'waiting', ?)",
                (umo, due_ts, content, time.time(), origin),
            )
            pending_id = int(cur.lastrowid or -1)
            if staged:
                self._db.execute("INSERT INTO pending_life_events VALUES(?,?)",
                                 (pending_id, events_json))
            if communication:
                self._db.execute("INSERT INTO pending_communication VALUES(?,?)",
                                 (pending_id, communication_json))
            return pending_id

    def pending_communication(self, pending_id: int) -> dict:
        with self._lock:
            row = self._db.execute("SELECT payload FROM pending_communication WHERE pending_id=?",
                                   (pending_id,)).fetchone()
        return json.loads(row["payload"]) if row else {}

    def clear_pending_communication(self, pending_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM pending_communication WHERE pending_id=?", (pending_id,))
            self._db.commit()

    def due_pending(self, now_ts: float | None = None) -> list[sqlite3.Row]:
        from . import freeze
        if freeze.is_frozen():
            return []
        now = time.time() if now_ts is None else now_ts
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM pending WHERE status='waiting' AND due_ts <= ? ORDER BY due_ts ASC",
                (now,),
            ).fetchall()
        return [row for row in rows if freeze.elapsed_since(row["created_ts"], now)
                >= max(0.0, row["due_ts"] - row["created_ts"])]

    def claim_pending(self, pid: int, now: float) -> dict | None:
        from . import freeze
        with self._lock, self._db:
            row = self._db.execute("SELECT * FROM pending WHERE id=? AND status='waiting'", (pid,)).fetchone()
            if row is None or freeze.is_frozen() or row["due_ts"] > now:
                return None
            if freeze.elapsed_since(row["created_ts"], now) < max(0.0, row["due_ts"] - row["created_ts"]):
                return None
            claimed = dict(row)
            claimed["communication"] = self.pending_communication(pid)
            claimed["events"] = self.pending_life_events(pid)
            self._db.execute("UPDATE pending SET status='sending' WHERE id=?", (pid,))
            return claimed

    def retry_uncertain_pending(self, pid: int) -> bool:
        with self._lock, self._db:
            cur = self._db.execute("UPDATE pending SET status='waiting' WHERE id=? AND status='uncertain'", (pid,))
            return bool(cur.rowcount)

    def stage_pending_life_events(self, pending_id: int, events: list[dict]) -> None:
        from .shared_life import normalize_event
        staged = [event for event in events if event.get("basis") == "reply"]
        for event in staged:
            normalize_event(event)
        if not staged:
            return
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO pending_life_events(pending_id,events) VALUES(?,?)",
                (pending_id, json.dumps(staged, ensure_ascii=False)),
            )
            self._db.commit()

    def pending_life_events(self, pending_id: int) -> list[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT events FROM pending_life_events WHERE pending_id=?",
                (pending_id,),
            ).fetchone()
        return json.loads(row["events"]) if row else []

    def clear_pending_life_events(self, pending_id: int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM pending_life_events WHERE pending_id=?",
                             (pending_id,))
            self._db.commit()

    def cancel_pending_for(self, umo: str) -> int:
        with self._lock:
            self._db.execute(
                "DELETE FROM pending_life_events WHERE pending_id IN "
                "(SELECT id FROM pending WHERE umo=? AND status='waiting')",
                (umo,),
            )
            self._db.execute(
                "DELETE FROM pending_communication WHERE pending_id IN "
                "(SELECT id FROM pending WHERE umo=? AND status='waiting')", (umo,))
            cur = self._db.execute(
                "UPDATE pending SET status='cancelled' "
                "WHERE umo=? AND status='waiting'",
                (umo,),
            )
            self._db.commit()
            return cur.rowcount or 0

    def mark_pending(self, pid: int, status: str) -> bool:
        with self._lock, self._db:
            if status == "cancelled":
                cur = self._db.execute("UPDATE pending SET status=? WHERE id=? AND status IN ('waiting','uncertain')", (status, pid))
            else:
                cur = self._db.execute("UPDATE pending SET status=? WHERE id=?", (status, pid))
            if not cur.rowcount:
                return False
            if status == "cancelled":
                self._db.execute("DELETE FROM pending_life_events WHERE pending_id=?", (pid,))
                self._db.execute("DELETE FROM pending_communication WHERE pending_id=?", (pid,))
            return True

    def update_pending(self, pid: int, *, content: str | None = None,
                       due_ts: float | None = None) -> bool:
        with self._lock, self._db:
            row = self._db.execute("SELECT content,status FROM pending WHERE id=?", (pid,)).fetchone()
            if row is None or row["status"] == "sending":
                return False
            if content is not None:
                if row and row["content"] != content:
                    self._db.execute("DELETE FROM pending_life_events WHERE pending_id=?", (pid,))
                    self._db.execute("DELETE FROM pending_communication WHERE pending_id=?", (pid,))
                self._db.execute("UPDATE pending SET content=? WHERE id=?", (content, pid))
            if due_ts is not None:
                self._db.execute("UPDATE pending SET due_ts=? WHERE id=?", (due_ts, pid))
            return True

    def waiting_count(self, umo: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS c FROM pending WHERE umo=? AND status='waiting'",
                (umo,),
            ).fetchone()
        return int(row["c"]) if row else 0


    def add_intent(self, umo: str, kind: str, summary: str, not_before: float,
                   meta: dict | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO intents(umo, kind, summary, not_before, created_ts, status, meta) "
                "VALUES(?,?,?,?,?, 'waiting', ?)",
                (umo, kind, summary, not_before, time.time(),
                 json.dumps(meta or {}, ensure_ascii=False)),
            )
            self._db.commit()
            return int(cur.lastrowid or -1)

    def due_intents(self, now_ts: float | None = None) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM intents WHERE status='waiting' AND not_before <= ? "
                "ORDER BY not_before ASC",
                (now_ts if now_ts is not None else time.time(),),
            ).fetchall()

    def set_intent_status(self, iid: int, status: str) -> int:
        with self._lock:
            cur = self._db.execute(
                "UPDATE intents SET status=? WHERE id=?", (status, iid)
            )
            self._db.commit()
            return cur.rowcount

    def next_due_intent(self, now_ts: float | None = None) -> int | None:
        with self._lock:
            row = self._db.execute(
                "SELECT id FROM intents WHERE status='waiting' AND not_before > ? "
                "ORDER BY not_before ASC LIMIT 1",
                (now_ts if now_ts is not None else time.time(),),
            ).fetchone()
        return int(row["id"]) if row else None

    def count_waiting_intents(self, umo: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS c FROM intents WHERE umo=? AND status='waiting'",
                (umo,),
            ).fetchone()
        return int(row["c"]) if row else 0

    def list_intents(self, umo: str, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM intents WHERE umo=? ORDER BY not_before ASC LIMIT ?",
                (umo, limit),
            ).fetchall()

    def list_pending(self, umo: str, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM pending WHERE umo=? ORDER BY due_ts ASC LIMIT ?",
                (umo, limit),
            ).fetchall()


    def list_all_intents(self, limit: int = 200, offset: int = 0,
                         status: str | None = None,
                         umo: str | None = None) -> list[sqlite3.Row]:
        where, params = [], []
        if status:
            where.append("status=?")
            params.append(status)
        if umo:
            where.append("umo=?")
            params.append(umo)
        sql = "SELECT * FROM intents"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY not_before ASC, id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def list_all_pending(self, limit: int = 200, offset: int = 0,
                         status: str | None = None,
                         umo: str | None = None) -> list[sqlite3.Row]:
        where, params = [], []
        if status == "active":
            where.append("status IN ('waiting','sending','uncertain')")
        elif status:
            where.append("status=?")
            params.append(status)
        if umo:
            where.append("umo=?")
            params.append(umo)
        sql = "SELECT * FROM pending"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY due_ts ASC, id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def list_all_entries(self, limit: int = 200, offset: int = 0,
                         kind: str | None = None,
                         umo: str | None = None) -> list[sqlite3.Row]:
        where, params = [], []
        if kind:
            where.append("kind=?")
            params.append(kind)
        if umo:
            where.append("umo=?")
            params.append(umo)
        sql = "SELECT * FROM entries"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def entries_by_ids(self, ids: list[int]) -> list[sqlite3.Row]:
        wanted: list[int] = []
        for i in ids or []:
            s = str(i).strip()
            if s.isdigit() and int(s) not in wanted:
                wanted.append(int(s))
        if not wanted:
            return []
        wanted = wanted[:200]
        ph = ",".join("?" * len(wanted))
        with self._lock:
            return self._db.execute(
                f"SELECT id, umo, ts, kind, text, meta FROM entries "
                f"WHERE id IN ({ph}) ORDER BY ts ASC, id ASC",
                wanted,
            ).fetchall()

    def count_rows(self, table: str, umo: str | None = None) -> int:
        if table not in ("intents", "pending", "entries", "memories"):
            return 0
        with self._lock:
            if umo:
                row = self._db.execute(
                    f"SELECT COUNT(*) AS c FROM {table} WHERE umo=?", (umo,)
                ).fetchone()
            else:
                row = self._db.execute(
                    f"SELECT COUNT(*) AS c FROM {table}"
                ).fetchone()
        return int(row["c"]) if row else 0

    def list_umos(self) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT DISTINCT umo FROM entries "
                "UNION SELECT DISTINCT umo FROM intents "
                "UNION SELECT DISTINCT umo FROM pending ORDER BY umo"
            ).fetchall()
        return [r["umo"] for r in rows]

    def reschedule_intent(self, iid: int, not_before: float) -> int:
        with self._lock:
            cur = self._db.execute(
                "UPDATE intents SET not_before=?, status='waiting' WHERE id=?",
                (not_before, iid),
            )
            self._db.commit()
            return cur.rowcount

    def last_activity_ts(self, umo: str) -> float | None:
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(ts) AS m FROM entries WHERE umo=? "
                "AND kind IN ('received','delivered')",
                (umo,),
            ).fetchone()
        return float(row["m"]) if row and row["m"] is not None else None


    def add_factual_memory(self, umo: str, text: str, importance: float = 0.5) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO memories(umo, text, importance, ts) VALUES(?,?,?,?)",
                (umo, text, importance, time.time()),
            )
            self._db.commit()
            return int(cur.lastrowid or -1)


    def _memory_rows(self, umo: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT id, text, source_entry_ids FROM memories WHERE umo=?", (umo,)
            ).fetchall()

    def list_promise_memories(self, umo: str) -> list[tuple[int, str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, text FROM memories WHERE umo=? AND text LIKE ? ORDER BY id",
                (umo, _memory.PROMISE_PREFIX + "%"),
            ).fetchall()
        return [(int(r["id"]), str(r["text"])) for r in rows]

    def _memory_refresh(self, mid: int, ts: float, source_ids: str,
                        text: str | None = None) -> int:
        with self._lock:
            if text is None:
                cur = self._db.execute(
                    "UPDATE memories SET ts=?, source_entry_ids=? WHERE id=?",
                    (ts, source_ids, mid),
                )
            else:
                cur = self._db.execute(
                    "UPDATE memories SET ts=?, source_entry_ids=?, text=? WHERE id=?",
                    (ts, source_ids, text, mid),
                )
            self._db.commit()
            return int(cur.rowcount)

    def _memory_insert(self, umo: str, text: str, importance: float,
                       ts: float, source_ids: str) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO memories(umo, text, importance, ts, source_entry_ids) "
                "VALUES(?,?,?,?,?)",
                (umo, text, importance, ts, source_ids),
            )
            self._db.commit()
            return int(cur.lastrowid or -1)

    def upsert_factual_memory(self, umo: str, text: str,
                              source_entry_ids: "list[int] | None" = None) -> str:
        return _memory.upsert_factual_memory(self, umo, text,
                                             source_entry_ids=source_entry_ids)

    def update_factual_memory(self, mid: int, text: str, importance: float | None = None) -> int:
        sql = "UPDATE memories SET text=?"
        params = [text]
        if importance is not None:
            sql += ", importance=?"
            params.append(importance)
        sql += " WHERE id=?"
        params.append(mid)
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()


        self._drop_vectors("memories", [mid])
        return cur.rowcount

    def list_all_memories(self, limit: int = 100, offset: int = 0,
                          umo: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM memories"
        params = []
        if umo:
            sql += " WHERE umo=?"
            params.append(umo)
        sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def delete_memory(self, mid: int) -> int:
        with self._lock:
            cur = self._db.execute("DELETE FROM memories WHERE id=?", (mid,))
            self._db.commit()


        self._drop_vectors("memories", [mid])
        return cur.rowcount

    def _drop_vectors(self, table: str, ids: "list[int]") -> None:
        sc = self._vec_sidecars.get(table)
        if sc is None:
            return
        try:
            sc.drop([int(i) for i in ids])
        except Exception:
            logger.exception("[spectrum·向量] 删除 %s 的向量失败（关键词召回不受影响）", table)

    def get_core_memories(self, umo: str, limit: int = 15) -> list[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT text FROM memories WHERE umo=? ORDER BY importance DESC, ts DESC LIMIT ?",
                (umo, limit),
            ).fetchall()
        return [r["text"] for r in rows]

    def list_core_memories(self, umo: str) -> list[tuple[int, str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, text FROM memories WHERE umo=? ORDER BY id ASC", (umo,)
            ).fetchall()
        return [(int(r["id"]), r["text"]) for r in rows]

    def search_factual_memories(self, umo: str, query: str, limit: int = 5,
                                qvec=None) -> list[str]:
        if not query or not query.strip():
            return []

        clean_query = "".join(c if c.isalnum() else " " for c in query).strip()
        if not clean_query:
            return []

        results: list[str] = []
        seen: set[str] = set()
        with self._lock:

            try:
                rows = self._db.execute(
                    "SELECT m.text FROM memories m JOIN memories_fts f ON m.id = f.rowid "
                    "WHERE m.umo=? AND memories_fts MATCH ? ORDER BY rank LIMIT ?",
                    (umo, clean_query, limit)
                ).fetchall()
                for r in rows:
                    if r["text"] not in seen:
                        seen.add(r["text"])
                        results.append(r["text"])
            except sqlite3.OperationalError:
                pass


            if len(results) < limit:


                uniq = _recall.recall_keys(clean_query, recall_pattern_cap())
                if uniq:
                    like = " OR ".join(["(text LIKE ?)"] * len(uniq))


                    score = " + ".join(["(text LIKE ?)"] * len(uniq))
                    params: list = ([f"%{t}%" for t in uniq]
                                    + [umo] + [f"%{t}%" for t in uniq] + [limit])
                    try:
                        rows = self._db.execute(
                            f"SELECT text, ({score}) AS _score FROM memories "
                            f"WHERE umo=? AND ({like}) "
                            "ORDER BY _score DESC, importance DESC, ts DESC LIMIT ?",
                            params,
                        ).fetchall()
                        for r in rows:
                            if len(results) >= limit:
                                break
                            if r["text"] not in seen:
                                seen.add(r["text"])
                                results.append(r["text"])
                    except sqlite3.OperationalError:
                        pass


            if qvec is not None and len(results) < limit:
                ids = self._vec_search("memories", qvec, limit + len(results))
                if ids:
                    try:
                        ph = ",".join("?" * len(ids))
                        with self._lock:
                            rows = self._db.execute(
                                f"SELECT id, text FROM memories WHERE umo=? AND id IN ({ph})",
                                [umo] + ids).fetchall()
                        by_id = {int(r["id"]): r["text"] for r in rows}
                        for i in ids:
                            t = by_id.get(i)
                            if t and t not in seen:
                                seen.add(t)
                                results.append(t)
                                if len(results) >= limit:
                                    break
                    except Exception:
                        logger.exception("[spectrum·向量] 合并结果失败（已保留关键词结果）")
        return results[:limit]


    def vector_status(self) -> dict:
        out = {}
        for t, sc in self._vec_sidecars.items():
            sc.load()
            out[t] = 0 if sc.ids is None else int(len(sc.ids))
        return out

    def pending_vectors(self, table: str, limit: int = 500) -> list[tuple[int, str]]:
        sc = self._vec_sidecars.get(table)
        if sc is None:
            return []
        try:
            sql = _VEC_PENDING_SQL.get(
                table, f"SELECT id, text FROM {table} ORDER BY id")
            with self._lock:
                rows = self._db.execute(sql).fetchall()
        except Exception:
            return []
        return sc.missing([(int(r["id"]), str(r["text"] or "")) for r in rows])[:limit]

    def prune_vectors(self, table: str) -> int:
        sc = self._vec_sidecars.get(table)
        if sc is None:
            return 0
        try:
            with self._lock:
                live = {int(r[0]) for r in self._db.execute(f"SELECT id FROM {table}")}
        except Exception:
            return 0
        return sc.prune(live)

    def add_vectors(self, table: str, pairs: list[tuple[int, list[float]]]) -> int:
        sc = self._vec_sidecars.get(table)
        return sc.add(pairs) if sc else 0

    def _vec_search(self, table: str, qvec, k: int = 0) -> list[int]:
        if qvec is None or not _cfg("memory_vector_enabled", True):
            return []
        sc = self._vec_sidecars.get(table)
        if sc is None:
            return []
        n = int(k) if int(k) > 0 else int(_cfg("memory_vector_topk", 6))
        return sc.search(qvec, n)

    def memories_for_prompt(self, umo: str, query: str = "", *,
                            recent_n: int = 4, related_n: int = 8,
                            qvec=None) -> list[str]:
        recent = self.get_core_memories(umo, limit=max(0, int(recent_n)))
        if related_n <= 0 or not str(query or "").strip():
            return recent
        out = list(recent)
        seen = set(recent)

        want = len(out) + int(related_n)

        for t in self.search_factual_memories(umo, query, limit=max(want * 2, want), qvec=qvec):
            if len(out) >= want:
                break
            if t not in seen:
                seen.add(t)
                out.append(t)
        return out

    def _origin_exclude_ids(self, umo: str) -> set[int]:
        n = int(_cfg("exchange_limit", 12))
        if n <= 0:
            return set()
        return {int(r["id"]) for r in self.recent_exchange(umo, n)}

    def recall_original(self, umo: str, query: str, *,
                        max_segments: int | None = None,
                        segment_chars: int | None = None,
                        radius: int | None = None,
                        qvec=None,
                        exclude_ids: "set[int] | None" = None) -> list[dict]:
        if not umo or not query or not str(query).strip():
            return []
        if not bool(_cfg("memory_origin_enabled", False)):
            return []
        seg_n = int(max_segments if max_segments is not None
                    else _cfg("memory_origin_segments", 2))
        budget = int(segment_chars if segment_chars is not None
                     else _cfg("memory_origin_segment_chars", 1500))
        rad = int(radius if radius is not None
                  else _cfg("memory_origin_radius", _recall.DEFAULT_RADIUS))
        if seg_n <= 0 or budget <= 0 or rad <= 0:
            return []

        keys = _recall.recall_keys(query, recall_pattern_cap())
        if not keys:
            return []

        with self._lock:
            rows = self._db.execute(
                "SELECT id, ts, kind, text, meta FROM entries "
                "WHERE umo=? AND kind IN ('received','delivered') "
                "ORDER BY ts ASC, id ASC",
                (umo,),
            ).fetchall()


        excl = (set(int(i) for i in exclude_ids) if exclude_ids is not None
                else self._origin_exclude_ids(umo))
        if excl:
            rows = [r for r in rows if int(r["id"]) not in excl]
        if not rows:
            return []

        texts = [str(r["text"] or "") for r in rows]
        order = _recall.rank_relevant_anchors(query, texts, [float(r["ts"]) for r in rows])


        if (len(order) < seg_n and qvec is not None
                and bool(_cfg("memory_origin_vector_enabled", False))):
            vids = self._vec_search(
                "entries", qvec, int(_cfg("memory_origin_vector_topk", 12)))
            if vids:
                pos = {int(rows[j]["id"]): j for j in range(len(rows))}
                taken = set(order)
                for vid in vids:
                    j = pos.get(int(vid))
                    if j is not None and j not in taken:
                        taken.add(j)
                        order.append(j)

        out: list[dict] = []
        covered: set[int] = set()
        for i in order:
            if len(out) >= seg_n:
                break
            if i in covered:
                continue
            lo, hi, truncated = _recall.pick_segment(texts, i, budget, rad)
            covered.update(range(lo, hi))
            out.append({
                "entry_ids": [int(rows[j]["id"]) for j in range(lo, hi)],
                "anchor_id": int(rows[i]["id"]),
                "truncated": truncated,
                "items": [
                    {
                        "id": int(rows[j]["id"]),
                        "ts": float(rows[j]["ts"]),
                        "kind": str(rows[j]["kind"]),
                        "sender": str(
                            self.parse_meta(rows[j]["meta"]).get("sender") or ""
                        ).strip(),
                        "text": texts[j],
                    }
                    for j in range(lo, hi)
                ],
            })


        if out:
            logger.info(
                "[spectrum·原文召回] %s：%d 段 / %d 条 / %d 字 / 锚点 %s",
                umo, len(out), sum(len(sg["entry_ids"]) for sg in out),
                sum(len(it["text"]) for sg in out for it in sg["items"]),
                ",".join(str(sg["anchor_id"]) for sg in out))
        return out


    def upsert_person(self, person_id: str, platform: str = "", name: str = "") -> None:
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO persons(person_id, platform, name, first_seen, last_seen) "
                "VALUES(?,?,?,?,?) "
                "ON CONFLICT(person_id) DO UPDATE SET "
                "  last_seen=excluded.last_seen,"
                "  name=CASE WHEN excluded.name!='' THEN excluded.name ELSE persons.name END",
                (person_id, platform, name or "", now, now),
            )
            self._db.commit()

    def get_person(self, person_id: str) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM persons WHERE person_id=?", (person_id,)
            ).fetchone()

    def update_person(self, person_id: str, **fields) -> int:
        allowed = ("impression", "name", "affinity", "trust", "meta")
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed or v is None:
                continue
            if k in ("affinity", "trust"):
                v = max(0.0, min(1.0, float(v)))
            if k == "meta" and not isinstance(v, str):
                v = json.dumps(v, ensure_ascii=False)
            sets.append(f"{k}=?")
            params.append(v)
        if not sets:
            return 0
        params.append(person_id)
        with self._lock:
            if "trust" in fields and fields["trust"] is not None:

                row = self._db.execute("SELECT meta FROM persons WHERE person_id=?", (person_id,)).fetchone()
                if row is not None:
                    meta_index = next((i for i, item in enumerate(sets) if item == "meta=?"), None)
                    raw = params[meta_index] if meta_index is not None else row["meta"]
                    meta = json.loads(raw or "{}")
                    meta.pop("relationship_state", None)
                    meta.pop("relationship_fusion", None)
                    if meta_index is None:
                        sets.append("meta=?")
                        params.insert(-1, json.dumps(meta, ensure_ascii=False))
                    else:
                        params[meta_index] = json.dumps(meta, ensure_ascii=False)
            cur = self._db.execute(
                f"UPDATE persons SET {', '.join(sets)} WHERE person_id=?", params
            )
            self._db.commit()
            return cur.rowcount

    def apply_relationship(self, person_id: str, umo: str, signals: dict) -> dict | None:
        from .relationship import evolve
        if signals.get("person_id") != person_id or not signals.get("event_key"):
            return None
        with self._lock, self._db:
            person = self._db.execute("SELECT * FROM persons WHERE person_id=?", (person_id,)).fetchone()
            if person is None:
                return None
            if self._db.execute(
                "SELECT 1 FROM relationship_events WHERE person_id=? AND event_key=?",
                (person_id, signals["event_key"]),
            ).fetchone():
                return None
            meta = json.loads(person["meta"] or "{}")
            result = evolve(person["affinity"], person["trust"], signals,
                            meta.get("relationship_state"))
            meta["contact"] = int(meta.get("contact") or 0) + 1
            meta["relationship_version"] = 3
            meta["relationship_state"] = result["state"]
            meta["relationship_fusion"] = result["fusion"]
            self._db.execute(
                "INSERT INTO relationship_events VALUES(?,?,?,?,?,?,?,?,?)",
                (person_id, signals["event_key"], umo, time.time(),
                 json.dumps({**signals, "fusion": result["fusion"], "version": 3}, ensure_ascii=False), person["affinity"],
                 result["affinity"], person["trust"], result["trust"]),
            )
            self._db.execute("UPDATE persons SET affinity=?,trust=?,meta=? WHERE person_id=?",
                             (result["affinity"], result["trust"], json.dumps(meta, ensure_ascii=False), person_id))
            return result

    def add_person_alias(self, person_id: str, alias: str, umo: str = "") -> None:
        alias = (alias or "").strip()
        if not alias:
            return
        with self._lock:
            self._db.execute(
                "INSERT INTO person_aliases(person_id, alias, umo, last_seen) "
                "VALUES(?,?,?,?) "
                "ON CONFLICT(person_id, alias) DO UPDATE SET "
                "  umo=excluded.umo, last_seen=excluded.last_seen",
                (person_id, alias, umo, time.time()),
            )
            self._db.commit()

    def list_person_aliases(self, person_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT alias, umo, last_seen FROM person_aliases "
                "WHERE person_id=? ORDER BY last_seen DESC",
                (person_id,),
            ).fetchall()

    def all_person_aliases(self) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT alias, person_id FROM person_aliases "
                "WHERE alias IS NOT NULL AND alias != '' "
                "ORDER BY last_seen DESC"
            ).fetchall()
        return [(str(r["alias"]), str(r["person_id"])) for r in rows]


    def add_life(self, life_id: str, kind: str, started_at: float,
                 owner_session: str = "", with_who: str = "") -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO lives(life_id, kind, started_at, "
                "owner_session, with_who) VALUES(?,?,?,?,?)",
                (life_id, kind, started_at, owner_session, with_who),
            )
            self._db.commit()

    def end_life(self, life_id: str, ended_at: float,
                 title: str = "", summary: str = "") -> int:
        if not life_id:
            return 0
        with self._lock:

            cur = self._db.execute(
                "UPDATE lives SET ended_at=?, "
                "title=CASE WHEN ?!='' THEN ? ELSE title END, "
                "summary=CASE WHEN ?!='' THEN ? ELSE summary END "
                "WHERE life_id=? AND ended_at IS NULL",
                (ended_at, title, title, summary, summary, life_id),
            )
            self._db.commit()
            return cur.rowcount

    def pending_title_lives(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM lives WHERE title_pending=1 "
                "ORDER BY started_at ASC LIMIT ?", (limit,),
            ).fetchall()

    def mark_title_pending(self, life_id: str, pending: bool = True) -> int:
        if not life_id:
            return 0
        with self._lock:
            cur = self._db.execute(
                "UPDATE lives SET title_pending=? WHERE life_id=?",
                (1 if pending else 0, life_id),
            )
            self._db.commit()
            return cur.rowcount

    def expire_life_title(self, life_id: str) -> int:
        return self.mark_title_pending(life_id, False)

    def set_life_title(self, life_id: str, title: str = "", summary: str = "") -> int:
        if not life_id:
            return 0
        with self._lock:

            cur = self._db.execute(
                "UPDATE lives SET title=?, summary=?, title_pending=0 WHERE life_id=?",
                (title or "", summary or "", life_id),
            )
            self._db.commit()
            return cur.rowcount


    _LIFE_COLS = ("kind", "started_at", "ended_at", "owner_session",
                  "with_who", "title", "summary")

    def update_life_fields(self, life_id: str, **fields) -> int:
        bad = set(fields) - set(self._LIFE_COLS)
        if bad:
            raise ValueError(f"update_life_fields 收到未知列：{sorted(bad)}")
        if not life_id or not fields:
            return 0
        if "title" in fields:
            fields["title_pending"] = 0
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            cur = self._db.execute(
                f"UPDATE lives SET {cols} WHERE life_id=?",
                (*fields.values(), life_id))
            self._db.commit()
            return cur.rowcount

    def delete_life(self, life_id: str) -> dict:
        life_id = (life_id or "").strip()
        if not life_id:
            return {"entries": 0, "lives": 0}
        with self._lock:
            ids = [int(r["id"]) for r in self._db.execute(
                "SELECT id FROM entries WHERE life_id=?", (life_id,))]
            if ids:
                self._db.execute("DELETE FROM entries WHERE life_id=?", (life_id,))
            cur = self._db.execute("DELETE FROM lives WHERE life_id=?", (life_id,))
            self._db.commit()
        if ids:
            self._drop_vectors("entries", ids)
        return {"entries": len(ids), "lives": cur.rowcount}

    def get_life(self, life_id: str) -> sqlite3.Row | None:
        if not life_id:
            return None
        with self._lock:
            return self._db.execute(
                "SELECT * FROM lives WHERE life_id=?", (life_id,)
            ).fetchone()

    def list_lives(self, limit: int = 50, offset: int = 0) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM lives ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()

    def entries_of_life(self, life_id: str, kind: str | None = None,
                        limit: int = 200) -> list[sqlite3.Row]:
        sql = "SELECT * FROM entries WHERE life_id=?"
        params: list = [life_id]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY ts ASC LIMIT ?"
        params.append(limit)
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def get_person_name(self, person_id: str) -> str:
        try:
            with self._lock:
                row = self._db.execute(
                    "SELECT name FROM persons WHERE person_id=?",
                    (person_id,),
                ).fetchone()
            return str(row["name"]).strip() if row and row["name"] else ""
        except Exception:
            return ""

    def touch_person_session(self, person_id: str, umo: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO person_sessions(person_id, umo, last_seen, msg_count) "
                "VALUES(?,?,?,1) "
                "ON CONFLICT(person_id, umo) DO UPDATE SET "
                "  last_seen=excluded.last_seen, "
                "  msg_count=person_sessions.msg_count + 1",
                (person_id, umo, time.time()),
            )
            self._db.commit()

    def list_person_sessions(self, person_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT umo, last_seen, msg_count FROM person_sessions "
                "WHERE person_id=? ORDER BY last_seen DESC",
                (person_id,),
            ).fetchall()

    def list_all_persons(self, limit: int = 100, offset: int = 0) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM persons ORDER BY last_seen DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()

    def sessions_of_person_name(self, name: str) -> list[str]:
        n = (name or "").strip()
        if not n:
            return []
        try:
            with self._lock:
                rows = self._db.execute(
                    "SELECT DISTINCT ps.umo AS umo FROM person_sessions ps "
                    "WHERE ps.person_id IN ("
                    "  SELECT person_id FROM persons WHERE name=? "
                    "  UNION SELECT person_id FROM person_aliases WHERE alias=?"
                    ")",
                    (n, n),
                ).fetchall()
            return [r["umo"] for r in rows]
        except Exception:
            logger.exception("[spectrum·受众] 人名->会话 查询失败：%s", n)
            return []

    def count_persons(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) AS c FROM persons").fetchone()
        return int(row["c"]) if row else 0

    def delete_person(self, person_id: str) -> None:
        with self._lock:

            _mids = [int(r["id"]) for r in self._db.execute(
                "SELECT id FROM person_memories WHERE person_id=?", (person_id,))]
            self._db.execute("DELETE FROM person_memories WHERE person_id=?", (person_id,))
            self._db.execute("DELETE FROM person_sessions WHERE person_id=?", (person_id,))
            self._db.execute("DELETE FROM relationship_events WHERE person_id=?", (person_id,))
            self._db.execute("DELETE FROM persons WHERE person_id=?", (person_id,))
            self._db.commit()
        self._drop_vectors("person_memories", _mids)

    def _person_memory_rows(self, person_id: str) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT id, text, scope FROM person_memories WHERE person_id=?",
                (person_id,),
            ).fetchall()

    def _person_memory_insert(self, person_id: str, text: str, origin_umo: str,
                              scope: str, importance: float) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO person_memories(person_id, text, origin_umo, scope, "
                "importance, ts) VALUES(?,?,?,?,?,?)",
                (person_id, text, origin_umo, scope, importance, time.time()),
            )
            self._db.commit()
            return int(cur.lastrowid or -1)

    def add_person_memory(
        self, person_id: str, text: str, origin_umo: str = "",
        scope: str = "public", importance: float = 0.5,
    ) -> int:
        return _memory.upsert_person_memory(
            self, person_id, text, origin_umo=origin_umo,
            scope=scope, importance=importance)

    def get_person_memories(
        self, person_id: str, limit: int = 8, scope: str | None = None,
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM person_memories WHERE person_id=?"
        params: list = [person_id]
        if scope:
            sql += " AND scope=?"
            params.append(scope)
        sql += " ORDER BY importance DESC, ts DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def search_person_memories(
        self, person_id: str, query: str, limit: int = 4, *, scope: str | None = None,
    ) -> list[sqlite3.Row]:
        if not query or not query.strip():
            return []
        clean = "".join(c if c.isalnum() else " " for c in query).strip()
        if not clean:
            return []
        out: list[sqlite3.Row] = []
        seen: set[int] = set()
        with self._lock:
            try:
                rows = self._db.execute(
                    "SELECT m.* FROM person_memories m "
                    "JOIN person_memories_fts f ON m.id = f.rowid "
                    "WHERE m.person_id=? AND person_memories_fts MATCH ? "
                    + ("AND m.scope=? " if scope else "")
                    + "ORDER BY rank LIMIT ?",
                    (person_id, clean, *([scope] if scope else []), limit),
                ).fetchall()
                for r in rows:
                    seen.add(r["id"])
                    out.append(r)
            except sqlite3.OperationalError:
                pass
            if len(out) < limit:
                toks: list[str] = []
                nosp = clean.replace(" ", "")[:64]
                if len(nosp) >= 2:
                    toks.append(nosp)
                    toks.extend(nosp[i:i + 2] for i in range(len(nosp) - 1))
                _cap = recall_pattern_cap()
                uniq: list[str] = []
                for t in toks:
                    if t not in uniq:
                        uniq.append(t)


                    if len(uniq) >= _cap:
                        break
                if uniq:
                    like = " OR ".join(["(text LIKE ?)"] * len(uniq))


                    score = " + ".join(["(text LIKE ?)"] * len(uniq))
                    scope_sql = "AND scope=? " if scope else ""
                    params: list = ([f"%{t}%" for t in uniq] + [person_id]
                                    + ([scope] if scope else [])
                                    + [f"%{t}%" for t in uniq] + [limit])
                    try:
                        rows = self._db.execute(
                            f"SELECT *, ({score}) AS _score FROM person_memories "
                            f"WHERE person_id=? {scope_sql}AND ({like}) "
                            "ORDER BY _score DESC, importance DESC, ts DESC LIMIT ?",
                            params,
                        ).fetchall()
                        for r in rows:
                            if len(out) >= limit:
                                break
                            if r["id"] not in seen:
                                seen.add(r["id"])
                                out.append(r)
                    except sqlite3.OperationalError:
                        pass
        return out[:limit]

    def update_person_memory(self, mid: int, text: str,
                             importance: float | None = None) -> int:
        sql = "UPDATE person_memories SET text=?"
        params: list = [text]
        if importance is not None:
            sql += ", importance=?"
            params.append(importance)
        sql += " WHERE id=?"
        params.append(mid)
        with self._lock:
            cur = self._db.execute(sql, params)
            self._db.commit()

        self._drop_vectors("person_memories", [mid])
        return cur.rowcount

    def delete_person_memory(self, mid: int) -> int:
        with self._lock:
            cur = self._db.execute("DELETE FROM person_memories WHERE id=?", (mid,))
            self._db.commit()
        self._drop_vectors("person_memories", [mid])
        return cur.rowcount


    def is_asleep(self) -> bool:
        return self.get_state(GLOBAL_UMO, "sleep_state") == "asleep"

    def sleep_due(self, now: float | None = None) -> bool:
        from . import freeze
        sleep = self.get_sleep()
        if not sleep or not sleep["until"] or freeze.is_frozen():
            return False
        raw_until = float(self.get_state(GLOBAL_UMO, "sleep_until") or 0)
        return freeze.elapsed_since(sleep["start"], now) >= max(0.0, raw_until - sleep["start"])

    def get_sleep(self) -> dict:
        if not self.is_asleep():
            return {}
        from . import freeze
        start = float(self.get_state(GLOBAL_UMO, "sleep_start") or 0)
        until = float(self.get_state(GLOBAL_UMO, "sleep_until") or 0)
        return {"until": freeze.effective_deadline(start, until) if until else 0,
                "start": start}

    def set_sleep_asleep(self, until: float | None = None) -> None:
        self.set_state(GLOBAL_UMO, "sleep_state", "asleep")
        self.set_state(GLOBAL_UMO, "sleep_start", repr(time.time()))
        if until is not None:
            self.set_state(GLOBAL_UMO, "sleep_until", repr(until))
        else:

            self.set_state(GLOBAL_UMO, "sleep_until", repr(time.time()))
        self.set_state(GLOBAL_UMO, "sleep_inbox", "[]")

    def set_sleep_awake(self) -> None:
        if self.is_asleep():
            sleep = self.get_sleep()
            self.set_state(GLOBAL_UMO, "last_sleep", json.dumps(
                {"start": sleep["start"], "end": time.time()}))
        self.set_state(GLOBAL_UMO, "sleep_state", "awake")

    def push_sleep_inbox(self, text: str, sender: str = "对方",
                         umo: str = "") -> None:
        with self._lock:
            inbox = self.get_sleep_inbox()
            inbox.append({"t": time.time(), "from": sender, "text": text, "umo": umo})
            if len(inbox) > 30:
                inbox = inbox[-30:]
            self.set_state(GLOBAL_UMO, "sleep_inbox", json.dumps(inbox, ensure_ascii=False))

    def get_sleep_inbox(self) -> list[dict]:
        raw = self.get_state(GLOBAL_UMO, "sleep_inbox", "[]")
        try:
            return json.loads(raw)
        except Exception:
            return []

    def take_sleep_inbox(self) -> list[dict]:
        inbox = self.get_sleep_inbox()
        self.set_state(GLOBAL_UMO, "sleep_inbox", "[]")
        return inbox

    def acknowledge_sleep_inbox(self, consumed: list[dict]) -> None:
        with self._lock:
            remaining = self.get_sleep_inbox()
            for item in consumed:
                if item in remaining:
                    remaining.remove(item)
            self.set_state(GLOBAL_UMO, "sleep_inbox", json.dumps(remaining, ensure_ascii=False))


    def world_read(self):
        """返回可重入读写锁，用于一次世界快照的连续读取。"""
        return self._lock

    def world_version(self) -> str:
        """对共同生活、作息、近期事件和观测记录生成版本指纹。"""
        with self._lock:
            state = self._db.execute(
                "SELECT key, value FROM state WHERE umo=? "
                "AND (key LIKE 'life_%' OR key IN ('sleep_state', 'sleep_until', 'sleep_start')) "
                "ORDER BY key", (GLOBAL_UMO,),
            ).fetchall()
            life_id = self.get_state(GLOBAL_UMO, "life_current_id", "") or ""
            events = self._db.execute('SELECT * FROM shared_life_events ORDER BY id').fetchall()
            observed = self.observed_rows(limit=2)
            content = json.dumps([[dict(r) for r in rows] for rows in (state, events, observed)],
                                 ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def get_state(self, umo: str, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM state WHERE umo=? AND key=?", (umo, key)
            ).fetchone()
        return row["value"] if row else default

    def set_state(self, umo: str, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO state(umo, key, value) VALUES(?,?,?) "
                "ON CONFLICT(umo, key) DO UPDATE SET value=excluded.value",
                (umo, key, value),
            )
            self._db.commit()

    def bump_counter(self, umo: str, key: str) -> int:
        try:
            cur = int(self.get_state(umo, key, "0") or "0")
        except ValueError:
            cur = 0
        cur += 1
        self.set_state(umo, key, str(cur))
        return cur


    STORY_STATE_DEFAULTS: dict = {
        "turn_seq": 0,
        "tension": 1,
        "arc_target": 3,
        "beat_cursor": "",
        "beat_started_at": 0.0,
        "beat_end_seq": 0,


        "beats_consumed": 0,
        "last_beat_at_n": 0,


        "total_beats": 0,
        "act_no": 1,
        "act_started_at": 0.0,
        "phase": "BUILD_UP",
        "phase_since": 0.0,
        "intensity": 0.0,
        "intensity_per": {},


        "pacing_seen": {},
        "decay_suspended_until": 0.0,
        "scene_id": "default",
        "scene_since": 0.0,
        "open_threads": [],


        "intervention_budget": 2.4,
        "budget_at": 0.0,
        "deferred": {},


        "tier3_bucket": {},
        "tier2_cache": {},
        "ending_name": "",
        "updated_at": 0.0,
    }

    def get_story_state(self) -> dict:
        raw = self.get_state(GLOBAL_UMO, "story_state", "")
        d = {}
        if raw:
            try:
                loaded = json.loads(raw)
                if isinstance(loaded, dict):
                    d = loaded
            except Exception:
                logger.warning("[spectrum·导演] story_state 不是合法 JSON，退回出厂值")
        out = copy.deepcopy(self.STORY_STATE_DEFAULTS)
        out.update(d)


        try:
            out["total_beats"] = max(int(out.get("total_beats") or 0),
                                     int(out.get("beats_consumed") or 0))
        except (TypeError, ValueError):
            out["total_beats"] = int(out.get("beats_consumed") or 0)
        return out

    def set_story_state(self, d: dict) -> None:
        self.set_state(GLOBAL_UMO, "story_state",
                       json.dumps(d, ensure_ascii=False))

    def add_director_fire(self, *, ts: float, sid: str = "",
                          kind: str = "storylet", umo: str = "", life_id: str = "",
                          turn_seq: int = 0, narr_index: int = 0,
                          delivered: int = 0, narrative: str = "",
                          meta: dict | None = None) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO director_fires"
                "(ts, sid, kind, umo, life_id, turn_seq, narr_index, delivered,"
                " narrative, meta) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (ts, sid, kind, umo, life_id, turn_seq, narr_index, delivered,
                 narrative, json.dumps(meta or {}, ensure_ascii=False)),
            )
            self._db.commit()
            return int(cur.lastrowid or 0)

    def set_director_fire_delivered(self, fid: int, delivered: bool = True) -> None:
        with self._lock:
            self._db.execute("UPDATE director_fires SET delivered=? WHERE id=?",
                             (1 if delivered else 0, fid))
            self._db.commit()

    def director_fire_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT sid, COUNT(*) n FROM director_fires "
                "WHERE sid != '' GROUP BY sid").fetchall()
        out = {r["sid"]: int(r["n"]) for r in rows}
        for sid, n in self._director_baseline().items():
            out[sid] = out.get(sid, 0) + int(n)
        return out


    def _director_baseline(self) -> dict:
        raw = self.get_state(GLOBAL_UMO, "director_baseline", "")
        if not raw:
            return {}
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) else {}
        except Exception:
            logger.warning("[spectrum·导演] director_baseline 不是合法 JSON，按空处理")
            return {}

    def count_director_fires(self) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) n FROM director_fires").fetchone()
        return int(row["n"]) if row else 0

    def rollup_director_fires(self, max_rows: int) -> int:
        if max_rows <= 0:
            return 0
        total = self.count_director_fires()
        if total <= max_rows:
            return 0
        drop = total - max_rows
        with self._lock:

            rows = self._db.execute(
                "SELECT sid, COUNT(*) n FROM director_fires "
                "WHERE sid != '' AND id IN (SELECT id FROM director_fires "
                "ORDER BY id ASC LIMIT ?) GROUP BY sid", (drop,)).fetchall()
            base = self._director_baseline()
            for r in rows:
                base[r["sid"]] = int(base.get(r["sid"], 0)) + int(r["n"])
            self.set_state(GLOBAL_UMO, "director_baseline",
                           json.dumps(base, ensure_ascii=False))
            self._db.execute(
                "DELETE FROM director_fires WHERE id IN "
                "(SELECT id FROM director_fires ORDER BY id ASC LIMIT ?)",
                (drop,))
            self._db.commit()
        logger.warning("[spectrum·导演] 转录超过 %d 行，折算掉最老的 %d 行"
                       "（基线已落库，消费计数不变）", max_rows, drop)
        return drop

    def director_last_fire_ts(self) -> dict[str, float]:
        with self._lock:
            rows = self._db.execute(
                "SELECT sid, MAX(ts) t FROM director_fires "
                "WHERE sid != '' GROUP BY sid").fetchall()
        return {r["sid"]: float(r["t"]) for r in rows}

    def director_last_fire_ts_by_session(self, sid: str) -> dict[str, float]:
        with self._lock:
            rows = self._db.execute(
                "SELECT umo, MAX(ts) t FROM director_fires "
                "WHERE sid = ? AND umo != '' GROUP BY umo",
                (str(sid),)).fetchall()
        return {r["umo"]: float(r["t"]) for r in rows}

    def director_fires_all(self, limit: int = 500) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM director_fires ORDER BY id DESC LIMIT ?",
                (int(limit),)).fetchall()


    def tool_notes_all(self) -> dict[str, str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT tool_name, note FROM tool_notes WHERE note != ''").fetchall()
        return {str(r["tool_name"]): str(r["note"]) for r in rows}

    def tool_note_set(self, name: str, note: str) -> bool:
        name = str(name or "").strip()
        note = str(note or "").strip()
        if not name:
            return False
        with self._lock:
            if not note:
                self._db.execute("DELETE FROM tool_notes WHERE tool_name=?", (name,))
            else:
                self._db.execute(
                    "INSERT INTO tool_notes(tool_name, note, updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(tool_name) DO UPDATE SET "
                    "note=excluded.note, updated_at=excluded.updated_at",
                    (name, note, time.time()))
            self._db.commit()
        return True

    def record_relation_snapshot(self, ts: float, rows: list[dict]) -> None:
        if not rows:
            return
        with self._lock:
            self._db.executemany(
                "INSERT INTO relation_history(ts, umo, person_id, affinity, trust, valence) "
                "VALUES(?,?,?,?,?,?)",
                [(ts, r.get("umo", ""), r.get("person_id", ""), r.get("affinity"),
                  r.get("trust"), r.get("valence")) for r in rows])
            self._db.commit()

    def relation_history(self, umo: str = "", limit: int = 20000) -> list[sqlite3.Row]:
        with self._lock:
            if umo:
                return self._db.execute(
                    "SELECT * FROM relation_history WHERE umo=? ORDER BY ts LIMIT ?",
                    (umo, int(limit))).fetchall()
            return self._db.execute(
                "SELECT * FROM relation_history ORDER BY ts LIMIT ?",
                (int(limit),)).fetchall()

    def last_received_ts(self, umo: str) -> float | None:
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(ts) AS m FROM entries WHERE umo=? AND kind='received'",
                (umo,),
            ).fetchone()
        return float(row["m"]) if row and row["m"] is not None else None

    def last_activity_ts_any(self) -> float | None:
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(ts) t FROM entries WHERE kind IN ('received','delivered')"
            ).fetchone()
        return float(row["t"]) if row and row["t"] else None

    def count_received_since(self, ts: float, *, umo: str | None = None) -> int:
        with self._lock:
            if umo is not None:
                row = self._db.execute(
                    "SELECT COUNT(*) n FROM entries WHERE umo=? AND kind='received' AND ts > ?",
                    (umo, ts)).fetchone()
                return int(row["n"])
            row = self._db.execute(
                "SELECT COUNT(*) n FROM entries WHERE kind='received' AND ts > ?",
                (ts,)).fetchone()
        return int(row["n"]) if row else 0


    _WORLDBOOK_COLS = (
        "title", "text", "keys", "secondary", "selective",
        "selective_logic", "constant", "order_value", "case_sensitive",
        "match_whole_words", "use_regex", "probability", "group_name",
        "group_weight", "use_group_scoring", "prioritize", "vectorized",
        "ignore_budget", "sticky", "cooldown", "delay", "scan_depth",
        "exclude_recursion", "prevent_recursion", "delay_until_recursion",
        "position", "enabled",
    )

    def worldbook_all(self, include_disabled: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM worldbook"
        if not include_disabled:
            sql += " WHERE enabled=1"
        sql += " ORDER BY order_value DESC, id ASC"
        with self._lock:
            return self._db.execute(sql).fetchall()

    def get_worldbook(self, entry_id: int) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM worldbook WHERE id=?", (int(entry_id),)).fetchone()

    def upsert_worldbook(self, entry_id: int | None = None, **fields) -> int:
        bad = set(fields) - set(self._WORLDBOOK_COLS)
        if bad:
            raise ValueError(f"upsert_worldbook 收到未知列：{sorted(bad)}")
        now = time.time()
        changed_text = False
        with self._lock:
            eid = int(entry_id) if entry_id else 0
            if eid and not self._db.execute(
                    "SELECT 1 FROM worldbook WHERE id=?", (eid,)).fetchone():
                eid = 0
            if not eid:
                cols = ["created_at", "updated_at"] + list(fields)
                vals = [now, now] + list(fields.values())
                ph = ", ".join("?" * len(cols))
                cur = self._db.execute(
                    f"INSERT INTO worldbook({', '.join(cols)}) VALUES({ph})", vals)
                eid = int(cur.lastrowid or -1)
            elif fields:
                cols = ", ".join(f"{k}=?" for k in fields)
                self._db.execute(
                    f"UPDATE worldbook SET {cols}, updated_at=? WHERE id=?",
                    (*fields.values(), now, eid))
                changed_text = fields.get("text") is not None
            self._db.commit()


        if changed_text:
            self._drop_vectors("worldbook", [eid])
        return eid

    def delete_worldbook(self, entry_id: int) -> int:
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM worldbook WHERE id=?", (int(entry_id),))

            self._db.execute(
                "DELETE FROM worldbook_state WHERE entry_id=?", (int(entry_id),))
            self._db.commit()


        self._drop_vectors("worldbook", [int(entry_id)])
        return cur.rowcount

    def set_worldbook_enabled(self, entry_id: int, enabled: bool) -> int:
        with self._lock:
            cur = self._db.execute(
                "UPDATE worldbook SET enabled=?, updated_at=? WHERE id=?",
                (1 if enabled else 0, time.time(), int(entry_id)))
            self._db.commit()
            return cur.rowcount


    def worldbook_fts_ids(self, query: str, limit: int = 50) -> list[int]:
        clean = "".join(c if c.isalnum() else " " for c in str(query or "")).strip()
        if not clean:
            return []
        with self._lock:
            try:
                rows = self._db.execute(
                    "SELECT rowid FROM worldbook_fts "
                    "WHERE worldbook_fts MATCH ? LIMIT ?",
                    (clean, int(limit))).fetchall()
            except sqlite3.OperationalError:
                return []
        return [int(r[0]) for r in rows]

    def _worldbook_like_ids(self, query: str, limit: int = 50) -> list[int]:
        clean = "".join(c if c.isalnum() else " " for c in str(query or "")).strip()
        if not clean:
            return []
        uniq = _recall.recall_keys(clean, recall_pattern_cap())
        if not uniq:
            return []
        like = " OR ".join(["(text LIKE ?)"] * len(uniq))
        with self._lock:
            rows = self._db.execute(
                f"SELECT id FROM worldbook WHERE ({like}) LIMIT ?",
                (*[f"%{k}%" for k in uniq], int(limit))).fetchall()
        return [int(r["id"]) for r in rows]


    def worldbook_scan_lines(self, umo: str, turns: int) -> list[str]:
        n = max(1, int(turns or 1))
        out: list[str] = []
        for r in self.recent_exchange(umo, n):
            meta = self.parse_meta(r["meta"])
            sender = str(meta.get("sender") or "").strip()
            if str(r["kind"]) == "received":
                label = (sender if sender and sender not in self._PLACEHOLDER_SENDERS
                         else ("群友" if "GroupMessage" in umo else "对方"))
            else:
                label = "我"
            text = str(r["text"] or "").strip()
            if text:
                out.append(f"{label}：{text}")
        return out

    def _worldbook_scan_cap(self, default_depth: int) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT MAX(scan_depth) AS m FROM worldbook WHERE enabled=1"
            ).fetchone()
        own = int(row["m"] or 0) if row else 0
        return max(1, int(default_depth or 0), own)


    def worldbook_seq(self, umo: str) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS c FROM entries WHERE umo=?", (umo,)).fetchone()
        return int(row["c"]) if row else 0

    def worldbook_states(self, umo: str) -> dict:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM worldbook_state WHERE umo=?", (umo,)).fetchall()
        return {int(r["entry_id"]): r for r in rows}

    def touch_worldbook_state(self, umo: str, entry_id: int, *, fired_seq: int,
                              sticky_until: int, cooldown_until: int) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO worldbook_state"
                "(umo, entry_id, last_fired_seq, sticky_until, cooldown_until) "
                "VALUES(?,?,?,?,?)",
                (umo, int(entry_id), int(fired_seq),
                 int(sticky_until), int(cooldown_until)))
            self._db.commit()


    def worldbook_activate(self, head: str, *, lines=None,
                           default_depth: int = 0, extra: str = "",
                           qvec=None,
                           recursive: bool = False, timed=None) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM worldbook WHERE enabled=1 "
                "ORDER BY order_value DESC, id ASC").fetchall()
        seq, states = (0, {}) if timed is None else timed
        by_id = {int(r["id"]): r for r in rows}
        hits: dict[int, sqlite3.Row] = {}
        auto: set[int] = set()
        vec_ok: set[int] = set()

        def _hay(depth: int) -> str:
            t = _worldbook.scan_text(head, lines, depth)
            return t + "\n" + extra if extra else t


        scan = _hay(default_depth)
        for r in rows:
            eid = int(r["id"])
            gate = _worldbook.GATE_NORMAL
            if timed is not None:
                gate = _worldbook.timed_gate(r, seq, states.get(eid))
                if gate == _worldbook.GATE_SUPPRESS:
                    continue
            if gate != _worldbook.GATE_STICKY:
                if recursive and int(r["exclude_recursion"] or 0):
                    continue
                if not recursive and int(r["delay_until_recursion"] or 0):
                    continue
            if gate == _worldbook.GATE_STICKY or _worldbook.is_constant(r):
                hits[eid] = r
            elif _worldbook.split_terms(r["keys"]):
                own = int(r["scan_depth"] or 0)
                if _worldbook.keyword_activate(
                        r, _hay(own if own > 0 else default_depth)):
                    hits[eid] = r
            else:
                auto.add(eid)
            if int(r["vectorized"] or 0):
                vec_ok.add(eid)
        if auto and scan.strip():
            for eid in self.worldbook_fts_ids(scan) + self._worldbook_like_ids(scan):
                if eid in auto and eid not in hits:
                    hits[eid] = by_id[eid]
        if (vec_ok and qvec is not None
                and _cfg("worldbook_vector_enabled", False)):
            for eid in self._vec_search("worldbook", qvec, k=max(len(vec_ok), 1)):
                if eid in vec_ok and eid not in hits and eid in by_id:
                    hits[eid] = by_id[eid]
        return list(hits.values())

    def _worldbook_scan(self, head: str, *, lines=None, default_depth: int = 0,
                        qvec=None, timed=None) -> list[sqlite3.Row]:
        steps = 1
        if _cfg("worldbook_recursion", False):
            steps = _worldbook.clamp_steps(_cfg("worldbook_max_steps", 3))
        found: dict[int, sqlite3.Row] = {}
        feeds: list[str] = []
        for rnd in range(steps):
            if rnd == 0:
                extra, vec, rec = "", qvec, False
            elif feeds:
                extra, vec, rec = "\n".join(feeds), None, True
            else:
                break
            new_feed: list[str] = []
            for r in self.worldbook_activate(head, lines=lines,
                                             default_depth=default_depth,
                                             extra=extra, qvec=vec,
                                             recursive=rec, timed=timed):
                eid = int(r["id"])
                if eid in found:
                    continue
                found[eid] = r
                if int(r["prevent_recursion"] or 0):
                    continue
                txt = str(r["text"] or "").strip()
                if txt:
                    new_feed.append(txt)
            feeds.extend(new_feed)
            if not new_feed:
                break
        return list(found.values())

    def worldbook_hits(self, head: str, *, umo: str | None = None, qvec=None,
                       rng=None, max_entries: int = None,
                       token_budget: int = None) -> list[sqlite3.Row]:
        """触发候选后处理互斥组、概率和预算，再更新本会话回合状态。"""
        rng = rng or _worldbook.DEFAULT_RNG


        if max_entries is None:
            max_entries = int(_cfg("worldbook_max", 0) or 0)
        if token_budget is None:
            token_budget = _worldbook.resolve_budget(
                _cfg("worldbook_budget_percent", 25),
                _cfg("worldbook_context_tokens", 32000),
                _cfg("worldbook_token_budget", 0))


        default_depth = int(_cfg("worldbook_scan_depth", 2) or 0)
        lines = (self.worldbook_scan_lines(umo, self._worldbook_scan_cap(default_depth))
                 if umo else None)
        scan = _worldbook.scan_text(head, lines, default_depth)

        timed = None
        states: dict = {}
        seq = 0
        if umo:
            seq = self.worldbook_seq(umo)
            states = self.worldbook_states(umo)
            timed = (seq, states)
        sticky_ids = {eid for eid, st in states.items()
                      if int(st["sticky_until"] or 0) > seq}

        active = self._worldbook_scan(head, lines=lines,
                                      default_depth=default_depth,
                                      qvec=qvec, timed=timed)


        singles: list[sqlite3.Row] = []
        groups: dict[str, list[sqlite3.Row]] = {}
        for r in active:
            g = str(r["group_name"] or "").strip()
            if g:
                groups.setdefault(g, []).append(r)
            else:
                singles.append(r)
        for g in sorted(groups):
            pool = groups[g]
            sticky = [x for x in pool if int(x["id"]) in sticky_ids]
            win = _worldbook.pick_from_group(sticky or pool, scan, rng)
            if win is not None:
                singles.append(win)


        kept = [r for r in singles if _worldbook.probability_pass(r, rng)]


        kept.sort(key=lambda r: (0 if _worldbook.is_constant(r) else 1,
                                 -int(r["order_value"]), int(r["id"])))
        out, dropped = _worldbook.trim_to_budget(kept, max_entries, token_budget)


        if out or dropped:
            logger.info(
                "[spectrum·世界书] %s：注入 %d 条 / %d 字，被裁 %d 条"
                "（上限 %s 条 / %s token）",
                umo or "-", len(out),
                sum(len(str(r["text"] or "")) for r in out), dropped,
                max_entries, token_budget)

        if umo:
            for r in out:
                nxt = _worldbook.next_timed_state(
                    r, seq, int(r["id"]) in sticky_ids)
                if nxt:
                    self.touch_worldbook_state(umo, int(r["id"]), fired_seq=seq, **nxt)
        return out


    def storylets_all(self, include_disabled: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM storylets"
        if not include_disabled:
            sql += " WHERE enabled=1"
        sql += " ORDER BY priority DESC, sid ASC"
        with self._lock:
            return self._db.execute(sql).fetchall()

    def get_storylet(self, sid: str) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM storylets WHERE sid=?", (sid,)).fetchone()

    def upsert_storylet(self, sid: str, **fields) -> None:
        allowed = {
            "kind", "enabled", "scope", "scene_id", "got_scene_id", "ending_name",
            "narrative", "condition", "depends_on", "k_turns", "priority", "weight",
            "effects", "eval_annot", "meta",
        }
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"upsert_storylet 收到未知列：{sorted(bad)}")
        now = time.time()
        with self._lock:
            exists = self._db.execute(
                "SELECT 1 FROM storylets WHERE sid=?", (sid,)).fetchone()
            if not exists:
                self._db.execute(
                    "INSERT INTO storylets(sid, created_at, updated_at) VALUES(?,?,?)",
                    (sid, now, now))
            if fields:
                cols = ", ".join(f"{k}=?" for k in fields)
                self._db.execute(
                    f"UPDATE storylets SET {cols}, updated_at=? WHERE sid=?",
                    (*fields.values(), now, sid))
            self._db.commit()

    def set_storylet_enabled(self, sid: str, enabled: bool) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE storylets SET enabled=?, updated_at=? WHERE sid=?",
                (1 if enabled else 0, time.time(), sid))
            self._db.commit()

    def delete_storylet(self, sid: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM storylets WHERE sid=?", (sid,))
            self._db.commit()


    def threads_all(self, state: str = "") -> list[sqlite3.Row]:
        from .threads import CLOSED_STATES, OPEN_STATES
        groups = {"open": OPEN_STATES, "closed": CLOSED_STATES}
        with self._lock:
            if not state:
                return self._db.execute(
                    "SELECT * FROM drama_threads ORDER BY opened_at").fetchall()
            vals = groups.get(state, (state,))
            ph = ",".join("?" * len(vals))
            return self._db.execute(
                f"SELECT * FROM drama_threads WHERE state IN ({ph}) "
                "ORDER BY opened_at", tuple(vals)).fetchall()

    def paid_threads_n(self, act_no: int = 0) -> int:
        sql = "SELECT count(*) AS n FROM drama_threads WHERE state='paid'"
        args: tuple = ()
        if act_no:
            sql += " AND paid_act=?"
            args = (int(act_no),)
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
            return int(row["n"] or 0)

    def open_thread(self, tid: str, title: str = "", now: float | None = None,
                    *, element: str = "", visibility: str = "", scope: str = "",
                    kind: str = "", payoff_form: str = "",
                    planned_payoff: str = "", trigger: dict | None = None,
                    open_beat: int = 0, due_beats: int = 0) -> None:
        ts = now or time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO drama_threads"
                "(tid, title, state, opened_at, element, visibility, scope, kind,"
                " payoff_form, planned_payoff, trigger_spec, open_beat, due_beats)"
                " VALUES(?,?,'planted',?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(tid) DO UPDATE SET"
                " state='planted', opened_at=?, closed_at=0, abandoned_at=0,"
                " abandon_reason='', title=excluded.title,"
                " element=excluded.element, visibility=excluded.visibility,"
                " scope=excluded.scope, kind=excluded.kind,"
                " payoff_form=excluded.payoff_form,"
                " planned_payoff=excluded.planned_payoff,"
                " trigger_spec=excluded.trigger_spec,"
                " open_beat=excluded.open_beat, due_beats=excluded.due_beats,"
                " touch_n=0, last_touch_at=0",
                (tid, title, ts, element, visibility, scope, kind, payoff_form,
                 planned_payoff, json.dumps(trigger or {}, ensure_ascii=False),
                 int(open_beat), int(due_beats), ts))
            self._db.commit()

    def thread_get(self, tid: str) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM drama_threads WHERE tid=?", (tid,)).fetchone()

    def touch_thread(self, tid: str, now: float | None = None) -> str:
        from .threads import is_open, next_state_on_touch
        ts = now or time.time()
        with self._lock:
            row = self._db.execute(
                "SELECT state FROM drama_threads WHERE tid=?", (tid,)).fetchone()
            if row is None or not is_open(row["state"]):
                return row["state"] if row is not None else ""
            new = next_state_on_touch(row["state"])
            self._db.execute(
                "UPDATE drama_threads SET state=?, touch_n=touch_n+1,"
                " last_touch_at=? WHERE tid=?", (new, ts, tid))
            self._db.commit()
            return new

    def pay_thread(self, tid: str, now: float | None = None, *,
                   act_no: int | None = None) -> bool:
        ts = now or time.time()
        with self._lock:
            if act_no is None:
                act_no = int(self.get_story_state().get("act_no") or 1)
            cur = self._db.execute(
                "UPDATE drama_threads SET state='paid', closed_at=?, paid_act=? "
                "WHERE tid=? AND state!='paid'", (ts, int(act_no), tid))
            self._db.commit()
            return bool(cur.rowcount)

    def abandon_thread(self, tid: str, reason: str = "",
                       now: float | None = None) -> bool:
        ts = now or time.time()
        with self._lock:
            cur = self._db.execute(
                "UPDATE drama_threads SET state='abandoned', closed_at=?,"
                " abandoned_at=?, abandon_reason=? "
                "WHERE tid=? AND state NOT IN ('paid','abandoned')",
                (ts, ts, reason, tid))
            self._db.commit()
            return bool(cur.rowcount)

    def close_thread(self, tid: str, now: float | None = None) -> None:
        self.pay_thread(tid, now=now)


    def fire_intent(self, iid: int, now: float | None = None) -> bool:
        now = now or time.time()
        with self._lock:
            row = self._db.execute(
                "SELECT meta FROM intents WHERE id=? AND status='waiting'", (iid,)).fetchone()
            if row is None:
                return False
            meta = self.parse_meta(row["meta"])
            meta["attempts"] = int(meta.get("attempts", 0)) + 1
            meta["last_fire_ts"] = now
            meta["attempt_complete"] = False
            self._db.execute(
                "UPDATE intents SET status='fired', meta=? WHERE id=?",
                (json.dumps(meta, ensure_ascii=False), iid))
            self._db.commit()
            return True

    def complete_intent_attempt(self, iid: int) -> None:
        with self._lock:
            row = self._db.execute("SELECT meta FROM intents WHERE id=?", (iid,)).fetchone()
            if row is None:
                return
            meta = self.parse_meta(row["meta"])
            meta["attempt_complete"] = True
            self._db.execute("UPDATE intents SET meta=? WHERE id=?",
                             (json.dumps(meta, ensure_ascii=False), iid))
            self._db.commit()

    def recover_interrupted_intents(self, now: float, max_attempts: int) -> None:
        from . import freeze
        with self._lock:
            rows = self._db.execute("SELECT id, meta FROM intents WHERE status='fired'").fetchall()
            for row in rows:
                meta = self.parse_meta(row["meta"])

                if meta.get("attempt_complete") is not False:
                    continue
                if freeze.elapsed_since(float(meta["last_fire_ts"]), now) < 60:
                    continue
                status = "expired" if int(meta["attempts"]) >= max_attempts else "waiting"
                self._db.execute("UPDATE intents SET status=? WHERE id=?", (status, row["id"]))
            self._db.commit()

    def expire_stale_fired_intents(self, older_than_s: float,
                                   max_attempts: int, now: float | None = None) -> int:
        now = now or time.time()
        changed = 0
        with self._lock:
            rows = self._db.execute(
                "SELECT id, meta FROM intents WHERE status='fired'").fetchall()
            for r in rows:
                meta = self.parse_meta(r["meta"])
                last = float(meta.get("last_fire_ts", 0) or 0)
                attempts = int(meta.get("attempts", 0) or 0)
                from . import freeze
                if attempts >= max_attempts and freeze.elapsed_since(last, now) >= older_than_s:
                    self._db.execute(
                        "UPDATE intents SET status='expired' WHERE id=?", (r["id"],))
                    logger.info("[spectrum·导演] 承诺 #%s 尝试 %d 次未兑现，作废",
                                r["id"], attempts)
                    changed += 1
            if changed:
                self._db.commit()
        return changed
