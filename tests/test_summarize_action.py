"""Offline contract tests for the summarize-to-obsidian action.

All tests run in isolated temp directories.  No network call or real vault
write occurs.  Tests cover:
  - context.json parsing (absent, malformed, valid)
  - Prompt construction with typed context blocks (glossary, roster, @Russ note)
  - Vault note omits raw transcript even when context is present
  - Title precedence: typed > model > auto-generated fallback
  - Live notes rendered in Obsidian output with timestamps
  - MEETING_SPEAKERS never treated as attendance ground truth
  - Legacy fallback when context.json is absent
"""
import importlib.machinery
import importlib.util
import io
import json
import os
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch


ACTION = Path(__file__).resolve().parents[1] / "actions" / "summarize-to-obsidian"

SAMPLE_TRANSCRIPT = (
    "[00:10] Russ: Let's review the QRadar integration.\n"
    "[00:25] Adam: The FileBound export is ready for testing.\n"
    "[00:40] Russ: Follow up with the vendor on the timeline.\n"
)

CONTEXT_JSON = {
    "version": 1,
    "attendees": "Adam, Russ",
    "glossary": "FileBound, QRadar",
    "notes": [
        {"offset_s": 12.0, "text": "@Russ follow up"},
        {"offset_s": 38.0, "text": "Need FileBound export by Friday"},
    ],
}


def load_action():
    loader = importlib.machinery.SourceFileLoader("summarize_to_obsidian", str(ACTION))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# context.json reading
# ---------------------------------------------------------------------------

