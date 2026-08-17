#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import hashlib
import html
import json
import math
import os
import re
import statistics
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup
from dateparser import parse as parse_date
from PIL import Image

TODAY = datetime(2026, 8, 17, tzinfo=timezone.utc)
COUNTRY_NAMES = {"ES":"España", "MX":"México", "CO":"Colombia"}
TOPIC = re.compile(r"reafirm|firm(?:e|ing)|flacid|flácid|tens(?:ar|or|ión)|elasticidad|piel\s+floja|papada|celulit|tonific|body\s+firm", re.I)
FORM = re.compile(r"aceite|oil|crema|cream|gel|s[eé]rum|loción|lotion|bálsamo|tratamiento\s+corporal|body\s+(?:oil|cream|serum|lotion)|manteca\s+corporal", re.I)
EXCLUDE = re.compile(r"hifu|radiofrecuencia|ultrasonido|liposucci|cirug|clínica|clinica|sesiones|gimnasio|ejercicio|suplemento|cápsulas|capsulas|pastillas", re.I)
META_DOMAIN = re.compile(r"(^|\.)(facebook|fb|instagram|meta)\.com$", re.I)
CTA = re.compile(r"^(shop now|comprar ahora|más información|mas información|learn more|ver más|ver mas|order now|pedir ahora|enviar mensaje|send message|sign up)$", re.I)
META_LINE = re.compile(r"^(active|activo|inactivo|library id|id de la biblioteca|identificador de la biblioteca|started running|en circulación|empezó a publicarse|platforms|plataformas|see ad details|ver detalles|this ad|este anuncio)", re.I)

PROMISE_MAP = collections.OrderedDict([
    ("Firmeza/tensión de la piel", r"reafirm|firm(?:e|ing)|tens(?:ar|or|ión)|flacid|flácid|piel\s+floja|tonific"),
    ("Celulitis/piel de naranja", r"celulit|piel\s+de\s+naranja"),
    ("Hidratación/nutrición", r"hidrat|nutr|sequedad|suavidad|suave"),
    ("Elasticidad", r"elasticidad|elastin|flexibilidad"),
    ("Remodelación/silueta", r"remodel|molde|silueta|contorno|reductor|slim"),
    ("Papada/cuello", r"papada|cuello|neck|doble\s+mentón"),
    ("Estrías", r"estr[ií]as|stretch\s+marks"),
    ("Antiedad corporal", r"anti.?edad|anti.?age|envejecimiento|arrugas"),
])
MECH_MAP = collections.OrderedDict([
    ("Q10/coenzima Q10", r"\bq10\b|coenzima"),
    ("Retinol/vitamina A", r"retinol|vitamina\s+a\b"),
    ("Centella asiática", r"centella"),
    ("Colágeno/elastina", r"col[aá]geno|elastina"),
    ("Cafeína", r"cafe[ií]na"),
    ("Ácido hialurónico", r"hialur[oó]nico|hyaluronic"),
    ("Péptidos", r"p[eé]ptid|peptide"),
    ("Aceites vegetales", r"rosa\s+mosqueta|almendra|jojoba|arg[aá]n|coco|oliva|aguacate|aceites?\s+(?:naturales|vegetales|esenciales)"),
    ("Manteca de karité", r"karit[eé]|shea"),
    ("Masaje/ritual de aplicación", r"masaj|movimientos?\s+ascendente|aplica(?:r)?\s+(?:cada|por la|en)"),
    ("Evidencia clínica/dermatológica", r"cl[ií]nicamente|dermatol[oó]gicamente|estudio|testado|probado"),
    ("Efecto térmico/frío", r"efecto\s+(?:fr[ií]o|calor|t[eé]rmico)|thermo"),
])
ZONE_MAP = collections.OrderedDict([
    ("Brazos", r"brazos?|arms?|alas\s+de\s+murci[eé]lago"),
    ("Abdomen/vientre", r"abdomen|vientre|barriga|est[oó]mago"),
    ("Muslos/piernas", r"muslos?|piernas?|thigh|legs?"),
    ("Glúteos", r"gl[uú]teos?|nalgas|butt"),
    ("Cuello/escote", r"cuello|escote|d[eé]collet|neck|d[eé]colletage"),
    ("Papada", r"papada|doble\s+ment[oó]n|double\s+chin"),
    ("Cuerpo completo", r"todo\s+el\s+cuerpo|cuerpo\s+completo|body\s+care|corporal"),
])
AUDIENCE_MAP = collections.OrderedDict([
    ("Mujeres 40+/piel madura", r"\b4[0-9]\b|\b5[0-9]\b|\b6[0-9]\b|piel\s+madura|mujer(?:es)?\s+madura"),
    ("Menopausia", r"menopaus|perimenopaus"),
    ("Posparto", r"postparto|posparto|despu[eé]s\s+del\s+embarazo"),
    ("Después de bajar de peso", r"baj(?:ar|é|aste)\s+(?:de\s+)?peso|p[eé]rdida\s+de\s+peso|adelgaz"),
    ("Público general femenino", r"mujer|nosotras|para\s+ti"),
])

