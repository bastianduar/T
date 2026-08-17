#!/usr/bin/env python3
import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

TERMS = [
    "aceite corporal reafirmante",
    "crema reafirmante",
    "flacidez",
    "piel flácida",
    "firmeza corporal",
    "aceite firmeza",
    "brazos flacidez",
    "papada",
]

META_RE = re.compile(r"(?:Library ID|ID de la biblioteca|Identificador de la biblioteca)\s*:?\s*(\d{6,})", re.I)
START_PATTERNS = [
    re.compile(r"Started running on\s+([^\n]+)", re.I),
    re.compile(r"En circulación desde(?: el)?\s+([^\n]+)", re.I),
    re.compile(r"Empezó a publicarse(?: el)?\s+([^\n]+)", re.I),
]

CARD_JS = r"""
() => {
  const metaRe = /(Library ID|ID de la biblioteca|Identificador de la biblioteca)\s*:?\s*\d{6,}/i;
  const sponsorRe = /(Sponsored|Patrocinado|Publicidad)/i;
  const visible = (el) => {
    const s = getComputedStyle(el); const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 250 && r.height > 30;
  };
  const seeds = Array.from(document.querySelectorAll('div,span')).filter(el => {
    const t = (el.innerText || '').trim();
    return t.length < 350 && metaRe.test(t) && visible(el);
  });
  const cards = [];
  for (const seed of seeds) {
    let el = seed;
    let chosen = null;
    for (let i=0; i<15 && el; i++, el=el.parentElement) {
      const t = (el.innerText || '').trim();
      const r = el.getBoundingClientRect();
      const ids = (t.match(/(Library ID|ID de la biblioteca|Identificador de la biblioteca)/gi) || []).length;
      const media = el.querySelectorAll('img,video').length;
      if (ids === 1 && sponsorRe.test(t) && r.width > 420 && r.height > 280 && (media > 0 || t.length > 250)) {
        chosen = el; break;
      }
    }
    if (!chosen) {
      el = seed;
      for (let i=0; i<12 && el; i++, el=el.parentElement) {
        const t = (el.innerText || '').trim(); const r = el.getBoundingClientRect();
        if (metaRe.test(t) && r.width > 420 && r.height > 250 && t.length > 180) { chosen = el; break; }
      }
    }
    if (chosen && !cards.includes(chosen)) cards.push(chosen);
  }
  return cards.slice(0,80).map((card, idx) => {
    const marker = 'oai-card-' + idx + '-' + Math.random().toString(36).slice(2,8);
    card.setAttribute('data-oai-card', marker);
    const cr = card.getBoundingClientRect();
    const links = Array.from(card.querySelectorAll('a')).map(a => ({
      text:(a.innerText||a.getAttribute('aria-label')||'').trim(), href:a.href||'',
      aria:a.getAttribute('aria-label')||''
    })).filter(x=>x.href);
    const images = Array.from(card.querySelectorAll('img')).map(img => {
      const r=img.getBoundingClientRect();
      return {src:img.currentSrc||img.src||'', alt:img.alt||'', width:r.width, height:r.height, x:r.x, y:r.y};
    }).filter(x=>x.width>30 && x.height>30);
    const videos = Array.from(card.querySelectorAll('video')).map(v => {
      const r=v.getBoundingClientRect();
      return {src:v.currentSrc||v.src||'', poster:v.poster||'', width:r.width, height:r.height, x:r.x, y:r.y};
    });
    const leaves = Array.from(card.querySelectorAll('*')).filter(el => {
      const t=(el.innerText||'').trim(); if(!t || t.length>1500 || !visible(el)) return false;
      return !Array.from(el.children).some(c => (c.innerText||'').trim()===t);
    }).slice(0,600).map(el => {
      const s=getComputedStyle(el), r=el.getBoundingClientRect();
      return {text:(el.innerText||'').trim(), tag:el.tagName, role:el.getAttribute('role')||'',
        aria:el.getAttribute('aria-label')||'', font_size:s.fontSize, font_weight:s.fontWeight,
        x:r.x-cr.x, y:r.y-cr.y, width:r.width, height:r.height};
    });
    return {marker, text:(card.innerText||'').trim(), html:card.outerHTML.slice(0,250000),
      rect:{x:cr.x,y:cr.y,width:cr.width,height:cr.height}, links, images, videos, leaves};
  });
}
"""

async def safe_click_cookie(page):
    labels = [
        re.compile(r"Allow all cookies", re.I), re.compile(r"Permitir todas las cookies", re.I),
        re.compile(r"Aceptar todas", re.I), re.compile(r"Allow essential and optional cookies", re.I),
    ]
    for pat in labels:
        try:
            loc = page.get_by_role("button", name=pat)
            if await loc.count():
                await loc.first.click(timeout=3000)
                await page.wait_for_timeout(1500)
                return
        except Exception:
            pass

async def expand_text(page):
    pats = [re.compile(r"^See more$", re.I), re.compile(r"^Ver más$", re.I), re.compile(r"^Más información$", re.I)]
    for pat in pats:
        try:
            loc = page.get_by_text(pat, exact=True)
            count = min(await loc.count(), 80)
            for i in range(count):
                try:
                    await loc.nth(i).click(timeout=1200)
                    await page.wait_for_timeout(80)
                except Exception:
                    pass
        except Exception:
            pass

