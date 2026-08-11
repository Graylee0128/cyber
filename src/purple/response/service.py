"""Golden target 內的 response agent systemd entrypoint。"""

from __future__ import annotations

import logging
import os
import time

from purple.response.agent import ResponseAgent
from purple.response.http_link import HttpLink

log = logging.getLogger("purple.response.service")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    base_url = os.environ["PURPLE_RESPONSE_URL"]
    poll_s = float(os.environ.get("PURPLE_RESPONSE_POLL_S", "2"))
    agent = ResponseAgent(link=HttpLink(base_url))
    log.info("response agent 就緒；只主動 pull %s", base_url)

    while True:
        try:
            events = agent.run_once()
            for event in events:
                log.info(
                    "%s source_ip=%s event_id=%s",
                    event["event_type"],
                    event["target"]["source_ip"],
                    event["event_id"],
                )
        except Exception:
            # 控制面暫時不可達不該讓 unit 死掉；下輪仍由 target 主動重試。
            log.exception("pull/report 失敗；%ss 後重試", poll_s)
        time.sleep(poll_s)


if __name__ == "__main__":
    main()
