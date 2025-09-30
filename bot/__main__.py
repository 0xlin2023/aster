from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from typing import Optional

from .config import BotConfig, load_config
from .mvp_bot import AsterMVPGridBot


def _configure_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _apply_overrides(cfg: BotConfig, *, dry_run: Optional[bool], log_level: Optional[str]) -> BotConfig:
    updates = {}
    if dry_run is not None and dry_run != cfg.dry_run:
        updates["dry_run"] = dry_run
    if log_level:
        updates["log_level"] = log_level.upper()
    return replace(cfg, **updates) if updates else cfg


async def _async_main(args: argparse.Namespace) -> None:
    cfg = load_config(args.config)
    cfg = _apply_overrides(cfg, dry_run=args.dry_run, log_level=args.log_level)
    _configure_logging(cfg.log_level)
    bot = AsterMVPGridBot(cfg, api_key=args.api_key, api_secret=args.api_secret)
    try:
        await bot.run()
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Interrupted by user, shutting down...")
        bot.request_stop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Aster MVP v2.1 grid bot")
    parser.add_argument("config", type=Path, help="Path to YAML config")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", dest="dry_run", action="store_const", const=True, help="Force dry-run mode")
    group.add_argument("--live", dest="dry_run", action="store_const", const=False, help="Enable live trading mode")
    parser.set_defaults(dry_run=None)
    parser.add_argument("--log-level", dest="log_level", help="Override log level (INFO, DEBUG, ...)")
    parser.add_argument("--api-key", dest="api_key", help="API key override", default=None)
    parser.add_argument("--api-secret", dest="api_secret", help="API secret override", default=None)
    args = parser.parse_args()
    asyncio.run(_async_main(args))


if __name__ == "__main__":
    main()
