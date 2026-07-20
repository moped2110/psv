# src/psv/i18n.py
from __future__ import annotations

from typing import Any

SUPPORTED = ("en", "de")
DEFAULT_LANG = "en"

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "app.name": "PSV",
        "cli.help": "PSV command line interface",
        "cli.ok": "OK",
        "cli.error": "Error: {detail}",
        "cli.done": "Done.",
        "cli.lang_set": "Language set to {lang}",
        "cli.unknown_lang": "Unsupported language: {lang}",
        "finding.severity.info": "info",
        "finding.severity.warn": "warning",
        "finding.severity.crit": "critical",
        "finding.quote_mismatch": "Quote mismatch for nonce {nonce}",
        "finding.nonce_reuse": "Nonce reuse detected: {nonce}",
        "finding.negative_amount": "Negative amount rejected: {amount}",
        "finding.invalid_chain": "Invalid chain id: {chain_id}",
        "finding.balance_drift": "Balance drift for {token}: truth={truth} belief={belief}",
        "finding.replay_ok": "Replay session {session_id} matched",
        "finding.replay_fail": "Replay session {session_id} diverged",
        "output.summary": "{count} finding(s), {crit} critical",
        "output.no_findings": "No findings.",
        "output.chain": "Chain {chain_id}",
        "output.block": "Block {block_number}",
    },
    "de": {
        "app.name": "PSV",
        "cli.help": "PSV-Kommandozeilenschnittstelle",
        "cli.ok": "OK",
        "cli.error": "Fehler: {detail}",
        "cli.done": "Fertig.",
        "cli.lang_set": "Sprache auf {lang} gesetzt",
        "cli.unknown_lang": "Nicht unterstützte Sprache: {lang}",
        "finding.severity.info": "info",
        "finding.severity.warn": "warnung",
        "finding.severity.crit": "kritisch",
        "finding.quote_mismatch": "Quote-Abweichung für Nonce {nonce}",
        "finding.nonce_reuse": "Nonce-Wiederverwendung erkannt: {nonce}",
        "finding.negative_amount": "Negativer Betrag abgelehnt: {amount}",
        "finding.invalid_chain": "Ungültige Chain-ID: {chain_id}",
        "finding.balance_drift": "Saldo-Abweichung für {token}: Wahrheit={truth} Annahme={belief}",
        "finding.replay_ok": "Replay-Sitzung {session_id} stimmt überein",
        "finding.replay_fail": "Replay-Sitzung {session_id} weicht ab",
        "output.summary": "{count} Fund(e), {crit} kritisch",
        "output.no_findings": "Keine Funde.",
        "output.chain": "Chain {chain_id}",
        "output.block": "Block {block_number}",
    },
}

_current_lang = DEFAULT_LANG


def set_lang(lang: str) -> str:
    global _current_lang
    normalized = (lang or DEFAULT_LANG).lower().strip()
    if normalized not in _MESSAGES:
        raise ValueError(t("cli.unknown_lang", lang=lang))
    _current_lang = normalized
    return _current_lang


def get_lang() -> str:
    return _current_lang


def t(key: str, lang: str | None = None, **kwargs: Any) -> str:
    use = (lang or _current_lang).lower()
    table = _MESSAGES.get(use) or _MESSAGES[DEFAULT_LANG]
    template = table.get(key) or _MESSAGES[DEFAULT_LANG].get(key) or key
    try:
        return template.format(**kwargs) if kwargs else template
    except KeyError:
        return template


def finding(code: str, severity: str = "info", lang: str | None = None, **kwargs: Any) -> dict[str, str]:
    sev_key = f"finding.severity.{severity}"
    msg_key = f"finding.{code}"
    return {
        "code": code,
        "severity": t(sev_key, lang=lang),
        "severity_id": severity,
        "message": t(msg_key, lang=lang, **kwargs),
    }


def format_findings(findings: list[dict[str, str]], lang: str | None = None) -> str:
    if not findings:
        return t("output.no_findings", lang=lang)
    lines = [f"[{f.get('severity', '?')}] {f.get('message', '')}" for f in findings]
    crit = sum(1 for f in findings if f.get("severity_id") == "crit")
    lines.append(t("output.summary", lang=lang, count=len(findings), crit=crit))
    return "\n".join(lines)

