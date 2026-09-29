"""Подбор места новой площадки (E15, долг №60, заказчик №28)."""

from __future__ import annotations

import math

from core.catalog import VehicleCatalog
from core.pipeline import PlanningRun
from core.schemas import Scenario
from core.sites import site_candidates, suggest_sites


def test_candidates_grow_from_the_farthest_work(runs: dict[str, PlanningRun]) -> None:
    """Скопление начинается с самой дальней от площадок подобласти и растёт по долям."""
    run = runs["medium"]
    assert run.result.decomposition is not None
    cells = run.result.decomposition.cells
    sites = [run.work_area.site(site.id) for site in run.scenario.primary_sites()]

    def gap(cell) -> float:
        return min(math.hypot(cell.centroid.x_m - s.x_m, cell.centroid.y_m - s.y_m) for s in sites)

    groups = site_candidates(cells, sites, (0.1, 0.3))
    assert [share for share, _ in groups] == [0.1, 0.3]
    farthest = max(cells, key=gap)
    total = sum(cell.area_m2 for cell in cells)
    for share, group in groups:
        assert group[0].id == farthest.id
        assert sum(cell.area_m2 for cell in group) >= share * total or len(group) == len(cells) // 2
    assert len(groups[0][1]) <= len(groups[1][1])


def test_suggested_sites_are_checked_by_planning(
    simple_scenario: Scenario, catalog: VehicleCatalog
) -> None:
    """Каждый кандидат — полный расчёт; первый расчёт — базовый, без новой площадки."""
    trials = suggest_sites(simple_scenario, catalog, shares=(0.2,))
    base, *candidates = trials
    assert base.position is None and base.feasible
    assert candidates
    for trial in candidates:
        assert trial.position is not None
        assert trial.share == 0.2
        assert trial.feasible and trial.makespan_s is not None
