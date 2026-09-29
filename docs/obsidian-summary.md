# Summarize a meeting to Obsidian

This is an **opt-in action in this fork**, not a feature of the original Meeting Recorder. It was inspired by a separate Meeting Notes application. Meeting Recorder captures and transcribes locally first. Only when you choose **Actions → Summarize to Obsidian** does `actions/summarize-to-obsidian` send transcript text to the model endpoint you configure. It saves a structured note in your local Obsidian vault. **The full transcript is not copied into that note**; it remains in the recording folder. The separate **Store transcript in Obsidian** action has different behavior.

This uses Python 3's standard library, with no Python packages or Obsidian plugin required. The provider must expose an OpenAI-compatible `/chat/completions` API. You need an account and API key for a cloud provider (which may charge you), or a running local Ollama server. Review the provider's privacy and retention terms before sending private meetings.

## 1. Install this fork

Use the [source-build instructions in the README](../README.md#install-this-fork). On Omarchy/Arch, make sure the build/runtime dependencies are present first:

```bash
sudo pacman -S --needed base-devel rust cmake shaderc vulkan-headers vulkan-icd-loader gtk4 libadwaita libpulse ffmpeg pkgconf clang python
```

The Omarchy package, this repository's inherited `packaging/aur/PKGBUILD`, and releases under the original author's GitHub account install the upstream recorder, **not** these changes. This fork's `install.sh` stops with a pointer to these instructions instead of silently installing the wrong binary. The release build of this fork must be on your `PATH` (do not assume a same-version upstream package is the right binary):

```bash
command -v omarchy-meeting-recorder
readlink -f "$(command -v omarchy-meeting-recorder)"
omarchy-meeting-recorder --version
```

The resolved executable should point into this clone's `target/release/` directory.

The first transcription may download a local whisper model. Open the recorder and verify microphone and computer-audio meters before the meeting. After stopping, wait for transcription to finish and open the meeting's done page.

## 2. Configure the model without committing a key

The action reads `~/.config/meeting-recorder-obsidian/config.json`. Create that directory and file **outside the Git checkout**. For Ollama Cloud with Kimi, the JSON has this shape (replace `<your-key>` locally):

```json
{
  "provider": "ollama_cloud",
  "model": "kimi-k2.6",
  "api_key": "<your-key>",
  "base_url": "https://ollama.com/v1"
}
```

Save the JSON above as `~/.config/meeting-recorder-obsidian/config.json` after creating the directory. Then keep the file private:

```bash
mkdir -p "$HOME/.config/meeting-recorder-obsidian"
chmod 700 "$HOME/.config/meeting-recorder-obsidian"
# After saving config.json with your real key:
chmod 600 "$HOME/.config/meeting-recorder-obsidian/config.json"
```

You can instead use `provider: "openai"` with `https://api.openai.com/v1`, `provider: "openrouter"` with `https://openrouter.ai/api/v1`, or `provider: "ollama"` with a local server at `http://localhost:11434/v1` and an exact installed model name. Change `model` to one your selected endpoint actually offers. For local Ollama, `api_key` may be empty; the client supplies Ollama's dummy token. The script supports `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, and `LLM_BASE_URL` environment overrides. For Ollama Cloud, OpenRouter, and OpenAI it can also fall back to `OLLAMA_API_KEY`, `OPENROUTER_API_KEY`, and `OPENAI_API_KEY` respectively. A GUI-launched app may not inherit variables from your interactive shell, so the private JSON file is the most reliable setup. Never put the key in the `[[action]]` command, source file, screenshots, issue reports, or this repository.

### Why thinking must be off for Kimi

Some reasoning models consume the entire response token budget in **hidden thinking**. The endpoint can return HTTP 200 with `finish_reason: "length"` but no visible `message.content`; an empty or partial response cannot make a useful summary. For `provider: "ollama_cloud"`, this action explicitly sends the top-level OpenAI-compatible request field `"thinking": false` alongside `model`, `messages`, and `max_tokens`. You do not have to set an extra config switch. The action allows 12,288 output tokens by default (16,384 for long prompts) and retries an empty/truncated first result at 16,384. If visible output is still empty or truncated, it fails instead of claiming to have saved a complete note. Some providers may ignore the flag; if the retry fails, use another model or endpoint rather than repeatedly submitting the meeting. This toggle is implemented for the Ollama Cloud adapter, not promised for every OpenAI-compatible provider.

## 3. Add the click action

Edit `~/.config/omarchy-meeting-recorder/config.toml` and **append** this table. Do not replace existing settings or actions. Substitute your actual checkout location for `/path/to/omarchy-meeting-recorder`; the action script is executable in this repository.

```toml
[[action]]
name = "Summarize to Obsidian"
command = "OBSIDIAN_VAULT=$HOME/Documents/Obsidian/MyVault /path/to/omarchy-meeting-recorder/actions/summarize-to-obsidian"
```

Set `OBSIDIAN_VAULT` to the **existing absolute root** of your vault, not its `Meetings` folder. The action refuses to run if it is missing or points somewhere that does not exist, so it will not silently create a new vault. It creates `<vault>/Meetings/` by default; override the subfolder with `OBSIDIAN_FOLDER` if desired. The menu reads actions from the TOML configuration when opened.

**Do not add the separate “Store transcript in Obsidian” action unless you want full transcripts in the vault.** Summarization alone does not invoke it.

## 4. Put status in the taskbar

From the repository root, after building the binary:

```bash
mkdir -p "$HOME/.config/omarchy/plugins/jankeesvw.meeting-recorder"
cp plugin/manifest.json plugin/StatusWidget.qml "$HOME/.config/omarchy/plugins/jankeesvw.meeting-recorder/"
omarchy plugin validate "$HOME/.config/omarchy/plugins/jankeesvw.meeting-recorder"
omarchy-shell shell rescanPlugins
omarchy plugin enable jankeesvw.meeting-recorder --section right
```

The plugin folder must be a **real directory**, not a symlink; Omarchy's validator rejects folder symlinks. If an old symlink is already at that path, move it aside yourself before creating the directory. If the plugin was already enabled, the last command is optional. Plugin discovery is asynchronous; if enable says it cannot find the plugin, wait for `omarchy-shell shell listPlugins` to show it, then retry. When updating this fork, rebuild the binary, recopy `manifest.json` and `StatusWidget.qml`, rescan the shell, and restart any old recorder process. The bar is hidden when the recorder is idle. While the action runs it shows `summarizing MM:SS`, then `saving note…` after the model returns, followed by `summary saved` or `summary failed` for one minute. A click opens the recorder for details.

## 5. Run and check

1. Open a transcribed meeting in this fork's recorder. Select **Actions → Summarize to Obsidian**.
2. While the model runs, watch the elapsed timer in the bar. It is **not** a fabricated percentage of model work.
3. Wait for `summary saved` and the result toast. The `Meetings/` note should contain the summary, key points, actions, decisions, questions, metadata and a link back to the local recording, but no `## Transcript` section.
4. Confirm `transcript.md` remains in the meeting folder. The note's `daily_note` property references a `Daily/YYYY/MM-Month/...` path; this reference is optional and harmless if your vault does not use that layout.

You can also run an action from the CLI after a meeting exists:

```bash
omarchy-meeting-recorder action "Summarize to Obsidian" "/path/to/your/meeting-folder"
```

**This CLI invocation sends the real transcript to the configured model and writes a real vault note.** Use the offline unit test instead if you only want a safe configuration-independent smoke check:

```bash
python -m unittest discover -s tests -v
cargo test --quiet
```

The unit test uses a fake model and a temporary vault; it does not check your API credential. A full real-provider check requires your own deliberate action click.


## 5. Typed meeting context (context.json)

Before starting, enter a comma-separated **Attendees** roster and any tricky
names or technical terms under **Meeting context**. You can edit both while
recording. Terms and attendee names seed Whisper's initial prompt when the
recording is transcribed. During the meeting, type in the fixed **Live notes**
field: `@Ru` then Tab completes a name from your typed roster; Enter saves a
note with its recording timestamp. Saved notes are visible in the meeting
folder and via **View live notes** on the finished screen. These fields are
per-meeting and never inferred from detected speaker labels.

When a `context.json` sidecar exists in the meeting folder, the action reads
it automatically and passes the typed context into the model prompt as
separate XML blocks:

| Block | Source | Purpose |
|---|---|---|
| `<attendees>` | Typed roster | Ground-truth attendance — used for name spelling, people field |
| `<glossary>` | Typed terms | Proper-noun corrections for transcription errors |
| `<user_notes>` | Timestamped live notes | Authoritative for item existence and ownership |
| `<transcript>` | Always present | Machine speech transcription (least reliable for names) |

The `context.json` schema (version 1):

```json
{
  "version": 1,
  "attendees": "Adam, Russ",
  "glossary": "FileBound, QRadar",
  "notes": [
    {"offset_s": 12, "text": "@Russ follow up"}
  ]
}
```

**Key rules:**

- **Typed roster is attendance truth.** Detected speaker labels (`MEETING_SPEAKERS`) are never treated as attendees — voice detection is not ground truth for who was present.
- **Typed title takes precedence.** If `MEETING_TITLE` is not blank or auto-generated (`Meeting HH:MM`), it is used as the note title instead of the model-generated title.
- **Live notes appear in the Obsidian note.** Timestamped notes are rendered under `## Live Notes`; raw transcript lines are never copied into the vault note.
- **Legacy fallback.** Without `context.json`, the action behaves as before: no glossary/user_notes blocks, speakers used for the people field with generic labels filtered out.

## Troubleshooting

| Symptom | Check |
|---|---|
| No action in menu | Correct TOML table and command path in `~/.config/omarchy-meeting-recorder/config.toml`; reopen the Actions menu. |
| “No API key” | Confirm private JSON `api_key`, provider name, file permissions, or a correctly inherited environment variable. Do not post the key in a bug report. |
| Empty or incomplete response | With Ollama Cloud, confirm `provider: "ollama_cloud"` so the request includes `"thinking": false`. Kimi can still ignore it. Try a different model if the guarded retry fails. |
| Summary saved somewhere unexpected | `OBSIDIAN_VAULT` must point to your existing vault root. The action fails rather than using a personal default. |
| Bar absent but note saved | Confirm the release binary is this fork, the installed plugin folder is not a symlink, `omarchy plugin validate` passes, plugin is enabled, and the shell was rescanned. The original package's widget cannot display these summary states. |
| `summary failed` | Click the bar or open the recorder to see the action error. The raw transcript remains local for retry. |

This fork makes no claim that the provider will never retain submitted transcript text. Choose a local Ollama endpoint if the transcript must not leave your machine.
