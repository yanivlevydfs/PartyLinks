"""A safety net on every log handler: no email address, OAuth code / state or unsubscribe token
reaches a log line (uvicorn's failed-request lines carry the query string). Code logs user ids only."""

from __future__ import annotations

import logging
import re

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
SECRET_PARAMS = re.compile(r"\b(code|state|t|token)=[^&\s\"']+")


def scrub(text: str) -> str:
    return SECRET_PARAMS.sub(r"\1=***", EMAIL.sub("***@***", text))


class Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(scrub(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = scrub(record.msg)
        return True
