"""Precompute page references on the existing Mac, without copying PDF/audio.

Output stays in the private data directory. Uncertain pages are listed for
source review; they are never converted into guessed scoring targets.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.reading_text import ReadingText, POLICY


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=Path('data'))
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--levels',nargs='+',default=['AA','A','B','C','D','E','F','G','H'])
    parser.add_argument('--workers',type=int,choices=range(1,5),default=2)
    parser.add_argument('--pages',type=int,nargs='+',help='Only these PDF page numbers; default is all pages')
    args=parser.parse_args()
    manifest=json.loads((args.manifest or args.data/'manifest.json').read_text())
    cases=[(book,page) for book in manifest['books'] if book['level'] in args.levels
           for page in (args.pages or range(1,book['page_count']+1)) if 1<=page<=book['page_count']]
    def prepare(case):
        book,page=case
        record={'book_id':book['book_id'],'title':book['title'],'page':page}
        try:
            reading=ReadingText(args.data).read(book,page)['reading']
            record.update(status=reading['status'],reference_id=reading['id'])
        except (ValueError,RuntimeError) as error:
            record.update(status='error',error=str(error))
        return record
    results=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for record in pool.map(prepare,cases):
            results.append(record)
            if len(results)%50==0:print(f'{len(results)}/{len(cases)} pages checked',flush=True)
    args.data.mkdir(parents=True,exist_ok=True)
    report=args.data/'reading-text-audit.json'
    report.write_text(json.dumps({'policy':POLICY,'pages':results},ensure_ascii=False,indent=2))
    print(json.dumps({'checked':len(results),'ready':sum(r['status']=='ready' for r in results),
                      'needs_review':sum(r['status']=='uncertain' for r in results),
                      'errors':sum(r['status']=='error' for r in results),'report':str(report)}))


if __name__=='__main__':main()
