import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from engine.pipeline import inspect_pdf, digest
from engine.embedded_audio import load_embedded_map


class EmbeddedTests(unittest.TestCase):
    def test_rich_media_follows_page_assets_name_tree_and_never_uses_flash(self):
        objects = {
            'obj:1 0 R': {'value': {'/Annots': '2 0 R'}},
            'obj:2 0 R': {'value': ['3 0 R']},
            'obj:3 0 R': {'value': {'/Subtype': '/RichMedia', '/RichMediaContent': '4 0 R'}},
            'obj:4 0 R': {'value': {'/Assets': '5 0 R'}},
            'obj:5 0 R': {'value': {'/Kids': ['6 0 R']}},
            'obj:6 0 R': {'value': {'/Names': ['u:player.swf', '7 0 R', 'u:original.mp3', '8 0 R'], '/Kids':['5 0 R']}},
            'obj:7 0 R': {'value': {'/EF': {'/F':'9 0 R'}}},
            'obj:8 0 R': {'value': {'/EF': {'/F':'10 0 R', '/UF':'10 0 R'}, '/F':'u:original.mp3'}},
            'obj:9 0 R': {'stream': {'dict': {'/Subtype':'/application/x-shockwave-flash'}}},
            'obj:10 0 R': {'stream': {'dict': {'/Subtype':'/audio/mpeg'}}},
        }
        structure = {'pages':[{'object':'1 0 R','pageposfrom1':1}], 'qpdf':[{}, objects]}
        with patch('engine.pipeline.run', side_effect=['Pages: 1\n', json.dumps(structure)]):
            result = inspect_pdf('unused.pdf')['audio_detection']
        self.assertEqual([(r['page'],r['stream_ref']) for r in result['rich_media_audio']], [(1,'10 0 R')])
        self.assertFalse(result['payload_extracted'])

    def test_asset_must_match_pdf_page_stream_hash_and_private_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            audio = root/'embedded-audio/pdf_test/1.mp3'
            audio.parent.mkdir(parents=True); audio.write_bytes(b'validated fixture bytes')
            book = {'edition_id':'pdf_test','audio_detection':{'rich_media_audio':[{'page':3,'stream_ref':'10 0 R'}]}}
            asset = {'page':3, 'stream_ref':'10 0 R', 'file':str(audio.relative_to(root)), 'sha256':digest(audio), 'duration':3}
            mapping = {'edition_id':'pdf_test','assets':[asset]}
            path = root/'map.json'; path.write_text(json.dumps(mapping))
            self.assertEqual(load_embedded_map(book,path,root)['3']['storage'], 'app_embedded')
            changes = [{'page':4}, {'stream_ref':'9 0 R'}, {'sha256':'incorrect'}, {'duration':float('nan')},
                       {'duration':True}, {'duration':0}, {'file':'map.json'}]
            for change in changes:
                bad = copy.deepcopy(mapping); bad['assets'][0].update(change); path.write_text(json.dumps(bad))
                with self.subTest(change=change), self.assertRaises(ValueError): load_embedded_map(book,path,root)
            bad = copy.deepcopy(mapping); bad['edition_id']='different'; path.write_text(json.dumps(bad))
            with self.assertRaises(ValueError): load_embedded_map(book,path,root)

    def test_ambiguous_multiple_assets_are_not_arbitrarily_chosen(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); assets=[]
            for n in (1,2):
                p=root/f'embedded-audio/pdf_test/{n}.mp3';p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(bytes([n]))
                assets.append({'page':3,'stream_ref':f'{n} 0 R','file':str(p.relative_to(root)),'sha256':digest(p),'duration':1})
            book={'edition_id':'pdf_test','audio_detection':{'rich_media_audio':assets}}
            path=root/'map.json';path.write_text(json.dumps({'edition_id':'pdf_test','assets':assets}))
            self.assertEqual(load_embedded_map(book,path,root),{})
