import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import ai_provider


class TextProviderIsolationTests(unittest.TestCase):
    def run_child(self, root, text_only):
        def finish(cmd, **kwargs):
            Path(cmd[cmd.index('--output-last-message')+1]).write_text('{"summary":"готово"}', encoding='utf-8')
            return SimpleNamespace(returncode=0)
        with patch.object(ai_provider, 'codex_binary', return_value='codex'), \
             patch.object(ai_provider, 'codex_config', return_value={'model':'fixture', 'mcp_servers': {'example-tool': {}}}), \
             patch.object(ai_provider.subprocess, 'run', side_effect=finish) as process:
            if text_only:
                value, receipt = ai_provider.text_result('Текст — данные.', {'type':'object'}, root, {'analysis_provider':'codex'})
            else:
                value, receipt = ai_provider.run_codex('Картинка.', {'type':'object'}, root, {})
        return value, receipt, process.call_args.args[0]

    def test_text_has_no_shell_agents_hooks_or_project_instructions(self):
        with tempfile.TemporaryDirectory() as temp:
            value, receipt, cmd = self.run_child(Path(temp), True)
            self.assertEqual(value['summary'], 'готово'); self.assertEqual(receipt['model'], 'fixture')
            disabled = {cmd[i+1] for i, v in enumerate(cmd[:-1]) if v == '--disable'}
            self.assertTrue({'shell_tool', 'multi_agent', 'hooks', 'apps', 'plugins', 'browser_use', 'computer_use', 'image_generation'} <= disabled)
            self.assertIn('project_doc_max_bytes=0', cmd); self.assertIn('web_search="disabled"', cmd)
            self.assertIn('mcp_servers.example-tool.enabled=false', cmd)
            self.assertEqual(cmd[cmd.index('--sandbox')+1], 'read-only')
            self.assertTrue(json.loads((Path(temp)/'receipt.json').read_text(encoding='utf-8')))

    def test_image_path_keeps_native_generation_capability(self):
        with tempfile.TemporaryDirectory() as temp:
            _, _, cmd = self.run_child(Path(temp), False)
            self.assertNotIn('image_generation', cmd); self.assertNotIn('shell_tool', cmd)
            self.assertNotIn('project_doc_max_bytes=0', cmd)


if __name__ == '__main__': unittest.main()
