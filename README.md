# Grok CAD Agent

A native **FreeCAD 1.0 / 1.1+ workbench** that turns the modeler into an agentic mechanical-design environment powered by [xAI Grok](https://x.ai/).

You speak natural language. Grok plans, calls tools, writes PartDesign Python, reads the feature tree, looks at high-resolution viewport screenshots, and iterates until the part is right. Everything lives inside FreeCAD — no Electron app, no extra windowing toolkit.

Display name in the workbench selector: **Grok CAD Agent**.

---

## What you get

- Dockable dark-themed chat with markdown, code blocks, tool-call cards, and screenshot previews
- Streaming replies from Grok (`grok-4.6` preferred, graceful fallback)
- Full OpenAI-style tool calling against a live document
- Vision: isometric / front / top / right / … captures sent as high-detail PNGs
- Human-in-the-loop modes, from “plan only” to “full auto”
- Undo-stack-friendly `execute_python` with a static safety scan and a confirmation dialog
- Preferences page for the API key (never hardcoded)
- Fedora-first installer → `~/.local/share/FreeCAD/Mod/GrokCAD/`

FreeCAD itself works fully offline. The only network dependency is `https://api.x.ai/v1`.

---

## Architecture

```
User ──► Chat dock (PySide, GUI thread)
            │
            ├─ Conversation (system prompt + history + tool results)
            │
            ▼
       GrokWorker (QThread) ── HTTP ──►  https://api.x.ai/v1
            │                              model: grok-4.6
            ▼
       tool_calls (JSON)
            │
            ▼
       tools.execute_tool  (GUI thread — FreeCAD is not thread-safe)
            ├─ execute_python   (transaction + restricted import)
            ├─ get_document_state / inspect_object / get_selection
            ├─ take_screenshot  (Coin saveImage, multi-view)
            ├─ recompute / undo / redo
            ├─ documents + STEP/STL export
            └─ run_fem_analysis (CalculiX, if FEM + ccx exist)
            │
            ▼
       tool results (+ images) ──► next Grok turn ──► until done
```

Key rule: **FreeCAD API only on the GUI thread**. The worker never touches `App` / `Gui`.

---

## Installing on Fedora

Tested target: **Fedora 41 / 42+**, GNOME or KDE, **Wayland preferred** (X11 also fine). Uses the official `dnf` FreeCAD package (system Python 3.12+), not Flatpak.

### One command

From this directory:

```bash
chmod +x install_fedora.sh
./install_fedora.sh
```

If FreeCAD is not installed yet:

```bash
sudo dnf install -y freecad python3-pip python3-pillow
./install_fedora.sh
```

or, as a single privileged step that also installs FreeCAD:

```bash
sudo ./install_fedora.sh --system-freecad
```

The script:

1. Checks that `freecad` is on `PATH`
2. `pip install --user openai pillow requests`
3. Copies the workbench to `~/.local/share/FreeCAD/Mod/GrokCAD/`
4. Leaves Addon Manager metadata (`package.xml`) in place

### Manual install

```bash
sudo dnf install -y freecad python3-pip python3-pillow
python3 -m pip install --user --upgrade openai pillow requests
mkdir -p ~/.local/share/FreeCAD/Mod
cp -a GrokCAD ~/.local/share/FreeCAD/Mod/GrokCAD
```

Optional FEM extras (only if you want `run_fem_analysis` to actually solve):

```bash
sudo dnf install -y CalculiX gmsh
```

### Uninstall

```bash
rm -rf ~/.local/share/FreeCAD/Mod/GrokCAD
# optional: pip uninstall openai
```

### Flatpak FreeCAD (Flathub `org.freecad.FreeCAD`)

FreeCAD **1.1** does **not** load addons from `…/data/FreeCAD/Mod`. It uses a versioned folder. On 1.1.3 that is:

```text
~/.var/app/org.freecad.FreeCAD/data/FreeCAD/v1-1/Mod/
```

Confirm inside FreeCAD (View → Panels → Python console):

```python
import FreeCAD, os
print(os.path.join(FreeCAD.getUserAppDataDir(), "Mod"))
```

Install (copy, do not symlink out of the sandbox):

```bash
SRC="$HOME/Downloads/GrokCAD"   # or wherever the workbench tree is
DEST="$HOME/.var/app/org.freecad.FreeCAD/data/FreeCAD/v1-1/Mod/GrokCAD"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -a "$SRC"/. "$DEST"/
# InitGui.py must sit directly in DEST, not DEST/GrokCAD/
ls "$DEST/InitGui.py"

flatpak override --user --share=network org.freecad.FreeCAD
flatpak run --share=network --command=python3 org.freecad.FreeCAD \
  -m pip install --user openai pillow
flatpak run org.freecad.FreeCAD
```

Or from the workbench tree: `./install_fedora.sh --flatpak --skip-pip`

If **Grok CAD Agent** still does not appear, enable it under **Edit → Preferences → Workbenches**.

---

## First-run checklist

1. Start FreeCAD (`freecad`). Confirm **Help → About** reports 1.0 or newer.
2. Workbench selector → **Grok CAD Agent**. The chat dock opens on the right.
3. **Edit → Preferences → Grok CAD Agent**
   - Paste an xAI API key from [console.x.ai](https://console.x.ai/)
   - Or skip the field and `export XAI_API_KEY=xai-...` before launching FreeCAD
   - Preferred model: `grok-4.6` (falls back if the API does not list it)
4. Click **Models** in the chat toolbar once, to refresh the live model list.
5. Mode: start with **Auto-run safe tools, approve code**.
6. Type a request, or click **📎** / drop a PNG, JPG, or PDF drawing into the composer. **Ctrl+Enter** to send.
7. When a Python card appears, read it, click **Approve** (or **Approve pending** on the toolbar).
8. Watch the viewport. If Grok asks for a screenshot or you want a second opinion, click **Screenshot**.

If the chat says it cannot import `openai`, re-run `python3 -m pip install --user openai` and restart FreeCAD. `Init.py` adds the user site-packages path automatically.

---

## Using the agent

### Chat dock

| Control | What it does |
|---|---|
| Model / Temp / Max tokens | Per-session overrides (persisted to prefs) |
| Mode | Plan only · approve every tool · auto-run safe + approve code · full auto |
| Attach view | Send an isometric screenshot with the next message |
| New session | Wipe history, keep prefs |
| Clear chat | Wipe history, keep the system prompt |
| Export | Markdown or JSON transcript (API key never written) |
| Screenshot | Capture Iso + Front + Top + Right and queue them |
| Send / Stop | Ctrl+Enter / Esc |
| Approve pending | Run every waiting tool card |

Keyboard: **G then C** opens chat, **G then S** screenshots, **G then A** approves.

You can switch to PartDesign / Sketcher / Assembly while the dock stays open. The workbench does not steal the 3D view.

### Agent modes

| Mode | Reads / screenshots | `execute_python` | Dangerous patterns (`os`, `subprocess`, …) |
|---|---|---|---|
| Plan only | not executed | not executed | — |
| Approve every tool | ask | ask | ask |
| Auto-run safe, approve code *(default)* | auto | ask | ask |
| Full auto (expert) | auto | auto | still asks |

Failed Python is never fatal: the traceback is returned to Grok and it retries.

### Tools Grok can call

`execute_python`, `get_document_state`, `inspect_object`, `take_screenshot`, `set_camera_view`, `recompute`, `undo`, `redo`, `create_new_document`, `list_open_documents`, `set_active_document`, `export_step`, `export_stl`, `get_selection`, `search_objects`, `get_workbench_info`, `run_fem_analysis`.

`execute_python` runs in a transaction (`doc.openTransaction` / `commit` / `abort`) so **Edit → Undo** is a real safety net.

---

## Example conversations

These are realistic sessions, condensed. Your live run will include extra `get_document_state` / screenshot turns.

### 1. Design a 6-axis robot base joint (Adam-style)

**You**

> Design a robot base joint like the Adam demo: a compact revolute shoulder for a 6-axis arm. Cast-style housing, 80 mm through-bore for a harmonic drive, four M6 PCB holes on a 110 mm PCD for the motor, a 160 mm square mounting flange 16 mm thick with 4× Ø9 mm holes on a 130 mm square, and a 40 mm tall cylindrical body. Fillet the outside 3 mm. Parametric, PartDesign, named dimensions.

**Grok** *(plans, then tools)*

1. `create_new_document` → `RobotJoint`
2. `execute_python` — `Vars` spreadsheet: `Bore=80`, `PCD=110`, `Flange=160`, `Thick=16`, `BodyH=40`, `Fillet=3`
3. `execute_python` — `Body_Joint`: sketch on XY, square flange, pad `Thick`; pocket through-bore; pad cylindrical body; pocket motor PCD; pocket flange holes; fillet
4. `take_screenshot` views `Isometric,Top,Front`
5. Looks at the images, reverses a pad that grew the wrong way, recomputes
6. Reports: *Body_Joint / Pad_Flange / Pad_Body / Pocket_Bore / Pocket_MotorPCD / Pocket_FlangeHoles / Fillet_Outer. Edit `Vars.Bore` to retarget a different harmonic.*

**You**

> Drop the bore to 70 mm and add a 2 mm × 2 mm retaining-ring groove 8 mm down from the top of the bore.

Grok edits `Vars.Bore`, adds `Sketch_Groove` on XZ, revolution-pocket, screenshots, done.

### 2. Add mounting holes and fillets to this bracket

*(A PartDesign L-bracket is already on screen. You select `Pad_Vertical`.)*

**You**

> Add four M5 clearance holes through the base flange, 8 mm from each edge, and a 2 mm fillet on every outside sharp edge. Don’t touch the ribs.

**Grok**

1. `get_selection` → `Pad_Vertical`, Face5
2. `get_document_state` / `inspect_object` `Body_Bracket`
3. `execute_python` — new `Sketch_M5` mapped to the base face, four Ø5.5 circles, `Pocket_M5` through-all
4. `execute_python` — `Fillet_Outer` on the outer edges discovered from `Shape.Edges` (skips rib roots by length / adjacency)
5. `take_screenshot` → notices one fillet failed on a short edge, retries with a smaller radius on that edge only
6. Summary with the hole pattern and the fillet radii so you can edit them

### 3. Turn this mesh into a parametric PartDesign model

*(You imported `widget_scan.stl`. It is a roughly 42 × 28 × 12 mm rectangular housing with a 6 mm corner radius and two Ø4 holes.)*

**You**

> Rebuild this mesh as a parametric PartDesign part. Measure it first. I need to change the length later.

**Grok**

1. `inspect_object` `widget_scan` → bounding box 42.14 × 27.91 × 12.05, 2 holes spotted in a screenshot
2. `execute_python` — `Vars.Length=42`, `Vars.Width=28`, `Vars.Height=12`, `Vars.CornerR=6`, `Vars.Hole=4`, `Vars.HoleInset=8`
3. `execute_python` — new `Body_Housing`, fully constrained rounded-rectangle sketch, pad, two holes, hide the mesh
4. `take_screenshot` overlay comparison (mesh visible at 50 %, body solid)
5. Tweaks `Vars.Length` to 50 as a demo, recomputes, restores 42
6. Tells you: *change `Vars.Length` in the spreadsheet; do not scale the mesh.*

---

## Project layout

```
GrokCAD/
├── Init.py                 # console load; adds user site-packages
├── InitGui.py              # workbench class, toolbar, prefs page
├── commands.py             # FreeCAD commands
├── package.xml             # Addon Manager metadata
├── metadata.txt
├── LICENSE                 # MIT
├── README.md
├── install_fedora.sh
├── prompts/
│   └── system_prompt.txt   # mechanical-design system prompt
├── core/
│   ├── grok_client.py      # OpenAI-compatible client + QThread worker
│   ├── tools.py            # tool schemas + implementations
│   ├── screenshot.py       # multi-view PNG capture
│   ├── document_state.py   # feature tree / topology / selection
│   ├── safety.py           # scan + restricted exec + confirm dialog
│   ├── prefs.py            # FreeCAD parameter group
│   ├── qtcompat.py         # PySide / PySide6 / PySide2
│   ├── paths.py
│   └── log.py              # Report View + ring buffer
├── ui/
│   ├── GrokChatPanel.py    # dock widget + agent loop
│   ├── ChatMessage.py      # bubbles, tool cards, thumbs
│   ├── PreferencesPage.py
│   └── markdownutil.py
└── resources/
    ├── icons/              # GrokCAD.svg + toolbar icons
    └── styles/chat.qss
```

---

## Preferences (stored in FreeCAD)

`User parameter:BaseApp/Preferences/Mod/GrokCAD`

| Key | Default | Notes |
|---|---|---|
| `ApiKey` | *(empty)* | Falls back to `$XAI_API_KEY` or `$GROK_API_KEY` |
| `BaseUrl` | `https://api.x.ai/v1` | |
| `Model` | `grok-4.6` | |
| `Temperature` | `0.2` | |
| `MaxTokens` | `8192` | |
| `AgentMode` | `approve_code` | |
| `ScreenshotWidth/Height` | `1600×1200` | |
| `AutoScreenshotAfterCode` | `true` | |
| `MaxToolIterations` | `16` | anti-runaway |

The key is stored the same way FreeCAD stores other secrets: in the user parameter file. It is **not encrypted**. Do not export that file to a public gist.

---

## Safety model

This is a **human-in-the-loop CAD copilot**, not a hostile-code sandbox.

- Static AST / regex scan flags `os.system`, `subprocess`, `socket`, `ctypes`, `eval`, `exec`, arbitrary `open("/home/…")`, etc.
- Importer inside `execute_python` only allows FreeCAD modules and a short stdlib allow-list (`math`, `json`, …).
- Default mode still pops a confirmation dialog for every Python script.
- Every script is one undo transaction. **Ctrl+Z** is the big red button.
- `Full auto` still confirms scripts that trip the danger scan.

Do not point this at a document you have not saved, and do not paste an API key into the chat.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Workbench missing | Put files in `App.getUserAppDataDir()+"Mod"` (FreeCAD 1.1 Flatpak: `…/FreeCAD/v1-1/Mod/GrokCAD/`). If Report view says `name '__file__' is not defined`, update to a GrokCAD build whose `Init.py` / `InitGui.py` do not use `__file__` at load time, then restart. Also check **Preferences → Workbenches**. |
| `openai` import error | `python3 -m pip install --user openai` then restart FreeCAD. |
| 401 from xAI | Preferences key vs `$XAI_API_KEY`. Create a key at console.x.ai. |
| Model 404 | Click **Models**, pick a listed `grok-*`. |
| Black / empty screenshots | Open a document, click in the 3D view once, retry. Try Preferences → screenshot background **White**. |
| Wayland grab looks wrong | The primary path is Coin `saveImage` (offscreen), not a window grab. Update FreeCAD if `saveImage` fails. |
| FEM tool fails | `sudo dnf install CalculiX gmsh`. Add constraints/loads before `run_solver=true`. |

Logs go to **View → Panels → Report view**, prefixed `[GrokCAD]`.

---

## License

MIT — see [LICENSE](LICENSE).

Grok and xAI are trademarks of xAI. FreeCAD is LGPLv2+. This workbench is an independent addon.
