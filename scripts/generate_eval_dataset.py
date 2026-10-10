"""Generate the synthetic, labeled evaluation dataset used by eval scripts.

The dataset is fully synthetic: every message starts with [SIMULATED], every
channel is demo_channel_*, and ground truth labels are assigned by this script's
explicit construction, not by a human annotation effort. Rerunning the script is
deterministic and reproduces the same fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "telegram_ai_monitor_demo"
    / "fixtures"
    / "eval_messages.json"
)

# (text, true_relevant, intent) — intent documents why the row exists.
ROWS: list[tuple[str, bool, str]] = [
    # True positives the rule should get right.
    ("Python task queue library 4.0 released with a stable public demo.", True, "clean release"),
    ("A new Python typing helper is now available for local testing.", True, "clean availability"),
    ("Python data validation toolkit 2.3 released; changelog in repo.", True, "clean release"),
    ("Lightweight Python scheduler released with offline examples.", True, "clean release"),
    ("Python HTTP mock library 1.2 is available on the package index.", True, "clean availability"),
    ("Python CLI framework 0.9 released with documentation updates.", True, "clean release"),
    ("The Python retry helper you asked about is now available.", True, "clean availability"),
    ("Python config loader 3.1 released; backwards compatible.", True, "clean release"),
    ("A Python log analyzer was released as an open source demo.", True, "clean release"),
    ("Python test fixture generator is available for evaluation.", True, "clean availability"),
    ("Python SQLite wrapper 2.0 released with WAL support.", True, "clean release"),
    ("New Python circuit breaker library released this week.", True, "clean release"),
    ("Python rate limiter 1.1 is available; benchmarks included.", True, "clean availability"),
    ("A Python schema validator was released with strict mode.", True, "clean release"),
    ("Python queue inspector tool available for local install.", True, "clean availability"),
    # Promotions the rule's fixed blocklist misses (LLM/fallback target).
    ("Python course released with a limited discount code this weekend.", False, "promo outside blocklist"),
    ("Grab the Python bootstrapping kit — limited offer ends today.", False, "promo outside blocklist"),
    ("Python mentorship program launched; coupon code inside.", False, "promo outside blocklist"),
    ("Exclusive deal: Python tooling bundle at half price for subscribers.", False, "promo outside blocklist"),
    ("Python ebook giveaway — click the affiliate link before it expires.", False, "promo outside blocklist"),
    ("Last chance to buy the Python automation bundle at 80% off.", False, "promo outside blocklist"),
    ("Sponsored placement: Python hosting provider recommends this stack.", False, "promo in blocklist"),
    ("This advertisement covers a Python certification program.", False, "promo in blocklist"),
    ("Promotion alert: Python template marketplace flash sale.", False, "promo in blocklist"),
    # Rule false positives: keyword match but actually promo/course/chat.
    ("A paid Python certification course released its enrollment page.", False, "rule false positive"),
    ("The marketing team released a Python-themed merchandise line.", False, "rule false positive"),
    ("Someone released a Python raffle bot to farm giveaway entries.", False, "rule false positive"),
    ("A retail store promotion mentions Python socks are available.", False, "rule false positive"),
    # Chatter / discussion.
    ("Weekend weather discussion and general community chat.", False, "chatter"),
    ("Anyone here following the football match tonight?", False, "chatter"),
    ("Reminder: community call moved to Thursday.", False, "chatter"),
    ("What is everyone using for note taking these days?", False, "chatter"),
    ("Congrats to the maintainers on the forum milestone!", False, "chatter"),
    ("Memes thread — post your best ones below.", False, "chatter"),
    ("Is the meetup room still open after 9pm?", False, "chatter"),
    ("Sharing my vacation photos from last month.", False, "chatter"),
    # Off-topic technical.
    ("Rust compiler 1.80 released with async improvements.", False, "off-topic release"),
    ("Go 1.23 is available; generics iterator support added.", False, "off-topic release"),
    ("JavaScript bundler released a new tree-shaking mode.", False, "off-topic release"),
    ("A C++ graphics library is now available for download.", False, "off-topic release"),
    # Python-related but no release/availability signal.
    ("Python packaging standards are being debated again.", False, "python without release"),
    ("Why does Python keep winning the language survey?", False, "python without release"),
    ("My Python script broke and I cannot figure out why.", False, "python without release"),
    ("Python style guide discussion continues in the comments.", False, "python without release"),
    # Duplicates of an earlier true positive.
    ("Python task queue library 4.0 released with a stable public demo.", True, "duplicate"),
    ("Python task queue library 4.0 released with a stable public demo.", True, "duplicate"),
    # Ambiguous / edge cases.
    ("Python tool will be available next month, not yet.", False, "ambiguous future"),
    ("A Python thing was released, whatever that means.", True, "ambiguous minimal"),
    ("Pythons released from the zoo are available for adoption.", False, "ambiguous trap"),
    ("PYTHON TOOLKIT released (all caps announcement).", True, "case variation"),
    ("python toolkit available", True, "minimal phrasing"),
    ("The python-based crawler toolkit v2 released quietly.", True, "hyphenated topic"),
    ("Monty Python episode remastered and available on streaming.", False, "not the language"),
    ("Python 3.13 RC1 released for downstream testing.", True, "version release"),
    ("Beta access available for a Python profiling suite.", True, "beta availability"),
    ("A Python GUI framework released its roadmap, not the code.", False, "roadmap only"),
    ("Python security patch released; update your installs.", True, "security release"),
    ("Discounted Python course — final promotion reminder.", False, "promo mix"),
    ("Available now: the annual Python survey results.", False, "survey not tool"),
    ("A Python web scraper framework released version 5.", True, "clean release"),
    ("Free trial available for a Python cloud IDE (marketing).", False, "promo mix"),
    ("Python build backend released with reproducible wheels.", True, "clean release"),
    ("Community poll: which Python feature should we cover next?", False, "chatter"),
    ("Python dependency resolver 2.1 is available for testing.", True, "clean availability"),
    ("Sponsored webinar on Python deployment best practices.", False, "promo in blocklist"),
    ("A Python documentation generator released its 8th major version.", True, "clean release"),
    ("Off-topic: the channel banner got a redesign.", False, "chatter"),
    ("Python mocking helper available behind a paywall signup.", False, "promo mix"),
    ("Local Python user group announced a social evening.", False, "chatter"),
    ("Python microservice template released under MIT license.", True, "clean release"),
    ("Reminder that advertising is not allowed in this channel.", False, "meta mention"),
    ("A Python debugging talk was released as a recorded video.", False, "content not tool"),
    ("Python memory profiler snapshots are available in the docs.", True, "clean availability"),
    ("Rumor: a Python package manager rewrite may be released someday.", False, "rumor future"),
    ("Python linting suite 3.0 released; drop-in replacement.", True, "clean release"),
    ("Job posting: Python developer wanted, relocation required.", False, "job posting"),
    ("Python ORM benchmark suite is available on the mirror.", True, "clean availability"),
    ("The word python appears here without any release or availability.", False, "keyword only"),
    # Rule false negatives: relevant, but without the rule's keyword markers.
    ("Python background task library just shipped version 6.", True, "rule false negative"),
    ("A fresh Python uptime monitor is out, install instructions inside.", True, "rule false negative"),
    ("Python retry decorator library hit its 1.0 milestone today.", True, "rule false negative"),
    ("New Python config validator landed on the package index.", True, "rule false negative"),
]


def main() -> int:
    channels = ("demo_channel_alpha", "demo_channel_beta", "demo_channel_gamma")
    rows = []
    for index, (text, relevant, intent) in enumerate(ROWS, start=1):
        rows.append(
            {
                "message_id": f"sim-msg-{index:03d}",
                "channel": channels[index % len(channels)],
                "text": f"[SIMULATED] {text}",
                "true_relevant": relevant,
                "intent": intent,
            }
        )
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    total = len(rows)
    positives = sum(1 for row in rows if row["true_relevant"])
    print(f"wrote {FIXTURE_PATH}")
    print(f"rows={total} relevant_true={positives} relevant_false={total - positives}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
