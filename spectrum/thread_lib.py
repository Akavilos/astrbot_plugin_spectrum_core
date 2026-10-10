"""定义故事线类型库，展开带@的引用，并生成开线与收线素材。"""


from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .threads import (
    ELEMENTS, KINDS, PAYOFF_FORMS, SCOPES, VISIBILITIES, ThreadSpec,
)


REF_PREFIX = "@"


@dataclass(frozen=True)
class ThreadType:
    tid: str
    title: str
    visibility: str
    element: str
    scope: str
    kind: str
    payoff_form: str
    due_beats: int
    body: str
    payoff: str


    open_text: tuple[str, ...] = ()

    close_text: tuple[str, ...] = ()

    open_gate: tuple[dict, ...] = ()


    close_nl: str = ""

    close_cond: tuple[dict, ...] = ()
    example: str = ""
    trigger: dict = field(default_factory=dict)
    trigger_note: str = ""

    def to_spec(self, tid: str = "") -> ThreadSpec:
        return ThreadSpec(
            tid=tid or self.tid, title=self.title,
            element=self.element, visibility=self.visibility, scope=self.scope,
            kind=self.kind, payoff_form=self.payoff_form,
            planned_payoff=self.payoff, due_beats=self.due_beats,
            trigger=dict(self.trigger),
        )


