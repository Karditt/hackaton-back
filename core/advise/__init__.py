"""Диагностика невыполнимости и рекомендации (E15, P1).

Частичных планов нет (инвариант 10). Если задание невыполнимо, пользователь
получает причину и то, чего не хватает: площадку (с предлагаемым местом),
борт, другую модель, окончание временного запрета (заказчик, №33, №42).

Советы выводятся правилами по кодам причин (`diagnose`) и проверяются расчётом
(`verify_recommendations`): сценарий с правкой считается конвейером, и к
совету дописывается, хватает ли её. Подбор площадок для всей области (№28,
«будет вообще шикарно») — P2.
"""

from __future__ import annotations

from core.advise.diagnose import advice_note, diagnose, site_spot
from core.advise.verify import ADVISED_SITE_ID, PlanFn, verify_recommendations

__all__ = [
    "ADVISED_SITE_ID",
    "PlanFn",
    "advice_note",
    "diagnose",
    "site_spot",
    "verify_recommendations",
]
