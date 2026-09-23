"""Only reviewed batch rows may reach the remote recall form."""
import copy
import datetime as dt
from pathlib import Path
from urllib.parse import urlsplit

from . import core
from .jdy import Client, date_string
from .privacy import clean_text


def row_key(row, private_terms=()):
    raw = str(row.get("来源URL") or "").strip()
    parsed = urlsplit(raw)
    if parsed.hostname in ("samr.gov.cn", "www.samr.gov.cn"):
        raw = "samr.gov.cn" + parsed.path.rstrip("/")
    return "|".join((raw, row["日期str"],
                     clean_text(str(row["公司"]).strip(), private_terms),
                     str(int(row["数量"]))))


def synchronize(base, config=None, *, dry_run=False, client=None):
    base = Path(base)
    path = base / "batches" / "current.json"
    batch = core.read(path)
    if batch["status"] == "complete":
        return {"status": "complete", "skipped": True}
    validated = core.review(dict(copy.deepcopy(batch), status="pending"), batch["reviews"])
    rows = core.batch_rows(validated)
    private_terms = (config or {}).get("private_terms", [])
    keys = [row_key(row, private_terms) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate recall rows in batch")
    text = {key: clean_text(value, private_terms) for key, value in core.log_text(validated).items()}
    if dry_run:
        return {"status": batch["status"], "new_announcements": len(batch["new"]),
                "recall_rows": len(rows), "detail": text["detail"], "dry_run": True}
    if config is None:
        raise ValueError("A local Jiandaoyun configuration is required")
    client = client or Client(config)
    fields = config["record_fields"]
    log_fields = config["log_fields"]
    batch.setdefault("created_ids", [])
    batch.setdefault("attempted_keys", [])
    existing = {}
    # Zero new qualifying rows => no remote recall-form read at all.
    for raw in client.list(config["recall_entry_id"]) if rows else []:
        record = raw.get("data") or raw
        key = row_key({"来源URL": record.get(fields["来源URL"], ""),
                       "日期str": date_string(record.get(fields["日期"])),
                       "公司": record.get(fields["公司"], ""),
                       "数量": record.get(fields["数量"], 0)}, private_terms)
        did = record.get("_id") or raw.get("_id")
        if not did:
            raise RuntimeError("Existing recall record has no data_id")
        existing[key] = str(did)
    created = skipped = 0
    for row, key in zip(rows, keys):
        if key in batch["uploaded"] or key in existing:
            if key not in batch["uploaded"]:
                batch["uploaded"][key] = existing[key]
                if key in batch["attempted_keys"] and existing[key] not in batch["created_ids"]:
                    batch["created_ids"].append(existing[key])
                core.save(path, batch)
            skipped += 1
            continue
        values = {fields[name]: clean_text(row.get(name, ""), private_terms)
                  for name in ("公司", "车型", "分类", "原因", "来源URL", "备注")}
        date = dt.datetime.fromisoformat(row["日期str"]).replace(tzinfo=core.CST)
        values[fields["日期"]] = date.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        values[fields["数量"]] = row["数量"]
        if key not in batch["attempted_keys"]:
            batch["attempted_keys"].append(key)
        core.save(path, batch)
        # No blind retry of a potentially accepted create.
        did = client.create(config["recall_entry_id"], values)
        batch["uploaded"][key] = did
        batch["created_ids"].append(did)
        core.save(path, batch)
        existing[key] = did
        created += 1
    marker = "[batch:%s]" % batch["id"]
    if not batch.get("log_id") and batch.get("log_attempted"):
        for raw in client.list(config["log_entry_id"]):
            record = raw.get("data") or raw
            if marker in str(record.get(log_fields["判定结论"], "")):
                batch["log_id"] = record.get("_id") or raw.get("_id")
                if not batch["log_id"]:
                    raise RuntimeError("Existing log has no data_id")
                break
    if not batch.get("log_id"):
        batch["log_attempted"] = True
        core.save(path, batch)
        values = {
            "运行时间": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "本次新增召回": len(rows),
            "本次推送简道云": len(batch["created_ids"]),
            "判定结论": text["conclusion"] + "\n" + marker,
            "逐条明细": text["detail"],
            "子代理交叉比对": text["crosscheck"],
            "推送 data_id": "\n".join(batch["created_ids"]) or "无",
        }
        batch["log_id"] = client.create(config["log_entry_id"],
                                       {log_fields[key]: value for key, value in values.items()})
    core.save(path, batch)
    core.complete(batch, base)
    return {"status": "complete", "new_announcements": len(batch["new"]),
            "recall_rows": len(rows), "created_this_attempt": created,
            "skipped_this_attempt": skipped, "log_written": bool(batch["log_id"])}
