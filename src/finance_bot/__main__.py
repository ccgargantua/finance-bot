import logging
import os
import sys

from finance_bot.config import ConfigError, load_config, load_dotenv


def main() -> int:
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        load_dotenv(os.environ)
        cfg = load_config(os.environ)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    from finance_bot.discord_client import FinanceBot

    bot = FinanceBot(cfg)
    bot.run(cfg.discord_token, log_handler=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
