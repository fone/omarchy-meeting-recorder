//! Live meeting context: typed attendees, glossary terms, and timestamped notes.
//!
//! Persisted as `context.json` alongside recording staging and in the final
//! meeting folder. The file is versioned (currently v1) and atomically written
//! through a temp file + fsync + rename. Missing files return sensible defaults;
//! corrupt files are reported via `eprintln!` and fall back to defaults.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use serde_json::Value;

/// The sidecar file name inside a meeting directory.
pub const CONTEXT_FILE: &str = "context.json";

/// A single timestamped note committed during a recording.
#[derive(Debug, Clone, PartialEq)]
pub struct LiveNote {
    /// Seconds from recording start.
    pub offset_s: i64,
    /// The note text.
    pub text: String,
}

/// Typed meeting context: who is here, domain vocabulary, and live notes.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct MeetingContext {
    /// Comma-separated roster of attendees, typed by the user.
    pub attendees: String,
    /// Comma-separated domain vocabulary for ASR initial prompt.
    pub glossary: String,
    /// Timestamped notes committed during the recording.
    pub notes: Vec<LiveNote>,
}

impl MeetingContext {
    /// Seed Whisper with tricky terms and typed attendee names, without
    /// treating detected speaker labels as identities.
    pub fn transcription_terms(&self) -> String {
        prompt_glossary(&format!("{}, {}", self.glossary, self.attendees))
    }

    fn to_json(&self) -> Value {
        serde_json::json!({
            "version": 1,
            "attendees": self.attendees,
            "glossary": self.glossary,
            "notes": self.notes.iter().map(|n| serde_json::json!({
                "offset_s": n.offset_s,
                "text": n.text,
            })).collect::<Vec<_>>(),
        })
    }

