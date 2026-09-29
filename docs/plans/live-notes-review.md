# Live notes review and stop safety

Goal: prevent accidental Stop, use the typed attendee roster for speaker-name Tab completion, and make notes visible/editable in the transcript timeline without changing transcript.md or real meeting files during QA.

1. `src/ui.rs`: Stop button opens an Adwaita confirmation with Keep recording as default/close response; only confirmed Stop calls `stop()`. Increase spacing from composer and Pause. Verify cancel/confirm state on an isolated scratch recording if safely possible.
2. `src/context.rs`: add pure validated edit/delete operations on `MeetingContext.notes`; preserve timestamps, reject empty edits and invalid indices; persist candidate atomically before changing memory/UI. Test round trip, failed write and delete/edit cases in scratch.
3. `src/ui.rs`: attach @prefix Tab completion to done-page speaker fields using only typed attendees. A direct selection sets the plain canonical name (not `@name`), leaves applying via existing merge-confirmation path, and does not intercept ordinary Tab without a match.
4. `src/ui.rs`: interleave timestamped note cards with transcript paragraphs/chapters. Notes get distinct visual treatment, seek-on-click, and Edit/Delete controls with a delete confirmation. Edits/deletes update context.json first, then recording log, done count and timeline; leave transcript.md untouched. Ensure paragraph aggregation does not hide a note inside a long speech turn.
5. Synthetic fixtures only: Rust tests, Python action tests, clippy, release Vulkan build, GTK runtime screenshots/interaction where safe. Check complete pathway and clean diff; local commit only, no push or real meeting mutations.