class ContextJsonTest(TestCase):
    def test_absent_context_returns_none(self):
        with TemporaryDirectory() as tmp:
            mod = load_action()
            result = mod.read_context_json(Path(tmp))
            self.assertIsNone(result)

    def test_malformed_json_returns_none(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "context.json"
            path.write_text("{invalid json")
            mod = load_action()
            result = mod.read_context_json(Path(tmp))
            self.assertIsNone(result)

    def test_wrong_version_returns_none(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "context.json"
            path.write_text(json.dumps({"version": 99}))
            mod = load_action()
            result = mod.read_context_json(Path(tmp))
            self.assertIsNone(result)

    def test_invalid_fields_do_not_break_summary(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "context.json"
            path.write_text(json.dumps({
                "version": 1, "attendees": ["Adam"], "glossary": 42,
                "notes": [None, {"offset_s": "bad", "text": "ignore"},
                          {"offset_s": 12, "text": "@Russ follow up"}],
            }))
            ctx = load_action().read_context_json(Path(tmp))
            self.assertEqual(ctx["attendees"], "")
            self.assertEqual(ctx["glossary"], "")
            self.assertEqual(ctx["notes"], [{"offset_s": 12, "text": "@Russ follow up"}])

    def test_valid_context_round_trips(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "context.json"
            path.write_text(json.dumps(CONTEXT_JSON))
            mod = load_action()
            ctx = mod.read_context_json(Path(tmp))
            self.assertIsNotNone(ctx)
            self.assertEqual(ctx["version"], 1)
            self.assertEqual(ctx["attendees"], "Adam, Russ")
            self.assertEqual(ctx["glossary"], "FileBound, QRadar")
            self.assertEqual(len(ctx["notes"]), 2)
            self.assertEqual(ctx["notes"][0]["text"], "@Russ follow up")
            self.assertAlmostEqual(ctx["notes"][0]["offset_s"], 12.0)


# ---------------------------------------------------------------------------
# Prompt construction — the core contract test
# ---------------------------------------------------------------------------

class PromptConstructionTest(TestCase):
    """Verify the prompt contains the right XML blocks and omits raw transcript in vault."""

    def setUp(self):
        self.mod = load_action()

    def test_prompt_contains_glossary_block(self):
        prompt = self.mod.build_prompt(
            SAMPLE_TRANSCRIPT,
            attendees="Adam, Russ",
            glossary="FileBound, QRadar",
        )
        self.assertIn("<glossary>", prompt)
        self.assertIn("FileBound", prompt)
        self.assertIn("QRadar", prompt)
        self.assertIn("</glossary>", prompt)

    def test_prompt_contains_attendees_block(self):
        prompt = self.mod.build_prompt(
            SAMPLE_TRANSCRIPT,
            attendees="Adam, Russ",
            glossary="FileBound, QRadar",
        )
        self.assertIn("<attendees>", prompt)
        self.assertIn("Adam, Russ", prompt)
        self.assertIn("</attendees>", prompt)

    def test_prompt_contains_user_notes_block_with_timestamp(self):
        prompt = self.mod.build_prompt(
            SAMPLE_TRANSCRIPT,
            attendees="Adam, Russ",
            glossary="FileBound, QRadar",
            user_notes=CONTEXT_JSON["notes"],
        )
        self.assertIn("<user_notes>", prompt)
        self.assertIn("[00:12] @Russ follow up", prompt)
        self.assertIn("[00:38] Need FileBound export by Friday", prompt)
        self.assertIn("</user_notes>", prompt)

    def test_prompt_contains_transcript_block(self):
        prompt = self.mod.build_prompt(
            SAMPLE_TRANSCRIPT,
            attendees="Adam, Russ",
            glossary="FileBound, QRadar",
        )
        self.assertIn("<transcript>", prompt)
        self.assertIn("QRadar integration", prompt)
        self.assertIn("</transcript>", prompt)

    def test_prompt_omits_empty_blocks(self):
        prompt = self.mod.build_prompt(SAMPLE_TRANSCRIPT)
        # The prompt template prose mentions <attendees> etc. in reliability
        # rules — check for the actual data blocks (tag on its own line followed
        # by content), not just the tag name.
        self.assertNotIn("<attendees>\n", prompt)
        self.assertNotIn("<glossary>\n", prompt)
        self.assertNotIn("<user_notes>\n", prompt)
        self.assertIn("<transcript>\n", prompt)

    def test_source_reliability_section_present(self):
        """The prompt must explain that roster and glossary spellings are authoritative."""
        prompt = self.mod.build_prompt(
            SAMPLE_TRANSCRIPT,
            attendees="Adam, Russ",
            glossary="FileBound, QRadar",
            user_notes=CONTEXT_JSON["notes"],
        )
        self.assertIn("SOURCE RELIABILITY", prompt)
        self.assertIn("<user_notes>", prompt)

    def test_meting_speakers_not_in_prompt(self):
        """MEETING_SPEAKERS (voice-detected) must never appear in the prompt
        as attendees — voice detection is not attendance ground truth."""
        prompt = self.mod.build_prompt(
            SAMPLE_TRANSCRIPT,
            attendees="Adam, Russ",
        )
        # The env var MEETING_SPEAKERS should NOT be injected by build_prompt;
        # it is only consumed by main() for legacy fallback, never in prompt.
        self.assertNotIn("MEETING_SPEAKERS", prompt)


# ---------------------------------------------------------------------------
# Vault note output — never contains raw transcript
# ---------------------------------------------------------------------------

class VaultNoteOutputTest(TestCase):
    """Full integration: context.json present → prompt has blocks, vault note
    has live notes but omits raw transcript."""

    def test_full_action_with_context(self):
        """End-to-end: context.json exists → prompt has glossary+roster+notes,
        vault note has live notes but no raw transcript."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            meeting = root / "recording"
            meeting.mkdir()
            transcript = meeting / "transcript.md"
            transcript.write_text(SAMPLE_TRANSCRIPT)

            # Write context.json
            (meeting / "context.json").write_text(json.dumps(CONTEXT_JSON))

            vault = root / "vault"
            vault.mkdir()

            env = {
                "OBSIDIAN_VAULT": str(vault),
                "MEETING_DIR": str(meeting),
                "MEETING_TRANSCRIPT": str(transcript),
                "MEETING_TITLE": "QRadar Review",
                "MEETING_DATE": "2026-09-29 10:00",
                "MEETING_DURATION": "60",
                "MEETING_PROGRESS_FILE": str(root / "progress"),
            }

            captured_prompt = {}
            fake_response = (
                "TITLE:\nQRadar Review\n"
                "OVERVIEW:\nThe team reviewed the QRadar integration.\n"
                "ATTENDEES:\nAdam, Russ\n"
                "KEY POINTS:\n- FileBound export ready\n"
                "ACTION ITEMS:\n- Russ to follow up with vendor\n"
                "DECISIONS:\n- Proceed with FileBound testing\n"
                "OPEN QUESTIONS:\n- None identified\n"
                "OWNERS:\nAdam, Russ\n"
            )

            stdout = io.StringIO()
            with patch.dict(os.environ, env), redirect_stdout(stdout):
                action = load_action()
                action.VAULT = vault
                action.call_llm = lambda prompt, config: (
                    captured_prompt.__setitem__("text", prompt) or fake_response
                )
                action.main()

            # --- Verify prompt content ---
            prompt_text = captured_prompt["text"]
            self.assertIn("<glossary>", prompt_text)
            self.assertIn("FileBound", prompt_text)
            self.assertIn("QRadar", prompt_text)
            self.assertIn("<attendees>", prompt_text)
            self.assertIn("Adam, Russ", prompt_text)
            self.assertIn("[00:12] @Russ follow up", prompt_text)
            self.assertIn("<transcript>", prompt_text)

            # --- Verify vault note ---
            notes = list((vault / "Meetings").glob("*.md"))
            self.assertEqual(len(notes), 1)
            note = notes[0].read_text()

            # Live notes present in vault note
            self.assertIn("## Live Notes", note)
            self.assertIn("@Russ follow up", note)
            self.assertIn("**[00:12]**", note)

            # Raw transcript must NOT appear in vault note
            self.assertNotIn("## Transcript", note)
            self.assertNotIn("[00:10] Russ:", note)
            self.assertNotIn("[00:25] Adam:", note)

            # Summary content present
            self.assertEqual(notes[0].name, "2026-09-29 QRadar Review.md")
            self.assertIn("## Summary", note)
            self.assertIn("## Action Items", note)
            self.assertIn("QRadar Review", note)

            # Transcript file unchanged
            self.assertEqual(transcript.read_text(), SAMPLE_TRANSCRIPT)

    def test_full_action_without_context_preserves_legacy(self):
        """Without context.json, action falls back to legacy behavior:
        no glossary/user_notes blocks, speakers used for people."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            meeting = root / "recording"
            meeting.mkdir()
            transcript = meeting / "transcript.md"
            transcript.write_text(SAMPLE_TRANSCRIPT)

            vault = root / "vault"
            vault.mkdir()

            env = {
                "OBSIDIAN_VAULT": str(vault),
                "MEETING_DIR": str(meeting),
                "MEETING_TRANSCRIPT": str(transcript),
                "MEETING_TITLE": "Weekly Sync",
                "MEETING_DATE": "2026-09-29 10:00",
                "MEETING_DURATION": "60",
                "MEETING_SPEAKERS": "Adam\nRuss",
            }

            captured_prompt = {}
            fake_response = (
                "TITLE:\nWeekly Sync\n"
                "OVERVIEW:\nA routine sync.\n"
                "KEY POINTS:\n- Discussed project status\n"
                "ACTION ITEMS:\nNone identified\n"
                "DECISIONS:\nNone identified\n"
                "OPEN QUESTIONS:\nNone identified\n"
                "OWNERS:\nNone identified\n"
            )

            stdout = io.StringIO()
            with patch.dict(os.environ, env), redirect_stdout(stdout):
                action = load_action()
                action.VAULT = vault
                action.call_llm = lambda prompt, config: (
                    captured_prompt.__setitem__("text", prompt) or fake_response
                )
                action.main()

            prompt_text = captured_prompt["text"]
            # No context blocks
            self.assertNotIn("<glossary>\n", prompt_text)
            self.assertNotIn("<attendees>\n", prompt_text)
            self.assertNotIn("<user_notes>\n", prompt_text)
            self.assertIn("<transcript>\n", prompt_text)

            # Vault note: speakers used as people (legacy fallback)
            notes = list((vault / "Meetings").glob("*.md"))
            note = notes[0].read_text()
            self.assertIn("Adam", note)
            self.assertIn("Russ", note)
            self.assertNotIn("## Live Notes", note)


# ---------------------------------------------------------------------------
# Title precedence
# ---------------------------------------------------------------------------

class TitlePrecedenceTest(TestCase):
    def test_typed_title_over_model_title(self):
        """Typed title from context takes precedence when not auto-generated."""
        mod = load_action()
        from datetime import datetime
        summary = {"title": "Model Title", "overview": "", "attendees": [],
                    "key_points": [], "action_items": [], "decisions": [],
                    "open_questions": [], "owners": []}
        note = mod.format_note(
            summary, datetime(2026, 9, 29, 10, 0), 60, [], [], Path("/tmp/rec"),
            typed_title="Typed Title",
        )
        self.assertIn("Typed Title", note)
        self.assertNotIn("Model Title", note)

    def test_auto_title_yields_to_model(self):
        """Auto-generated 'Meeting HH:MM' title yields to model title."""
        mod = load_action()
        from datetime import datetime
        summary = {"title": "Real Topic", "overview": "", "attendees": [],
                    "key_points": [], "action_items": [], "decisions": [],
                    "open_questions": [], "owners": []}
        note = mod.format_note(
            summary, datetime(2026, 9, 29, 10, 0), 60, [], [], Path("/tmp/rec"),
            typed_title="Meeting 14:30",
        )
        self.assertIn("Real Topic", note)
        self.assertNotIn("Meeting 14:30", note)

    def test_blank_typed_title_yields_to_model(self):
        """Blank typed title yields to model title."""
        mod = load_action()
        from datetime import datetime
        summary = {"title": "From Model", "overview": "", "attendees": [],
                    "key_points": [], "action_items": [], "decisions": [],
                    "open_questions": [], "owners": []}
        note = mod.format_note(
            summary, datetime(2026, 9, 29, 10, 0), 60, [], [], Path("/tmp/rec"),
            typed_title="",
        )
        self.assertIn("From Model", note)

    def test_no_title_at_all_falls_back_to_date(self):
        mod = load_action()
        from datetime import datetime
        summary = {"title": None, "overview": "", "attendees": [],
                    "key_points": [], "action_items": [], "decisions": [],
                    "open_questions": [], "owners": []}
        note = mod.format_note(
            summary, datetime(2026, 9, 29, 10, 0), 60, [], [], Path("/tmp/rec"),
            typed_title="",
        )
        self.assertIn("Meeting 2026-09-29 10:00", note)


# ---------------------------------------------------------------------------
# Live notes rendering in vault output
# ---------------------------------------------------------------------------

class LiveNotesRenderingTest(TestCase):
    def test_timestamped_notes_appear_in_vault(self):
        mod = load_action()
        from datetime import datetime
        summary = {"title": "Test", "overview": "", "attendees": [],
                    "key_points": [], "action_items": [], "decisions": [],
                    "open_questions": [], "owners": []}
        notes = [
            {"offset_s": 12.0, "text": "@Russ follow up"},
            {"offset_s": 75.5, "text": "Check the config"},
            {"offset_s": 3723.0, "text": "Long meeting note"},
        ]
        note = mod.format_note(
            summary, datetime(2026, 9, 29, 10, 0), 60, [], [], Path("/tmp/rec"),
            live_notes=notes,
        )
        self.assertIn("## Live Notes", note)
        self.assertIn("**[00:12]** @Russ follow up", note)
        self.assertIn("**[01:15]** Check the config", note)
        self.assertIn("**[01:02:03]** Long meeting note", note)

    def test_empty_notes_produces_no_live_notes_section(self):
        mod = load_action()
        from datetime import datetime
        summary = {"title": "Test", "overview": "", "attendees": [],
                    "key_points": [], "action_items": [], "decisions": [],
                    "open_questions": [], "owners": []}
        note = mod.format_note(
            summary, datetime(2026, 9, 29, 10, 0), 60, [], [], Path("/tmp/rec"),
            live_notes=[],
        )
        self.assertNotIn("## Live Notes", note)

    def test_notes_with_empty_text_are_skipped(self):
        mod = load_action()
        from datetime import datetime
        summary = {"title": "Test", "overview": "", "attendees": [],
                    "key_points": [], "action_items": [], "decisions": [],
                    "open_questions": [], "owners": []}
        notes = [
            {"offset_s": 5.0, "text": ""},
            {"offset_s": 10.0, "text": "Real note"},
        ]
        note = mod.format_note(
            summary, datetime(2026, 9, 29, 10, 0), 60, [], [], Path("/tmp/rec"),
            live_notes=notes,
        )
        self.assertIn("**[00:10]** Real note", note)
        # Only one live note line (the empty one is skipped)
        self.assertEqual(note.count("**["), 1)


# ---------------------------------------------------------------------------
# People field — typed attendees vs detected speakers
# ---------------------------------------------------------------------------

class PeopleFieldTest(TestCase):
    def test_typed_attendees_used_as_people(self):
        """Typed roster from context.json is used for people, not MEETING_SPEAKERS."""
        mod = load_action()
        from datetime import datetime
        summary = {"title": "Test", "overview": "", "attendees": ["Model Name"],
                    "key_points": [], "action_items": [], "decisions": [],
                    "open_questions": [], "owners": []}
        note = mod.format_note(
            summary, datetime(2026, 9, 29, 10, 0), 60,
            ["Speaker 1", "Speaker 2"], [],
            Path("/tmp/rec"),
            typed_attendees="Adam, Russ",
        )
        self.assertIn("Adam", note)
        self.assertIn("Russ", note)
        self.assertNotIn("Speaker 1", note)
        self.assertNotIn("Speaker 2", note)

    def test_legacy_fallback_to_speakers(self):
        """Without typed attendees, generic speaker labels are filtered out."""
        mod = load_action()
        from datetime import datetime
        summary = {"title": "Test", "overview": "", "attendees": [],
                    "key_points": [], "action_items": [], "decisions": [],
                    "open_questions": [], "owners": []}
        note = mod.format_note(
            summary, datetime(2026, 9, 29, 10, 0), 60,
            ["You", "Speaker 1", "Russ"], [],
            Path("/tmp/rec"),
            typed_attendees="",
        )
        self.assertIn("Russ", note)
        self.assertNotIn("You", note)
        self.assertNotIn("Speaker 1", note)


# ---------------------------------------------------------------------------
# Missing vault guard
# ---------------------------------------------------------------------------

class MissingVaultTest(TestCase):
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


# ---------------------------------------------------------------------------
# LLM client internals
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# User notes formatting helper
# ---------------------------------------------------------------------------

class UserNotesFormatTest(TestCase):
    def test_format_user_notes_renders_stamps(self):
        mod = load_action()
        result = mod._format_user_notes(CONTEXT_JSON["notes"])
        self.assertIn("[00:12] @Russ follow up", result)
        self.assertIn("[00:38] Need FileBound export by Friday", result)

    def test_format_user_notes_empty_list(self):
        mod = load_action()
        result = mod._format_user_notes([])
        self.assertEqual(result, "")

    def test_format_user_notes_skips_empty_text(self):
        mod = load_action()
        result = mod._format_user_notes([{"offset_s": 5.0, "text": ""}])
        self.assertEqual(result, "")


if __name__ == "__main__":
    main()
