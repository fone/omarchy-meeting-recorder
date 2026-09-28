"""Offline contract test for the recorder action (no API call or real vault write)."""
import importlib.machinery
import importlib.util
import io
import json
from contextlib import redirect_stderr, redirect_stdout
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch


ACTION = Path(__file__).resolve().parents[1] / "actions" / "summarize-to-obsidian"


def load_action():
    loader = importlib.machinery.SourceFileLoader("summarize_to_obsidian", str(ACTION))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class SummaryOutputTest(TestCase):
    def test_missing_vault_fails_before_model_or_note_write(self):
        with TemporaryDirectory() as tmp:
            meeting = Path(tmp)
            transcript = meeting / "transcript.md"
            transcript.write_text("Private meeting")
            env = {
                "MEETING_DIR": str(meeting),
                "MEETING_TRANSCRIPT": str(transcript),
                "MEETING_TITLE": "Test",
                "MEETING_DATE": "2026-09-28 09:00",
            }
            with patch.dict(os.environ, env, clear=True), redirect_stderr(io.StringIO()) as errors:
                with self.assertRaises(SystemExit):
                    load_action().main()
            self.assertIn("OBSIDIAN_VAULT", errors.getvalue())
            self.assertEqual(sorted(p.name for p in meeting.iterdir()), ["transcript.md"])

    def test_full_action_keeps_raw_transcript_local_only(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            meeting = root / "recording"
            meeting.mkdir()
            original = "# Meeting\n\n## Transcript\n\n**[00:03] You:** PRIVATE-TRANSCRIPT-MARKER\n"
            transcript = meeting / "transcript.md"
            transcript.write_text(original)
            vault = root / "vault"
            vault.mkdir()
            env = {
                "OBSIDIAN_VAULT": str(vault),
                "MEETING_DIR": str(meeting),
                "MEETING_TRANSCRIPT": str(transcript),
                "MEETING_TITLE": "Test",
                "MEETING_DATE": "2026-09-28 09:00",
                "MEETING_DURATION": "60",
                "MEETING_SPEAKERS": "Adam",
                "MEETING_PROGRESS_FILE": str(root / "progress"),
            }
            stdout = io.StringIO()
            with patch.dict(os.environ, env), redirect_stdout(stdout):
                action = load_action()
                action.VAULT = vault
                action.call_llm = lambda prompt, config: (
                    "TITLE:\nTest Meeting\nOVERVIEW:\nThe meeting covered work.\n"
                    "ACTION ITEMS:\n- Adam to follow up.\n"
                ) if "PRIVATE-TRANSCRIPT-MARKER" in prompt else self.fail("Transcript omitted from model prompt")
                action.main()
            notes = list((vault / "Meetings").glob("*.md"))
            self.assertEqual(len(notes), 1)
            note = notes[0].read_text()
            self.assertIn("## Summary", note)
            self.assertIn("## Action Items", note)
            self.assertNotIn("## Transcript", note)
            self.assertNotIn("PRIVATE-TRANSCRIPT-MARKER", note)
            self.assertEqual(transcript.read_text(), original)
            self.assertEqual((root / "progress").read_text(), "1.0")
            self.assertIn("obsidian://open?", stdout.getvalue())


class ModelThinkingTest(TestCase):
    def responses(self, responses):
        requests = []
        replies = iter(responses)

        def fake_urlopen(request, timeout):
            self.assertEqual(timeout, 180)
            requests.append(json.loads(request.data))
            content, finish = next(replies)
            payload = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
            return io.BytesIO(json.dumps(payload).encode())

        return requests, fake_urlopen

    def test_ollama_cloud_disables_thinking(self):
        requests, fake = self.responses([("TITLE:\nUseful", "stop")])
        with patch("urllib.request.urlopen", fake):
            result = load_action().call_llm("meeting transcript", {
                "provider": "ollama_cloud", "model": "kimi-k2.6",
                "base_url": "https://ollama.com/v1", "api_key": "test-key",
            })
        self.assertEqual(result, "TITLE:\nUseful")
        self.assertEqual(len(requests), 1)
        self.assertIs(requests[0]["thinking"], False)
        self.assertEqual(requests[0]["max_tokens"], 12288)

    def test_repeated_token_exhaustion_fails_without_saving_a_partial_note(self):
        requests, fake = self.responses([("", "length"), ("TITLE:\nTruncated", "length")])
        with patch("urllib.request.urlopen", fake), redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                load_action().call_llm("meeting transcript", {
                    "provider": "ollama_cloud", "model": "kimi-k2.6",
                    "base_url": "https://ollama.com/v1", "api_key": "test-key",
                })
        self.assertEqual([r["max_tokens"] for r in requests], [12288, 16384])
        self.assertTrue(all(r["thinking"] is False for r in requests))


if __name__ == "__main__":
    main()
