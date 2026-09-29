# Live meeting context in Meeting Recorder

Goal: capture a typed attendee roster, vocabulary, and timestamped live notes before/during a recording, use the vocabulary for local Whisper transcription, and pass the typed context into the opt-in Obsidian summary action. Keep source notes with the meeting and never infer attendance from detected voices.

1. Add a versioned, atomic `context.json` sidecar with title-independent roster/terms and timestamped notes. Unit-test round trips, empty/legacy behavior, completion and prompt formatting. Stage it alongside `recording.json`, copy to the saved meeting folder before cleanup, and restore it on crash recovery.
2. Add GTK rows for attendee roster and terms, a note composer and visible committed-note log. Tab completes `@name` from the typed roster only; Enter commits a timestamped entry and clears the composer after a successful disk write. Keep the controls visible while recording and allow pre-start context entry. Reset on a new recording; restore on opening a saved one. Do not mutate real meetings for QA.
3. Plumb glossary into `whisper-rs` initial prompt, including the re-transcription path. Preserve the default transcribe CLI behavior when no glossary was provided.
4. Update the opt-in `summarize-to-obsidian` action: read `context.json`, pass separate attendees/glossary/user_notes blocks, use the typed roster as attendance truth, preserve a user-chosen title, and render live notes in the summary note. Keep the raw transcript only in the local meeting folder.
5. Test synthetic persistence, action prompt/output, compilation, clippy, release build, and GTK runtime if an isolated run is possible. Do not publish the fork or modify a real meeting without explicit approval.
