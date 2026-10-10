"""结合历史证据质量与当前关系信号更新好感、信任及融合记录。
没有有效信任证据时保持信任值；好感变化按方向与剩余区间缩放。"""


TRUST_WEIGHTS = {"reliability": 0.45, "honesty": 0.35, "benevolence": 0.20}
PRIOR_MASS = 12.0
MAX_MASS = 40.0


def evolve(affinity: float, trust: float, signals: dict, state: dict | None = None) -> dict:
    """按证据权重融合历史信任与当前信号；零信任证据不产生固定扣分。"""
    state = state or {"alpha": trust * PRIOR_MASS, "beta": (1.0 - trust) * PRIOR_MASS,
                      "evidence_mass": 0.0}
    alpha, beta = state["alpha"], state["beta"]
    code_score = alpha / (alpha + beta)
    weights = signals.get("evidence_weight", {})
    liking = signals["liking"] * weights.get("liking", 0.0)
    terms = {key: signals[key] * weights.get(key, 0.0) * weight
             for key, weight in TRUST_WEIGHTS.items()}
    signed = sum(terms.values())

    amount = abs(signed)
    model_score = 1.0 if signed > 0 else 0.0 if signed < 0 else code_score
    mass = alpha + beta
    if amount:

        retained = min(mass, MAX_MASS - amount)
        alpha, beta = alpha * retained / mass, beta * retained / mass
        alpha += max(signed, 0.0)
        beta += max(-signed, 0.0)
        new_trust = alpha / (alpha + beta)
        model_weight = amount / (retained + amount)
    else:
        new_trust, model_weight = trust, 0.0
    evidence_mass = min(MAX_MASS, state["evidence_mass"] + amount)
    room = 1.0 - affinity if liking > 0 else affinity
    return {
        "affinity": affinity + 0.04 * liking * room,
        "trust": new_trust,
        "liking_signal": liking, "trust_signal": signed,
        "state": {"alpha": alpha, "beta": beta, "evidence_mass": evidence_mass},
        "fusion": {"code_score": code_score, "model_score": model_score,
                   "code_weight": 1.0 - model_weight, "model_weight": model_weight,
                   "terms": terms, "evidence_amount": amount,
                   "uncertainty": 2.0 / (2.0 + evidence_mass)},
    }
