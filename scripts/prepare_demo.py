"""Generate original example books locally. Never reads an external book library."""
import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reportlab.pdfgen import canvas
from engine.pipeline import APP, LEVELS, SCHEMA_VERSION, connect, index_pdf
from scripts.make_fixture import make, TEXT

EXAMPLES = [
    ('A Sunny Day', 'sun', '#f8e8bb', ['The sun is warm.', 'I play outside.']),
    ('The Little Boat', 'boat', '#dcebed', ['I see a little boat.', 'The boat is on the water.']),
    ('Under the Moon', 'moon', '#e5dff1', ['The moon is bright.', 'I see stars in the sky.']),
    ('Our Cozy Home', 'home', '#f0ddd4', ['This is our home.', 'We read a book together.']),
    ('Falling Leaves', 'leaves', '#e4ead7', ['The leaves are falling.', 'I find a yellow leaf.']),
]


def color(c, value):
    c.setFillColor(value)


def artwork(c, kind):
    c.saveState()
    color(c, '#365c50')
    if kind == 'sun':
        color(c, '#e8b44d'); c.circle(300, 360, 82, fill=1, stroke=0)
        c.setStrokeColor('#e8b44d'); c.setLineWidth(10)
        for i in range(12):
            a = i * math.pi / 6
            c.line(300+105*math.cos(a),360+105*math.sin(a),300+135*math.cos(a),360+135*math.sin(a))
    elif kind == 'boat':
        c.setStrokeColor('#365c50'); c.setLineWidth(8); c.line(300, 270, 300, 480)
        color(c, '#fffdf6'); p=c.beginPath();p.moveTo(310,475);p.lineTo(440,315);p.lineTo(310,315);p.close();c.drawPath(p,fill=1,stroke=0)
        color(c, '#ba785a'); p=c.beginPath();p.moveTo(155,290);p.lineTo(445,290);p.lineTo(390,220);p.lineTo(210,220);p.close();c.drawPath(p,fill=1,stroke=0)
        c.setStrokeColor('#83aeb4');c.setLineWidth(6)
        for y in (190,170): c.bezier(100,y,220,y+18,380,y-18,500,y)
    elif kind == 'moon':
        color(c, '#f9e7a4');c.circle(300,360,100,fill=1,stroke=0)
        color(c, '#e5dff1');c.circle(345,395,95,fill=1,stroke=0)
        color(c, '#b6a1ce')
        for x,y,r in [(140,470,9),(440,260,8),(460,480,10),(140,260,7)]: c.circle(x,y,r,fill=1,stroke=0)
    elif kind == 'home':
        color(c, '#fbfaf3');c.roundRect(185,220,230,190,8,fill=1,stroke=0)
        color(c, '#b97761');p=c.beginPath();p.moveTo(155,400);p.lineTo(300,515);p.lineTo(445,400);p.close();c.drawPath(p,fill=1,stroke=0)
        color(c, '#365c50');c.roundRect(270,220,60,100,5,fill=1,stroke=0)
        color(c, '#e7c878');c.roundRect(215,335,45,45,5,fill=1,stroke=0);c.roundRect(340,335,45,45,5,fill=1,stroke=0)
    else:
        c.setStrokeColor('#78916a');c.setLineWidth(7);c.line(300,200,300,485)
        for y, side, tint in [(260,-1,'#a4b887'),(330,1,'#d7ad57'),(400,-1,'#bd805c')]:
            color(c,tint);c.ellipse(300 if side>0 else 185,y,415 if side>0 else 300,y+70,fill=1,stroke=0)
    c.restoreState()


def example_pdf(path, title, kind, background, sentences):
    c=canvas.Canvas(str(path),pagesize=(600,760),invariant=1)
    c.setTitle(title);c.setAuthor('RAZ Reader contributors — original example')
    for number, sentence in enumerate([title, *sentences],1):
        color(c,background);c.rect(0,0,600,760,fill=1,stroke=0)
        color(c,'#365c50');c.setFont('Helvetica',11);c.drawCentredString(300,710,'RAZ READER / ORIGINAL EXAMPLE')
        c.setFont('Helvetica-Bold',32 if number==1 else 23);c.drawCentredString(300,605,sentence)
        artwork(c,kind)
        color(c,'#6f7d70');c.setFont('Helvetica',11);c.drawCentredString(300,76,'A small story. A little discovery.')
        c.drawCentredString(300,50,str(number));c.showPage()
    c.save()


def prepare(data=APP/'data'):
    data=Path(data).resolve()
    if (data/'manifest.json').exists():
        old=json.loads((data/'manifest.json').read_text())
        if not old.get('example_library'):
            raise ValueError('Existing non-demo library found. Use --data with a separate empty directory.')
    make()
    root=APP/'fixtures/generated'
    for title,kind,background,sentences in EXAMPLES:
        path=root/(title+'.pdf')
        if not path.exists(): example_pdf(path,title,kind,background,sentences)
    db=connect(data)
    books=[]
    from engine.pipeline import validate_audio_map
    for title in ['A Little Garden', *[entry[0] for entry in EXAMPLES]]:
        book,_=index_pdf(db,root/(title+'.pdf'),root,'AA',True)
        if title=='A Little Garden' and (root/'page_audio_map.json').exists():
            book['audio']=validate_audio_map(book,json.loads((root/'page_audio_map.json').read_text()),root)
            for page in book['pages']: page['audio']=book['audio']['page_map'].get(str(page['number']))
        books.append(book)
    manifest={'schema_version':SCHEMA_VERSION,'example_library':True,
        'resource_root':str(root),'fixture_root':str(root),'scope':'Original examples',
        'levels':[{'level':level,'exists':level=='AA','directory':None,
                   'discovered_count':len(books) if level=='AA' else 0,'prepared':level=='AA'} for level in LEVELS],
        'books':books,'errors':[],'exact_duplicate_groups':[]}
    (data/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    db.close()
    print(f'Prepared {len(books)} original example books. Generated files remain private/ignored.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,default=APP/'data')
    prepare(parser.parse_args().data)
