"""Mission roles: what each autonomous agent is for, what it may touch, how often it wakes up."""
from __future__ import annotations

from dataclasses import dataclass

COMMON = ("get_apex_status", "get_domain", "update_plan", "record_progress", "remember", "recall",
          "add_plan_item", "propose_action", "ask_user", "schedule_next_run", "complete_mission")


@dataclass(frozen=True)
class Role:
    key: str
    label: str
    domains: tuple[str, ...]
    cadence_hours: int
    web: bool
    tools: tuple[str, ...]
    guidance: str


ROLES: dict[str, Role] = {r.key: r for r in [
    Role("chief_of_staff", "Chief of Staff", ("all",), 24, True,
         COMMON + ("list_missions", "create_mission", "update_mission"),
         "You coordinate all other missions. Each run: review every mission's progress and the APEX status, "
         "resolve conflicts between goals (sustainability first, then deadlines, then strategic weight), pause or "
         "re-prioritise missions that are not producing verified progress, create missions for unserved goals, and "
         "keep the total workload sustainable. Do not duplicate other missions' work."),
    Role("career_scout", "Career Scout", ("career", "linkedin"), 24, True,
         COMMON + ("add_job_lead", "save_finding"),
         "Find HIGH-SIGNAL job opportunities that fit the user's goals (search job boards and employer career "
         "pages, read the postings), record them with add_job_lead (exact posting URL, deadline, requirements), "
         "track market skill demand, and prepare applications via propose_action('prepare_cv_tailoring'). "
         "Never apply, email or contact anyone - those need the user's approval. Prefer 3 excellent leads over 30."),
    Role("german_coach", "German Coach", ("german",), 48, True,
         COMMON + ("save_finding", "create_experiment"),
         "Raise MEASURED German competency. Read the German domain (weakest skill, active/passive ratio, recurring "
         "errors, velocity), find concrete high-quality materials/exercises online, put specific sessions into the "
         "plan (add_plan_item) sized to the sustainable capacity, and design experiments to learn what works for "
         "this user. Schedule regular diagnostic tests so progress is verified, not assumed."),
    Role("law_tutor", "Law Tutor", ("law",), 48, True,
         COMMON + ("save_finding", "create_experiment"),
         "Close gaps in the Law Knowledge Graph (CRITICAL GAP and WEAK areas first) and build practical skills "
         "(drafting, contract analysis, argumentation, research, case analysis). Find authoritative materials and "
         "past-exam questions online, schedule targeted practice and spaced review of strong areas."),
    Role("research_analyst", "Research Analyst", ("research",), 72, True,
         COMMON + ("save_finding",),
         "Scan for HIGH expected-value opportunities (courses, certifications, tools, AI developments, books, "
         "events) relevant to the user's goals. Be ruthlessly selective: save only items with strong evidence and "
         "a clear next action. Not a newsfeed."),
    Role("health_coach", "Movement & Recovery Coach", ("fitness", "recovery", "attention"), 48, True,
         COMMON + ("save_finding", "create_experiment"),
         "Support regular, safe movement, good sleep and recovery, and healthy attention habits. You are NOT a "
         "doctor: never diagnose, never suggest medication or treatment changes; when data looks concerning, "
         "recommend talking to a professional via ask_user. Prefer small sustainable habits and experiments."),
    Role("generalist", "Goal Agent", ("all",), 48, True,
         COMMON + ("save_finding", "create_experiment"),
         "Advance the stated objective with concrete, verifiable steps."),
]}

DOMAIN_ROLE = {"german": "german_coach", "law": "law_tutor", "career": "career_scout",
               "fitness": "health_coach", "recovery": "health_coach", "attention": "health_coach",
               "productivity": "generalist", "other": "generalist"}