POINTERS: tuple[ThreadType, ...] = (
    ThreadType(
        tid="situational_pressure",
        title="处境或性格施加的逼迫",
        visibility="pointer", element="event", scope="segment",
        kind="info", payoff_form="direct", due_beats=12,
        body="有一件外界的事在往一个方向推：一个期限、天气、别人的处境、"
             "身体的不舒服。不需要谁说任何话，是环境自己在挤。"
             "她可以装作没看见，但那件事不会走开，它只会在下一次被提到时更近一点。",
        payoff="那个「挤」真的兑现成一个必须做的选择。回收时要有明确的选择动作，"
               "不能只是「又想起来了一次」。",
        open_text=(
            "我持续受到某件事的影响，但它尚未形成明确结果，也未被我处理。",
        ),
        close_text=(
            "我已经对这件事作出明确回应；无论结果是哪一种，它都不再构成当前压力。",
        ),
        close_nl="我已经对这件事作出明确回应；无论结果是哪一种，它都不再构成当前压力。",
        trigger_note="「必须做的选择」是语义判断，Tier-1 的六种谓词表达不了",
    ),
    ThreadType(
        tid="character_action",
        title="人物的一个动作",
        visibility="pointer", element="event", scope="local",
        kind="person", payoff_form="direct", due_beats=8,
        body="有人做了一件不那么必要的小动作。它的意义不在当下，"
             "而在于它说明这个人接下来会做什么。写的时候不要解释，"
             "把它当成一件普通的事写过去就行。",
        payoff="那个动作被证明是一个更大的动作的前兆。",
        open_text=(
            "我注意到某人的一个具体动作，并将其保留在当前认知中；当下无法确定其意义。",
        ),
        close_text=(
            "我已经明确理解或确定了该动作的含义。",
        ),
        close_nl="我已经明确理解或确定了该动作的含义。",
        trigger_note="「这个人在为什么做铺垫」是语义判断，Tier-1 表达不了",
    ),
    ThreadType(
        tid="stated_wish_or_threat",
        title="说出一个愿望，或一句威胁",
        visibility="pointer", element="speech_act", scope="segment",
        kind="info", payoff_form="direct", due_beats=15,
        body="她说出了想要什么，或者把一句狠话放在了桌上。"
             "说出口的那一刻它就变成了一个承诺，别人会记着，"
             "而她自己也会发现自己被这句话绑住了。",
        payoff="那个愿望被满足或被明确拒绝；那句威胁真的兑现，或明确收回。"
               "**「明确」两个字是重点** —— 含糊过去不算回收。",
        open_text=(
            "我已经说出一个明确的愿望或威胁，但其后果尚未兑现或落定。",
        ),
        close_text=(
            "这句话已经产生明确后果；无论最终实现还是落空，均视为已有下文。",
        ),
        close_nl="这句话已经产生明确后果；无论最终实现还是落空，均视为已有下文。",
        trigger_note="「愿望被满足 / 威胁被收回」是语义判断，Tier-1 表达不了",
    ),
    ThreadType(
        tid="prediction_or_omen",
        title="预言、预告",
        visibility="pointer", element="speech_act", scope="act",
        kind="info", payoff_form="variant", due_beats=25,
        body="有人把还没发生的事先说了出来。可能是认真的预告，"
             "也可能只是一句玩笑，但话已经在那儿了。"
             "写的时候让它听起来像随口一说，回收的时候才有落差。",
        payoff="那件事发生了，而且发生的形状跟当时说的**不完全一样**。"
               "（取 variant 而不是 direct：原样兑现会显得只是在交作业。）",
        open_text=(
            "我已经获知某件尚未发生之事的预告或预言，其结果尚未出现。",
        ),
        close_text=(
            "所预告的事情已经实际发生；具体结果与原先描述是否一致，不影响收束。",
        ),
        close_nl="所预告的事情已经实际发生；具体结果与原先描述是否一致，不影响收束。",
        trigger_note="「那件事发生了」是语义判断，Tier-1 表达不了",
    ),
    ThreadType(
        tid="discovery_about_past",
        title="对过去的一次发现",
        visibility="pointer", element="object", scope="segment",
        kind="info", payoff_form="direct", due_beats=15,
        body="她知道了过去某件事的另一面。知道之后，「过去」这个背景就变了，"
             "之前发生过的事要重新理解一遍。这条线的力量在于"
             "**它改的不是将来，是已经发生过的部分**。",
        payoff="那个新理解导致她现在做一个跟以前不同的决定。",
        open_text=(
            "我已经获得关于过去某件事的新信息，并因此改变了对既往事实的理解。",
        ),
        close_text=(
            "我已经基于新的理解作出与原先不同的明确决定。",
        ),
        close_nl="我已经基于新的理解作出与原先不同的明确决定。",
        trigger_note="「她现在的决定跟以前不同」是语义判断，Tier-1 表达不了",
    ),
    ThreadType(
        tid="unanswered_question",
        title="一个没有被回答的问题",
        visibility="pointer", element="speech_act", scope="segment",
        kind="feeling", payoff_form="direct", due_beats=12,
        body="有一句话已经成形，但没说出口。它还在。"
             "她可以一直不说，但它不会自己消失，"
             "只是每次见到这个人的时候会再浮上来一次。",
        payoff="要么说出口了，要么明确决定永远不说。**两个都算回收** ——"
               "「决定不说」也是一个了结，不该让它一直挂着。",


        close_cond=({"tier": 2, "var": "unsaid", "op": "==", "value": False},),
        open_text=(
            "我已经提出或意识到一个尚未得到处理的问题。",
        ),
        close_text=(
            "我已经实际作出回应；或者我已经明确决定不再回应。",
        ),
        close_nl="我已经实际作出回应；或者我已经明确决定不再回应。",
        trigger_note="「说出口 / 决定不说」要用 `unsaid` 判，而它是 Tier-2 变量，"
                     "Tier-1 的 resolve() 读不到（见本文件顶部与本节注释）。"
                     "收线改走 storylet，那条路径读得到。",
    ),
    ThreadType(
        tid="charged_object",
        title="一件被赋予了意义的物件",
        visibility="pointer", element="object", scope="segment",
        kind="prop", payoff_form="direct", due_beats=15,
        body="一件普通东西，因为某件事变得不普通了。别人看不出区别，只有她知道。"
             "它会一直在她身边，而她不会主动提它。",
        payoff="这个东西被用上、被送出去、或者弄丢了。",
        open_text=(
            "我已经将某件物品赋予特殊意义，并明确意识到这一意义。",
        ),
        close_text=(
            "该物品已经不再由我持有；无论是使用、赠出还是遗失，均视为收束。",
        ),
        close_nl="该物品已经不再由我持有；无论是使用、赠出还是遗失，均视为收束。",
        trigger_note="「这个东西被用上」是语义判断，Tier-1 表达不了",
    ),
)