async def collect(country: str, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    all_records = []
    diagnostics = []
    network_seen = set()
    network_dir = out_dir / "network"
    network_dir.mkdir(exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu",
                "--lang=en-US,en", "--window-size=1440,1800",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 1800},
            locale="en-US",
            timezone_id={"ES":"Europe/Madrid","MX":"America/Mexico_City","CO":"America/Bogota"}.get(country,"UTC"),
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
            extra_http_headers={"Accept-Language":"en-US,en;q=0.9,es;q=0.8"},
        )
        await context.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = await context.new_page()
        response_tasks = []

        async def save_response(response, qslug):
            try:
                url = response.url
                if not ("graphql" in url or "ads/library" in url or "search_ads" in url):
                    return
                ct = (response.headers.get("content-type") or "").lower()
                if not any(x in ct for x in ("json","text","javascript")):
                    return
                body = await response.body()
                if not body or len(body) > 12_000_000:
                    return
                digest = hashlib.sha256(body).hexdigest()
                if digest in network_seen:
                    return
                network_seen.add(digest)
                ext = ".json" if "json" in ct else ".txt"
                (network_dir / f"{qslug}_{len(network_seen):04d}_{digest[:10]}{ext}").write_bytes(body)
            except Exception:
                return

        current_slug = "init"
        def on_response(resp):
            response_tasks.append(asyncio.create_task(save_response(resp, current_slug)))
        page.on("response", on_response)

        for qi, term in enumerate(TERMS):
            current_slug = f"q{qi+1:02d}"
            url = (
                "https://www.facebook.com/ads/library/?active_status=active&ad_type=all"
                f"&country={country}&q={quote_plus(term)}&search_type=keyword_unordered&media_type=all&locale=en_US"
            )
            started = time.time()
            error = None
            status = None
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=90000)
                status = resp.status if resp else None
                await page.wait_for_timeout(7000 + random.randint(0,2500))
                await safe_click_cookie(page)
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                for _ in range(10):
                    await expand_text(page)
                    await page.mouse.wheel(0, random.randint(1200,1900))
                    await page.wait_for_timeout(1800 + random.randint(0,1000))
                await expand_text(page)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

            body_text = ""
            cards = []
            try:
                body_text = await page.locator("body").inner_text(timeout=15000)
                cards = await page.evaluate(CARD_JS)
            except Exception as exc:
                error = (error + " | " if error else "") + f"extract:{type(exc).__name__}:{exc}"

            qdir = out_dir / current_slug
            qdir.mkdir(exist_ok=True)
            (qdir / "body.txt").write_text(body_text, encoding="utf-8")
            try:
                (qdir / "page.html").write_text(await page.content(), encoding="utf-8")
            except Exception:
                pass
            try:
                await page.screenshot(path=str(qdir / "page.png"), full_page=False)
            except Exception:
                pass

            for idx, card in enumerate(cards):
                text = card.get("text","")
                m = META_RE.search(text)
                ad_id = m.group(1) if m else None
                start_text = None
                for pat in START_PATTERNS:
                    mm = pat.search(text)
                    if mm:
                        start_text = mm.group(1).strip(); break
                rec = {
                    "country": country, "query": term, "query_index": qi+1,
                    "ad_id": ad_id, "start_text": start_text, "active": True,
                    "search_url": url, "collected_at": datetime.now(timezone.utc).isoformat(),
                    **card,
                }
                marker = card.get("marker")
                base = ad_id or hashlib.sha1(text.encode("utf-8","ignore")).hexdigest()[:14]
                try:
                    locator = page.locator(f'[data-oai-card="{marker}"]')
                    await locator.screenshot(path=str(qdir / f"card_{idx:03d}_{base}.png"), timeout=15000)
                    rec["card_screenshot"] = str(qdir / f"card_{idx:03d}_{base}.png")
                except Exception:
                    rec["card_screenshot"] = None
                try:
                    media = sorted((card.get("images") or []) + (card.get("videos") or []), key=lambda x:(x.get("width",0)*x.get("height",0)), reverse=True)
                    if media:
                        # Clip the largest visible media rectangle from the current viewport when possible.
                        md = media[0]
                        if md.get("width",0) > 140 and md.get("height",0) > 120:
                            clip={"x":max(0,md.get("x",0)),"y":max(0,md.get("y",0)),"width":min(md.get("width",0),1440),"height":min(md.get("height",0),1800)}
                            if clip["y"]+clip["height"] <= 1800 and clip["x"]+clip["width"] <= 1440:
                                await page.screenshot(path=str(qdir / f"creative_{idx:03d}_{base}.png"), clip=clip)
                                rec["creative_screenshot"] = str(qdir / f"creative_{idx:03d}_{base}.png")
                except Exception:
                    rec["creative_screenshot"] = None
                all_records.append(rec)

            lower = body_text.lower()
            blocked_terms = [x for x in ["rate limit", "temporarily blocked", "something went wrong", "try again later", "límite de frecuencia", "se produjo un error"] if x in lower]
            diagnostics.append({
                "country":country,"query":term,"url":url,"http_status":status,"error":error,
                "cards":len(cards),"body_chars":len(body_text),"blocked_terms":blocked_terms,
                "seconds":round(time.time()-started,1),
            })
            await page.wait_for_timeout(2500 + random.randint(0,2500))

        if response_tasks:
            await asyncio.gather(*response_tasks, return_exceptions=True)
        await browser.close()

    (out_dir / "records.json").write_text(json.dumps(all_records, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "diagnostics.json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"country":country,"records":len(all_records),"network_files":len(network_seen),"diagnostics":diagnostics}, ensure_ascii=False))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--country", required=True, choices=["ES","MX","CO"])
    ap.add_argument("--out", required=True)
    args=ap.parse_args()
    asyncio.run(collect(args.country, Path(args.out)))

if __name__ == "__main__":
    main()
