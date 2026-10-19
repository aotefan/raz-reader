"""Index an explicitly supplied local PDF library; never uploads or deletes books."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from engine.pipeline import APP, LEVELS, build, discover


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True,help='Your local directory containing AA.PDF, A.PDF, etc.')
    parser.add_argument('--data',type=Path,default=APP/'data')
    args=parser.parse_args();root=args.root.resolve();data=args.data.resolve()
    if not root.is_dir(): parser.error('Library root does not exist')
    if data.is_relative_to(root): parser.error('Keep generated state outside the source library')
    entries=[];levels=[]
    for row in discover(root):
        if not row['exists']: continue
        folder=root/row['directory'];levels.append(row['level'])
        for file in sorted(folder.iterdir()):
            if file.is_file() and file.suffix.lower()=='.pdf' and file.resolve().is_relative_to(root):
                entries.append({'level':row['level'],'path':file.relative_to(root).as_posix()})
    if not entries: parser.error('No PDFs found. Expected folders such as AA.PDF and A.PDF.')
    data.mkdir(parents=True,exist_ok=True)
    (data/'library-selection.json').write_text(json.dumps({'label':'My library','levels':levels,
        'include_demo':False,'books':entries},ensure_ascii=False,indent=2)+'\n')
    manifest,_=build(root,data)
    print(f'Indexed {len(manifest["books"])} PDFs; {len(manifest["errors"])} errors. Source files unchanged.')


if __name__=='__main__': main()
