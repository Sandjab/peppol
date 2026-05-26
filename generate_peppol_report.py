#!/usr/bin/env python3
"""
generate_peppol_report.py
─────────────────────────
Génère un rapport quotidien de comptages Peppol Directory (doctypes France),
avec historique persistant et graphique d'évolution.

Mode par défaut (brief)  : comptages bruts + table d'évolution + 2 graphiques.
Mode --detailed          : analyse complète avec signatures, échantillons, etc.

Dépendances :
    pip install requests jinja2 weasyprint

Usage typique (cron quotidien) :
    python generate_peppol_report.py
    python generate_peppol_report.py --detailed
    python generate_peppol_report.py --no-pdf --no-api

Sortie par défaut dans ./out/ :
    peppol_extension_report.html
    peppol_extension_report.pdf
    peppol_history.json
    peppol_extension_report_stats.json        (--detailed uniquement)

Auteur original du rapport : @Sandjab
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import sys
import time
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PARIS_TZ = ZoneInfo("Europe/Paris")

try:
    import requests
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError as e:
    sys.stderr.write(
        f"Dépendance manquante : {e.name}.\n"
        "Installer : pip install requests jinja2 weasyprint\n"
    )
    sys.exit(1)

# ════════════════════════════════════════════════════════════════════
#  Configuration
# ════════════════════════════════════════════════════════════════════

DIRECTORY_API_URL = "https://directory.peppol.eu/search/1.0/json"
PASR_URL = (
    "https://openpeppol.atlassian.net/wiki/download/attachments/2889318401/"
    "France%20-%20Peppol%20Authority%20Specific%20Requirements_2026.02.27.pdf?api=v2"
)
RATE_LIMIT_DELAY_S = 0.7
REQUEST_TIMEOUT_S = 90
DEFAULT_SAMPLE_SIZE = 1000
SCHEME = "busdox-docid-qns"
HISTORY_FILENAME = "peppol_history.json"

HTTP_PROXIES: dict[str, str] | None = None


def _normalize_proxy_host(raw: str) -> str:
    """Accepte [scheme://]host[:port]. Refuse les credentials inline."""
    raw = raw.strip()
    if not raw:
        raise ValueError("--proxy : valeur vide.")
    if "://" not in raw:
        raw = "http://" + raw
    scheme, _, rest = raw.partition("://")
    if not rest:
        raise ValueError("--proxy : hôte manquant.")
    if "@" in rest:
        raise ValueError(
            "--proxy n'accepte pas les credentials inline. "
            "Saisie au prompt, ou via PEPPOL_PROXY_USER / PEPPOL_PROXY_PASS."
        )
    return f"{scheme}://{rest}"


def _build_proxy_url(host_url: str, user: str, password: str) -> str:
    if not user:
        return host_url
    scheme, _, rest = host_url.partition("://")
    cred = urllib.parse.quote(user, safe="")
    if password:
        cred += ":" + urllib.parse.quote(password, safe="")
    return f"{scheme}://{cred}@{rest}"

DOCTYPES_FR: dict[str, dict[str, Any]] = {
    "ubl_cius": {
        "label": "France UBL Invoice CIUS",
        "urn_short": "urn:peppol:france:billing:cius:1.0 · UBL 2.1",
        "urn": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice##urn:cen.eu:en16931:2017#compliant#urn:peppol:france:billing:cius:1.0::2.1",
        "ext": False,
    },
    "ubl_ext": {
        "label": "France UBL Invoice EXTENDED-CTC-FR",
        "urn_short": "urn:peppol:france:billing:extended:1.0 · UBL 2.1",
        "urn": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice##urn:cen.eu:en16931:2017#conformant#urn:peppol:france:billing:extended:1.0::2.1",
        "ext": True,
    },
    "cii_cius": {
        "label": "France CII Invoice CIUS",
        "urn_short": "urn:peppol:france:billing:cius:1.0 · CII D22B",
        "urn": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100::CrossIndustryInvoice##urn:cen.eu:en16931:2017#compliant#urn:peppol:france:billing:cius:1.0::D22B",
        "ext": False,
    },
    "cii_ext": {
        "label": "France CII Invoice EXTENDED-CTC-FR",
        "urn_short": "urn:peppol:france:billing:extended:1.0 · CII D22B",
        "urn": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100::CrossIndustryInvoice##urn:cen.eu:en16931:2017#conformant#urn:peppol:france:billing:extended:1.0::D22B",
        "ext": True,
    },
    "facturx": {
        "label": "France Factur-X",
        "urn_short": "urn:peppol:france:billing:Factur-X:1.0 · PDF/A-3 + CII",
        "urn": "urn:peppol:doctype:pdf+xml##urn:cen.eu:en16931:2017#conformant#urn:peppol:france:billing:Factur-X:1.0::D22B",
        "ext": False,
    },
    "cdar": {
        "label": "France CDAR (statuts cycle de vie)",
        "urn_short": "urn:peppol:france:billing:cdv:1.0",
        "urn": "urn:un:unece:uncefact:data:standard:CrossDomainAcknowledgementAndResponse:100::CrossDomainAcknowledgementAndResponse##urn:peppol:france:billing:cdv:1.0::D22B",
        "ext": False,
    },
}

DOCTYPE_BIS = {
    "label": "Peppol BIS Billing 3.0 (UBL)",
    "urn": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice##urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0::2.1",
}

DOCTYPE_STYLES = {
    "ubl_cius": {"color": "#181818", "dash": "none", "width": 2.0, "short": "UBL CIUS"},
    "ubl_ext":  {"color": "#D90D25", "dash": "none", "width": 2.5, "short": "UBL EXT"},
    "cii_cius": {"color": "#181818", "dash": "5,3", "width": 2.0, "short": "CII CIUS"},
    "cii_ext":  {"color": "#D90D25", "dash": "5,3", "width": 2.5, "short": "CII EXT"},
    "facturx":  {"color": "#660000", "dash": "none", "width": 2.0, "short": "Factur-X"},
    "cdar":     {"color": "#960000", "dash": "2,3", "width": 1.6, "short": "CDAR"},
}


# ════════════════════════════════════════════════════════════════════
#  API Peppol Directory
# ════════════════════════════════════════════════════════════════════

def _encoded_doctype(urn: str) -> str:
    return urllib.parse.quote(f"{SCHEME}::{urn}", safe="")


def query_directory(urn: str, country: str | None, rpc: int = 1, rpi: int = 0) -> dict:
    url = f"{DIRECTORY_API_URL}?doctype={_encoded_doctype(urn)}&rpc={rpc}&rpi={rpi}"
    if country:
        url += f"&country={country}"
    resp = requests.get(url, timeout=REQUEST_TIMEOUT_S, proxies=HTTP_PROXIES)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} sur {url[:120]}…")
    return resp.json()


