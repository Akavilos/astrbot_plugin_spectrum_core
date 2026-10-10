"""渲染自我经历和跨会话回忆，保持来源身份、历史时间与表达选择分离。"""
from datetime import datetime
from zoneinfo import ZoneInfo

from . import audience

_CONVERSATIONAL_KEYS = frozenset(('昨天', '今天', '明天', '刚才', '现在', '这个', '那个', '那杯', '这杯',
                                '是谁', '什么', '怎么', '哪里', '是不是', '谁送', '送的', '来的', '你说', '我说', '说过'))
_LINKING_CHARS = frozenset('的了是和我你他她它')

def topic_keys(query, cap=24):
    """在长话题的前、中、后部均取检索词，避免末尾的事件名被截掉。"""
    clean = ''.join(c if c.isalnum() else ' ' for c in str(query).casefold())
    words = [w for w in clean.split() if 2 <= len(w) <= 32]
    text = clean.replace(' ', '')
    pairs = list(dict.fromkeys(text[i:i + 2] for i in range(len(text) - 1)
                              if not any(c in _LINKING_CHARS for c in text[i:i + 2])))
    words = list(dict.fromkeys(words))[:max(1, cap // 4)]
    pair_budget = max(1, cap - len(words))
    if len(pairs) > pair_budget:
        edge = max(1, pair_budget // 3)
        middle = pairs[edge:-edge]
        slots = max(1, pair_budget - 2 * edge)
        pairs = pairs[:edge] + [middle[min(len(middle) - 1, i * len(middle) // slots)] for i in range(slots)] + pairs[-edge:]
    keys = list(dict.fromkeys(words + pairs))
    # 单独分词先保留，剩余位置均匀分配给字符词；全文很短时保留原话。
    if 1 <= len(text) <= 32:
        keys.insert(0, text)
    return [key for key in dict.fromkeys(keys) if key not in _CONVERSATIONAL_KEYS][:cap]


def session_name(store, umo):
    if ':FriendMessage:' in umo:
        platform, _, uid = umo.split(':', 2)
        return store.get_person_name(platform + ':' + uid) or ('QQ ' + uid)
    return store.get_state(umo, 'group_name', '') or umo


def render_self_state(store, life, viewer):
    """补充自我现状中被当前受众过滤的条目，不读取整个私聊档案。"""
    state = life.get_state()
    lines = []
    for item in state.get('items') or []:
        scope = audience.parse(item.get('audience'))
        if audience.admit(scope, viewer) or not item.get('text'):
            continue
        sources = sorted(scope) if isinstance(scope, (set, frozenset)) else []
        where = '、'.join(session_name(store, u) for u in sources) or session_name(store, state.get('src') or '')
        lines.append(f"- 我记在与{where}相关的自身经历：{item['text']}")
    if not lines:
        return ''
    return ('【我在其他往来中记下的自身经历】\n'
            '我换一个聊天窗口仍是同一个人。我知道这段经历，不代表当前对象知道它或参与过它。'
            '其中的人称和称呼沿用来源身份，不指向当前对象。摘要记录时间不等于事件发生时间；我结合事件日期理解过去和现在。'
            '这部分是现状摘要；我以带来源的事件链确认发生日期和后续处理，再理解摘要与旧疑问。'
            '回忆不等于公开，我根据自己的意愿和关系决定是否提起。\n' + '\n'.join(lines))


def render_cross_recall(store, segments, timezone='Asia/Shanghai'):
    lines = []
    zone = ZoneInfo(timezone)
    for segment in segments:
        origin = segment['source_umo']
        name = session_name(store, origin)
        channel = '私聊' if ':FriendMessage:' in origin else '群聊'
        lines.append(f"我想起与{name}的{channel}往来（来源会话：{origin}）：")
        for item in segment['items']:
            when = datetime.fromtimestamp(item['ts'], zone).strftime('%Y-%m-%d %H:%M')
            who = '我' if item['kind'] == 'delivered' else item['sender'] or name
            lines.append(f"[{when}；{who}；原话#{item['id']}] {item['text']}")
        if segment['truncated']:
            lines.append('我这里只回忆了相关片段，前后还有往来。')
    if not lines:
        return ''
    return ('【我被当前话题唤起的其他会话回忆】\n'
            '这些是我曾经真实收到或说过的话，不是眼前的新消息。'
            '每段中的“你”和称呼属于当时的通信关系；我记得谁说了什么，不会把他们认成当前对象。'
            '别人说过某事与某事实际发生是不同证据，我当时的疑问也不表示现在仍未知。'
            '我能回忆，不等于我要转述；说不说、说多少是我的选择。\n' + '\n'.join(lines))
