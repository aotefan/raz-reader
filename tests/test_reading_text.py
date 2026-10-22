import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.reading_text import (ReadingText, POLICY, UNCERTAIN, consensus, crop_pgm,
                                 line_item, ocr_items, read_pgm, reading_block)


def line(text, x=.14, y=.85, height=.032, confidence=96):
    words=[]
    for n,word in enumerate(text.split()):
        words.append({'text':word,'x':x+n*.1,'y':y,'width':.09,'height':height,'confidence':confidence})
    return line_item(words)


class ReadingReferenceTests(unittest.TestCase):
    def test_bordered_illustration_cannot_hide_a_clear_caption_from_discovery(self):
        # Generate an original image-only page; no commercial library required.
        python = Path(__file__).resolve().parents[1]/'.venv/bin/python'
        if not python.exists():
            self.skipTest('Run bootstrap.sh to install the original-demo generator')
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            subprocess.run([str(python), '-c', '''
from reportlab.pdfgen import canvas
from pathlib import Path
import subprocess,sys
root=Path(sys.argv[1]);c=canvas.Canvas(str(root/'art.pdf'),pagesize=(600,800))
c.setLineWidth(7);c.rect(70,170,460,560);c.setFont('Helvetica',32)
c.drawString(160,95,'A blue boat.');c.showPage();c.save()
subprocess.run(['pdftoppm','-singlefile','-scale-to','1800','-png',str(root/'art.pdf'),str(root/'art')],check=True,capture_output=True)
c=canvas.Canvas(str(root/'source.pdf'),pagesize=(600,800))
c.drawImage(str(root/'art.png'),0,0,600,800);c.showPage();c.save()
''', d], check=True, capture_output=True)
            reader=ReadingText(root/'cache');recognize=reader.recognize
            def no_full_page_layout(path, size, mode, offset=(0,0)):
                return [] if path.name=='page.pgm' else recognize(path,size,mode,offset)
            book={'source_path':str(root/'source.pdf'),'source_root':d,'page_count':1}
            with patch.object(reader,'recognize',side_effect=no_full_page_layout):
                result=reader.reference(book,1)
        self.assertEqual(result['text'], 'A blue boat.')

    def test_picture_fragments_of_any_spelling_are_not_reading_prose(self):
        body=line('Four carts.')
        self.assertEqual(reading_block([line('whatever',.7,.3,.03),line('xyz',.7,.2,.009),body,
                                       line('6',.14,.94,.017)]),[body])
        body=line('Two letters.')
        self.assertEqual(reading_block([line('mM',.14,.69,.1),body]),[body])

    def test_paragraph_is_kept_and_separate_unexplained_prose_blocks_processing(self):
        first=line('We like to read.',y=.71)
        last=line('I read a book.',y=.78)
        self.assertEqual(reading_block([first,last]),[first,last])
        self.assertEqual(reading_block([line('Another paragraph is up here.',y=.2),first,last]),[])

    def test_consensus_never_silently_drops_an_unrecognized_word(self):
        self.assertIsNone(consensus([[line('Four carts.')],[line('Four trucks.')]]))
        self.assertIsNone(consensus([[line('I go.')],[line('| go.')],[line('| go.')]]))
        self.assertIsNone(consensus([[line('Can 1?',confidence=90)],[line('Can 1?',confidence=90)]]))
        self.assertIsNone(consensus([[line('We have hair.',confidence=40)],[line('We have hair.',confidence=40)]]))
        result=consensus([[line('I go.')],[line('I go!')]])
        self.assertEqual(result[0]['str'],'I go.')
        self.assertIsNone(consensus([[line('Three letters.',confidence=76)]]*2))
        self.assertIsNotNone(consensus([[line('Three letters.',confidence=76)]]*3))

    def test_weak_word_cannot_remove_a_whole_neighbouring_sentence(self):
        first=line('Oh no cried Mrs Beaver.',y=.808,height=.029)
        second=line('The bread is as flat as my tail!',y=.857,height=.035)
        second['words'][-1]['confidence']=13
        self.assertEqual(reading_block([first,second]),[first,second])
        # A separate paragraph with a weak word cannot silently disappear either.
        above=line('Another paragraph is up here.',y=.2)
        above['words'][-1]['confidence']=13
        self.assertEqual(reading_block([above,first,second]),[])

    def test_pgm_crop_preserves_whitespace_valued_first_pixels(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'page.pgm';pixels=bytes([10,32,255,0,0,255])
            path.write_bytes(b'P5\n# comment\n3 2\n255\n'+pixels)
            size,data=read_pgm(path)
            self.assertEqual(data,pixels)
            crop=Path(d)/'crop.pgm';self.assertEqual(crop_pgm(crop,size,data,(0,0,2/3,1)),(0,0))
            self.assertEqual(read_pgm(crop),((2,2),bytes([10,32,0,0])))

    def test_ocr_does_not_erase_low_confidence_evidence(self):
        tsv='level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'
        tsv+='5\t1\t1\t1\t1\t1\t10\t20\t20\t30\t10\tweak\n'
        result=ocr_items(tsv,(100,200),(10,100))
        self.assertEqual(result[0]['str'],'weak')
        self.assertEqual(result[0]['words'][0]['confidence'],10)
        self.assertEqual(result[0]['words'][0]['y'],.6)

    def test_reference_is_bound_to_pdf_bytes_page_and_policy(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);pdf=root/'book.pdf';pdf.write_bytes(b'original')
            book={'source_path':str(pdf),'source_root':str(root),'page_count':2}
            reader=ReadingText(root/'data')
            with patch.object(reader,'extract',return_value=([line('We read.')],'ocr-block-consensus',True)) as extract:
                first=reader.read(book,1)
                self.assertEqual(reader.read(book,1),first);self.assertEqual(extract.call_count,1)
                self.assertEqual(first['reading']['policy'],POLICY)
                self.assertNotEqual(reader.read(book,2)['reading']['id'],first['reading']['id'])
                self.assertEqual(pdf.read_bytes(),b'original')
                pdf.write_bytes(b'different original')
                self.assertNotEqual(reader.read(book,1)['reading']['id'],first['reading']['id'])
                self.assertEqual(extract.call_count,3)

    def test_uncertain_reference_never_returns_gibberish_as_text_or_words(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);pdf=root/'book.pdf';pdf.write_bytes(b'original')
            reader=ReadingText(root/'data');book={'source_path':str(pdf),'source_root':str(root),'page_count':1}
            with patch.object(reader,'extract',return_value=([line('n uo o ll')],'ocr-block-consensus',False)):
                result=reader.read(book,1)
                self.assertEqual(result['items'],[]);self.assertEqual(result['reading']['text'],'')
                with self.assertRaisesRegex(ValueError,UNCERTAIN):reader.reference(book,1)
                self.assertEqual(list(reader.directory.glob('*.json')),[])

    def test_old_reference_policy_cannot_reuse_previous_answer_key(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);pdf=root/'book.pdf';pdf.write_bytes(b'original')
            reader=ReadingText(root/'data');book={'source_path':str(pdf),'source_root':str(root),'page_count':1}
            with patch.object(reader,'extract',return_value=([line('Four trucks.')],'ocr-block-consensus',True)) as extract:
                reader.read(book,1)
                path=next(reader.directory.glob('*.json'));old=json.loads(path.read_text())
                old['reading'].update(policy='old',text='n uo o ll');path.write_text(json.dumps(old))
                self.assertEqual(reader.reference(book,1)['text'],'Four trucks.')
                self.assertEqual(extract.call_count,2)


if __name__=='__main__':unittest.main()
