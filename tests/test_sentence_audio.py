import copy
import hashlib
import json
import unittest
from engine.sentence_audio import validate_page_timeline, validate_sentences
from scripts.align_original_sentences import align


class SentenceAudioTests(unittest.TestCase):
    def setUp(self):
        self.book = {'edition_id': 'pdf_test', 'sha256': 'c'*64, 'page_count': 3,
                     'audio': {'whole_book': {'sha256': 'a'*64, 'duration': 10}}}
        self.mapping = {'edition_id': 'pdf_test', 'audio_sha256': 'a'*64,
                        'verification': 'local_text_asr_silence_clip_checked',
                        'evidence': {k: 'b'*64 for k in ('text_sha256','words_sha256','silence_sha256','clip_review_sha256')},
                        'sentences': [{'page': 2, 'text': 'A seed grows.', 'start': 1, 'end': 3, 'clip_text_verified': True}]}

    def test_valid_exact_asset_mapping(self):
        self.assertEqual(validate_sentences(self.book, self.mapping)[0]['id'], '1')

    def test_rejects_wrong_asset_unreviewed_and_ambiguous_entries(self):
        changes = [{'audio_sha256': 'c'*64}, {'edition_id': 'other'}, {'evidence': {}}, {'sentences': []}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_sentences(self.book, {**self.mapping, **change})
        for change in [{'page': 0}, {'page': 4}, {'start': -1}, {'end': 11}, {'end': float('nan')}, {'clip_text_verified': False}]:
            m = copy.deepcopy(self.mapping); m['sentences'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError): validate_sentences(self.book, m)
        m = copy.deepcopy(self.mapping); m['sentences'].append({**m['sentences'][0], 'start': 4, 'end': 5})
        with self.assertRaises(ValueError): validate_sentences(self.book, m)

    def test_rejects_non_objects_and_overlap(self):
        for m in [[], {**self.mapping, 'sentences': [None]}, {**self.mapping, 'sentences': self.mapping['sentences']*2}]:
            with self.assertRaises(ValueError): validate_sentences(self.book, m)

    def test_whisperx_word_alignment_is_hash_bound_and_makes_sparse_page_timeline(self):
        words = [[{'text': 'A', 'start': .2, 'end': .3, 'score': .9},
                  {'text': 'seed', 'start': .31, 'end': .7, 'score': .8},
                  {'text': 'grows.', 'start': .72, 'end': 1.1, 'score': .85}],
                 [{'text': 'The', 'start': 3., 'end': 3.2, 'score': .9},
                  {'text': 'flower', 'start': 3.21, 'end': 3.7, 'score': .8},
                  {'text': 'grows.', 'start': 3.72, 'end': 4.1, 'score': .85}]]
        items = [{'page': 1, 'text': 'A seed grows.', 'start': .2, 'end': 1.1, 'word_alignment': words[0]},
                 {'page': 3, 'text': 'The flower grows.', 'start': 3., 'end': 4.1, 'word_alignment': words[1]}]
        def sha(value):
            return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                               separators=(',', ':')).encode()).hexdigest()
        spans = [{k: item[k] for k in ('page', 'start', 'end', 'text')} for item in items]
        evidence = {'method': 'WhisperX wav2vec2 forced alignment', 'model': 'WhisperX test model',
                    'pdf_sha256': self.book['sha256'], 'audio_sha256': self.book['audio']['whole_book']['sha256'],
                    'text_sha256': hashlib.sha256('\n'.join(i['text'] for i in items).encode()).hexdigest(),
                    'words_sha256': sha(words), 'alignment_sha256': sha(spans)}
        mapping = {'edition_id': 'pdf_test', 'source_pdf_sha256': self.book['sha256'], 'audio_sha256': 'a'*64,
                   'verification': 'whisperx_forced_alignment_pdf_match', 'evidence': evidence,
                   'sentences': items}
        validated = validate_sentences(self.book, mapping)
        self.assertEqual([s['page'] for s in validated], [1, 3])
        self.assertEqual(validate_page_timeline(self.book, mapping, validated),
                         [{'page': 1, 'start': .2, 'end': 1.1}, {'page': 3, 'start': 3., 'end': 4.1}])
        invalid = copy.deepcopy(mapping); invalid['sentences'][0]['word_alignment'][1]['start'] = .4
        with self.assertRaises(ValueError): validate_sentences(self.book, invalid)

    def test_alignment_rejects_missing_words_and_missing_silence(self):
        words = [{'text':'A seed grows.', 'offsets':{'from':1000,'to':3000}}]
        for texts, silence in [([{'page':1,'text':'A flower grows.'}],''),([{'page':1,'text':'A seed grows.'}],'')]:
            with self.assertRaises(ValueError): align(texts, words, silence, 5)

if __name__ == '__main__': unittest.main()
