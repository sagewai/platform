# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Deterministic intake: template routing, schedule phrases, clarification, preview."""

from __future__ import annotations

from sagewai.work.tasks.intake import (
    extract_schedule,
    route,
    score_template,
    tokenize,
)
from sagewai.work.tasks.models import TaskDefaults
from sagewai.work.tasks.templates import CATALOGUE

DEFAULTS = TaskDefaults(project_id="project-a", timezone="Europe/Berlin")


def test_tokenize_drops_stopwords_and_short_tokens() -> None:
    assert tokenize("Run daily research on 10 competitor websites and email me") == [
        "run", "daily", "research", "competitor", "websites", "email",
    ]


def test_extract_schedule_compiles_phrases_to_cron() -> None:
    assert extract_schedule("every weekday at 9 AM scan the vendor blogs") == "0 9 * * 1-5"
    assert extract_schedule("daily at 7:30 visit each URL") == "30 7 * * *"
    assert extract_schedule("every morning summarise the news") == "0 8 * * *"
    assert extract_schedule("nightly crawl of product pages") == "0 2 * * *"
    assert extract_schedule("compile a weekly landscape report") == "0 8 * * 1"
    assert extract_schedule("fetch prices hourly") == "0 * * * *"
    assert extract_schedule("add a pause control to the game") is None


def test_route_auto_routes_a_clear_research_brief() -> None:
    result = route("Every weekday at 9 AM scan these 5 vendor blogs and produce a digest", DEFAULTS)
    assert result.template_id == "scheduled_research_report"
    assert result.band == "auto_route"
    assert result.cron == "0 9 * * 1-5"
    assert result.timezone == "Europe/Berlin"
    assert [question.id for question in result.questions] == ["sources"]
    assert result.questions[0].defaultable is False
    assert "read" in result.preview.lower() and "approval" in result.preview.lower()


def test_route_auto_routes_a_clear_software_brief() -> None:
    brief = (
        "Build the web application described in this design document: a dashboard with "
        "login, an API, and persistence, covered by deterministic tests in the repository"
    )
    result = route(brief, DEFAULTS.model_copy(update={"target": None}))
    assert result.template_id == "software_delivery"
    assert result.band == "auto_route"
    assert result.cron is None
    assert [question.id for question in result.questions] == ["repository"]


def test_route_short_or_unmatched_brief_is_synthesis_with_outcome_question() -> None:
    result = route("make it better", DEFAULTS)
    assert result.template_id == "software_delivery"
    assert result.band == "synthesis"
    ids = [question.id for question in result.questions]
    assert "outcome" in ids and "repository" in ids
    outcome = next(question for question in result.questions if question.id == "outcome")
    assert outcome.defaultable and outcome.default


def test_route_never_asks_more_than_three_questions() -> None:
    result = route("do", DEFAULTS)
    assert len(result.questions) <= 3


def test_score_template_is_symmetric_in_catalogue_order() -> None:
    brief = tokenize("Monitor these 8 news sources every morning and summarise AI headlines")
    scores = {template_id: score_template(brief, template) for template_id, template in CATALOGUE.items()}
    assert scores["scheduled_research_report"] > scores["software_delivery"]


def test_route_prefilled_repository_removes_the_repository_question() -> None:
    from sagewai.work.tasks.models import SoftwareTarget

    defaults = DEFAULTS.model_copy(
        update={"target": SoftwareTarget(repository_path="/repo", owner="o", repo="r", verification_image="sha256:" + "a" * 64)}
    )
    result = route("Add a pause control to the browser game and cover it with tests", defaults)
    assert result.template_id == "software_delivery"
    assert "repository" not in [question.id for question in result.questions]
    assert result.slots["repository"] == "/repo"
