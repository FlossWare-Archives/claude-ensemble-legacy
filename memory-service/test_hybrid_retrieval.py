import tempfile
import unittest
from pathlib import Path

from hybrid_retrieval import HybridRetrievalIndex, chunk_markdown


class HybridRetrievalTests(unittest.TestCase):
    def test_heading_hierarchy_is_preserved(self):
        chunks = chunk_markdown('# Architecture\n\nIntro.\n\n## Install root\n\nThe default deployment root is ~/.FlossWare/claude-ensemble.\n')
        target = next(item for item in chunks if 'default deployment root' in item['content'])
        self.assertEqual(target['heading_path'], 'Architecture > Install root')

    def test_natural_language_query_finds_install_root_and_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'index.sqlite3'
            index = HybridRetrievalIndex(path)
            index.replace_document('installation', 'rev1', [{'header': 'Install root', 'heading_path': 'Architecture > Install root', 'content': 'The default deployment root is ~/.FlossWare/claude-ensemble. Release code is immutable under releases/MAJOR.MINOR.'}])
            index.replace_document('unrelated', 'rev1', [{'header': 'Quick reference', 'heading_path': 'Quick reference', 'content': 'Claude Ensemble supports memory retrieval, model selection, and hooks.'}])
            results = index.search('What install root did we establish as the default for Claude Ensemble?', 5, {'installation', 'unrelated'})
            self.assertEqual(results[0]['file'], 'installation')
            reopened = HybridRetrievalIndex(path)
            self.assertEqual(reopened.search('default deployment root', 3, {'installation'})[0]['file'], 'installation')

    def test_exact_phrase_scores_above_related_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = HybridRetrievalIndex(Path(tmp) / 'index.sqlite3')
            index.replace_document('exact', '1', [{'header': 'Path', 'heading_path': 'Path', 'content': 'The default path is /opt/claude-ensemble.'}])
            index.replace_document('related', '1', [{'header': 'Notes', 'heading_path': 'Notes', 'content': 'Claude Ensemble has a configurable installation location.'}])
            results = index.search('The default path is /opt/claude-ensemble.', 5, {'exact', 'related'})
            self.assertEqual(results[0]['file'], 'exact')


if __name__ == '__main__':
    unittest.main()