PLANTS: tuple[ThreadType, ...] = (
    ThreadType(
        tid="future_condition",
        title="日后会变得重要的处境、性格或动机",
        visibility="plant", element="object", scope="act",
        kind="person", payoff_form="implicit", due_beats=30,
        body="她的一个性子、一个习惯、或者身上一直带着的一个条件。"
             "现在看只是「她就是这样的人」，不需要任何强调。"
             "将来它会变成那件事的答案。",
        payoff="这个条件成了一件大事的直接原因。",
        open_text=(
            "我已经确认自身存在某种持续的处境、性格倾向或动机，但它尚未直接导致当前事件。",
        ),
        close_text=(
            "该因素已经直接影响我的一个决定，并成为该决定的直接原因。",
        ),
        close_nl="该因素已经直接影响我的一个决定，并成为该决定的直接原因。",
        example="《绿野仙踪》坏女巫对水的退缩；"
                "《长夜漫漫路迢迢》玛丽·蒂龙的失眠和没胃口（预示她重新用药）",
        trigger_note="「它成了一件大事的原因」是语义判断，Tier-1 表达不了",
    ),
    ThreadType(
        tid="character_attitude",
        title="会引发后续动作的人物态度",
        visibility="plant", element="speech_act", scope="segment",
        kind="person", payoff_form="direct", due_beats=15,
        body="她对某件事、某个人有一个态度。当下它只是态度，"
             "没造成任何后果 —— 但**态度会攒**。",
        payoff="那个态度变成一次具体的行动。",
        open_text=(
            "我已经形成对某人的明确看法，该看法尚未转化为具体行为。",
        ),
        close_text=(
            "该看法已经直接导致我的言语或行为。",
        ),
        close_nl="该看法已经直接导致我的言语或行为。",
        example="《Bug》彼得相信军方在搞秘密实验",
        trigger_note="「态度变成了行动」是语义判断，Tier-1 表达不了",
    ),
    ThreadType(
        tid="loaded_line",
        title="一句有隐藏含义的台词",
        visibility="plant", element="speech_act", scope="segment",
        kind="info", payoff_form="variant", due_beats=15,
        body="一句说得太平常的话，当下听起来只是随口一说。"
             "它的另一层意思要等到那件事发生之后才听得出来。",
        payoff="那句话被重新想起来，而且这次听懂了。"
               "（取 variant：同一句话换一层意思回来，这是它唯一的回收方式。）",
        open_text=(
            "我记得某人说过一句表面含义明确、但其更深层含义尚未被我理解的台词。",
        ),
        close_text=(
            "我已经理解或重新意识到这句话的隐藏含义。",
        ),
        close_nl="我已经理解或重新意识到这句话的隐藏含义。",
        example="《金童》乔把快车说成「流在我血里的毒」",
        trigger_note="「听懂了一句旧台词」是语义判断，Tier-1 表达不了",
    ),
    ThreadType(
        tid="rule_to_be_broken",
        title="一条会被打破的规则或仪式",
        visibility="plant", element="rule", scope="act",
        kind="info", payoff_form="direct", due_beats=20,
        body="一个她一直在守的规矩，或者一件每次都会做的事。"
             "现在它只是背景，没人会去想它 —— 所以写的时候也不要解释它。",
        payoff="它被打破了。**打破它比任何解释都更能说明发生了什么**，"
               "所以回收时不要配大段的心理描写。",
        open_text=(
            "我长期重复执行某项固定规则或仪式，并将其视为惯常行为。",
        ),
        close_text=(
            "我首次明确违反该规则或仪式；一次实际违背即可收束。",
        ),
        close_nl="我首次明确违反该规则或仪式；一次实际违背即可收束。",
        example="《怀疑》里神父和修女不能单独相处的禁忌",
        trigger_note="「规矩被打破了」是语义判断，Tier-1 表达不了",
    ),
    ThreadType(
        tid="future_object",
        title="一件日后会变得重要的物件",
        visibility="plant", element="object", scope="segment",
        kind="prop", payoff_form="direct", due_beats=15,
        body="一件此刻只是「在那儿」的东西。它的存在感要低，"
             "低到专门提一句会显得刻意 —— 所以只用一句话带过，"
             "混杂在别的动作里。",
        payoff="它在关键时刻被用上。",
        open_text=(
            "我身边出现一件当前尚无实际用途、但已进入我当前生活范围的物品。",
        ),
        close_text=(
            "该物品已经实际发挥作用。",
        ),
        close_nl="该物品已经实际发挥作用。",
        example="《兔子洞》里孩子洗好的衣服",
        trigger_note="「它被用上」是语义判断，Tier-1 表达不了",
    ),
    ThreadType(
        tid="not_what_it_seems",
        title="一件不是表面那样的事",
        visibility="plant", element="event", scope="act",
        kind="info", payoff_form="variant", due_beats=25,
        body="一件事发生了，看起来就是那个意思。"
             "🔴 **写的当下不要暗示** —— 让它在当时完全成立，"
             "不要留破绽给读者去猜。它的另一面要等到后面才翻开。",
        payoff="那件事的真相翻过来，前面所有的画面都要重看一遍。",
        open_text=(
            "我已经经历某件表面意义明确的事情，但尚未获得足以确认其真实情况的信息。",
        ),
        close_text=(
            "我已经确认事情与表面呈现不同，并需要据此重新理解此前事实。",
        ),
        close_nl="我已经确认事情与表面呈现不同，并需要据此重新理解此前事实。",
        example="《第六感》那场「结婚纪念日晚餐」",
        trigger_note="「真相翻过来」是语义判断，Tier-1 表达不了",
    ),
    ThreadType(
        tid="scaled_recurrence",
        title="会以更大规模重演的小事",
        visibility="plant", element="event", scope="act",
        kind="info", payoff_form="variant", due_beats=25,
        body="一件小得几乎不算事的事发生了，带一点当时的情绪。"
             "它本身不重要，重要的是它的**形状**。",
        payoff="同一个形状以更大的规模再来一次，这一次绕不过去。",
        open_text=(
            "我已经经历一次影响较小的异常或偏差，其性质尚未再次出现。",
        ),
        close_text=(
            "同类异常以明显更大规模再次发生，且我无法再将其视为偶发的小事。",
        ),
        close_nl="同类异常以明显更大规模再次发生，且我无法再将其视为偶发的小事。",
        example="《人鼠之间》伦尼不小心弄死老鼠，后来弄死柯利的妻子",
        trigger_note="「同一个形状重演」是语义判断，Tier-1 表达不了",
    ),
)


