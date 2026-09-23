"""Official-list metadata collection only. Never downloads announcement bodies."""
import datetime as dt
import json
import re
from html.parser import HTMLParser
from urllib import error, request
from urllib.parse import urljoin, urlsplit

from . import core

DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def official_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ("https", "http") or parsed.hostname not in ("samr.gov.cn", "www.samr.gov.cn") or parsed.username or parsed.password:
        raise ValueError("Only public SAMR listing URLs are supported")
    # Collection must not silently read a known announcement instead of a list.
    if "/art/" in parsed.path:
        raise ValueError("Provide a listing URL, not an announcement body URL")
    return url


class OfficialRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class ListingParser(HTMLParser):
    """Recognize explicit dated LI rows; reject incomplete or ambiguous recall links."""
    def __init__(self, base_url):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.stack = []
        self.anchor = None
        self.items = []
        self.unresolved = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "li":
            self.stack.append({"links": [], "text": []})
        elif tag == "a" and self.stack:
            self.anchor = {"url": attrs.get("href", ""), "text": [],
                           "title": attrs.get("title", "")}

    def handle_data(self, data):
        if self.stack:
            self.stack[-1]["text"].append(data)
        if self.anchor is not None:
            self.anchor["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.anchor is not None:
            if self.stack:
                self.stack[-1]["links"].append(self.anchor)
            self.anchor = None
        elif tag == "li" and self.stack:
            row = self.stack.pop()
            links = []
            for anchor in row["links"]:
                title = anchor["title"] or "".join(anchor["text"]).strip()
                if "召回" in title and "/art/" in anchor["url"]:
                    links.append((title, urljoin(self.base_url, anchor["url"])))
            if not links:
                return
            dates = set(DATE.findall(" ".join(row["text"])))
            if len(links) != 1 or len(dates) != 1:
                self.unresolved = True
                return
            title, url = links[0]
            if "…" in title or "..." in title:
                self.unresolved = True
                return
            self.items.append({"title": title, "date": dates.pop(), "url": url})


def parse_listing(text, source_url):
    try:
        obj = json.loads(text)
    except ValueError:
        obj = text
    found = []

    def visit(value):
        if isinstance(value, list):
            for part in value:
                visit(part)
        elif isinstance(value, dict):
            title = value.get("title") or value.get("Title")
            date = value.get("date") or value.get("MakeTime") or value.get("publishTime")
            url = value.get("url") or value.get("href") or value.get("Url")
            if title and "召回" in str(title) and url and not date:
                raise ValueError("Listing recall row is missing its date")
            if title and date and url:
                if "召回" in str(title):
                    found.append({"title": title, "date": str(date)[:10],
                                  "url": urljoin(source_url, url)})
            else:
                for part in value.values():
                    visit(part)
        elif isinstance(value, str) and "<li" in value.lower():
            parser = ListingParser(source_url)
            parser.feed(value)
            if parser.unresolved:
                raise ValueError("Listing has undated, truncated or ambiguous recall rows")
            found.extend(parser.items)
    visit(obj)
    # Empty JS shells, API error JSON, and 403 HTML must not become "no new".
    return core.validate_candidates(found)


def make_snapshot(items, source_urls, coverage_since):
    dt.date.fromisoformat(coverage_since)
    if not source_urls:
        raise ValueError("At least one official listing source is required")
    for url in source_urls:
        official_url(url)
    items = core.validate_candidates(items)
    if min(item["date"] for item in items) > coverage_since:
        raise ValueError("Listing does not reach the requested overlap date; fetch more pages")
    return {"version": 1, "checked_at": dt.datetime.now(core.CST).isoformat(),
            "source_urls": source_urls, "coverage_since": coverage_since,
            "coverage_complete": True, "items": items}


def fetch_snapshot(source_urls, coverage_since):
    items = []
    opener = request.build_opener(OfficialRedirect())
    for url in source_urls:
        official_url(url)
        req = request.Request(url, headers={"User-Agent": "RecallMonitor/0.1 (metadata-only)",
                                          "Accept": "text/html,application/json"})
        try:
            with opener.open(req, timeout=45) as response:
                text = response.read().decode("utf-8-sig")
        except error.HTTPError as exc:
            raise RuntimeError("Official listing HTTP %s; no successful check recorded" % exc.code) from None
        except (error.URLError, TimeoutError, OSError, UnicodeError):
            raise RuntimeError("Official listing unavailable; no successful check recorded") from None
        items.extend(parse_listing(text, url))
    return make_snapshot(items, source_urls, coverage_since)


def load_snapshot(path):
    snapshot = core.read(path)
    if not isinstance(snapshot, dict) or snapshot.get("coverage_complete") is not True:
        raise ValueError("A complete metadata snapshot is required, not a raw/partial array")
    checked = dt.datetime.fromisoformat(snapshot["checked_at"])
    if checked.tzinfo is None or checked.astimezone(core.CST).date().isoformat() != core.today():
        raise ValueError("Use a listing checked today; resume saved batches separately")
    validated = make_snapshot(snapshot["items"], snapshot["source_urls"], snapshot["coverage_since"])
    validated["checked_at"] = snapshot["checked_at"]
    return validated