    fn from_json(value: &Value) -> Option<Self> {
        let version = value.get("version")?.as_u64()?;
        if version != 1 {
            return None;
        }
        let attendees = value
            .get("attendees")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_owned();
        let glossary = value
            .get("glossary")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_owned();
        let notes = value
            .get("notes")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|n| {
                        Some(LiveNote {
                            offset_s: n.get("offset_s")?.as_i64()?,
                            text: n.get("text")?.as_str()?.to_owned(),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        Some(MeetingContext {
            attendees,
            glossary,
            notes,
        })
    }
}

/// Read the context from `dir/context.json`. Returns `Default` if the file is
/// absent or cannot be parsed (logs the error to stderr).
pub fn read(dir: &Path) -> MeetingContext {
    let path = dir.join(CONTEXT_FILE);
    let text = match fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return MeetingContext::default(),
        Err(e) => {
            eprintln!(
                "{}: context: could not read {}: {e}",
                crate::APP_NAME,
                path.display()
            );
            return MeetingContext::default();
        }
    };
    let value: Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(e) => {
            eprintln!(
                "{}: context: could not parse {}: {e}",
                crate::APP_NAME,
                path.display()
            );
            return MeetingContext::default();
        }
    };
    match MeetingContext::from_json(&value) {
        Some(ctx) => ctx,
        None => {
            eprintln!(
                "{}: context: invalid schema in {}",
                crate::APP_NAME,
                path.display()
            );
            MeetingContext::default()
        }
    }
}

/// Atomically write the context to `dir/context.json` (temp + fsync + rename).
pub fn write(dir: &Path, ctx: &MeetingContext) -> std::io::Result<()> {
    fs::create_dir_all(dir)?;
    let target = dir.join(CONTEXT_FILE);
    let text = serde_json::to_string_pretty(&ctx.to_json())?;
    // Write to a temp file in the same directory so rename is atomic on the
    // same filesystem.
    let mut part = target.as_os_str().to_owned();
    part.push(".tmp");
    let part = PathBuf::from(part);
    {
        let mut f = fs::File::create(&part)?;
        f.write_all(text.as_bytes())?;
        f.write_all(b"\n")?;
        f.sync_all()?;
    }
    fs::rename(&part, &target)?;
    Ok(())
}

/// Copy `context.json` from one directory to another. Missing source is `Ok(())`
/// (legacy meetings without a context file).
pub fn copy(from: &Path, to: &Path) -> std::io::Result<()> {
    let src = from.join(CONTEXT_FILE);
    if !src.exists() {
        return Ok(());
    }
    fs::copy(&src, to.join(CONTEXT_FILE)).map(|_| ())
}

/// Complete an `@name` prefix against the typed attendee roster.
///
/// `attendees` is a comma-separated list. Returns the first name whose
/// trimmed, case-insensitive form starts with `prefix` (also trimmed, case-
/// insensitive). Returns `None` if the prefix is empty or matches nothing.
pub fn complete_name(attendees: &str, prefix: &str) -> Option<String> {
    let prefix = prefix.trim().to_lowercase();
    if prefix.is_empty() {
        return None;
    }
    attendees
        .split(',')
        .map(str::trim)
        .find(|name| name.to_lowercase().starts_with(&prefix) && !name.is_empty())
        .map(str::to_owned)
}

/// Format glossary terms for whisper's initial prompt.
///
/// Joins comma- or newline-separated terms into a single prompt-friendly
/// line. The result is capped at 500 bytes to avoid hallucination from an
/// excessively long prompt; terms that would exceed the cap are dropped.
pub fn prompt_glossary(glossary: &str) -> String {
    const MAX_BYTES: usize = 500;
    let terms: Vec<&str> = glossary
        .split([',', '\n'])
        .map(str::trim)
        .filter(|t| !t.is_empty())
        .collect();
    let mut out = String::new();
    for (i, term) in terms.iter().enumerate() {
        let separator = if i == 0 { "" } else { ", " };
        let candidate = format!("{out}{separator}{term}");
        if candidate.len() > MAX_BYTES {
            break;
        }
        out = candidate;
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmpdir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join("omarchy-context-test").join(name);
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn round_trip_empty() {
        let dir = tmpdir("round_trip_empty");
        let ctx = MeetingContext::default();
        write(&dir, &ctx).unwrap();
        let loaded = read(&dir);
        assert_eq!(loaded, ctx);
    }

    #[test]
    fn round_trip_full() {
        let dir = tmpdir("round_trip_full");
        let ctx = MeetingContext {
            attendees: "Adam, Russ, Pat".into(),
            glossary: "PipeWire, EasyEffects".into(),
            notes: vec![
                LiveNote {
                    offset_s: 42,
                    text: "ACTION: Fix the meter".into(),
                },
                LiveNote {
                    offset_s: 120,
                    text: "QUESTION: Release date?".into(),
                },
            ],
        };
        write(&dir, &ctx).unwrap();
        let loaded = read(&dir);
        assert_eq!(loaded, ctx);
    }

    #[test]
    fn read_absent_returns_default() {
        let dir = tmpdir("read_absent");
        let ctx = read(&dir);
        assert_eq!(ctx, MeetingContext::default());
    }

    #[test]
    fn read_corrupt_returns_default() {
        let dir = tmpdir("read_corrupt");
        fs::write(dir.join(CONTEXT_FILE), "{not valid json").unwrap();
        let ctx = read(&dir);
        assert_eq!(ctx, MeetingContext::default());
    }

    #[test]
    fn read_wrong_version_returns_default() {
        let dir = tmpdir("read_wrong_version");
        fs::write(
            dir.join(CONTEXT_FILE),
            r#"{"version": 99, "attendees": "x"}"#,
        )
        .unwrap();
        let ctx = read(&dir);
        assert_eq!(ctx, MeetingContext::default());
    }

    #[test]
    fn copy_missing_source_is_ok() {
        let from = tmpdir("copy_from");
        let to = tmpdir("copy_to");
        assert!(copy(&from, &to).is_ok());
        assert!(!to.join(CONTEXT_FILE).exists());
    }

    #[test]
    fn copy_reports_missing_destination_instead_of_silently_dropping_context() {
        let from = tmpdir("copy_from_missing_destination");
        let dest = from.join("does-not-exist");
        write(
            &from,
            &MeetingContext {
                attendees: "Adam".into(),
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(
            copy(&from, &dest).unwrap_err().kind(),
            std::io::ErrorKind::NotFound
        );
    }

    #[test]
    fn copy_round_trip() {
        let from = tmpdir("copy_from_rt");
        let to = tmpdir("copy_to_rt");
        let ctx = MeetingContext {
            attendees: "Adam".into(),
            glossary: "Rust".into(),
            notes: vec![LiveNote {
                offset_s: 10,
                text: "hi".into(),
            }],
        };
        write(&from, &ctx).unwrap();
        copy(&from, &to).unwrap();
        let loaded = read(&to);
        assert_eq!(loaded, ctx);
    }

    #[test]
    fn complete_name_basic() {
        let roster = "Adam, Russ, Pat";
        assert_eq!(complete_name(roster, "A"), Some("Adam".into()));
        assert_eq!(complete_name(roster, "r"), Some("Russ".into()));
        assert_eq!(complete_name(roster, "pat"), Some("Pat".into()));
        assert_eq!(
            complete_name("Adam Potter, Russ", "Adam P"),
            Some("Adam Potter".into())
        );
        assert_eq!(complete_name(roster, "z"), None);
        assert_eq!(complete_name(roster, ""), None);
    }

    #[test]
    fn complete_name_whitespace() {
        assert_eq!(
            complete_name("  Adam , Russ  ", "  a  "),
            Some("Adam".into())
        );
    }

    #[test]
    fn complete_name_first_match() {
        assert_eq!(
            complete_name("Adam, Alex, Alice", "Al"),
            Some("Alex".into())
        );
    }

    #[test]
    fn transcription_terms_include_roster_and_vocabulary() {
        let ctx = MeetingContext {
            attendees: "Adam, Russ".into(),
            glossary: "QRadar, QNAP".into(),
            ..Default::default()
        };
        assert_eq!(ctx.transcription_terms(), "QRadar, QNAP, Adam, Russ");
        assert_eq!(MeetingContext::default().transcription_terms(), "");
    }

    #[test]
    fn prompt_glossary_empty() {
        assert_eq!(prompt_glossary(""), "");
        assert_eq!(prompt_glossary("  ,  "), "");
    }

    #[test]
    fn prompt_glossary_joins_terms() {
        assert_eq!(
            prompt_glossary("PipeWire, EasyEffects"),
            "PipeWire, EasyEffects"
        );
        assert_eq!(prompt_glossary("Rust\nWhisper\nGTK"), "Rust, Whisper, GTK");
    }

    #[test]
    fn prompt_glossary_caps_length() {
        let long = (0..100)
            .map(|i| format!("term_{i:04}"))
            .collect::<Vec<_>>()
            .join(", ");
        let result = prompt_glossary(&long);
        assert!(result.len() <= 500);
        assert!(result.len() > 400); // should have room for several terms
    }
}
