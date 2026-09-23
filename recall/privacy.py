"""Sanitize outbound text; credentials and installation IDs are never logged."""
import re

TAG = "〔已隐去本地信息〕"
PATH = re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\|/Users/|/home/|/tmp/)[^\s，。、；;）)\"'|]*")
FILE = re.compile(r"[\w\u4e00-\u9fff().（）-]+\.(?:xlsx|xlsm|xls|csv|json|py|env|log|txt|md|db|sqlite|docx|pptx|zip)\b", re.I)


def clean_text(value, private_terms=()):
    if not isinstance(value, str):
        return value
    text = PATH.sub(TAG, value)
    for term in private_terms:
        if term:
            text = text.replace(str(term), TAG)
    text = FILE.sub(TAG, text)
    if PATH.search(text) or FILE.search(text):
        raise ValueError("Outbound text contains local information")
    return text
