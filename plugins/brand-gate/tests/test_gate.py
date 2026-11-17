"""Offline contract tests using the installed public packages."""

from __future__ import annotations

import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib.metadata import entry_points
from importlib.machinery import ModuleSpec
from pathlib import Path
from unittest.mock import Mock, patch

from bernstein.core.persistence.journal import JournalReader
from bernstein.plugins.manager import HookBlockingError, PluginManager
from bid.classify import MockClassifier

from bernstein_brand_gate.cli import main
from bernstein_brand_gate.config import address, digest, load_config
from bernstein_brand_gate.gate import GateUnavailable, check
from bernstein_brand_gate.plugin import BrandGatePlugin

CONFIG = '''enabled = true
brand_name = "Example"
official_domains = ["example.com"]
[tools]
visit = "url"
'''

IPV4_HOSTS = (
    '127.0.0.1',
    '１２７.０.０.１',  # Full-width decimal digits
    '𝟏𝟐𝟕.𝟎.𝟎.𝟏',  # Mathematical bold decimal digits
    '𝟙𝟚𝟟.𝟘.𝟘.𝟙',  # Mathematical double-struck decimal digits
    '1２𝟕.0.０.𝟙',  # Mixed decimal digit forms
    '127。0。0。1',  # U+3002
    '127．0．0．1',  # U+FF0E
    '127｡0｡0｡1',  # U+FF61
    '１２７。0．０｡𝟙',  # Mixed digits and full stops
    '127.0.0.1.',
    '１２７.０.０.１。',
    '１２７.０.０.１．',
    '１２７.０.０.１｡',
)
IP_LITERAL_TARGETS = tuple(
    target for host in IPV4_HOSTS
    for target in (host, 'http://' + host, 'https://' + host + ':443/path')
) + (
    '::1', '[::1]', 'https://[::1]', 'http://[::1]:80/path',
    'https://[0:0:0:0:0:0:0:1]', 'https://[2001:DB8::1]',
    'https://[::ffff:127.0.0.1]', 'https://[fe80::1%25eth0]',
    'https://[::１]', 'https://[２００１:db8::１]',
)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'gate.toml'
        self.path.write_text(CONFIG)
        self.config = load_config(self.path)
        self.plugin = BrandGatePlugin()
        for method in ('connect', 'connect_ex'):
            guard = self.enterContext(patch.object(socket.socket, method, side_effect=AssertionError('network forbidden')))
            self.addCleanup(guard.assert_not_called)
        guard = self.enterContext(patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')))
        self.addCleanup(guard.assert_not_called)
        self.enterContext(patch.dict(os.environ, {'BERNSTEIN_BRAND_GATE_CONFIG': str(self.path)}))

    def run_check(self, target, **kwargs):
        return check(target, self.config, session_id='session-1', tool='visit', **kwargs)

    def reader(self, session='session-1'):
        return JournalReader(self.config.journal_dir / digest(session))

    def test_real_offline_verdicts(self):
        for target, decision, label in [
            ('example.com', 'allow', 'legit'),
            ('https://www.example.com:443/a?secret=hidden#fragment', 'allow', 'legit'),
            ('https://examp1e.com', 'block', 'impersonation'),
            ('https://example.com.evil.invalid', 'block', 'suspicious'),
            ('unrelated.invalid', 'block', 'suspicious'),
            ('exаmple.com', 'block', 'impersonation'),  # Cyrillic a
        ]:
            with self.subTest(target=target):
                result = self.run_check(target)
                self.assertEqual(result['decision'], decision)
                # Some unrelated domains can still have a similarity signal.
                if target != 'https://example.com.evil.invalid':
                    self.assertEqual(result['verdict']['label'], label)
                self.assertIsNone(result['error'])
        self.assertTrue(self.reader().verify().ok)

    def test_one_classifier_call(self):
        original = MockClassifier().classify
        with patch.object(MockClassifier, 'classify', side_effect=original) as classify:
            self.run_check('example.com')
        self.assertEqual(classify.call_count, 1)

    def test_idna_private_suffix_and_multilevel_suffix(self):
        self.assertEqual(address('https://xn--exmple-4nf.com')[0], 'exаmple.com')
        self.assertEqual(address('https://x.example.co.uk')[1], 'example.co.uk')
        self.assertEqual(address('https://tenant.github.io')[1], 'tenant.github.io')
        cfg = replace(self.config, official_domains=('example.co.uk',))
        self.assertEqual(check('example.co.uk', cfg, session_id='s', tool='visit')['decision'], 'allow')

    def test_address_rejects_ip_literals_after_normalization(self):
        for target in IP_LITERAL_TARGETS:
            with self.subTest(target=target), self.assertRaises(ValueError):
                address(target)

    def test_ip_literals_block_before_classifier_with_receipts(self):
        with patch.object(MockClassifier, 'classify') as classify:
            for target in IP_LITERAL_TARGETS:
                with self.subTest(target=target):
                    result = self.run_check(target)
                    self.assertEqual((result['decision'], result['error']),
                                     ('block', 'invalid_target'))
                    self.assertIsNone(result['host'])
                    self.assertIsNone(result['domain'])
                    self.assertIsNone(result['verdict'])
            classify.assert_not_called()
        entries = list(self.reader().entries())
        self.assertEqual(len(entries), len(IP_LITERAL_TARGETS))
        self.assertTrue(self.reader().verify().ok)

    def test_config_rejects_ip_literals_after_normalization(self):
        for target in IP_LITERAL_TARGETS:
            with self.subTest(target=target):
                self.path.write_text(CONFIG.replace('example.com', target))
                with self.assertRaises(ValueError):
                    load_config(self.path)

    def test_ordinary_internationalized_domains_remain_accepted(self):
        for target, host, domain in [
            ('bücher.de', 'bücher.de', 'bücher.de'),
            ('https://xn--bcher-kva.de/path', 'bücher.de', 'bücher.de'),
            ('https://例え。jp', '例え.jp', '例え.jp'),
            ('https://１２３。example.com', '123.example.com', 'example.com'),
        ]:
            with self.subTest(target=target):
                self.assertEqual(address(target), (host, domain))
        self.path.write_text(CONFIG.replace('example.com', 'bücher.de'))
        self.assertEqual(load_config(self.path).official_domains, ('bücher.de',))

    def test_invalid_targets_block_with_receipts(self):
        for target in [None, [], ['example.com'], '', 'example.com evil.com',
                       'https://example.com\\@evil.com', 'ftp://example.com',
                       'https://user:password@example.com', 'https://127.0.0.1',
                       'https://[::1]', 'example.com:443', '//example.com',
                       'https://example.com:bad', 'https://example.com:65536',
                       'https://example.com\n.evil.com', 'https://example.com%2eevil.com',
                       'localhost', 'com', '*.example.com', 'x..example.com']:
            with self.subTest(target=target):
                result = self.run_check(target)
                self.assertEqual(result['decision'], 'block')
                self.assertEqual(result['error'], 'invalid_target')
        self.assertEqual(len(list(self.reader().entries())), 20)

    def test_verdict_mapping_is_conservative(self):
        original = MockClassifier().classify
        for label in ['legit', 'fan', 'suspicious', 'impersonation', 'fraud']:
            for official in [True, False, None]:
                def classify(c, b):
                    v = original(c, b)
                    v.label = label
                    v.signals.domain_is_legit = official
                    return v
                with patch.object(MockClassifier, 'classify', side_effect=classify):
                    result = self.run_check('example.com')
                self.assertEqual(result['decision'], 'allow' if label == 'legit' and official is True else 'block')

    def test_unavailable_and_malformed_verdicts_block(self):
        for effect in [RuntimeError('secret=do-not-record'), {'label': 'unknown'}]:
            with self.subTest(effect=effect):
                kw = {'side_effect': effect} if isinstance(effect, Exception) else {'return_value': effect}
                with patch.object(MockClassifier, 'classify', **kw):
                    result = self.run_check('example.com')
                self.assertEqual((result['decision'], result['error']), ('block', 'check_unavailable'))
                self.assertNotIn('do-not-record', json.dumps(result))

    def test_candidate_mismatch_blocks(self):
        original = MockClassifier().classify
        def mismatched(c, b):
            verdict = original(c, b)
            verdict.candidate_id = 'different'
            return verdict
        with patch.object(MockClassifier, 'classify', side_effect=mismatched):
            self.assertEqual(self.run_check('example.com')['error'], 'check_unavailable')

    def test_optional_model_runtime_blocks_before_classifier(self):
        optional = types.ModuleType('fastembed')
        optional.__spec__ = ModuleSpec('fastembed', loader=None)
        optional.TextEmbedding = Mock(side_effect=RuntimeError('no model download'))
        with patch.dict(sys.modules, {'fastembed': optional}), patch.dict(
            os.environ, {'BID_DATA_DIR': str(self.root / 'detector-data')}
        ), patch.object(MockClassifier, 'classify', wraps=MockClassifier().classify) as classify:
            result = self.run_check('example.com')
        self.assertEqual((result['decision'], result['error']), ('block', 'check_unavailable'))
        classify.assert_not_called()
        optional.TextEmbedding.assert_not_called()

    def test_mutated_verdict_instance_is_revalidated(self):
        original = MockClassifier().classify
        for field in ['confidence', 'model_id', 'nested_signal']:
            def malformed(c, b):
                verdict = original(c, b)
                if field == 'nested_signal':
                    verdict.signals.handle_similarity = 'invalid'
                else:
                    setattr(verdict, field, None)
                return verdict
            with self.subTest(field=field), patch.object(MockClassifier, 'classify', side_effect=malformed):
                result = self.run_check('example.com')
            self.assertEqual((result['decision'], result['error']), ('block', 'check_unavailable'))

    def test_invalid_confidence_blocks(self):
        original = MockClassifier().classify
        for confidence in [float('nan'), float('inf'), -0.1, 1.1]:
            def malformed(c, b):
                verdict = original(c, b)
                verdict.confidence = confidence
                return verdict
            with self.subTest(confidence=confidence), patch.object(MockClassifier, 'classify', side_effect=malformed):
                result = self.run_check('example.com')
            self.assertEqual((result['decision'], result['error']), ('block', 'check_unavailable'))

    def test_domain_dependency_failure_is_unavailable(self):
        with patch.dict(sys.modules, {'bid.domains': None}):
            result = self.run_check('example.com')
        self.assertEqual((result['decision'], result['error']), ('block', 'check_unavailable'))

    def test_journal_receipt_contains_no_url_path_query_or_credentials(self):
        result = self.run_check('https://example.com/private-path?token=secret-token#secret-fragment')
        entries = list(self.reader().entries())
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry.tool_result, result)
        self.assertEqual(entry.model, 'mock-deterministic-v1')
        self.assertEqual(result['bid_version'], '0.1.1')
        self.assertTrue(result['checked_at'].endswith('Z'))
        self.assertEqual(result['config_sha256'], self.config.sha256)
        raw = self.reader().bucket_path.read_text()
        for text in ['private-path', 'secret-token', 'secret-fragment']:
            self.assertNotIn(text, raw)
        self.assertTrue(self.reader().verify().ok)

    def test_concurrent_writers_preserve_chain_and_session_containment(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(self.run_check, ['example.com', 'examp1e.com'] * 4))
        self.assertEqual(len(list(self.reader().entries())), 8)
        self.assertTrue(self.reader().verify().ok)
        check('example.com', self.config, session_id='../../escape', tool='visit')
        self.assertTrue(self.reader('../../escape').bucket_path.is_relative_to(self.config.journal_dir))

    def test_journal_failure_never_allows(self):
        for target in ['example.com', 'examp1e.com']:
            with patch('bernstein_brand_gate.gate.write_receipt', side_effect=OSError('sensitive-path')):
                with self.assertRaises(GateUnavailable) as error:
                    self.run_check(target)
                self.assertNotIn('sensitive-path', str(error.exception))
                with self.assertRaises(HookBlockingError):
                    self.plugin.on_pre_tool_use('s', 'visit', {'url': target})

    def test_damaged_journal_never_allows_or_appends(self):
        self.run_check('example.com')
        bucket = self.reader().bucket_path
        original = bucket.read_text()
        damaged_row = json.loads(original)
        damaged_row['tool_result']['decision'] = 'changed'
        for damaged in [original + '{"partial":', json.dumps(damaged_row) + '\n']:
            with self.subTest(damaged=damaged):
                bucket.write_text(damaged)
                with self.assertRaises(GateUnavailable):
                    self.run_check('example.com')
                self.assertEqual(bucket.read_text(), damaged)

    def test_disabled_and_unselected_hooks_do_nothing(self):
        with patch('bernstein_brand_gate.plugin.check') as checker:
            self.plugin.on_pre_tool_use('s', 'other', {})
            with patch.dict(os.environ, {'BERNSTEIN_BRAND_GATE_CONFIG': ''}):
                self.plugin.on_pre_tool_use('s', 'visit', {})
            self.path.write_text('enabled = false\n')
            self.plugin.on_pre_tool_use('s', 'visit', {})
            self.path.write_text('')
            self.plugin.on_pre_tool_use('s', 'visit', {})
        checker.assert_not_called()
        self.assertFalse(self.config.journal_dir.exists())

    def test_hook_blocks_missing_target_and_allows_official(self):
        self.plugin.on_pre_tool_use('s', 'visit', {'url': 'example.com'})
        for args in [{}, {'url': ['example.com', 'examp1e.com']}, {'url': 'examp1e.com'}, None]:
            with self.assertRaises(HookBlockingError):
                self.plugin.on_pre_tool_use('s', 'visit', args)

    def test_entry_point_and_public_manager_propagate_block(self):
        ep = next(e for e in entry_points(group='bernstein.plugins') if e.name == 'brand_gate')
        self.assertIs(ep.load(), BrandGatePlugin)
        manager = PluginManager(workdir=self.root)
        manager.register(ep.load()(), name='brand_gate')
        manager.fire_pre_tool_use('s', 'visit', {'url': 'example.com'})
        with self.assertRaises(HookBlockingError):
            manager.fire_pre_tool_use('s', 'visit', {'url': 'examp1e.com'})

    def test_config_errors_and_explicit_missing_file_block(self):
        for content in ['enabled = "yes"', 'enabled=true', 'typo="x"\n' + CONFIG,
                        CONFIG.replace('example.com', 'https://example.com'),
                        CONFIG.replace('example.com', 'www.example.com')]:
            self.path.write_text(content)
            with self.assertRaises(Exception):
                load_config(self.path)
            with self.assertRaises(HookBlockingError):
                self.plugin.on_pre_tool_use('s', 'visit', {'url': 'example.com'})
        self.path.unlink()
        with self.assertRaises(HookBlockingError):
            self.plugin.on_pre_tool_use('s', 'visit', {'url': 'example.com'})
        self.assertIsNone(load_config(None))

    def test_cli_exit_status_and_sanitized_errors(self):
        for target, code in [('example.com', 0), ('examp1e.com', 2), ('not a domain', 2)]:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([target, '--session', 's', '--tool', 'visit']), code)
            self.assertIn(json.loads(output.getvalue())['decision'], ['allow', 'block'])
        self.path.write_text('enabled=false\n')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['example.com', '--session', 's', '--tool', 'visit']), 0)
        self.assertEqual(json.loads(output.getvalue()), {'decision': 'disabled', 'checked': False})
        self.path.unlink()
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(main(['example.com', '--session', 's', '--tool', 'visit']), 2)
        self.assertNotIn(str(self.path), error.getvalue())

    def test_cli_help_explains_disabled_exit_zero(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            with self.assertRaises(SystemExit) as exit_status:
                main(['--help'])
        self.assertEqual(exit_status.exception.code, 0)
        help_text = ' '.join(output.getvalue().split())
        self.assertIn('disabled checks return 0 without checking', help_text)
        self.assertIn('Exit 0 means allow OR disabled', help_text)
        self.assertIn('must independently ensure enablement', help_text)

    def test_cli_disabled_cases_never_check_or_write_receipts(self):
        for content in [None, '', 'enabled = false\n', 'enabled = false\nbrand_name = 123\n']:
            with self.subTest(content=content), patch(
                'bernstein_brand_gate.cli.check'
            ) as checker:
                if content is not None:
                    self.path.write_text(content)
                env_path = '' if content is None else str(self.path)
                with patch.dict(os.environ, {'BERNSTEIN_BRAND_GATE_CONFIG': env_path}):
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        code = main(['not a domain', '--session', 's', '--tool', 'other'])
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(output.getvalue()),
                                 {'decision': 'disabled', 'checked': False})
                checker.assert_not_called()
                self.assertFalse(self.config.journal_dir.exists())

    def test_invalid_opt_in_files_block_both_interfaces_without_receipts(self):
        for content in ['enabled = [', 'enabled = "false"', 'enabled = true\n']:
            with self.subTest(content=content):
                self.path.write_text(content)
                with self.assertRaises(HookBlockingError):
                    self.plugin.on_pre_tool_use('s', 'visit', {'url': 'example.com'})
                with contextlib.redirect_stderr(io.StringIO()) as error:
                    code = main(['example.com', '--session', 's', '--tool', 'visit'])
                self.assertEqual(code, 2)
                self.assertIn('action blocked', error.getvalue())
                self.assertNotIn(str(self.path), error.getvalue())
                self.assertFalse(self.config.journal_dir.exists())

    def test_cli_unconfigured_tool_and_journal_failure_block(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['example.com', '--session', 's', '--tool', 'other']), 2)
            with patch('bernstein_brand_gate.gate.write_receipt', side_effect=OSError('secret')):
                self.assertEqual(main(['example.com', '--session', 's', '--tool', 'visit']), 2)
        self.assertFalse(self.config.journal_dir.exists())


if __name__ == '__main__':
    unittest.main()
