#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from pathlib import Path
from typing import Any

ID_RE=re.compile(r"(?:Library ID|ID de la biblioteca|Identificador de la biblioteca)\s*:?\s*(\d{6,})",re.I)
START_RE=[
 re.compile(r"Started running on\s+([^\n]+)",re.I),
 re.compile(r"En circulación desde(?: el)?\s+([^\n]+)",re.I),
 re.compile(r"Empezó a publicarse(?: el)?\s+([^\n]+)",re.I),
]

def walk(o:Any):
 if isinstance(o,dict):
  yield o
  for v in o.values(): yield from walk(v)
 elif isinstance(o,list):
  for v in o: yield from walk(v)

def strings(o:Any):
 if isinstance(o,str): yield o
 elif isinstance(o,dict):
  for k,v in o.items():
   if isinstance(v,str): yield v
   else: yield from strings(v)
 elif isinstance(o,list):
  for v in o: yield from strings(v)

def ids(o:Any):
 out=set()
 if isinstance(o,dict):
  for k,v in o.items():
   kl=str(k).lower().replace("_","")
   if ("archiveid" in kl or "adarchiveid" in kl) and re.fullmatch(r"\d{6,}",str(v)): out.add(str(v))
 return out

def load_jsonish(p:Path):
 try:
  raw=p.read_text("utf-8",errors="ignore").strip()
  raw=re.sub(r"^for\s*\(;;\);", "", raw).strip()
  try:return json.loads(raw)
  except Exception:
   vals=[]
   for line in raw.splitlines():
    try: vals.append(json.loads(line))
    except Exception: pass
   return vals
 except Exception:return None

def country_for(path:Path,root:Path):
 s=str(path).upper()
 for c in ("ES","MX","CO"):
  if f"-{c}" in s or f"_{c}" in s or f"/{c}/" in s:return c
 # diagnostics is authoritative when directory names were flattened.
 for d in path.parents:
  q=list(d.glob("**/diagnostics.json"))
  for p in q[:1]:
   try:
    arr=json.loads(p.read_text("utf-8"))
    if arr and arr[0].get("country"):return arr[0]["country"]
   except Exception:pass
 return ""

def main():
 ap=argparse.ArgumentParser();ap.add_argument("--root",required=True);args=ap.parse_args();root=Path(args.root)
 stats={"body_chunks":0,"network_ids":0,"files":0}
 # First augment each native records.json using visible full-page text.
 for recfile in root.rglob("records.json"):
  stats["files"]+=1
  try: arr=json.loads(recfile.read_text("utf-8"))
  except Exception:arr=[]
  existing={(str(x.get("ad_id")),x.get("query"),x.get("country")) for x in arr if x.get("ad_id")}
  base=recfile.parent
  try:
   diags=json.loads((base/"diagnostics.json").read_text("utf-8"))
  except Exception:diags=[]
  qmap={f"q{i+1:02d}":d for i,d in enumerate(diags)}
  for bodyfile in base.glob("q*/body.txt"):
   txt=bodyfile.read_text("utf-8",errors="ignore")
   matches=list(ID_RE.finditer(txt))
   qslug=bodyfile.parent.name;diag=qmap.get(qslug,{})
   for i,m in enumerate(matches):
    adid=m.group(1);chunk=txt[m.start():matches[i+1].start() if i+1<len(matches) else len(txt)]
    # Keep one card-sized window; page footers after the last card are excluded by a generous cap.
    chunk=chunk[:18000]
    st=""
    for pat in START_RE:
     mm=pat.search(chunk)
     if mm:st=mm.group(1).splitlines()[0].strip();break
    key=(adid,diag.get("query"),diag.get("country"))
    if key in existing:continue
    arr.append({
      "country":diag.get("country") or country_for(bodyfile,root),"query":diag.get("query") or qslug,
      "ad_id":adid,"start_text":st,"active":True,"text":chunk,"links":[],"images":[],"videos":[],"leaves":[],
      "rect":{},"card_screenshot":str(bodyfile.parent/"page.png") if (bodyfile.parent/"page.png").exists() else None,
      "creative_screenshot":None,"fallback_source":"full_page_text"
    });existing.add(key);stats["body_chunks"]+=1
  recfile.write_text(json.dumps(arr,ensure_ascii=False,indent=2),encoding="utf-8")

 # Then create a synthetic records file for archive IDs found only in GraphQL/async responses.
 synthetic=[]; seen=set()
 for p in root.rglob("network/*"):
  if not p.is_file():continue
  obj=load_jsonish(p)
  if obj is None:continue
  c=country_for(p,root)
  for d in walk(obj):
   dids=ids(d)
   if not dids:continue
   text="\n".join(dict.fromkeys(s.strip() for s in strings(d) if isinstance(s,str) and s.strip()))[:30000]
   for adid in dids:
    key=(adid,c)
    if key in seen:continue
    seen.add(key)
    st=""
    for pat in START_RE:
     mm=pat.search(text)
     if mm:st=mm.group(1).splitlines()[0].strip();break
    synthetic.append({"country":c,"query":"respuesta interna de Meta","ad_id":adid,"start_text":st,"active":True,"text":text,
      "links":[],"images":[],"videos":[],"leaves":[],"rect":{},"card_screenshot":None,"creative_screenshot":None,
      "fallback_source":"network_json"})
    stats["network_ids"]+=1
 sdir=root/"synthetic_network";sdir.mkdir(exist_ok=True)
 (sdir/"records.json").write_text(json.dumps(synthetic,ensure_ascii=False,indent=2),encoding="utf-8")
 (sdir/"diagnostics.json").write_text("[]",encoding="utf-8")
 print(json.dumps(stats,ensure_ascii=False))
if __name__=="__main__":main()