THREAD_LIB: tuple[ThreadType, ...] = POINTERS + PLANTS


LIB_BY_ID: dict[str, ThreadType] = {t.tid: t for t in THREAD_LIB}


OVERRIDABLE: frozenset[str] = frozenset({"title", "planned_payoff",
                                         "due_beats", "trigger"})


REF_ALSO_ALLOWS: frozenset[str] = OVERRIDABLE | {"id"}


def expand_thread_ref(raw: Any) -> tuple[Any, str]:
    """展开类型库引用，只允许覆盖标题、计划收尾、期限和触发等指定字段。"""
    if isinstance(raw, str):
        s = raw.strip()
        if not s.startswith(REF_PREFIX):
            return raw, ""
        return _expand(s, {})


    if not isinstance(raw, dict):
        return raw, ""
    tid = str(raw.get("id") or "").strip()
    if not tid.startswith(REF_PREFIX):
        return raw, ""
    return _expand(tid, raw)


def _expand(ref: str, over: dict) -> tuple[Any, str]:
    key = ref[len(REF_PREFIX):].strip()
    t = LIB_BY_ID.get(key)
    if t is None:
        return None, (f"线库里没有 {key!r}（可用的 {len(THREAD_LIB)} 条："
                      f"{'、'.join(LIB_BY_ID)}）")
    bad = sorted(set(over) - REF_ALSO_ALLOWS)
    if bad:
        return None, (f"引用线库时不能覆盖这些键：{bad} —— "
                      f"五轴是分类，改了就不是这一类了。"
                      f"不属于任何一类就别用 {REF_PREFIX}，直接写完整对象。")


    spec = t.to_spec()
    out: dict[str, Any] = {
        "id": spec.tid, "title": spec.title,
        "element": spec.element, "visibility": spec.visibility,
        "scope": spec.scope, "kind": spec.kind,
        "payoff_form": spec.payoff_form,
        "planned_payoff": spec.planned_payoff,
        "due_beats": spec.due_beats, "trigger": spec.trigger,
    }
    for k in OVERRIDABLE:
        if k in over and over[k] not in (None, ""):
            out[k] = over[k]
    return out, ""