def fetch_count(urn: str, country: str | None = None) -> int:
    return int(query_directory(urn, country=country, rpc=1, rpi=0).get("total-result-count", 0))


def fetch_sample(urn: str, country: str = "FR", rpc: int = DEFAULT_SAMPLE_SIZE) -> list[dict]:
    return query_directory(urn, country=country, rpc=min(rpc, 1000), rpi=0).get("matches", [])


# ════════════════════════════════════════════════════════════════════
#  Historique
# ════════════════════════════════════════════════════════════════════

def load_history(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "runs": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("runs", {})
    return data


def save_history(history: dict, path: Path) -> None:
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")


def upsert_today(history: dict, today_key: str, counts_fr: dict[str, int]) -> None:
    history["runs"][today_key] = {
        "fetched_at": datetime.now(PARIS_TZ).isoformat(timespec="seconds"),
        "counts_fr": counts_fr,
    }


def sorted_dates(history: dict) -> list[date]:
    return sorted(date.fromisoformat(k) for k in history.get("runs", {}).keys())


def get_count_at(history: dict, d: date, doctype: str) -> int | None:
    run = history["runs"].get(d.isoformat())
    if not run:
        return None
    return run.get("counts_fr", {}).get(doctype)


def closest_run_at_or_before(history: dict, target: date) -> date | None:
    dates = sorted_dates(history)
    candidates = [d for d in dates if d <= target]
    return candidates[-1] if candidates else None


# ════════════════════════════════════════════════════════════════════
#  Évolution
# ════════════════════════════════════════════════════════════════════

def format_delta(delta: int | None) -> tuple[str, str]:
    if delta is None:
        return ("—", "dash")
    if delta == 0:
        return ("0", "zero")
    sign = "+" if delta > 0 else "−"
    return (f"{sign}{fr_int(abs(delta))}", "pos" if delta > 0 else "neg")


def build_evolution(history: dict, today_key: str) -> dict:
    """
    Retourne un dict {'rows': [...], 'refs': {...}}.
    'refs' donne pour chaque colonne la date de référence effectivement utilisée
    et l'écart en jours réel (utile quand des runs sont manqués).
    """
    today_d = date.fromisoformat(today_key)
    dates = sorted_dates(history)
    if not dates:
        return {"rows": [], "refs": {}}
    origin_d = dates[0]
    j1_d = closest_run_at_or_before(history, today_d - timedelta(days=1))
    j7_d = closest_run_at_or_before(history, today_d - timedelta(days=7))

    rows = []
    for key, meta in DOCTYPES_FR.items():
        today_v = get_count_at(history, today_d, key)
        if today_v is None:
            continue

        def diff_against(d: date | None) -> int | None:
            if d is None or d == today_d:
                return None
            prev_v = get_count_at(history, d, key)
            return today_v - prev_v if prev_v is not None else None

        d1_text, d1_cls = format_delta(diff_against(j1_d))
        d7_text, d7_cls = format_delta(diff_against(j7_d))
        d_o_text, d_o_cls = format_delta(diff_against(origin_d) if origin_d != today_d else None)

        rows.append({
            "key": key, "label": meta["label"], "ext": meta["ext"],
            "value": today_v,
            "d1": d1_text, "d1_class": d1_cls,
            "d7": d7_text, "d7_class": d7_cls,
            "d_orig": d_o_text, "d_orig_class": d_o_cls,
        })

    def _ref(d: date | None, nominal_days: int) -> dict:
        if d is None or d == today_d:
            return {"date": None, "date_short": "—", "gap_days": None, "nominal": nominal_days, "drift": False}
        gap = (today_d - d).days
        return {
            "date": d.isoformat(),
            "date_short": _date_short(d),
            "gap_days": gap,
            "nominal": nominal_days,
            "drift": gap != nominal_days,
        }

    refs = {
        "j1":   _ref(j1_d, nominal_days=1),
        "j7":   _ref(j7_d, nominal_days=7),
        "orig": _ref(origin_d if origin_d != today_d else None, nominal_days=(today_d - origin_d).days),
    }
    return {"rows": rows, "refs": refs}


def build_counts_rows(counts_fr: dict[str, int]) -> list[dict]:
    return [{
        "key": k, "label": m["label"], "urn_short": m["urn_short"],
        "ext": m["ext"], "value": counts_fr.get(k, 0),
    } for k, m in DOCTYPES_FR.items()]


# ════════════════════════════════════════════════════════════════════
#  SVG en pur Python
# ════════════════════════════════════════════════════════════════════

def _nice_round(x: float) -> int:
    if x <= 0:
        return 1
    import math
    exp = math.floor(math.log10(x))
    base = 10 ** exp
    for mult in (1, 2, 2.5, 5, 10):
        cand = mult * base
        if cand >= x:
            return int(cand)
    return int(10 * base)


def _y_ticks(vmin: float, vmax: float, target: int = 5) -> list[int]:
    if vmax <= vmin:
        return [int(vmin)]
    step = _nice_round((vmax - vmin) / target)
    if step == 0:
        step = 1
    start = (int(vmin) // step) * step
    ticks, v = [], start
    while v <= vmax + step / 2:
        if v >= vmin - step / 2:
            ticks.append(v)
        v += step
    return ticks


def _x_ticks(dates: list[date], target: int = 6) -> list[date]:
    if len(dates) <= target:
        return dates
    step = max(1, len(dates) // target)
    ticks = dates[::step]
    if dates[-1] not in ticks:
        ticks.append(dates[-1])
    return ticks


def _date_short(d: date) -> str:
    return f"{d.day:02d}/{d.month:02d}"


def render_svg_volumes(history: dict, width: int = 800, height: int = 360) -> str:
    dates = sorted_dates(history)
    if len(dates) < 1:
        return f'<svg viewBox="0 0 {width} {height}"></svg>'

    series: dict[str, list[tuple[date, int]]] = {}
    for key in DOCTYPES_FR:
        pts = [(d, v) for d in dates if (v := get_count_at(history, d, key)) is not None]
        series[key] = pts

    all_values = [v for pts in series.values() for _, v in pts]
    if not all_values:
        return f'<svg viewBox="0 0 {width} {height}"></svg>'

    vmin = 0
    vmax = _nice_round(max(all_values) * 1.05)

    margin_l, margin_r, margin_t, margin_b = 64, 16, 18, 78
    plot_w = width - margin_l - margin_r
    plot_h = height - margin_t - margin_b

    def x_for(d: date) -> float:
        if len(dates) == 1:
            return margin_l + plot_w / 2
        span_days = (dates[-1] - dates[0]).days
        if span_days == 0:
            return margin_l + plot_w / 2
        return margin_l + ((d - dates[0]).days / span_days) * plot_w

    def y_for(v: float) -> float:
        if vmax == vmin:
            return margin_t + plot_h / 2
        return margin_t + (1 - (v - vmin) / (vmax - vmin)) * plot_h

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
           f'font-family="Geist Mono, monospace" font-size="9">']

    # Grid + Y labels
    for tick in _y_ticks(vmin, vmax):
        y = y_for(tick)
        out.append(f'<line x1="{margin_l}" y1="{y:.1f}" x2="{margin_l + plot_w}" y2="{y:.1f}" '
                   f'stroke="#FFC8C8" stroke-width="0.5"/>')
        label = f"{tick:,}".replace(",", "\u00a0")
        out.append(f'<text x="{margin_l - 6}" y="{y + 3:.1f}" text-anchor="end" fill="#660000">{label}</text>')

    # Axes
    out.append(f'<line x1="{margin_l}" y1="{margin_t}" x2="{margin_l}" y2="{margin_t + plot_h}" '
               f'stroke="#181818" stroke-width="0.8"/>')
    out.append(f'<line x1="{margin_l}" y1="{margin_t + plot_h}" x2="{margin_l + plot_w}" y2="{margin_t + plot_h}" '
               f'stroke="#181818" stroke-width="0.8"/>')

    # X ticks
    for d in _x_ticks(dates):
        x = x_for(d)
        out.append(f'<line x1="{x:.1f}" y1="{margin_t + plot_h}" x2="{x:.1f}" y2="{margin_t + plot_h + 4}" '
                   f'stroke="#181818" stroke-width="0.8"/>')
        out.append(f'<text x="{x:.1f}" y="{margin_t + plot_h + 16}" text-anchor="middle" fill="#181818">'
                   f'{_date_short(d)}</text>')

    # Courbes
    for key, pts in series.items():
        if not pts:
            continue
        style = DOCTYPE_STYLES[key]
        path_d = " ".join(
            f"{'M' if i == 0 else 'L'}{x_for(d):.1f},{y_for(v):.1f}"
            for i, (d, v) in enumerate(pts)
        )
        dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] != "none" else ""
        out.append(f'<path d="{path_d}" fill="none" stroke="{style["color"]}" '
                   f'stroke-width="{style["width"]}"{dash} stroke-linecap="round" stroke-linejoin="round"/>')
        for d, v in pts:
            out.append(f'<circle cx="{x_for(d):.1f}" cy="{y_for(v):.1f}" r="2.2" fill="{style["color"]}"/>')

    # Légende
    legend_y = margin_t + plot_h + 36
    cols = 3
    col_w = plot_w / cols
    for i, key in enumerate(DOCTYPES_FR.keys()):
        style = DOCTYPE_STYLES[key]
        x0 = margin_l + (i % cols) * col_w
        y0 = legend_y + (i // cols) * 14
        dash = f' stroke-dasharray="{style["dash"]}"' if style["dash"] != "none" else ""
        out.append(f'<line x1="{x0:.1f}" y1="{y0:.1f}" x2="{x0 + 24:.1f}" y2="{y0:.1f}" '
                   f'stroke="{style["color"]}" stroke-width="{style["width"]}"{dash}/>')
        out.append(f'<text x="{x0 + 30:.1f}" y="{y0 + 3:.1f}" fill="#181818">{style["short"]}</text>')

    out.append('</svg>')
    return "\n".join(out)


def render_svg_ratio(history: dict, width: int = 800, height: int = 260) -> str:
    dates = sorted_dates(history)
    if not dates:
        return f'<svg viewBox="0 0 {width} {height}"></svg>'

    pts: list[tuple[date, float]] = []
    for d in dates:
        cius = get_count_at(history, d, "ubl_cius")
        ext = get_count_at(history, d, "ubl_ext")
        if cius and ext is not None and cius > 0:
            pts.append((d, 100.0 * ext / cius))

    if not pts:
        return f'<svg viewBox="0 0 {width} {height}"></svg>'

    margin_l, margin_r, margin_t, margin_b = 64, 16, 18, 40
    plot_w = width - margin_l - margin_r
    plot_h = height - margin_t - margin_b

    values = [v for _, v in pts]
    vmin = max(0.0, min(values) - 2)
    vmax = min(100.0, max(values) + 2)
    if vmax - vmin < 4:
        center = (vmin + vmax) / 2
        vmin = max(0.0, center - 2)
        vmax = min(100.0, center + 2)

    def x_for(d: date) -> float:
        if len(dates) == 1:
            return margin_l + plot_w / 2
        span_days = (dates[-1] - dates[0]).days
        if span_days == 0:
            return margin_l + plot_w / 2
        return margin_l + ((d - dates[0]).days / span_days) * plot_w

    def y_for(v: float) -> float:
        if vmax == vmin:
            return margin_t + plot_h / 2
        return margin_t + (1 - (v - vmin) / (vmax - vmin)) * plot_h

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
           f'font-family="Geist Mono, monospace" font-size="9">']

    # Y ticks
    step = (vmax - vmin) / 5
    if step > 0:
        v = vmin
        while v <= vmax + 1e-6:
            y = y_for(v)
            out.append(f'<line x1="{margin_l}" y1="{y:.1f}" x2="{margin_l + plot_w}" y2="{y:.1f}" '
                       f'stroke="#FFC8C8" stroke-width="0.5"/>')
            label = f"{v:.1f}".replace(".", ",")
            out.append(f'<text x="{margin_l - 6}" y="{y + 3:.1f}" text-anchor="end" fill="#660000">{label}\u00a0%</text>')
            v += step

    # Axes
    out.append(f'<line x1="{margin_l}" y1="{margin_t}" x2="{margin_l}" y2="{margin_t + plot_h}" '
               f'stroke="#181818" stroke-width="0.8"/>')
    out.append(f'<line x1="{margin_l}" y1="{margin_t + plot_h}" x2="{margin_l + plot_w}" y2="{margin_t + plot_h}" '
               f'stroke="#181818" stroke-width="0.8"/>')

    # X ticks
    for d in _x_ticks(dates):
        x = x_for(d)
        out.append(f'<line x1="{x:.1f}" y1="{margin_t + plot_h}" x2="{x:.1f}" y2="{margin_t + plot_h + 4}" '
                   f'stroke="#181818" stroke-width="0.8"/>')
        out.append(f'<text x="{x:.1f}" y="{margin_t + plot_h + 16}" text-anchor="middle" fill="#181818">'
                   f'{_date_short(d)}</text>')

    # Courbe
    path_d = " ".join(f"{'M' if i == 0 else 'L'}{x_for(d):.1f},{y_for(v):.1f}" for i, (d, v) in enumerate(pts))
    out.append(f'<path d="{path_d}" fill="none" stroke="#D90D25" stroke-width="2.5" '
               f'stroke-linecap="round" stroke-linejoin="round"/>')
    for d, v in pts:
        out.append(f'<circle cx="{x_for(d):.1f}" cy="{y_for(v):.1f}" r="3.0" fill="#D90D25"/>')
        label = f"{v:.1f}".replace(".", ",")
        out.append(f'<text x="{x_for(d):.1f}" y="{y_for(v) - 8:.1f}" text-anchor="middle" '
                   f'fill="#C00404" font-weight="700">{label}\u00a0%</text>')

    out.append('</svg>')
    return "\n".join(out)


# ════════════════════════════════════════════════════════════════════
#  Analyse détaillée (mode --detailed)
# ════════════════════════════════════════════════════════════════════

@dataclass
class ParticipantFlags:
    ubl_cius: bool = False; ubl_ext: bool = False
    cii_cius: bool = False; cii_ext: bool = False
    ubl_cn_cius: bool = False
    facturx: bool = False; cdar: bool = False
    bis_inv: bool = False; bis_cn: bool = False


def classify(doctypes: list[dict]) -> ParticipantFlags:
    f = ParticipantFlags()
    for dt in doctypes:
        v = dt.get("value", "")
        is_inv = "Invoice-2::Invoice" in v
        is_cn = "CreditNote-2::CreditNote" in v
        is_cii_inv = ":CrossIndustryInvoice:" in v and "CrossIndustryInvoice##" in v
        if "france:billing:extended" in v and is_inv: f.ubl_ext = True
        elif "france:billing:cius" in v and is_inv: f.ubl_cius = True
        elif "france:billing:extended" in v and is_cii_inv: f.cii_ext = True
        elif "france:billing:cius" in v and is_cii_inv: f.cii_cius = True
        elif "france:billing:cius" in v and is_cn: f.ubl_cn_cius = True
        elif "Factur-X" in v: f.facturx = True
        elif "CrossDomainAcknowledgement" in v: f.cdar = True
        elif "poacc:billing:3.0" in v and is_inv: f.bis_inv = True
        elif "poacc:billing:3.0" in v and is_cn: f.bis_cn = True
    return f


_TOKEN_MAP = [("ubl_cius", "UBL-CIUS"), ("ubl_ext", "UBL-EXT"),
              ("cii_cius", "CII-CIUS"), ("cii_ext", "CII-EXT"),
              ("facturx", "FX"), ("cdar", "CDAR"),
              ("bis_inv", "BIS"), ("bis_cn", "BIS-CN")]
EXT_TOKENS = {"UBL-EXT", "CII-EXT"}


def make_signature(f: ParticipantFlags) -> tuple[str, ...]:
    return tuple(tok for attr, tok in _TOKEN_MAP if getattr(f, attr))


def extract_names(matches: list[dict], limit: int = 10) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in matches:
        for ent in m.get("entities", []) or []:
            if not isinstance(ent, dict):
                continue
            n = ent.get("name", "")
            if isinstance(n, list):
                for item in n:
                    if isinstance(item, dict) and item.get("name"):
                        nm = item["name"].strip()
                        if nm and nm not in seen and 2 < len(nm) < 60:
                            seen.add(nm); out.append(nm)
                            if len(out) >= limit: return out
            elif isinstance(n, str) and n.strip():
                nm = n.strip()
                if nm not in seen and 2 < len(nm) < 60:
                    seen.add(nm); out.append(nm)
                    if len(out) >= limit: return out
    return out


@dataclass
class CohortStats:
    sample_size: int
    signatures: list[dict] = field(default_factory=list)
    ext_tokens: list[str] = field(default_factory=lambda: sorted(EXT_TOKENS))


def analyze_cohort(matches: list[dict], top_n: int = 2) -> CohortStats:
    sigs: Counter[tuple[str, ...]] = Counter()
    for m in matches:
        sigs[make_signature(classify(m.get("docTypes", [])))] += 1
    total = sum(sigs.values())
    rows: list[dict] = []
    accounted = 0
    for sig, count in sigs.most_common(top_n):
        accounted += count
        rows.append({
            "count": count, "tokens": list(sig),
            "pct_fr": f"{100 * count / total:.1f}".replace(".", ",") if total else "0,0",
            "is_residual": False, "label": "",
        })
    if (residual := total - accounted) > 0:
        rows.append({
            "count": residual, "tokens": [],
            "pct_fr": f"{100 * residual / total:.1f}".replace(".", ",") if total else "0,0",
            "is_residual": True, "label": "variations résiduelles",
        })
    return CohortStats(sample_size=total, signatures=rows)


# ════════════════════════════════════════════════════════════════════
#  Formatage
# ════════════════════════════════════════════════════════════════════

_FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin",
              "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


def fr_date(d) -> str:
    return f"{d.day} {_FR_MONTHS[d.month - 1]} {d.year}"


def fr_datetime(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=PARIS_TZ)
    return f"{fr_date(dt)}, {dt.strftime('%H:%M')}\u00a0{dt.strftime('%Z')}"


def fr_int(n: int) -> str:
    return f"{n:,}".replace(",", "\u00a0")


def nbsp(s: str) -> str:
    return s.replace(" ", "\u00a0")


def _short_author(full: str) -> str:
    parts = full.rsplit(" ", 1)
    if len(parts) != 2:
        return full
    first, last = parts
    initials = "-".join(p[0].upper() + "." for p in first.split("-"))
    return f"{initials} {last}"


# ════════════════════════════════════════════════════════════════════
#  Pipeline
# ════════════════════════════════════════════════════════════════════

def collect_counts_fr() -> dict[str, int]:
    log = logging.getLogger("peppol")
    counts: dict[str, int] = {}
    for key, meta in DOCTYPES_FR.items():
        log.info("  %-12s FR…", key)
        counts[key] = fetch_count(meta["urn"], country="FR")
        time.sleep(RATE_LIMIT_DELAY_S)
    return counts


def collect_detailed(sample_size: int) -> dict[str, Any]:
    log = logging.getLogger("peppol")
    counts: dict[str, dict[str, int]] = {}
    for key, meta in DOCTYPES_FR.items():
        log.info("  %-12s world…", key)
        world = fetch_count(meta["urn"], country=None)
        time.sleep(RATE_LIMIT_DELAY_S)
        log.info("  %-12s FR…   (world=%s)", key, fr_int(world))
        fr = fetch_count(meta["urn"], country="FR")
        time.sleep(RATE_LIMIT_DELAY_S)
        counts[key] = {"world": world, "fr": fr}
    log.info("  bis_billing  world…")
    counts["bis_billing"] = {"world": fetch_count(DOCTYPE_BIS["urn"], country=None), "fr": 0}
    time.sleep(RATE_LIMIT_DELAY_S)

    log.info("Échantillons (rpc=%d)…", sample_size)
    sample_cius = fetch_sample(DOCTYPES_FR["ubl_cius"]["urn"], "FR", rpc=sample_size)
    time.sleep(RATE_LIMIT_DELAY_S)
    sample_ext = fetch_sample(DOCTYPES_FR["ubl_ext"]["urn"], "FR", rpc=sample_size)

    return {
        "counts": counts,
        "cohort_cius": asdict(analyze_cohort(sample_cius)),
        "cohort_ext": asdict(analyze_cohort(sample_ext)),
        "names_cius": extract_names(sample_cius, limit=10),
        "names_ext": extract_names(sample_ext, limit=10),
    }


def render_brief(history: dict, today_key: str, *, template_path: Path, author_full: str) -> str:
    today_d = date.fromisoformat(today_key)
    counts_fr = history["runs"][today_key]["counts_fr"]
    has_history = len(history["runs"]) >= 2

    env = Environment(loader=FileSystemLoader(template_path.parent),
                      autoescape=select_autoescape(["html"]))
    env.filters["fr_int"] = fr_int
    env.filters["nbsp"] = nbsp

    dates = sorted_dates(history)
    evo = build_evolution(history, today_key)
    return env.get_template(template_path.name).render(
        author_full=author_full,
        author_short=_short_author(author_full),
        production_date_short=fr_date(today_d),
        production_datetime_long=fr_datetime(datetime.now(PARIS_TZ)),
        counts_rows=build_counts_rows(counts_fr),
        evolution_rows=evo["rows"],
        evolution_refs=evo["refs"],
        has_history=has_history,
        svg_volumes=render_svg_volumes(history) if has_history else "",
        svg_ratio=render_svg_ratio(history) if has_history else "",
        num_runs=len(history["runs"]),
        date_range_start=dates[0].isoformat(),
        date_range_end=dates[-1].isoformat(),
        date_range_start_short=fr_date(dates[0]),
        date_range_end_short=fr_date(dates[-1]),
        pasr_url=PASR_URL,
    )


def render_detailed(detailed_stats: dict, today_key: str, *, template_path: Path, author_full: str) -> str:
    counts = detailed_stats["counts"]
    base = counts["ubl_cius"]["fr"]
    ext_fr = counts["ubl_ext"]["fr"]
    gap = base - ext_fr
    ratio_ext = (ext_fr / base * 100) if base else 0
    cdar_excess = max(0, counts["cdar"]["fr"] - base)

    def bar_for(k: str) -> dict:
        n = counts[k]["fr"]
        pct = (n / base * 100) if base else 0
        return {
            "bar_width": f"{min(pct, 100):.1f}",
            "ratio_label": f"{pct:.1f}".replace(".", ",") + "\u00a0%" if pct < 100 else "100\u00a0%",
        }

    doctype_bars = {k: bar_for(k) for k in ("cii_cius", "ubl_cius", "facturx", "ubl_ext", "cii_ext")}
    doctype_bars["bis_billing"] = {"bar_width": "100", "ratio_label": "monde"}
    doctype_bars["cdar"] = {"bar_width": "100", "ratio_label": "100\u00a0%"}

    env = Environment(loader=FileSystemLoader(template_path.parent),
                      autoescape=select_autoescape(["html"]))
    env.filters["fr_int"] = fr_int
    env.filters["nbsp"] = nbsp
    today_d = date.fromisoformat(today_key)
    return env.get_template(template_path.name).render(
        author_full=author_full,
        author_short=_short_author(author_full),
        production_date_short=fr_date(today_d),
        production_datetime_long=fr_datetime(datetime.now(PARIS_TZ)),
        counts=counts,
        doctype_bars=doctype_bars,
        gap=gap,
        ratio_extension_pct=f"{ratio_ext:.1f}".replace(".", ","),
        ratio_extension_round=f"{round(ratio_ext)}",
        noncompliance_pct=f"{100 - ratio_ext:.1f}".replace(".", ","),
        cdar_excess=cdar_excess,
        cdar_excess_pct=f"{cdar_excess / base * 100:.1f}".replace(".", ",") if base else "0,0",
        cohort_cius=detailed_stats["cohort_cius"],
        cohort_ext=detailed_stats["cohort_ext"],
        names_cius=detailed_stats["names_cius"],
        names_ext=detailed_stats["names_ext"],
        pasr_url=PASR_URL,
    )


def render_pdf(html_path: Path, pdf_path: Path) -> None:
    try:
        from weasyprint import HTML
    except ImportError:
        raise RuntimeError("weasyprint manquant — pip install weasyprint")
    HTML(filename=str(html_path)).write_pdf(str(pdf_path))


# ════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Génère le rapport quotidien Peppol Directory France.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--output-dir", "-o", type=Path, default=Path("./out"))
    parser.add_argument("--template-brief", type=Path, default=Path("./peppol_report_brief.html.j2"))
    parser.add_argument("--template-detailed", type=Path, default=Path("./peppol_report_template.html.j2"))
    parser.add_argument("--history", type=Path, default=None,
                        help="Chemin du JSON d'historique (défaut : <output-dir>/peppol_history.json).")
    parser.add_argument("--detailed", action="store_true",
                        help="Mode rapport complet (signatures, échantillons, KPIs).")
    parser.add_argument("--sample-size", "-n", type=int, default=DEFAULT_SAMPLE_SIZE,
                        help="Taille d'échantillon (mode --detailed uniquement).")
    parser.add_argument("--no-pdf", action="store_true")
    parser.add_argument("--no-api", action="store_true",
                        help="Re-rend depuis l'historique existant sans interroger l'API.")
    parser.add_argument("--author", default="@Sandjab")
    parser.add_argument("--proxy", default=None,
                        help="Proxy HTTP/HTTPS au format [scheme://]host[:port]. "
                             "Ex : proxy.corp:8080 ou http://proxy.corp:8080. "
                             "Credentials demandés au prompt, ou via les variables "
                             "d'environnement PEPPOL_PROXY_USER / PEPPOL_PROXY_PASS "
                             "(utile en cron/CI).")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("peppol")

    if args.proxy:
        try:
            host_url = _normalize_proxy_host(args.proxy)
        except ValueError as e:
            log.error("%s", e)
            return 2
        user = os.environ.get("PEPPOL_PROXY_USER", "")
        password = os.environ.get("PEPPOL_PROXY_PASS", "")
        is_interactive = sys.stdin is not None and sys.stdin.isatty()
        if not user and is_interactive:
            user = input("Proxy user (vide si pas d'auth) : ").strip()
        if user and not password and is_interactive:
            password = getpass.getpass("Proxy password : ")
        if not is_interactive and user and "PEPPOL_PROXY_PASS" not in os.environ:
            log.error(
                "PEPPOL_PROXY_USER défini sans PEPPOL_PROXY_PASS en environnement "
                "non-interactif : abandon."
            )
            return 2
        if password and not user:
            log.error("Proxy password défini sans utilisateur : abandon.")
            return 2
        proxy_url = _build_proxy_url(host_url, user, password)
        global HTTP_PROXIES
        HTTP_PROXIES = {"http": proxy_url, "https": proxy_url}
        # Exporte aussi en env standard (lowercase + uppercase) pour que
        # WeasyPrint (fetch fonts/CSS via urllib) et tout sous-processus
        # respectent le proxy.
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            os.environ[key] = proxy_url
        auth_state = f"avec auth ({user})" if user else "sans auth"
        log.info("Proxy actif : %s — %s", host_url, auth_state)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    history_path = args.history or (args.output_dir / HISTORY_FILENAME)
    history = load_history(history_path)
    today_key = date.today().isoformat()
    detailed_stats: dict | None = None

    # (c) Avertit si l'historique présente un gap avant aujourd'hui.
    if not args.no_api:
        last_existing = closest_run_at_or_before(history, date.today() - timedelta(days=1))
        if last_existing is not None:
            gap = (date.today() - last_existing).days
            if gap > 1:
                log.warning(
                    "Gap d'historique : dernier run = %s (il y a %d jours). "
                    "L'étiquetage des deltas reflète ce décalage.",
                    last_existing.isoformat(), gap,
                )

    if args.no_api:
        if today_key not in history.get("runs", {}):
            log.error("--no-api : pas d'entrée pour aujourd'hui dans l'historique.")
            return 2
        log.info("Mode --no-api : re-rendu depuis l'historique (%d runs).", len(history["runs"]))
    else:
        try:
            log.info("Collecte des comptages FR…")
            if args.detailed:
                detailed_stats = collect_detailed(args.sample_size)
                counts_fr = {k: v["fr"] for k, v in detailed_stats["counts"].items() if k in DOCTYPES_FR}
            else:
                counts_fr = collect_counts_fr()
            upsert_today(history, today_key, counts_fr)
            save_history(history, history_path)
            log.info("Historique mis à jour : %s (%d runs)", history_path, len(history["runs"]))
        except (requests.RequestException, RuntimeError) as e:
            log.error("Échec de la collecte : %s", e)
            return 2

    # Rendu
    if args.detailed:
        if detailed_stats is None:
            log.error("--detailed incompatible avec --no-api.")
            return 2
        template_path = args.template_detailed
        if not template_path.exists():
            log.error("Template detailed introuvable : %s", template_path)
            return 1
        html_content = render_detailed(detailed_stats, today_key,
                                       template_path=template_path,
                                       author_full=args.author)
        stats_path = args.output_dir / "peppol_extension_report_stats.json"
        stats_path.write_text(json.dumps(detailed_stats, indent=2, ensure_ascii=False))
        log.info("Stats détaillées : %s", stats_path)
    else:
        template_path = args.template_brief
        if not template_path.exists():
            log.error("Template brief introuvable : %s", template_path)
            return 1
        html_content = render_brief(history, today_key,
                                    template_path=template_path,
                                    author_full=args.author)

    html_path = args.output_dir / "peppol_extension_report.html"
    html_path.write_text(html_content, encoding="utf-8")
    log.info("HTML : %s (%d Ko)", html_path, len(html_content) // 1024)

    if not args.no_pdf:
        pdf_path = args.output_dir / "peppol_extension_report.pdf"
        try:
            render_pdf(html_path, pdf_path)
            log.info("PDF  : %s", pdf_path)
        except Exception as e:
            log.error("Échec PDF : %s — HTML reste dispo : %s", e, html_path)
            return 3

    log.info("OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
