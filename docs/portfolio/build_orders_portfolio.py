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
PDF=OUT/'김현태_우성_IT_AX_생산품질_주문_AI_포트폴리오_v10.pdf'
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
 def pic(file,x,t,w,h,page):
  im=Image.open(file);scale=min((w-12)/im.width,(h-12)/im.height);iw,ih=im.width*scale,im.height*scale
  ix=x+(w-iw)/2;it=t+(h-ih)/2
  rect(x,t,w,h,'#e9f0f5');rect(ix-3,it-3,iw+6,ih+6,'#ffffff','#52728a')
  c.drawImage(ImageReader(im),ix,H-it-ih,width=iw,height=ih,mask='auto')
  frames.append({'page':page,'file':str(file.relative_to(ROOT)),'image':[ix,it,iw,ih],'outerBorder':[ix-3,it-3,iw+6,ih+6],'stroke':'#52728a'})
  return ix,it,iw,ih
 def annotate(page,anchor,route,label,number,title,detail,color,heading_size=12,detail_size=11,detail_offset=21,badge=True,kind='screenshot'):
  ax,at=anchor;lx,lt=label;path=[anchor]+route
  # Leader overlays are separate from the unchanged source screenshot.
  # The label end and image end share a number; white backing keeps the line visible.
  for width,stroke in [(3.6,'#ffffff'),(1.2,color)]:
   c.setLineWidth(width);c.setStrokeColor(HexColor(stroke))
   for (x,t),(x2,t2) in zip(path,path[1:]):c.line(x,H-t,x2,H-t2)
  c.setFillColor(HexColor(color));c.setStrokeColor(HexColor('#ffffff'));c.setLineWidth(1.2)
  c.circle(ax,H-at,8.5 if badge else 3,fill=1,stroke=1)
  if badge:
   c.setFillColor(HexColor('#ffffff'));c.setFont('MGB',11)
   c.drawCentredString(ax,H-at-3.8,str(number))
  txt(f'{number:02d}  {title}',lx,lt,heading_size,color,True)
  txt(detail,lx,lt+detail_offset,detail_size,MUTED)
  callouts.append({'page':page,'kind':kind,'number':number,'title':title,'anchor':[ax,at],'leader':path,'label':[lx,lt],'detail':detail})
 def guide(frame,pixel,target,lane,number,title,detail,color):
  ix,it,iw,ih=frame;ax,at=ix+iw*pixel[0]/1265,it+ih*pixel[1]/712
  annotate(1,(ax,at),[(ax,lane),(target,lane),(target,625)],(target-75,630),number,title,detail,color,detail_offset=19)
  callouts[-1]['sourcePixel']=pixel
 def arrow(x,t,x2,t2,color=BLUE):
  c.setStrokeColor(HexColor(color));c.setLineWidth(1.5);c.line(x,H-t,x2,H-t2)
  import math
  ang=math.atan2(t2-t,x2-x)
  for da in [-.55,.55]:c.line(x2,H-t2,x2-8*math.cos(ang+da),H-(t2-8*math.sin(ang+da)))
 def base(page,label):
  rect(0,0,W,H,PAPER);txt('GRAINWORKS / IT & AX',48,22,13,BLUE,True)
  txt('김현태 · 우성 IT/AX 지원 · 2026.10.08',810,22,13,MUTED)
  rect(48,57,1184,1,LINE);rect(48,682,1184,1,LINE)
  txt(label,48,691,11,MUTED);txt(f'{page} / 3',1190,691,11,MUTED)
 # 01: One large integrated screen, the actual problem and personal responsibility.
 base(1,'01  문제·제품·직무 연결')
 txt(b['cover.title'],48,78,30,INK,True)
 txt(b['cover.subtitle'],48,123,17,BLUE)
 txt('공장·주문·품질을 한 창에서 확인',48,156,15,BLUE,True)
 txt('가정한 사용자의 질문',48,205,18,INK,True)
 para(b['cover.problem'],48,240,294,15,limit=117)
 txt('우성 업무와 연결한 이유',48,340,18,INK,True)
 para(b['cover.company'],48,375,294,14,limit=136)
 link('공개 MES 사례 ↗',MES,48,509)
 hero=pic(ORD/'order-hero.png',366,160,866,450,1)
 txt('관찰 안내',376,615,11,MUTED,True)
 guide(hero,(530,503),510,621,1,'공장 흐름','설비를 선택해 공정·상태 확인',BLUE)
 guide(hero,(586,593),817,617,2,'주문 관리','요청·보류·미배정 물량 비교','#287968')
 guide(hero,(1179,310),1103,613,3,'품질 근거','검사값·기준값·연결 Lot 대조',AMBER)
 txt('실제 실행 화면 · 주문·납기·검사 기준은 합성 가정',376,665,11,MUTED)
 txt('본인 / Codex 역할',48,542,16,INK,True)
 para(b['cover.role'],48,568,294,13,limit=100)
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
 for number,pixel,gutter,title,detail,col in [
  (1,(485,199),1230,'미출하 보류 8,400 kg','출하 전 품질 문제로 막힌 물량입니다.',AMBER),
  (2,(594,317),1222,'기출하 영향 4,000 kg','이미 출하한 기록의 영향을 따로 봅니다.',BLUE),
  (3,(265,259),758,'미배정 600 kg','요청량 중 아직 Lot에 연결하지 않은 물량입니다.','#287968')]:
  ax,at=dx+dw*pixel[0]/611,dt+dh*pixel[1]/400
  lane=505+(number-1)*53;lt=514+(number-1)*53
  if number==3:
   blank_row=dt+dh*287/400
   route=[(ax,blank_row),(gutter,blank_row),(gutter,lane),(805,lane),(805,lt-5)]
  else:route=[(gutter,at),(gutter,lane),(805,lane),(805,lt-5)]
  annotate(2,(ax,at),route,(790,lt),number,title,detail,col,heading_size=14,detail_size=12)
  callouts[-1]['sourcePixel']=pixel
 c.showPage()
 # 03: Logo flow is a verified runtime, not a vendor inventory or company deployment.
 base(3,'03  실제 오픈소스 LLM·검증·운영 경계')
 txt(b['agent.title'],48,78,30,INK,True)
 txt('공고의 AI 기반 업무를 위해, Qwen3 4B·Ollama·LangGraph에 근거 조회 도구를 연결했습니다.',48,125,16,BLUE)
 flow=OUT/'flow/architecture-flow.png'
 c.drawImage(ImageReader(Image.open(flow)),48,H-168-222,width=1184,height=222,mask='auto')
 for number,anchor_x,card_x,title,detail,col in [
  (1,211,48,'Python API · 근거 조회','주문·Lot 기록과 계산 결과를 조회합니다.',BLUE),
  (2,494,351,'LangGraph · 도구 연결','모델이 고른 허용 도구를 호출합니다.','#287968'),
  (3,776,654,'Ollama · 로컬 실행','Qwen 모델을 이 PC에서 실행합니다.',BLUE),
  (4,1057,957,'Qwen · 설명 생성','조회한 근거로 설명을 씁니다. 사람 검토 필요.',AMBER)]:
  rect(card_x,414,275,48,'#edf4f8',LINE)
  center=card_x+137.5
  annotate(3,(anchor_x,386),[(anchor_x,403),(center,403),(center,414)],(card_x+12,419),number,title,detail,col,badge=False,kind='architecture')
 txt('AI를 붙인 뒤, 실제 오류를 확인했습니다.',48,474,18,INK,True)
 para(b['agent.failure'],48,509,558,15,limit=95)
 para(b['agent.validation'],48,613,558,13.5,limit=54)
 txt('업무 지원 기능과 현재 경계',654,474,18,INK,True)
 txt('주문 질의 · CSV 단위 대조 · 문서 근거 · 인수인계 · 설비 비교',654,510,13,BLUE,True)
 para(b['agent.boundary'],654,539,578,14,limit=63)
 para(b['agent.next'],654,612,440,13.5,limit=47)
 qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_Q,box_size=8,border=4);qr.add_data(REPO);qr.make(fit=True)
 matrix=qr.get_matrix();cell=104/len(matrix);rect(1128,568,104,104,'#ffffff')
 c.setFillColor(HexColor('#000000'))
 for row,bits in enumerate(matrix):
  for col,on in enumerate(bits):
   if on:c.rect(1128+col*cell,H-568-(row+1)*cell,cell,cell,fill=1,stroke=0)
 c.linkURL(REPO,(1128,H-672,1232,H-568),relative=0)
 link('소스·실행·검수 기록 ↗',REPO,654,657,12)
 link('공식 IT/AX 공고 ↗',JD,862,657,12)
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
 report={'pageCount':3,'pages':pages,'screenshots':frames,'observationGuides':callouts,'allScreenshotBorders':'continuous four outer edges; final visual review pending','synthetic':True,'sha256':hashlib.sha256(PDF.read_bytes()).hexdigest(),'narrativeAccuracy':'Single recorded case; not a general AI accuracy claim','actualModelElapsedMs':live['elapsedMs'],'visualReview':'pending'}
 (OUT/'pdf-qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'pdf':str(PDF),'pages':3,'sha256':report['sha256']},ensure_ascii=False))
if __name__=='__main__':build()
