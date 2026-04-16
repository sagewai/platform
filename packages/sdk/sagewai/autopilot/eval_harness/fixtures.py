# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""SYNTHETIC golden-goal fixture set for the autopilot eval harness.

These are NOT production eval data. Production golden goals live in the
private ``sagewai/sagewai-llm`` repository.  The 50+ synthetic goals in
this module are designed purely to exercise the three confidence bands
(auto_route, picker, synthesis) against the three synthetic blueprints
defined in ``tests/autopilot/fixtures.py``.

All blueprint IDs used here are ``SYNTHETIC_*`` prefixed to make their
test-only nature unmistakable.

Goal distribution:
  - auto_route : 30 goals (10 per synthetic blueprint)
  - picker     : 10 goals (ambiguous phrasings that land in the middle band)
  - synthesis  : 12 goals (novel requests with no matching blueprint)
  Total        : 52 goals
"""

from __future__ import annotations

from .types import GoldenGoal, GoldenGoalSet

# ---------------------------------------------------------------------------
# Auto-route goals — SYNTHETIC_scheduled_research (10 goals)
# ---------------------------------------------------------------------------
_SCHEDULED_AUTO = [
    GoldenGoal(
        goal="Run daily research on 10 competitor websites and email me a summary each morning",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Every weekday at 9 AM scan the following 5 vendor blogs and produce a digest",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Schedule a recurring job to research what my top vendors shipped each week",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Automatically track competitor product launches daily using web search",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Monitor these 8 news sources every morning and summarise AI-related headlines",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Set up a scheduled research agent that fetches pricing from 3 competitors nightly",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Daily at 7 AM visit each URL in my vendor list and extract any new announcements",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Create a weekly research loop that compiles a market landscape report from vendor sites",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Crawl these 12 product pages every night and flag anything that changed",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Run a cron-driven agent to fetch and summarise the top 5 papers on arxiv daily",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="auto_route",
    ),
]

# ---------------------------------------------------------------------------
# Auto-route goals — SYNTHETIC_event_triage (10 goals)
# ---------------------------------------------------------------------------
_EVENT_AUTO = [
    GoldenGoal(
        goal="Classify and route incoming support tickets by severity using these 4 category labels",
        expected_blueprint_id="SYNTHETIC_event_triage",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Automatically triage GitHub issues as they arrive and assign them to the right team",
        expected_blueprint_id="SYNTHETIC_event_triage",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="On each new Slack message in #incidents classify urgency and page the on-call if P0",
        expected_blueprint_id="SYNTHETIC_event_triage",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Route inbound customer emails to sales or support based on intent classification",
        expected_blueprint_id="SYNTHETIC_event_triage",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Triage incoming webhook events from Stripe and classify as fraud or legitimate",
        expected_blueprint_id="SYNTHETIC_event_triage",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Event-driven agent: classify every new Jira ticket and assign the correct label",
        expected_blueprint_id="SYNTHETIC_event_triage",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="When a new form submission arrives classify it and forward to the matching department",
        expected_blueprint_id="SYNTHETIC_event_triage",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Automatically tag incoming bug reports by component using these taxonomy labels",
        expected_blueprint_id="SYNTHETIC_event_triage",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="On each new order event determine if it's high-value and route to account manager",
        expected_blueprint_id="SYNTHETIC_event_triage",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Classify real-time sensor alerts by severity and dispatch the right response team",
        expected_blueprint_id="SYNTHETIC_event_triage",
        expected_band="auto_route",
    ),
]

# ---------------------------------------------------------------------------
# Auto-route goals — SYNTHETIC_batch_etl (10 goals)
# ---------------------------------------------------------------------------
_BATCH_AUTO = [
    GoldenGoal(
        goal="Run a nightly ETL job that pulls data from Postgres, transforms it, and loads into BigQuery",
        expected_blueprint_id="SYNTHETIC_batch_extract",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Process last month's CSV exports, clean them, and generate a summary report",
        expected_blueprint_id="SYNTHETIC_batch_extract",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Batch-transform 500 raw JSON records into normalised form and write them to S3",
        expected_blueprint_id="SYNTHETIC_batch_extract",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Overnight: extract sales data from the warehouse, compute KPIs, and email the CFO",
        expected_blueprint_id="SYNTHETIC_batch_extract",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Run a weekly batch pipeline that merges CRM exports and deduplicates contacts",
        expected_blueprint_id="SYNTHETIC_batch_extract",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Process all images in the upload bucket with our vision model and store results in DB",
        expected_blueprint_id="SYNTHETIC_batch_extract",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Batch ingest log files from the last 7 days and flag anomalies using our rules",
        expected_blueprint_id="SYNTHETIC_batch_extract",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Monthly: aggregate user behaviour events and produce the retention cohort table",
        expected_blueprint_id="SYNTHETIC_batch_extract",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Run offline scoring on today's lead list using our propensity model and write scores",
        expected_blueprint_id="SYNTHETIC_batch_extract",
        expected_band="auto_route",
    ),
    GoldenGoal(
        goal="Extract, transform, and load yesterday's order data from MySQL into our analytics DB",
        expected_blueprint_id="SYNTHETIC_batch_extract",
        expected_band="auto_route",
    ),
]

# ---------------------------------------------------------------------------
# Picker goals — ambiguous phrasings that should land in the middle band
# ---------------------------------------------------------------------------
_PICKER = [
    GoldenGoal(
        goal="Research stuff about my competitors",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="picker",
    ),
    GoldenGoal(
        goal="Handle my tickets somehow",
        expected_blueprint_id="SYNTHETIC_event_triage",
        expected_band="picker",
    ),
    GoldenGoal(
        goal="Process some data and put it somewhere useful",
        expected_blueprint_id="SYNTHETIC_batch_extract",
        expected_band="picker",
    ),
    GoldenGoal(
        goal="I need an agent that does monitoring maybe every day or when something happens",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="picker",
    ),
    GoldenGoal(
        goal="Route things based on what kind they are",
        expected_blueprint_id="SYNTHETIC_event_triage",
        expected_band="picker",
    ),
    GoldenGoal(
        goal="Pull data and do something with it on a schedule",
        expected_blueprint_id="SYNTHETIC_batch_extract",
        expected_band="picker",
    ),
    GoldenGoal(
        goal="Track vendor activity kind of regularly",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="picker",
    ),
    GoldenGoal(
        goal="When things come in categorise them",
        expected_blueprint_id="SYNTHETIC_event_triage",
        expected_band="picker",
    ),
    GoldenGoal(
        goal="Do some transformation on data overnight maybe",
        expected_blueprint_id="SYNTHETIC_batch_extract",
        expected_band="picker",
    ),
    GoldenGoal(
        goal="I want to check some pages and summarise them but I'm not sure how often",
        expected_blueprint_id="SYNTHETIC_scheduled_research",
        expected_band="picker",
    ),
]

# ---------------------------------------------------------------------------
# Synthesis goals — novel requests with no matching blueprint
# ---------------------------------------------------------------------------
_SYNTHESIS = [
    GoldenGoal(
        goal="Build a real-time collaborative whiteboard with AI suggestion sidebars",
        expected_blueprint_id=None,
        expected_band="synthesis",
    ),
    GoldenGoal(
        goal="Generate and publish a full podcast episode from a blog post every week",
        expected_blueprint_id=None,
        expected_band="synthesis",
    ),
    GoldenGoal(
        goal="Run a tournament bracket for my internal hackathon and automatically post results",
        expected_blueprint_id=None,
        expected_band="synthesis",
    ),
    GoldenGoal(
        goal="Translate my entire product documentation to 12 languages and keep them in sync",
        expected_blueprint_id=None,
        expected_band="synthesis",
    ),
    GoldenGoal(
        goal="Design a personalised onboarding journey for each new user based on their role",
        expected_blueprint_id=None,
        expected_band="synthesis",
    ),
    GoldenGoal(
        goal="Negotiate SaaS pricing with vendors on my behalf using multi-turn email threads",
        expected_blueprint_id=None,
        expected_band="synthesis",
    ),
    GoldenGoal(
        goal="Automatically file patent applications based on engineer invention disclosures",
        expected_blueprint_id=None,
        expected_band="synthesis",
    ),
    GoldenGoal(
        goal="Compose and send personalised birthday messages to every customer on their birthday",
        expected_blueprint_id=None,
        expected_band="synthesis",
    ),
    GoldenGoal(
        goal="Run a simulation of our supply chain and surface bottlenecks in a visual report",
        expected_blueprint_id=None,
        expected_band="synthesis",
    ),
    GoldenGoal(
        goal="Write code reviews for every PR automatically and suggest architecture improvements",
        expected_blueprint_id=None,
        expected_band="synthesis",
    ),
    GoldenGoal(
        goal="Manage my calendar and proactively reschedule conflicting meetings with attendees",
        expected_blueprint_id=None,
        expected_band="synthesis",
    ),
    GoldenGoal(
        goal="Do something totally novel that no blueprint could possibly anticipate",
        expected_blueprint_id=None,
        expected_band="synthesis",
    ),
]

#: Synthetic golden-goal set for CI use.
#:
#: 52 goals across three bands:
#:   - auto_route  : 30 (10 per synthetic blueprint)
#:   - picker      : 10
#:   - synthesis   : 12
SYNTHETIC_GOLDEN_GOALS = GoldenGoalSet(
    version="1.0.0",
    description=(
        "Synthetic golden goals for autopilot eval CI. "
        "NOT production eval data — see sagewai/sagewai-llm for production fixtures."
    ),
    goals=tuple([*_SCHEDULED_AUTO, *_EVENT_AUTO, *_BATCH_AUTO, *_PICKER, *_SYNTHESIS]),
)
