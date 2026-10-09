"""Readable three-page case study, from actual factory/order screens and traces."""
from pathlib import Path
from html import escape
import hashlib,json,re,shutil
import fitz,qrcode
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'docs/portfolio';ORD=ROOT/'docs/orders';EVID=OUT/'evidence'
PDF=OUT/'김현태_우성_IT_AX_생산품질_주문_RAG_포트폴리오_v13.pdf'
REPO='https://github.com/Kimhyuntae9665/grainworks-agent'
MES='https://www.woosungfeed.co.kr/m11.php'
JD='https://woosung.recruiter.co.kr/career/jobs/128746'
W,H=1280,720
INK,BODY,MUTED='#183447','#35556b','#526c7d'
PAPER,LINE,BLUE,AMBER='#fafcfd','#beced8','#396e92','#8f6125'

def build():
 text=(OUT/'final-orders.txt').read_text(encoding='utf-8').split('<!-- HUMANIZE-SUMMARY')[0]
 p=re.split(r'^\[([a-z_.]+)\]\s*$',text,flags=re.M)
 b={p[i]:p[i+1].strip() for i in range(1,len(p),2)}
 text=(OUT/'final-rag.txt').read_text(encoding='utf-8').split('<!-- HUMANIZE-SUMMARY')[0]
 p=re.split(r'^\[([a-z_.]+)\]\s*$',text,flags=re.M)
 b.update({p[i]:p[i+1].strip() for i in range(1,len(p),2)})
 rag_live=json.loads((ROOT/'docs/rag/ui-normal-live.json').read_text(encoding='utf-8-sig'))
 rag_eval=json.loads((ROOT/'docs/evaluation/rag-live.json').read_text(encoding='utf-8'))
 rag=rag_live['result']['rag']
 assert rag_live['status']=='completed' and rag['validation']['status']=='passed'
 assert len(rag['claims'])==6 and all(x['status']=='accepted' for x in rag['claims'])
 assert rag['originalUnchanged'] and rag_live['result']['summaryVerified'] is False
 assert rag_eval['summary']['modelCalls']==6 and rag_eval['summary']['preflightSkips']==4
 pair={x['ragEnabled']:x for x in rag_eval['cases'] if x['case']=='normal'}
 assert pair[False]['question']==pair[True]['question']
 assert pair[False]['stateHashBefore']==pair[True]['stateHashBefore']
 assert rag_eval['summary']['runs']==10 and rag_eval['summary']['checksPassed']==10
 assert len(rag_eval['sourceSha256'])==13
 assert all(hashlib.sha256((ROOT/file).read_bytes()).hexdigest()==sha for file,sha in rag_eval['sourceSha256'].items())
 assert pair[False]['result']['rag']['validation']['status']=='abstained'
 assert pair[True]['result']['rag']['validation']['status']=='passed'
 qa=json.loads((ORD/'deterministic-qa.json').read_text(encoding='utf-8'))
 live=json.loads((ORD/'live-ui-order.json').read_text(encoding='utf-8'))
 ui=json.loads((ORD/'ui-qa.json').read_text(encoding='utf-8'))
 assert qa['passed'] and ui['passed'] and all(live['checks'].values())
 assert live['result']['summaryVerified'] is False
 order=live['result']['orders']['orders'][0]
 assert [order[k]for k in ['requestedKg','shippedKg','heldKg','unallocatedKg']]==[13000,4000,8400,600]
 pdfmetrics.registerFont(TTFont('MG','C:/Windows/Fonts/malgun.ttf'))
 pdfmetrics.registerFont(TTFont('MGB','C:/Windows/Fonts/malgunbd.ttf'))
 c=canvas.Canvas(str(PDF),pagesize=(W,H),pageCompression=1)
 c.setTitle('사료 생산·품질 데이터 통합 AI 업무 지원 시스템 | 김현태')
 c.setAuthor('김현태');c.setSubject('우성 IT/AX · 개인 프로젝트 · 실제 로컬 LLM · 합성 주문')
 frames=[];callouts=[]
 def rect(x,t,w,h,fill,stroke=None):
  c.setFillColor(HexColor(fill));c.setStrokeColor(HexColor(stroke or fill));c.setLineWidth(1.25)
  c.rect(x,H-t-h,w,h,fill=1,stroke=bool(stroke))
 def txt(s,x,t,size=15,color=INK,bold=False):
  c.setFillColor(HexColor(color));c.setFont('MGB'if bold else'MG',size);c.drawString(x,H-t-size,s)
 def para(s,x,t,w,size=15,color=BODY,bold=False,limit=180):
  style=ParagraphStyle('p',fontName='MGB'if bold else'MG',fontSize=size,leading=size*1.48,textColor=HexColor(color),wordWrap='CJK')
  obj=Paragraph(escape(s).replace('\n','<br/>'),style);_,h=obj.wrap(w,H)
  assert h<=limit,(s,h,limit)
  obj.drawOn(c,x,H-t-h);return h
 def link(s,url,x,t,size=12):
  txt(s,x,t,size,BLUE);c.linkURL(url,(x,H-t-size-4,x+pdfmetrics.stringWidth(s,'MG',size),H-t+3),relative=0)
 def pic(file,x,t,w,h,page,crop=None):
  im=Image.open(file);left,top,right,bottom=crop or (0,0,im.width,im.height)
  cw,ch=right-left,bottom-top;scale=min((w-12)/cw,(h-12)/ch);iw,ih=cw*scale,ch*scale
  ix=x+(w-iw)/2;it=t+(h-ih)/2
  rect(x,t,w,h,'#e9f0f5');rect(ix-3,it-3,iw+6,ih+6,'#ffffff','#52728a')
  c.saveState();clip=c.beginPath();clip.rect(ix,H-it-ih,iw,ih);c.clipPath(clip,stroke=0,fill=0)
  c.drawImage(ImageReader(im),ix-left*scale,H-it+top*scale-im.height*scale,width=im.width*scale,height=im.height*scale,mask='auto')
  c.restoreState()
  frames.append({'page':page,'file':str(file.relative_to(ROOT)),'image':[ix,it,iw,ih],'sourceCrop':[left,top,right,bottom],'outerBorder':[ix-3,it-3,iw+6,ih+6],'stroke':'#52728a'})
  return ix,it,iw,ih
 def annotate(page,anchor,route,label,number,title,detail,color,heading_size=12,detail_size=11,detail_offset=21,badge=True,kind='screenshot',region=None):
  ax,at=anchor;lx,lt=label;path=[anchor]+route
  # Leader overlays are separate from the unchanged source screenshot.
  # The label end and image end share a number; white backing keeps the line visible.
  for width,stroke in [(3.6,'#ffffff'),(1.2,color)]:
   c.setLineWidth(width);c.setStrokeColor(HexColor(stroke))
   for (x,t),(x2,t2) in zip(path,path[1:]):c.line(x,H-t,x2,H-t2)
  if region:
   rx,rt,rw,rh=region
   # Transparent annotation: the box encloses the real target, with its number outside.
   for width,stroke in [(1.8,'#ffffff'),(0.9,color)]:
    c.setLineWidth(width);c.setStrokeColor(HexColor(stroke));c.rect(rx,H-rt-rh,rw,rh,fill=0,stroke=1)
   rect(rx+rw-15,rt-15,15,15,color)
   c.setFillColor(HexColor('#ffffff'));c.setFont('MGB',11)
   c.drawCentredString(rx+rw-7.5,H-rt+3,str(number))
  else:
   c.setFillColor(HexColor(color));c.setStrokeColor(HexColor('#ffffff'));c.setLineWidth(1.2)
   c.circle(ax,H-at,8.5 if badge else 3,fill=1,stroke=1)
  if badge and not region:
   c.setFillColor(HexColor('#ffffff'));c.setFont('MGB',11)
   c.drawCentredString(ax,H-at-3.8,str(number))
  txt(f'{number:02d}  {title}',lx,lt,heading_size,color,True)
  txt(detail,lx,lt+detail_offset,detail_size,MUTED)
  callouts.append({'page':page,'kind':kind,'number':number,'title':title,'anchor':[ax,at],'leader':path,'label':[lx,lt],'detail':detail,'region':list(region) if region else None})
 def arrow(x,t,x2,t2,color=BLUE):
  c.setStrokeColor(HexColor(color));c.setLineWidth(1.5);c.line(x,H-t,x2,H-t2)
  import math
  ang=math.atan2(t2-t,x2-x)
  for da in [-.55,.55]:c.line(x2,H-t2,x2-8*math.cos(ang+da),H-(t2-8*math.sin(ang+da)))
 def base(page,label):
  rect(0,0,W,H,PAPER);txt('GRAINWORKS / IT & AX',48,22,13,BLUE,True)
  txt('김현태 · 우성 IT/AX 지원 · 2026.10.10',810,22,13,MUTED)
  rect(48,57,1184,1,LINE);rect(48,682,1184,1,LINE)
  txt(label,48,691,11,MUTED);txt(f'{page} / 3',1190,691,11,MUTED)
 # 01: Enlarge the real factory/order/quality viewport, with short action guides.
 base(1,'01  실제 화면·조작·근거 확인')
 txt(b['cover.title'],48,78,30,INK,True)
 txt(b['cover.subtitle'],48,123,17,BLUE)
 txt('실제 화면 일부 확대 · '+b['rag.motivation'],48,150,11,MUTED)
 link('공개 MES 사례 ↗',MES,1100,150,11)
 crop=(0,144,1265,630)
 hero=pic(ORD/'order-hero.png',48,169,1184,444,1,crop=crop)
 hx,ht,hw,hh=hero
 for number,source_box,pixel,target,lane,label,title,detail,col in [
  (1,(122,235,552,552),(337,552),244,623,(76,632),'설비를 선택해 공정 확인','설비를 눌러 연결 Lot과 진행 단계를 확인합니다.',BLUE),
  (2,(571,194,876,604),(724,604),646,619,(463,632),'주문별 출하 영향을 구분','요청 13,000kg의 보류·기출하 영향·미배정을 구분합니다.','#287968'),
  (3,(895,277,1177,494),(1177,494),1050,615,(858,632),'검사 근거를 보고 조치','검사값과 기준값을 대조하고, 보류·해제는 사람이 결정합니다.',AMBER)]:
  ax,at=hx+hw*(pixel[0]-crop[0])/(crop[2]-crop[0]),ht+hh*(pixel[1]-crop[1])/(crop[3]-crop[1])
  sl,st,sr,sb=source_box
  region=(hx+hw*(sl-crop[0])/(crop[2]-crop[0]),ht+hh*(st-crop[1])/(crop[3]-crop[1]),hw*(sr-sl)/(crop[2]-crop[0]),hh*(sb-st)/(crop[3]-crop[1]))
  route=[(ax,lane),(target,lane),(target,627)] if number<3 else [(1216,at),(1216,lane),(target,lane),(target,627)]
  annotate(1,(ax,at),route,label,number,title,detail,col,heading_size=14,detail_size=12,detail_offset=22,region=region)
  callouts[-1].update({'sourcePixel':pixel,'sourceRegion':source_box})
 c.showPage()
 # 02: Inventory allocation is an inclusion relation; quality changes propagate separately.
 base(2,'02  주문 연결·상태 구분·입력 변경 검증')
 txt(b['order.title'],48,78,30,INK,True)
 txt('공고의 데이터 기반 업무를 위해, 주문과 품질 보류의 영향을 함께 조회하는 화면을 만들었습니다.',48,126,16,BLUE)
 rect(48,171,700,56,'#edf4f8',LINE)
 txt('SO-001 · 육계 성장 사료',64,181,17,INK,True)
 txt('요청 13,000 kg = 출하 4,000 + 미출하 보류 8,400 + 미배정 600',64,209,13,BLUE)
 for x,w,label,total,detail,col in [(48,217,'01  미출하 보류','8,400 kg','LOT-001 4,800 + LOT-003 3,600',AMBER),(282,233,'02  이미 출하한 영향','4,000 kg','LOT-009 · 출하 이력 안의 영향',BLUE),(532,216,'03  미배정 수요','600 kg','재고를 추가 생성하지 않음','#287968')]:
  rect(x,246,w,117,'#fff9ef'if col==AMBER else'#f0f5f8',LINE)
  txt(label,x+14,258,14,col,True);txt(total,x+14,283,23,col,True)
  para(detail,x+14,325,w-28,12,limit=35)
 txt('품질 상태 전파',48,385,16,INK,True)
 txt('RAW-2401 15.8% / 시연 기준 14%',48,414,15,BODY)
 arrow(349,426,385,426)
 txt('LOT-001·003 보류 → TRK-01 출하 계획',401,414,14,AMBER,True)
 para('기출하 LOT-009는 현재 트럭 계획의 보류 물량에 다시 더하지 않습니다.',48,450,700,14,limit=30)
 txt('입력을 바꾸면 결과도 바뀝니다.',48,490,18,INK,True)
 rect(48,526,700,74,'#edf4f8',LINE)
 txt('LOT-001 주문 배정',64,540,14,BODY);txt('4,800 → 3,000 kg',296,540,16,BLUE,True)
 txt('보류 배정',64,570,14,BODY);txt('8,400 → 6,600 kg',175,570,14,AMBER,True)
 txt('미배정',420,570,14,BODY);txt('600 → 2,400 kg',499,570,14,BLUE,True)
 txt('Lot 재고 4,800 kg은 그대로이며, 품질 보류를 해제한 결과가 아닙니다.',48,610,13.5,BODY)
 para(b['order.integrity'],48,637,700,13.5,limit=43)
 txt('실제 주문 상세 확대 · 합성 주문 SO-001',770,153,11,MUTED)
 detail_frame=pic(EVID/'order-detail.png',770,170,442,300,2)
 txt('관찰 안내 · 화면의 숫자를 읽는 법',780,478,14,INK,True)
 dx,dt,dw,dh=detail_frame
 for number,source_box,pixel,gutter,title,detail,col in [
  (1,(408,166,520,226),(520,226),1230,'미출하 보류 8,400 kg','출하 전 품질 문제로 막힌 물량입니다.',AMBER),
  (2,(10,298,605,336),(590,336),1222,'기출하 영향 4,000 kg','이미 출하한 기록의 영향을 따로 봅니다.',BLUE),
  (3,(208,229,313,284),(258,284),758,'미배정 600 kg','요청량 중 아직 Lot에 연결하지 않은 물량입니다.','#287968')]:
  ax,at=dx+dw*pixel[0]/611,dt+dh*pixel[1]/400
  sl,st,sr,sb=source_box;region=(dx+dw*sl/611,dt+dh*st/400,dw*(sr-sl)/611,dh*(sb-st)/400)
  lane=505+(number-1)*53;lt=514+(number-1)*53
  if number==3:
   blank_row=dt+dh*287/400
   route=[(ax,blank_row),(gutter,blank_row),(gutter,lane),(805,lane),(805,lt-5)]
  else:route=[(gutter,at),(gutter,lane),(805,lane),(805,lt-5)]
  annotate(2,(ax,at),route,(790,lt),number,title,detail,col,heading_size=14,detail_size=12,region=region)
  callouts[-1].update({'sourcePixel':pixel,'sourceRegion':source_box})
 c.showPage()
 # 03: Real evidence, a paired comparison, and explicit responsibility boundaries.
 base(3,'03  RAG 사용 전후·답변 검증·사람의 조치')
 txt(b['rag.title'],48,78,30,INK,True)
 txt(b['rag.link'],48,125,16,BLUE)
 txt(b['rag.caption'],48,154,11,MUTED)
 crop=(104,496,1145,949)
 evidence=pic(ROOT/'docs/rag/ui-normal.jpg',48,178,832,368,3,crop=crop)
 ex,et,ew,eh=evidence
 def source_region(box):
  sl,st,sr,sb=box
  return (ex+ew*(sl-crop[0])/(crop[2]-crop[0]),et+eh*(st-crop[1])/(crop[3]-crop[1]),ew*(sr-sl)/(crop[2]-crop[0]),eh*(sb-st)/(crop[3]-crop[1]))
 for number,box,label,title,detail,col in [
  (1,(109,566,805,751),(50,558),'SOP 기준·절차','기준 14%와 재검사·별도 해제를 대조합니다.',BLUE),
  (2,(109,754,805,946),(332,558),'현재 검사·주문 수치','15.8%·8,400kg·4,000kg은 현재 기록입니다.','#287968'),
  (3,(827,566,1142,946),(624,558),'값과 출처를 함께 확인','출처 ID·버전이 다르면 답변을 승인하지 않습니다.',AMBER)]:
  rx,rt,rw,rh=region=source_region(box)
  if number==1:
   anchor=(rx,rt+rh/2);route=[(38,anchor[1]),(38,550),(150,550),(150,554)]
  elif number==2:
   anchor=(rx+rw*.55,rt+rh);route=[(anchor[0],550),(440,550),(440,554)]
  else:
   anchor=(rx+rw,rt+rh*.58);route=[(890,anchor[1]),(890,550),(733,550),(733,554)]
  annotate(3,anchor,route,label,number,title,detail,col,heading_size=13,detail_size=11,detail_offset=23,region=region)
  callouts[-1].update({'sourceRegion':box,'sourceCrop':list(crop)})
 txt('같은 질문, RAG 사용 전후',913,174,17,INK,True)
 for x,title,big,detail,col in [
  (913,'사용 전 · RAG 끔','답변 보류','Qwen 호출 후\n문서 근거가 없어\n기준·절차 답변 보류',AMBER),
  (1076,'사용 후 · RAG 켬','6항목 통과','SOP + 현재 기록\n값·출처를 대조한\n6항목만 제시','#287968')]:
  rect(x,207,156,117,'#fff9ef'if col==AMBER else'#eef6f2',LINE)
  txt(title,x+10,216,12,col,True);txt(big,x+10,239,19,col,True)
  para(detail,x+10,270,136,11.5,limit=53)
 txt('같은 합성 질문·상태의 비교 · 일반 정확도 아님',913,331,11,MUTED)
 txt('누가 어떤 일을 맡는가',913,357,17,INK,True)
 for i,role,title,detail,col in [
  (1,'코드 · 조회','기록·문서 검색','ID 조회 · EmbeddingGemma',BLUE),
  (2,'AI · 생성','답변 항목 초안','Qwen3 4B · Ollama','#287968'),
  (3,'코드 · 검증','값·출처 대조','Python 검증 · LangGraph',BLUE),
  (4,'사람 · 조치','원문 확인·결정','재검사·해제는 수동',AMBER)]:
  t=391+(i-1)*48;region=(913,t,116,34)
  rect(*region,'#edf4f8')
  txt(role,921,t+8,13,col,True)
  annotate(3,(1029,t+17),[(1040,t+17)],(1044,t-1),i,title,detail,col,heading_size=12,detail_size=11,detail_offset=19,kind='responsibility',region=region)
  if i<4:arrow(965,t+35,965,t+45,col)
 para(b['rag.scope'],48,607,832,12,limit=40)
 para(b['rag.contribution'],48,643,832,11.5,limit=34)
 para(b['rag.validation'],913,579,203,11.5,limit=75)
 qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_Q,box_size=8,border=4);qr.add_data(REPO);qr.make(fit=True)
 matrix=qr.get_matrix();cell=104/len(matrix);rect(1128,568,104,104,'#ffffff')
 c.setFillColor(HexColor('#000000'))
 for row,bits in enumerate(matrix):
  for col,on in enumerate(bits):
   if on:c.rect(1128+col*cell,H-568-(row+1)*cell,cell,cell,fill=1,stroke=0)
 c.linkURL(REPO,(1128,H-672,1232,H-568),relative=0)
 link('실행·검증 원본 ↗',REPO+'/blob/main/docs/rag/verification.md',913,657,11)
 c.showPage();c.save()
 shutil.copy2(PDF,OUT/'portfolio.pdf')
 doc=fitz.open(PDF);assert len(doc)==3
 pages=[]
 for i,page in enumerate(doc,1):
  spans=[s for block in page.get_text('dict')['blocks']if'lines'in block for line in block['lines']for s in line['spans']]
  assert all(47<=s['bbox'][0] and s['bbox'][2]<=1233 for s in spans),('horizontal bounds',i)
  assert all(s['bbox'][3]<=681 for s in spans if s['bbox'][1]<682),('footer overlap',i)
  assert min(s['size']for s in spans)>=11
  for scale,name in [(1,'preview-100'),(1.5,'preview')]:page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).save(OUT/f'{name}-page-{i}.png')
  pages.append({'page':i,'characters':len(page.get_text()),'minFontPt':min(s['size']for s in spans),'links':[l.get('uri')for l in page.get_links()if l.get('uri')]})
 contact=Image.new('RGB',(1920,360),'white')
 for i in range(1,4):
  im=Image.open(OUT/f'preview-page-{i}.png');im.thumbnail((640,360));contact.paste(im,((i-1)*640,0))
 contact.save(OUT/'contact-sheet.png')
 report={'pageCount':3,'pages':pages,'screenshots':frames,'observationGuides':callouts,'allScreenshotBorders':'continuous four outer edges; final visual review pending','synthetic':True,'sha256':hashlib.sha256(PDF.read_bytes()).hexdigest(),'narrativeAccuracy':'Paired synthetic case; field/value/source checks, not general AI accuracy','actualModelElapsedMs':rag_live['elapsedMs'],'ragEvidence':{'run':'docs/rag/ui-normal-live.json','pairedEvaluation':'docs/evaluation/rag-live.json','acceptedFields':6,'modelCallsAcrossEvaluation':6,'preflightSkipsAcrossEvaluation':4,'summaryVerified':False,'originalUnchanged':True,'pairedQuestionAndStateEqual':True,'sourceHashesMatched':13},'visualReview':'pending'}
 (OUT/'pdf-qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'pdf':str(PDF),'pages':3,'sha256':report['sha256']},ensure_ascii=False))
if __name__=='__main__':build()