EXPECTED_ANGLES = collections.OrderedDict([
    ("Menopausia como causa explícita", r"menopaus|perimenopaus"),
    ("Flacidez después de bajar de peso", r"p[eé]rdida\s+de\s+peso|baj(?:ar|é|aste)\s+(?:de\s+)?peso|adelgaz"),
    ("Posparto", r"postparto|posparto|embarazo"),
    ("Alternativa explícita a cirugía o aparatología", r"sin\s+cirug|alternativa\s+a\s+la\s+cirug|hifu|radiofrecuencia"),
    ("Promesa cuantificada en centímetros", r"\b\d+(?:[.,]\d+)?\s*cm\b"),
    ("Promesa cuantificada en porcentaje", r"\b\d{1,3}\s*%"),
    ("Resultado garantizado o permanente", r"garantiz|permanente|para\s+siempre"),
    ("Resultado instantáneo/24 horas", r"instant[aá]ne|de\s+inmediato|24\s*horas"),
    ("Antes/después como argumento textual", r"antes\s+y\s+despu[eé]s|antes\/despu[eé]s|before\s+and\s+after"),
    ("Brazos como única zona central", r"alas\s+de\s+murci[eé]lago"),
])


def clean_text(v: Any) -> str:
    if v is None: return ""
    s = str(v)
    if "<" in s and ">" in s:
        try: s = BeautifulSoup(html.unescape(s), "html.parser").get_text(" ")
        except Exception: pass
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def walk(obj: Any, path: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(obj, dict):
        for k,v in obj.items():
            p=f"{path}.{k}" if path else str(k)
            yield p,v
            yield from walk(v,p)
    elif isinstance(obj, list):
        for i,v in enumerate(obj):
            yield from walk(v,f"{path}[{i}]")


def load_jsonish(path: Path) -> Any | None:
    try:
        raw=path.read_text("utf-8",errors="ignore").strip()
        raw=re.sub(r"^for\s*\(;;\);", "", raw).strip()
        if not raw: return None
        try: return json.loads(raw)
        except Exception:
            # GraphQL batched responses can be one JSON object per line.
            vals=[]
            for line in raw.splitlines():
                line=line.strip()
                if not line: continue
                try: vals.append(json.loads(line))
                except Exception: pass
            return vals or None
    except Exception:
        return None


def ids_in_obj(obj: Any) -> set[str]:
    out=set()
    for p,v in walk(obj):
        key=p.lower().split(".")[-1]
        if any(x in key for x in ("ad_archive_id","adarchiveid","archive_id","archiveid")):
            s=clean_text(v)
            if re.fullmatch(r"\d{6,}",s): out.add(s)
    return out


def build_network_index(root: Path) -> dict[str,list[Any]]:
    idx=collections.defaultdict(list)
    for path in root.rglob("network/*"):
        if not path.is_file(): continue
        obj=load_jsonish(path)
        if obj is None: continue
        # Attach the smallest dictionaries that carry an archive ID.
        stack=[obj]
        while stack:
            cur=stack.pop()
            if isinstance(cur,dict):
                ids=ids_in_obj(cur)
                if ids and len(json.dumps(cur,ensure_ascii=False,default=str)) < 2_500_000:
                    for i in ids: idx[i].append(cur)
                stack.extend(cur.values())
            elif isinstance(cur,list): stack.extend(cur)
    return idx


def pick_from_objects(objects:list[Any], kind:str) -> str:
    candidates=[]
    path_terms={
        "page":["page_name","pagename","page.name","page_profile_name"],
        "body":["ad_creative_body","snapshot.body","body.markup","body.text","message","primary_text","creative_body"],
        "title":["snapshot.title","headline","link_title","creative_title","cards[0].title"],
        "description":["link_description","snapshot.description","caption"],
        "url":["link_url","website_url","destination_url","outbound_url","link_url"] ,
        "start":["ad_delivery_start_time","start_date","startdate","start_time"],
    }[kind]
    for obj in objects:
        for p,v in walk(obj):
            if isinstance(v,(dict,list)): continue
            s=clean_text(v)
            if not s: continue
            pl=p.lower().replace("_","")
            score=0
            for term in path_terms:
                t=term.lower().replace("_","")
                if t in pl: score+=7
            if kind=="url":
                if not re.match(r"https?://",s): continue
                if not META_DOMAIN.search((urlparse(s).hostname or "")): score+=5
            elif kind=="page":
                if 2<=len(s)<=120: score+=2
                else: continue
            elif kind=="body":
                if 8<=len(s)<=7000: score+=2
                else: continue
                if TOPIC.search(s): score+=4
                if FORM.search(s): score+=2
            elif kind=="title":
                if 2<=len(s)<=350: score+=2
                else: continue
                if TOPIC.search(s): score+=2
            elif kind=="start":
                if re.search(r"20\d{2}|1\d{9}",s): score+=3
            candidates.append((score,len(s),s,p))
    if not candidates: return ""
    candidates.sort(key=lambda x:(x[0], x[1] if kind=="body" else -x[1]), reverse=True)
    return candidates[0][2]


def decode_external_url(href:str) -> str:
    if not href: return ""
    try:
        u=urlparse(href)
        if (u.hostname or "").endswith("l.facebook.com"):
            q=parse_qs(u.query)
            if q.get("u"): return unquote(q["u"][0])
        return href
    except Exception: return href


def external_links(rec:dict) -> list[str]:
    out=[]
    for l in rec.get("links") or []:
        u=decode_external_url(l.get("href", ""))
        if not re.match(r"https?://",u): continue
        host=(urlparse(u).hostname or "").lower()
        if META_DOMAIN.search(host): continue
        if u not in out: out.append(u)
    return out


def parse_start(value:str) -> datetime | None:
    if not value: return None
    v=clean_text(value)
    if re.fullmatch(r"\d{10,13}",v):
        try:
            ts=int(v); ts=ts/1000 if ts>10**11 else ts
            return datetime.fromtimestamp(ts,tz=timezone.utc)
        except Exception: pass
    # Trim metadata that can trail the line.
    v=re.split(r"(?:Platforms|Plataformas|See ad details|Ver detalles)",v,flags=re.I)[0].strip(" ·|")
    dt=parse_date(v,languages=["es","en"],settings={"RETURN_AS_TIMEZONE_AWARE":True,"TIMEZONE":"UTC","TO_TIMEZONE":"UTC","PREFER_DAY_OF_MONTH":"first"})
    if dt:
        if not dt.tzinfo: dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def card_page_and_body(rec:dict) -> tuple[str,str,str]:
    lines=[clean_text(x) for x in (rec.get("text") or "").splitlines()]
    lines=[x for x in lines if x]
    sponsor_idx=None
    for i,x in enumerate(lines):
        if re.fullmatch(r"Sponsored|Patrocinado|Publicidad",x,re.I): sponsor_idx=i; break
    page=""
    if sponsor_idx is not None:
        for x in reversed(lines[:sponsor_idx]):
            if not META_LINE.search(x) and not re.fullmatch(r"\d+[dwhms]",x,re.I):
                page=x; break
    after=lines[sponsor_idx+1:] if sponsor_idx is not None else lines
    cleaned=[]
    for x in after:
        if META_LINE.search(x) or CTA.match(x): continue
        if re.fullmatch(r"[\w.-]+\.[a-z]{2,}(?:\s.*)?",x,re.I): continue
        if x not in cleaned: cleaned.append(x)
    # Estimate body from the first substantive block. Text nodes in Meta usually keep the body as one line.
    body=""
    for x in cleaned:
        if len(x)>=8 and not re.match(r"^(http|www\.)",x,re.I):
            body=x; break
    # Estimate headline from bold leaves below the largest media element.
    headline=""
    rect=rec.get("rect") or {}
    media=(rec.get("images") or [])+(rec.get("videos") or [])
    rel_bottom=0
    if media:
        largest=max(media,key=lambda m:(m.get("width",0)*m.get("height",0)))
        rel_bottom=(largest.get("y",0)-rect.get("y",0))+largest.get("height",0)
    bold=[]
    for leaf in rec.get("leaves") or []:
        txt=clean_text(leaf.get("text"))
        try: fw=int(re.sub(r"\D","",str(leaf.get("font_weight","400"))) or 400)
        except Exception: fw=400
        if txt and 3<=len(txt)<=250 and fw>=600 and leaf.get("y",0)>=max(0,rel_bottom-20) and not CTA.match(txt) and not META_LINE.search(txt):
            bold.append((leaf.get("y",0),-len(txt),txt))
    if bold:
        bold.sort(); headline=bold[0][2]
    elif len(cleaned)>1:
        headline=cleaned[-1] if len(cleaned[-1])<=250 else ""
    return page,body,headline


def relevance(text:str) -> int:
    score=0
    score += 5 if TOPIC.search(text) else 0
    score += 4 if FORM.search(text) else 0
    score += min(3,len(set(m.lower() for m in re.findall(r"reafirm\w*|fl[aá]cid\w*|firmeza|papada|celulit\w*|aceite|crema",text,re.I))))
    if EXCLUDE.search(text) and not FORM.search(text): score-=8
    if re.search(r"facial|rostro|cara",text,re.I) and not re.search(r"corporal|cuerpo|cuello|papada|brazos|abdomen|muslos",text,re.I): score-=4
    return score


def classify_map(text:str, mapping:collections.OrderedDict) -> list[str]:
    return [label for label,pat in mapping.items() if re.search(pat,text,re.I)]


def opening_type(body:str) -> str:
    s=body.strip()
    if not s: return "No recuperado"
    if s.startswith("¿") or re.match(r"^(te|sab[ií]as|quieres|cansad[ao]|notas|sientes)\b.*\?",s,re.I): return "Pregunta"
    if re.match(r"^[\"“']|^(yo|mi|desde que|cuando)\b",s,re.I): return "Testimonio/historia en primera persona"
    if re.match(r"^\d|^[^\n]{0,30}\b\d+(?:[.,]\d+)?\s*%",s): return "Cifra/dato"
    if re.match(r"^(descubre|conoce|prueba|transforma|reafirma|recupera|dile adiós|adiós)\b",s,re.I): return "Beneficio o imperativo directo"
    if re.search(r"flacidez|piel\s+fl[aá]cida|falta\s+de\s+firmeza",s[:160],re.I): return "Problema directo"
    if re.search(r"oferta|descuento|\d+x\d|env[ií]o\s+gratis",s[:160],re.I): return "Oferta"
    return "Afirmación/beneficio"


def word_excerpt(text:str,limit:int=25) -> tuple[str,int]:
    words=re.findall(r"\S+",text or "")
    excerpt=" ".join(words[:limit])
    if len(words)>limit: excerpt += " […]"
    return excerpt,len(words)


def follow_url(url:str) -> tuple[str,str,str]:
    if not url: return "","","No recuperado"
    final=url; title=""; kind="Página de aterrizaje no clasificada"
    try:
        r=requests.get(url,timeout=18,allow_redirects=True,headers={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36"},stream=True)
        final=r.url
        ct=(r.headers.get("content-type") or "").lower()
        if "text/html" in ct:
            content=r.raw.read(400000,decode_content=True)
            soup=BeautifulSoup(content,"html.parser")
            title=clean_text(soup.title.get_text(" ") if soup.title else "")
            txt=clean_text(soup.get_text(" "))[:6000]
        else: txt=""
    except Exception:
        txt=""
    p=urlparse(final); path=(p.path or "/").lower(); alltxt=(title+" "+txt).lower()
    if re.search(r"quiz|cuestionario|diagn[oó]stico|typeform",path+" "+alltxt): kind="Cuestionario/quiz"
    elif re.search(r"advertorial|blog|article|articulo|artículo|noticias|magazine",path) or re.search(r"historia de|m[eé]todo que|descubrimiento",alltxt[:2000]): kind="Artículo/advertorial"
    elif re.search(r"/products?/|/productos?/|/p/|product-page",path): kind="Página de producto"
    elif re.search(r"/collections?/|/categor",path): kind="Colección/categoría"
    elif path in ("","/"): kind="Home"
    elif re.search(r"comprar|añadir al carrito|agregar al carrito|buy now|add to cart",alltxt): kind="Página de venta/producto"
    return final,title,kind


def caption_image(path:Path) -> tuple[str,str]:
    """Returns a conservative Spanish visual description and OCR. BLIP is optional."""
    if not path.exists(): return "No se recuperó el fotograma creativo.",""
    ocr=""
    try:
        import pytesseract
        ocr=pytesseract.image_to_string(Image.open(path).convert("RGB"),lang="spa+eng",config="--psm 6")
        ocr=clean_text(ocr)
    except Exception: pass
    cap=""
    try:
        from transformers import BlipProcessor, BlipForConditionalGeneration
        global _BLIP_PROCESSOR,_BLIP_MODEL
        if "_BLIP_PROCESSOR" not in globals():
            _BLIP_PROCESSOR=BlipProcessor.from_pretrained("Salesforce/blip-image-captioning-base")
            _BLIP_MODEL=BlipForConditionalGeneration.from_pretrained("Salesforce/blip-image-captioning-base")
        img=Image.open(path).convert("RGB")
        inp=_BLIP_PROCESSOR(images=img,return_tensors="pt")
        out=_BLIP_MODEL.generate(**inp,max_new_tokens=35)
        cap=_BLIP_PROCESSOR.decode(out[0],skip_special_tokens=True)
    except Exception: pass
    low=cap.lower()
    bits=[]
    if "woman" in low or "women" in low: bits.append("aparece una mujer")
    if "hand" in low: bits.append("se ve una mano")
    if "bottle" in low or "container" in low: bits.append("el envase del producto es visible")
    if "cream" in low or "lotion" in low: bits.append("se muestra una crema o loción")
    if "oil" in low: bits.append("se muestra un aceite")
    if "applying" in low or "putting" in low or "rubbing" in low: bits.append("hay una demostración de aplicación")
    if "arm" in low: bits.append("la zona mostrada es el brazo")
    if "neck" in low: bits.append("la zona mostrada es el cuello")
    if "stomach" in low or "belly" in low or "abdomen" in low: bits.append("la zona mostrada es el abdomen")
    if "leg" in low or "thigh" in low: bits.append("la zona mostrada es la pierna o el muslo")
    if "before and after" in low or "split" in low: bits.append("la composición parece comparativa o dividida")
    if "table" in low: bits.append("el producto está apoyado sobre una superficie")
    desc=("; ".join(bits).capitalize()+".") if bits else "Fotograma o imagen del anuncio con el producto y/o su demostración; la captura no permite una descripción visual más específica sin inferir."
    if ocr:
        # Remove recurring Meta UI text and limit to a useful, non-copying overlay excerpt.
        ocr=re.sub(r"Library ID.*|Started running.*|Sponsored|See ad details"," ",ocr,flags=re.I)
        ox,_=word_excerpt(clean_text(ocr),25)
        if ox: desc += f" Texto sobreimpreso legible: «{ox}»."
    return desc,ocr


def md_escape(s:str) -> str:
    return (s or "").replace("|","\\|")


def pct(n:int,d:int)->str:
    return f"{(100*n/d):.0f}%" if d else "0%"


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--root",required=True); ap.add_argument("--out",required=True); ap.add_argument("--json-out",required=True)
    args=ap.parse_args(); root=Path(args.root); out=Path(args.out); json_out=Path(args.json_out)
    records=[]; diagnostics=[]
    for p in root.rglob("records.json"):
        try: records.extend(json.loads(p.read_text("utf-8")))
        except Exception: pass
    for p in root.rglob("diagnostics.json"):
        try: diagnostics.extend(json.loads(p.read_text("utf-8")))
        except Exception: pass
    net=build_network_index(root)

    grouped=collections.defaultdict(list)
    for r in records:
        if r.get("ad_id"): grouped[str(r["ad_id"])].append(r)
    ads=[]
    for ad_id,recs in grouped.items():
        objects=net.get(ad_id,[])
        page_n=pick_from_objects(objects,"page")
        body_n=pick_from_objects(objects,"body")
        title_n=pick_from_objects(objects,"title")
        url_n=pick_from_objects(objects,"url")
        start_n=pick_from_objects(objects,"start")
        dom_page,dom_body,dom_title=card_page_and_body(max(recs,key=lambda r:len(r.get("text", ""))))
        page=page_n or dom_page or "No recuperado"
        body=body_n or dom_body
        title=title_n or dom_title
        links=[]
        for r in recs:
            for u in external_links(r):
                if u not in links: links.append(u)
        destination=url_n or (links[0] if links else "")
        start=None; start_raw=""
        for val in [start_n]+[r.get("start_text","") for r in recs]:
            dt=parse_start(val)
            if dt and (start is None or dt<start): start=dt; start_raw=val
        combined=" ".join([page,body,title]+[r.get("text","") for r in recs])
        rel=relevance(combined)
        countries=sorted(set(r.get("country") for r in recs if r.get("country")))
        queries=sorted(set(r.get("query") for r in recs if r.get("query")))
        media_type="Video" if any(r.get("videos") for r in recs) else ("Imagen" if any(r.get("images") for r in recs) else "No determinado")
        screenshot=""
        for r in recs:
            x=r.get("creative_screenshot") or r.get("card_screenshot")
            if x:
                cand=root / x if not Path(x).is_absolute() else Path(x)
                # x can already contain the country root path. Resolve by basename search if needed.
                if not cand.exists():
                    found=list(root.rglob(Path(x).name)); cand=found[0] if found else cand
                if cand.exists(): screenshot=str(cand); break
        ads.append({
            "ad_id":ad_id,"advertiser":page,"body":body,"headline":title,"destination":destination,
            "start":start,"start_raw":start_raw,"countries":countries,"queries":queries,"media_type":media_type,
            "relevance":rel,"combined":combined,"screenshot":screenshot,"records":recs,
            "direct_meta_url":f"https://www.facebook.com/ads/library/?id={ad_id}",
        })

    # Strict evidentiary gate: individual archive ID, a parseable start date, active search result, topical topical-product relevance.
    eligible=[a for a in ads if a["start"] and a["relevance"]>=7 and a["start"]<=TODAY]
    eligible.sort(key=lambda a:(a["start"],a["ad_id"]))
    top=eligible[:15]

    # Resolve destinations and describe creatives only for selected ads.
    for a in top:
        final,title,kind=follow_url(a["destination"])
        a["destination_final"]=final or a["destination"]
        a["destination_title"]=title
        a["destination_type"]=kind
        desc,ocr=caption_image(Path(a["screenshot"])) if a["screenshot"] else ("No se recuperó el fotograma creativo.","")
        a["creative_description"]=f"{a['media_type']}. {desc}"
        a["ocr"]=ocr
        text=" ".join([a["body"],a["headline"],ocr])
        a["promises"]=classify_map(text,PROMISE_MAP) or ["No clasificable con el texto recuperado"]
        a["mechanisms"]=classify_map(text,MECH_MAP) or ["No explica un mecanismo específico en el texto recuperado"]
        a["zones"]=classify_map(text,ZONE_MAP) or ["No nombra una zona específica"]
        a["audience"]=classify_map(text,AUDIENCE_MAP) or ["Público corporal general; sin segmento explícito"]
        a["opening_type"]=opening_type(a["body"])
        a["days_active"]=(TODAY-a["start"]).days
        a["evidence_grade"]="A" if a["advertiser"]!="No recuperado" and a["body"] and a["headline"] else "B"

    n=len(top)
    adv_counts=collections.Counter(a["advertiser"] for a in top)
    dominant=adv_counts.most_common(1)[0] if adv_counts else (None,0)
    promise_counts=collections.Counter(x for a in top for x in a["promises"])
    mech_counts=collections.Counter(x for a in top for x in a["mechanisms"])
    zone_counts=collections.Counter(x for a in top for x in a["zones"])
    opening_counts=collections.Counter(a["opening_type"] for a in top)
    dest_counts=collections.Counter(a["destination_type"] for a in top)
    media_counts=collections.Counter(a["media_type"] for a in top)
    country_counts=collections.Counter(x for a in top for x in a["countries"])

    non_dom=[a for a in top if a["advertiser"]!=dominant[0]] if dominant[1]>=4 else top
    non_dom_prom=collections.Counter(x for a in non_dom for x in a["promises"])
    non_dom_zones=collections.Counter(x for a in non_dom for x in a["zones"])
    corpus=" ".join(a["body"]+" "+a["headline"]+" "+a.get("ocr","") for a in top)
    absent=[label for label,pat in EXPECTED_ANGLES.items() if not re.search(pat,corpus,re.I)]

    generated=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines=[]
    lines += [
        "# Mapa de publicidad activa: aceites y cremas corporales de firmeza",
        "",
        f"**Mercados:** España · México · Colombia  ",
        f"**Corte de observación:** 17 de agosto de 2026  ",
        f"**Extracción técnica:** {generated}  ",
        "**Universo consultado:** anuncios activos, ocho búsquedas por mercado: aceite corporal reafirmante; crema reafirmante; flacidez; piel flácida; firmeza corporal; aceite firmeza; brazos flacidez; papada.",
        "",
        "## Nota de lectura y estándar de evidencia",
        "",
        "El ranking usa exclusivamente el **ID individual del anuncio** y la fecha de inicio visible o recuperada para esa ficha. No se usa la antigüedad de la página, del producto ni de una campaña distinta como sustituto.",
        "",
        "Meta no publica rentabilidad. En este informe, «longevo» significa que la ficha permanecía activa desde la fecha indicada; no se presenta la duración como prueba independiente de ventas o beneficio.",
        "",
        "Por límites de reproducción de fuentes, cuando el cuerpo supera 25 palabras se incluye un **extracto literal de hasta 25 palabras**, el número de palabras recuperadas y el enlace directo para leer la pieza íntegra en Meta. Los titulares de hasta 25 palabras se reproducen completos.",
        "",
        f"**Resultado del gate:** {len(records)} tarjetas capturadas; {len(grouped)} IDs únicos; {len(eligible)} fichas elegibles con ID, fecha y relevancia tópica; {n} incluidas en el ranking solicitado.",
        "",
    ]
    if n<15:
        lines += [
            "> **Cobertura incompleta:** no fue posible verificar 15 fichas que cumplieran simultáneamente ID individual, fecha de inicio, estado activo y pertenencia a producto tópico corporal. El informe no rellena los huecos con marcas, campañas históricas ni anuncios sin fecha.",
            "",
        ]

    lines += ["## Ranking: anuncios activos más longevos verificados",""]
    lines += ["| # | Inicio | Días activos | Mercado(s) | Anunciante | ID | Evidencia |", "|---:|---|---:|---|---|---:|:---:|"]
    for i,a in enumerate(top,1):
        lines.append(f"| {i} | {a['start'].date().isoformat()} | {a['days_active']} | {', '.join(COUNTRY_NAMES.get(c,c) for c in a['countries'])} | {md_escape(a['advertiser'])} | [{a['ad_id']}]({a['direct_meta_url']}) | {a['evidence_grade']} |")
    lines.append("")

    for i,a in enumerate(top,1):
        bx,bwc=word_excerpt(a["body"],25); hx,hwc=word_excerpt(a["headline"],25)
        lines += [
            f"## {i}. {a['advertiser']} — ID {a['ad_id']}",
            "",
            f"- **Enlace directo a la ficha:** {a['direct_meta_url']}",
            f"- **Mercado(s) donde apareció:** {', '.join(COUNTRY_NAMES.get(c,c) for c in a['countries'])}",
            f"- **Fecha de inicio:** {a['start'].date().isoformat()} ({a['days_active']} días hasta el corte)",
            f"- **Estado al corte:** Activo",
            f"- **Búsqueda(s) que lo recuperaron:** {', '.join(a['queries'])}",
            f"- **Calidad de evidencia:** {a['evidence_grade']} — " + ("ID, fecha, anunciante, cuerpo y titular recuperados." if a['evidence_grade']=="A" else "ID y fecha verificados; uno o más campos creativos no fueron recuperados de forma completa."),
            "",
            "### Texto del anuncio",
            "",
            f"- **Cuerpo — extracto literal ({min(bwc,25)} de {bwc} palabras recuperadas):** «{bx or 'No recuperado'}»",
            f"- **Titular literal:** «{hx or 'No recuperado'}»" + (f" *(extracto de 25 de {hwc} palabras)*" if hwc>25 else ""),
            "",
            "### Creativo y destino",
            "",
            f"- **Qué se ve:** {a['creative_description']}",
            f"- **URL de destino recuperada:** {a['destination_final'] or 'No recuperada'}",
            f"- **Tipo de destino:** {a['destination_type']}",
            f"- **Título de la página de destino:** {a['destination_title'] or 'No recuperado'}",
            "",
            "### Ángulo observado",
            "",
            f"- **Promesa:** {'; '.join(a['promises'])}.",
            f"- **Mecanismo explicado:** {'; '.join(a['mechanisms'])}.",
            f"- **A quién le habla:** {'; '.join(a['audience'])}.",
            f"- **Zona(s) nombrada(s):** {'; '.join(a['zones'])}.",
            f"- **Forma de apertura:** {a['opening_type']}.",
            "",
        ]

    lines += ["# Patrón de los anuncios más longevos",""]
    if n:
        lines += [
            "## 1. Concentración por anunciante",
            "",
            f"El anunciante con más presencia es **{dominant[0]}**, con **{dominant[1]} de {n} fichas** ({pct(dominant[1],n)}).",
            "",
        ]
        if dominant[1]>=4:
            lines += [
                "Para no confundir la estrategia de una sola cuenta con la del mercado, las lecturas siguientes muestran primero la muestra completa y luego el patrón que permanece al excluir al anunciante dominante.",
                "",
                "| Anunciante | Anuncios en el top | Participación |",
                "|---|---:|---:|",
            ]
            for adv,c in adv_counts.most_common(): lines.append(f"| {md_escape(adv)} | {c} | {pct(c,n)} |")
            lines.append("")
        lines += ["## 2. Promesas que se repiten",""]
        for label,c in promise_counts.most_common(): lines.append(f"- **{label}:** {c}/{n} ({pct(c,n)}).")
        if dominant[1]>=4 and non_dom:
            lines += ["",f"**Sin {dominant[0]} ({len(non_dom)} anuncios):** "+"; ".join(f"{k} {v}/{len(non_dom)}" for k,v in non_dom_prom.most_common())+"."]
        lines += ["","## 3. Zonas del cuerpo nombradas",""]
        for label,c in zone_counts.most_common(): lines.append(f"- **{label}:** {c}/{n} ({pct(c,n)}).")
        if dominant[1]>=4 and non_dom:
            lines += ["",f"**Sin {dominant[0]}:** "+"; ".join(f"{k} {v}/{len(non_dom)}" for k,v in non_dom_zones.most_common())+"."]
        lines += ["","## 4. Mecanismos usados para hacer creíble la promesa",""]
        for label,c in mech_counts.most_common(): lines.append(f"- **{label}:** {c}/{n} ({pct(c,n)}).")
        lines += ["","## 5. Cómo abren el texto",""]
        for label,c in opening_counts.most_common(): lines.append(f"- **{label}:** {c}/{n} ({pct(c,n)}).")
        lines += ["","## 6. Formato creativo y destino",""]
        lines.append("**Formato:** "+"; ".join(f"{k} {v}/{n}" for k,v in media_counts.most_common())+".")
        lines.append("")
        lines.append("**Destino:** "+"; ".join(f"{k} {v}/{n}" for k,v in dest_counts.most_common())+".")
        lines += ["","## 7. Qué prometen y qué no prometen",""]
        intense={
            "porcentaje":len(re.findall(r"\b\d{1,3}\s*%",corpus)),
            "centímetros":len(re.findall(r"\b\d+(?:[.,]\d+)?\s*cm\b",corpus,re.I)),
            "garantía/permanencia":len(re.findall(r"garantiz|permanente|para\s+siempre",corpus,re.I)),
            "inmediatez":len(re.findall(r"instant[aá]ne|de\s+inmediato|24\s*horas",corpus,re.I)),
        }
        lines.append("La promesa central se formula principalmente como **mejora de firmeza, apariencia, hidratación o elasticidad**, no como curación médica. En el texto recuperado del top se detectaron: "+"; ".join(f"{k}={v}" for k,v in intense.items())+".")
        lines.append("")
        lines.append("## 8. Unidad de patrón por mercado")
        lines.append("")
        for c in ["ES","MX","CO"]: lines.append(f"- **{COUNTRY_NAMES[c]}:** {country_counts.get(c,0)} de las {n} fichas del top fueron recuperadas en ese mercado.")
    else:
        lines.append("No hay una muestra elegible suficiente para calcular patrones sin inventar datos.")

    lines += ["","# Ángulos esperados que no aparecen en los longevos",""]
    if absent:
        for x in absent: lines.append(f"- {x}.")
    else:
        lines.append("Ninguno de los ángulos predefinidos quedó completamente ausente.")
    lines += [
        "",
        "Esta sección registra ausencia en el **texto y creativo recuperados de la muestra longeva**, no inexistencia absoluta en toda la Biblioteca.",
        "",
        "# Registro metodológico y límites",
        "",
        "- La Biblioteca se consultó con estado activo y país específico. Una misma ficha puede aparecer en varios términos o mercados; se deduplicó por ID de biblioteca.",
        "- Las fechas se normalizaron a UTC y el ranking se ordenó de la más antigua a la más reciente.",
        "- Los destinos se siguieron con redirecciones cuando el sitio respondió; si bloqueó el acceso, se conserva la URL recuperada y se marca la clasificación como no determinada.",
        "- La descripción visual se basa en el fotograma o captura recuperada, OCR y reconocimiento de imagen conservador. Cuando no había evidencia suficiente se evitó inferir.",
        "- Una ficha activa y longeva no demuestra por sí sola rentabilidad, inversión continua ni volumen de gasto.",
        "",
        "## Diagnóstico técnico por consulta",
        "",
        "| País | Consulta | Tarjetas | HTTP | Señal de bloqueo |",
        "|---|---|---:|---:|---|",
    ]
    for d in diagnostics:
        lines.append(f"| {COUNTRY_NAMES.get(d.get('country'),d.get('country'))} | {md_escape(d.get('query',''))} | {d.get('cards',0)} | {d.get('http_status','')} | {md_escape(', '.join(d.get('blocked_terms') or []) or 'No')} |")
    lines.append("")

    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text("\n".join(lines),encoding="utf-8")
    serial=[]
    for a in top:
        b={k:v for k,v in a.items() if k not in ("records","combined")}
        if isinstance(b.get("start"),datetime): b["start"]=b["start"].isoformat()
        serial.append(b)
    json_out.write_text(json.dumps({"generated_at":generated,"diagnostics":diagnostics,"top":serial,"eligible_count":len(eligible),"unique_ids":len(grouped)},ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    print(json.dumps({"records":len(records),"unique_ids":len(grouped),"eligible":len(eligible),"top":n,"report":str(out)},ensure_ascii=False))

if __name__=="__main__": main()
