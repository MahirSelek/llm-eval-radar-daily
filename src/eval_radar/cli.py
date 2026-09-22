from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow `python -m eval_radar.cli` from src layout
ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def cmd_collect(args: argparse.Namespace) -> None:
    if args.accumulate:
        from eval_radar.jobs.dispatch import collect_and_accumulate

        payload = collect_and_accumulate()
        path = f"day-pool:{payload.get('report_day')}"
    else:
        from eval_radar.collect.pipeline import collect_all, save_digest

        payload = collect_all()
        path = str(save_digest(payload))
    print(json.dumps({"saved": path, "counts": payload.get("counts")}, indent=2))


def cmd_report(args: argparse.Namespace) -> None:
    from eval_radar.jobs.dispatch import collect_and_accumulate
    from eval_radar.report.render import render_report
    from eval_radar.telegram.send import send_message

    payload = collect_and_accumulate()
    text = render_report(payload)
    if args.print_only:
        print(text)
        return
    send_message(text)
    print("sent")


def cmd_bot(_: argparse.Namespace) -> None:
    from eval_radar.telegram.bot import main as bot_main

    bot_main()


def cmd_dashboard(args: argparse.Namespace) -> None:
    from eval_radar.web import create_app

    app = create_app()
    print(f"Eval Radar dashboard → http://127.0.0.1:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug)


def cmd_publish(args: argparse.Namespace) -> None:
    from eval_radar.publish.site import publish_daily_post

    result = publish_daily_post(dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def cmd_dispatch(args: argparse.Namespace) -> None:
    from eval_radar.jobs.dispatch import daily_dispatch

    result = daily_dispatch(force=args.force, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    p = argparse.ArgumentParser(prog="eval-radar")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="Fetch arXiv/GitHub/Reddit digest")
    c.add_argument(
        "--accumulate",
        action="store_true",
        help="Merge into today's day-pool instead of overwriting",
    )
    c.set_defaults(func=cmd_collect)

    r = sub.add_parser("report", help="Accumulate day-pool + Telegram send")
    r.add_argument("--print-only", action="store_true", help="Do not send Telegram")
    r.set_defaults(func=cmd_report)

    b = sub.add_parser("bot", help="Run interactive Telegram bot (polling + scheduler)")
    b.set_defaults(func=cmd_bot)

    d = sub.add_parser("dashboard", help="Local Flask control center")
    d.add_argument("--host", default="127.0.0.1")
    d.add_argument("--port", type=int, default=8765)
    d.add_argument("--debug", action="store_true")
    d.set_defaults(func=cmd_dashboard)

    pub = sub.add_parser("publish", help="Generate daily article + static docs site")
    pub.add_argument("--dry-run", action="store_true", help="Generate content without writing files")
    pub.set_defaults(func=cmd_publish)

    disp = sub.add_parser(
        "dispatch",
        help="Daily coordinated run: accumulate + Telegram + GitHub Pages (+ optional git push)",
    )
    disp.add_argument("--force", action="store_true", help="Ignore once-per-day marker")
    disp.add_argument("--dry-run", action="store_true")
    disp.set_defaults(func=cmd_dispatch)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
