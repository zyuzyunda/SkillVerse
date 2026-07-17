"""CLI для LLM-ассистента и готовых сценариев."""

from __future__ import annotations

import argparse

from src.llm.assistant import ask
from src.llm.scenarios import SCENARIOS, get_scenario


def main() -> None:
    parser = argparse.ArgumentParser(description="Competence platform LLM assistant")
    parser.add_argument("--question", "-q", type=str, default=None)
    parser.add_argument("--scenario", "-s", type=str, default=None, help="id сценария")
    parser.add_argument("--role", type=str, default=None)
    parser.add_argument("--list", action="store_true", help="Показать сценарии")
    parser.add_argument("--no-llm", action="store_true", help="Только fallback по фактам")
    args = parser.parse_args()

    if args.list:
        for s in SCENARIOS:
            print(f"{s.id:16} {s.title}\n  {s.question}\n")
        return

    question = args.question
    intent = None
    if args.scenario:
        sc = get_scenario(args.scenario)
        if not sc:
            raise SystemExit(f"Неизвестный сценарий: {args.scenario}")
        question = sc.question
        intent = sc.intent
    if not question:
        raise SystemExit("Укажите --question или --scenario (см. --list)")

    result = ask(question, role_group=args.role, use_llm=not args.no_llm, intent=intent)
    print(result["answer"])
    print(f"\n[{result['mode']} / {result['intent']} / role={result['role_group']}]")


if __name__ == "__main__":
    main()
