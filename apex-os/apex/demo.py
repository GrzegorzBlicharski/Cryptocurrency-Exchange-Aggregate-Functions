"""Synthetic DEMO data (source='demo') to explore APEX before real data exists.

Deliberately contains patterns the agents should find: morning study retains better,
phone time goes with less deep work, sleep worsens in the last week, a high-match job
with a 2-day deadline, a weak law area. Remove with `apex demo --remove`.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .audit import publish
from .models import (
    CareerOpportunity, DeepWork, Energy, Error, Goal, LanguageSession, LawSession, LearningSession, Movement,
    PlanItem, Question, Recovery, ScreenTime, Skill, Sleep, Test, Workout,
)

DEMO = "demo"


def seed(db: Session, today: date, days: int = 60, rng_seed: int = 7) -> None:
    rng = random.Random(rng_seed)
    db.add_all([
        Goal(title="Reach C1 German (Goethe C1) (demo)", domain="german", weight=5, metric="test %", target_value=80,
             deadline=today + timedelta(days=240)),
        Goal(title="Pass law exam with strong contract law (demo)", domain="law", weight=4),
        Goal(title="Land an in-house legal role in a German-speaking company (demo)", domain="career", weight=4),
        Goal(title="Train 3x/week without injury (demo)", domain="fitness", weight=2),
    ])
    for name, lvl, ev in [("German", 3, "B2 certificate"), ("Contract law", 3, "coursework"),
                          ("Legal research", 3, "thesis"), ("English", 5, "C2"), ("GDPR", 2, ""),
                          ("Contract drafting", 2, "")]:
        db.add(Skill(name=name, level=lvl, evidence=f"{ev} [demo]".strip(), domain="career"))

    skills = ["speaking", "writing", "reading", "listening", "vocabulary", "grammar"]
    base = {"speaking": 52, "writing": 60, "reading": 72, "listening": 66, "vocabulary": 64, "grammar": 58}
    areas = {"contract": 74, "civil": 68, "criminal": 52, "eu": 42, "labour": 61}

    for i in range(days, -1, -1):
        d = today - timedelta(days=i)
        late = i <= 6  # last week: worse sleep, more phone
        sleep_min = int(rng.gauss(400 if late else 455, 25))
        bed_h = 23 + (1 if late and rng.random() < 0.6 else 0)
        db.add(Sleep(day=d, bed_time=f"{bed_h % 24:02d}:{rng.choice([0, 15, 30, 45]):02d}",
                     wake_time="07:00", duration_min=sleep_min, quality=rng.randint(2, 4) if late else rng.randint(3, 5),
                     source=DEMO))
        db.add(Energy(day=d, hour=9, level=max(1, min(10, int(rng.gauss(5 if late else 7, 1)))), source=DEMO))
        db.add(Recovery(day=d, subjective=max(1, min(10, int(rng.gauss(5 if late else 7, 1)))),
                        resting_hr=int(rng.gauss(60, 2)), source=DEMO))
        if i == 0:
            continue  # it's morning: only sleep/energy/recovery exist for today
        phone = int(rng.gauss(170 if late else 110, 30))
        db.add(ScreenTime(day=d, total_min=phone + 200, phone_min=phone, social_min=int(phone * 0.45),
                          pickups=int(phone / 2), source=DEMO))
        dw = max(0, int(rng.gauss(200 - phone * 0.7, 20)))
        if dw:
            db.add(DeepWork(day=d, start_hour=9, minutes=dw, domain="law", task="study", planned=True,
                            interruptions=max(0, int(phone / 40)), context_switches=rng.randint(1, 5),
                            quality=rng.randint(3, 5), source=DEMO))
        steps = int(rng.gauss(4500 if late else 7500, 1500))
        db.add(Movement(day=d, steps=max(500, steps), active_min=rng.randint(10, 40), sedentary_min=rng.randint(500, 720),
                        source=DEMO))
        if d.weekday() in (0, 3) and not late:
            db.add(Workout(day=d, kind="strength", minutes=50, rpe=7, source=DEMO))

        # German: mostly passive reading/listening; morning sessions retain better.
        for _ in range(rng.choice([1, 1, 2])):
            skill = rng.choices(skills, weights=[1, 1, 3, 3, 2, 1])[0]
            hour = rng.choice([8, 9, 19, 20, 21])
            mode = "passive" if skill in ("reading", "listening") and rng.random() < 0.8 else "active"
            progress = (days - i) * 0.08
            acc = base[skill] + progress + rng.gauss(0, 5)
            ret = (72 if hour < 12 else 58) + rng.gauss(0, 7)
            s = LearningSession(domain="german", day=d, start_hour=hour, minutes=rng.choice([30, 45, 60]),
                                method=rng.choice(["podcast", "app", "tutor", "textbook"]), focus=rng.randint(2, 5),
                                distractions=rng.randint(0, 4), retention_score=round(max(0, min(100, ret)), 1),
                                source=DEMO)
            s.language = LanguageSession(skill=skill, mode=mode, accuracy=round(max(0, min(100, acc)), 1))
            db.add(s)
        if i % 10 == 0:
            for sk in skills:
                db.add(Test(domain="german", area=sk, title="weekly check", day=d, source=DEMO,
                            score=round(max(0, min(100, base[sk] + (days - i) * 0.1 + rng.gauss(0, 4))), 1)))

        # Law: questions daily on 1-2 areas.
        for area in rng.sample(list(areas), 2):
            hour = rng.choice([8, 10, 18, 20])
            mins = rng.choice([40, 60, 90])
            s = LearningSession(domain="law", day=d, start_hour=hour, minutes=mins, method="past papers",
                                focus=rng.randint(2, 5), retention_score=round((70 if hour < 12 else 60) + rng.gauss(0, 6), 1),
                                source=DEMO)
            s.law = LawSession(area=area, activity="questions" if rng.random() < 0.85 else "reading")
            db.add(s)
            p = areas[area] / 100
            for _k in range(8):
                topic = {"eu": rng.choice(["preliminary ruling", "direct effect"]),
                         "criminal": rng.choice(["mens rea", "attempt"])}.get(area, "general")
                db.add(Question(domain="law", area=area, topic=topic, day=d, correct=rng.random() < p,
                                time_sec=rng.gauss(75, 15), source=DEMO))

        if i >= 1:
            for title, dom, mins in [("Law questions block", "law", 90), ("German active practice", "german", 60),
                                     ("Gym / walk", "fitness", 45)]:
                st = rng.choices(["done", "partial", "skipped"], weights=[5, 2, 3])[0]
                db.add(PlanItem(day=d, title=f"{title} (demo)", domain=dom, planned_min=mins, status=st,
                                actual_min=mins if st == "done" else mins // 2 if st == "partial" else 0,
                                fail_reason="phone / distraction" if st == "skipped" and rng.random() < 0.6 else
                                ("tired" if st == "skipped" else "")))

    db.add(Error(domain="german", area="grammar", category="Dativ after Wechselpräpositionen", occurrences=6,
                 last_seen=today - timedelta(days=1), source=DEMO))
    db.add(Error(domain="law", area="eu", category="confuse direct effect vs. direct applicability", occurrences=3,
                 last_seen=today - timedelta(days=2), source=DEMO))
    now = datetime.now(timezone.utc)
    db.add_all([
        CareerOpportunity(title="Junior Legal Counsel (Contracts)", organization="Example GmbH (demo)",
                          location="Berlin / remote", deadline=today + timedelta(days=2), strategic_fit=5,
                          requirements=[{"skill": "German", "level": 4, "required": True},
                                        {"skill": "Contract law", "level": 3, "required": True},
                                        {"skill": "Contract drafting", "level": 3, "required": True},
                                        {"skill": "English", "level": 4, "required": False}],
                          salary_text="", source=DEMO, retrieved_at=now),
        CareerOpportunity(title="Legal Data Protection Analyst", organization="Sample AG (demo)",
                          location="Vienna", deadline=today + timedelta(days=20), strategic_fit=3,
                          requirements=[{"skill": "GDPR", "level": 4, "required": True},
                                        {"skill": "German", "level": 3, "required": True},
                                        {"skill": "Legal research", "level": 3, "required": False}],
                          source=DEMO, retrieved_at=now),
        CareerOpportunity(title="Paralegal (unrelated stack)", organization="Filtered Ltd (demo)", strategic_fit=1,
                          requirements=[{"skill": "Patent law", "level": 4}, {"skill": "French", "level": 4}],
                          source=DEMO, retrieved_at=now),
    ])
    publish(db, "demo.seeded")
    db.commit()


def remove(db: Session) -> int:
    n = 0
    for model in (LearningSession, Test, Question, Error, Sleep, Energy, Recovery, Movement, Workout, ScreenTime,
                  DeepWork, CareerOpportunity):
        n += db.query(model).filter_by(source=DEMO).delete()
    n += db.query(Goal).filter(Goal.title.like("% (demo)")).delete(synchronize_session=False)
    n += db.query(PlanItem).filter(PlanItem.title.like("% (demo)")).delete(synchronize_session=False)
    n += db.query(Skill).filter(Skill.evidence.like("%[demo]")).delete(synchronize_session=False)
    publish(db, "demo.removed")
    db.commit()
    return n
