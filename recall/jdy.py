"""Jiandaoyun transport. Installation identifiers come only from local config."""
import datetime as dt
import json
import os
import time
from urllib import error, request

from .core import CST, read

API = "https://api.jiandaoyun.com/api/v5/app/entry/data/"
RECORD_FIELDS = ("日期", "公司", "车型", "数量", "分类", "原因", "来源URL", "备注")
LOG_FIELDS = ("运行时间", "本次新增召回", "本次推送简道云", "判定结论",
              "逐条明细", "子代理交叉比对", "推送 data_id")


def load_config(path):
    config = read(path)
    for name in ("app_id", "recall_entry_id", "log_entry_id"):
        value = config.get(name)
        if not isinstance(value, str) or not value.strip() or value.startswith("REPLACE_"):
            raise ValueError("Configure " + name + " in your local config file")
    for name, required in (("record_fields", RECORD_FIELDS), ("log_fields", LOG_FIELDS)):
        fields = config.get(name, {})
        if not isinstance(fields, dict):
            raise ValueError("Invalid field mapping: " + name)
        for field in required:
            value = fields.get(field)
            if not isinstance(value, str) or not value or value.startswith("REPLACE_"):
                raise ValueError("Configure field mapping: " + name + "." + field)
        if len(set(fields.values())) != len(fields):
            raise ValueError("Duplicate destination field in " + name)
    if not isinstance(config.get("private_terms", []), list):
        raise ValueError("private_terms must be an array")
    return config


class NoRedirect(request.HTTPRedirectHandler):
    # Never forward a credential-bearing request to another host.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, config, key=None):
        self.config = config
        self._key = key if key is not None else os.environ.get("JDY_API_KEY", "")
        if not self._key or self._key.startswith("REPLACE_"):
            raise ValueError("Set JDY_API_KEY in the process environment")
        self._opener = request.build_opener(NoRedirect())
        self._last_request = 0.0

    def post(self, operation, payload):
        if operation not in ("list", "create"):
            raise ValueError("Unsupported operation")
        delay = 0.1 - (time.monotonic() - self._last_request)
        if delay > 0:
            time.sleep(delay)
        self._last_request = time.monotonic()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(API + operation, data=body, headers={
            "Authorization": "Bearer " + self._key,
            "Content-Type": "application/json",
        })
        try:
            with self._opener.open(req, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            # Responses may echo private fields; never log raw bodies/URLs/headers.
            raise RuntimeError("Jiandaoyun HTTP %s during %s; state retained" %
                               (exc.code, operation)) from None
        except (error.URLError, TimeoutError, OSError, ValueError):
            raise RuntimeError("Jiandaoyun request failed during %s; reconcile before retry" %
                               operation) from None
        if not isinstance(result, dict):
            raise RuntimeError("Invalid Jiandaoyun response")
        return result

    def list(self, entry):
        rows, cursor = [], ""
        seen = set()
        while True:
            result = self.post("list", {"app_id": self.config["app_id"],
                                      "entry_id": entry, "limit": 100, "data_id": cursor})
            page = result.get("data")
            if not isinstance(page, list) or any(not isinstance(row, dict) for row in page):
                raise RuntimeError("Jiandaoyun listing missing a data array")
            rows.extend(page)
            if len(page) < 100:
                return rows
            cursor = str(page[-1].get("_id") or "")
            if not cursor or cursor in seen:
                raise RuntimeError("Jiandaoyun pagination did not advance")
            seen.add(cursor)

    def create(self, entry, fields):
        result = self.post("create", {"app_id": self.config["app_id"], "entry_id": entry,
                                     "data": {key: {"value": value} for key, value in fields.items()}})
        data = result.get("data")
        did = (data.get("_id") if isinstance(data, dict) else None) or result.get("id")
        if not did:
            raise RuntimeError("Create response lacks data_id; reconcile before retry")
        return str(did)


def date_string(value):
    if isinstance(value, (float, int)):
        return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).astimezone(CST).date().isoformat()
    text = str(value or "").strip()
    if not text:
        raise ValueError("Existing remote record has no date")
    if len(text) == 10:
        return dt.date.fromisoformat(text).isoformat()
    parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CST)
    return parsed.astimezone(CST).date().isoformat()
