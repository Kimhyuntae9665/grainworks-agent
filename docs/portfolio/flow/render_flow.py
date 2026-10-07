"""Embed official symbol assets; rasterize without changing brand artwork."""
from pathlib import Path
import base64, hashlib, json, re, xml.etree.ElementTree as ET
import cairosvg

OUT = Path(__file__).resolve().parent
sources = [
    ('python.svg', 'Python API', 'https://s3.dualstack.us-east-2.amazonaws.com/pythondotorg-assets/media/files/python-logo-only.svg', 'https://www.python.org/community/logos/', 'Official standalone Python symbol; PSF logo/trademark usage terms apply.'),
    ('langgraph.png', 'LangGraph', 'https://raw.githubusercontent.com/langchain-ai/docs/85713b37be888ed867976d7b908a3fe80a92c19e/src/images/brand/langgraph-icon.png', 'https://www.langchain.com/brand-assets', 'Official documentation standalone icon; preserve artwork. Documentation/presentation use; trademark remains with owner.'),
    ('ollama.svg', 'Ollama', 'https://raw.githubusercontent.com/ollama/ollama/f864601538bd283dbcd011244db99e2760b125c1/docs/ollama-logo.svg', 'https://github.com/ollama/ollama', 'Official repository symbol; upstream MIT notices apply, no trademark endorsement.'),
    ('qwen.png', 'Qwen3 4B', 'https://avatars.githubusercontent.com/u/141221163?s=512&v=4', 'https://github.com/QwenLM/Qwen3', 'Official QwenLM organization family symbol reused for Qwen3 4B. Snapshot SHA identifies mutable avatar; model Apache-2.0 does not grant trademark rights.'),
]
manifest = {'diagram': 'Verified local runtime, not company deployment', 'code_revision': '635b0731fcb2f247d2aec68625d79f18d5e3c5b1', 'verified_on': '2026-10-07', 'assets': []}
svg = ['<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1600" height="300" viewBox="0 0 1600 300">',
       '<title>Grainworks local AI architecture</title>',
       '<desc>Python API calls LangGraph. LangGraph exchanges messages with Ollama, which hosts Qwen3 4B locally. LangGraph executes read-only tools and returns results to the model. A person reviews evidence and proposals.</desc>',
       '<defs><pattern id="dots" width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="3" cy="3" r="1" fill="#334454"/></pattern><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M1 1 L9 5 L1 9" fill="none" stroke="#aab6c2" stroke-width="1.5"/></marker></defs>',
       '<rect width="1600" height="300" rx="18" fill="#14212e"/>']
for x in range(20,1600,20):
    for y in range(20,300,20):svg.append(f'<circle cx="{x}" cy="{y}" r="1" fill="#334454"/>')
centers = [220,600,980,1360]
for i in range(3):
    start,end=centers[i]+81,centers[i+1]-81
    svg.append(f'<path d="M{start} 118 C{start+65} 78 {end-65} 78 {end} 118" fill="none" stroke="#aab6c2" stroke-width="2.2" />')
    svg.append(f'<path d="M{end} 150 C{end-65} 190 {start+65} 190 {start} 150" fill="none" stroke="#aab6c2" stroke-width="2.2" />')
    svg.append(f'<path d="M{end-10} 104 L{end} 118 L{end-16} 119 M{start+10} 164 L{start} 150 L{start+16} 149" fill="none" stroke="#aab6c2" stroke-width="2.2"/>')
for center,(filename,label,url,page,note) in zip(centers,sources):
    path=OUT/'logos'/filename; data=path.read_bytes()
    manifest['assets'].append({'file':'logos/'+filename,'label':label,'source_url':url,'source_page':page,'sha256':hashlib.sha256(data).hexdigest(),'license_trademark_note':note,'transformation':'Fit preserving aspect ratio; no artwork or color edits.'})
    svg.append(f'<rect x="{center-80}" y="54" width="160" height="160" rx="24" fill="#fff"/>')
    if path.suffix=='.svg':
        tree=ET.fromstring(data); vb=tree.get('viewBox')
        if vb:
            _,_,w,h=map(float,vb.split());tree.set('width',str(w*12));tree.set('height',str(h*12))
        data=cairosvg.svg2png(url=str(path),output_width=636,output_height=636); mime='image/png'
    else:mime='image/png'
    uri='data:'+mime+';base64,'+base64.b64encode(data).decode()
    svg.append(f'<image x="{center-53}" y="81" width="106" height="106" preserveAspectRatio="xMidYMid meet" xlink:href="{uri}"/>')
    svg.append(f'<text x="{center}" y="257" text-anchor="middle" fill="#f1f5f9" font-family="Arial,sans-serif" font-size="30" font-weight="600">{label}</text>')
svg.append('</svg>')
target=OUT/'architecture-flow.svg';target.write_text('\n'.join(svg),encoding='utf-8')
cairosvg.svg2png(url=str(target),write_to=str(OUT/'architecture-flow.png'),output_width=3200,output_height=600)
(OUT/'assets-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'graph.json').write_text(json.dumps({'nodes':[{'id':i,'name':s[1],'logo':'logos/'+s[0]} for i,s in enumerate(sources)],'edges':[{'from':i,'to':i+1,'meaning':'request / execution','return_edge':True} for i in range(3)],'model':'qwen3:4b-instruct','hosting':'Qwen model executes inside Ollama; not a separate service','tool_loop':'LangGraph model -> read-only tools -> model -> synthesis','human_boundary':'Evidence and proposals returned to UI; manual actions only'},ensure_ascii=False,indent=2),encoding='utf-8')
print(target)