def describe(tid: str) -> str:
    t = LIB_BY_ID.get(str(tid).lstrip(REF_PREFIX))
    if t is None:
        return ""
    return (f"{t.title}（{t.visibility}/{t.element}/{t.scope}"
            f"/{t.kind}/{t.payoff_form}，"
            f"{f'计划 {t.due_beats} 拍内收' if t.due_beats else '无期限'}）")


REUSED_OPEN: dict[str, str] = {
    "unanswered_question": "unsaid",
    "character_attitude": "bond-notice",
    "stated_wish_or_threat": "promise-pressure",
}


OPEN_PRIORITY = 4
CLOSE_PRIORITY = 8


OPEN_TENSION = 1
CLOSE_TENSION = -1


MAX_OPEN_FALLBACK = 5


def _open_condition(sid: str, max_open: int) -> dict:
    return {"all": [
        {"tier": 1, "pred": "flag", "var": "asleep", "equals": False},
        {"tier": 1, "pred": "threshold", "var": "open_threads_n",
         "op": "<", "value": max_open},
    ]}


def _close_condition(t: ThreadType, open_sid: str) -> dict:
    core = ([dict(c) for c in t.close_cond] if t.close_cond
            else [{"tier": 3, "nl": t.close_nl}])
    return {"all": core + [
        {"tier": 1, "pred": "flag", "var": "asleep", "equals": False},
    ]}, [open_sid]


def storylet_rows(max_open: int = MAX_OPEN_FALLBACK) -> list[dict]:
    import json as _json
    out: list[dict] = []
    for t in THREAD_LIB:
        if t.open_text and t.tid not in REUSED_OPEN:
            sid = f"open_{t.tid}"
            out.append({
                "sid": sid, "kind": "action",
                "narrative": _json.dumps(list(t.open_text), ensure_ascii=False),
                "condition": _json.dumps(_open_condition(sid, max_open),
                                         ensure_ascii=False),
                "effects": _json.dumps({"open_thread": REF_PREFIX + t.tid,
                                        "tension_delta": OPEN_TENSION},
                                       ensure_ascii=False),
                "meta": _json.dumps({"repeatable": True}, ensure_ascii=False),
                "priority": OPEN_PRIORITY, "weight": 1.0,
            })
        if t.close_text:
            sid = f"close_{t.tid}"
            open_sid = REUSED_OPEN.get(t.tid) or f"open_{t.tid}"
            cond, deps = _close_condition(t, open_sid)
            out.append({
                "sid": sid, "kind": "action",
                "narrative": _json.dumps(list(t.close_text), ensure_ascii=False),
                "condition": _json.dumps(cond, ensure_ascii=False),
                "depends_on": _json.dumps(deps, ensure_ascii=False),
                "effects": _json.dumps({"close_thread": REF_PREFIX + t.tid,
                                        "tension_delta": CLOSE_TENSION},
                                       ensure_ascii=False),
                "meta": _json.dumps({"repeatable": True}, ensure_ascii=False),
                "priority": CLOSE_PRIORITY, "weight": 1.0,
            })
    return out


def reuse_effect_patch() -> dict[str, dict]:
    return {sid: {"open_thread": REF_PREFIX + tid}
            for tid, sid in REUSED_OPEN.items()}


HANDWRITTEN_TENSION: dict[str, int] = {
    "unsaid": OPEN_TENSION,
    "bond-notice": OPEN_TENSION,
    "promise-pressure": OPEN_TENSION,
    "stall-relief": CLOSE_TENSION,
}


__all__ = [
    "REF_PREFIX", "ThreadType",
    "POINTERS", "PLANTS", "THREAD_LIB", "LIB_BY_ID",
    "OVERRIDABLE", "REF_ALSO_ALLOWS", "expand_thread_ref", "describe",
    "REUSED_OPEN", "storylet_rows", "reuse_effect_patch",
    "OPEN_PRIORITY", "CLOSE_PRIORITY",
    "OPEN_TENSION", "CLOSE_TENSION", "HANDWRITTEN_TENSION",

    "ELEMENTS", "VISIBILITIES", "SCOPES", "KINDS", "PAYOFF_FORMS",
]
