# Release checklist

obd-mcp ships through three surfaces, in this order: **PyPI** (source of
truth for the Python package), then **Smithery** and **mcp.so** (registry
index entries that point at the PyPI package).

## 0. Known blocker before 1.0 on PyPI

`pyproject.toml` currently pins python-OBD via a direct git URL
(`obd @ git+https://github.com/brendan-w/python-OBD.git@a378bdd8…`) —
PyPI rejects uploads that carry direct URL dependencies. Upload will fail
with `400 Bad Request: Invalid requirement: '…; direct URLs are not
allowed'.`

Two paths to unblock:

1. **Wait for brendan-w/python-OBD to cut a PyPI release** carrying the
   commit we're pinned to (`a378bdd8…`). Track
   <https://github.com/brendan-w/python-OBD/releases>. Then change the
   dependency to `obd>=0.7.X` in `pyproject.toml`.
2. **Vendor python-OBD** under `third_party/obd/` and drop the dependency.
   See `docs/DECISIONS.md` (the pin-vs-vendor entry); this is already
   the documented fallback.

Do not attempt PyPI publication until one of these is resolved. Smithery
and mcp.so listings can still be prepared behind the scenes — they won't
be published until there's a working PyPI package for them to point at.

## 1. Pre-flight

```bash
# Clean tree
git status                      # must be clean
git pull --ff-only              # up to date with main

# Quality gates
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest                   # all tests green

# Bump version if needed
# pyproject.toml: [project] version = "X.Y.Z"
# Semver: breaking tool-surface change → major, additive → minor, fix → patch.
```

Tag the release commit:

```bash
git commit -am "release: vX.Y.Z"
git tag -a vX.Y.Z -m "obd-mcp X.Y.Z"
git push origin main --tags
```

## 2. Build

```bash
rm -rf dist/
uv build                        # produces sdist + wheel in dist/
```

Verify the wheel includes vendored data:

```bash
python -c "import zipfile; z=zipfile.ZipFile('dist/obd_mcp-X.Y.Z-py3-none-any.whl'); [print(n) for n in z.namelist() if 'data' in n]"
# Expect: dtc.sqlite, dtc.sqlite.LICENSE, obdb/LICENSE, obdb/ford/mustang.json, obdb/ford/f-150.json
```

Sanity-install the wheel in a throwaway venv:

```bash
uv venv /tmp/obd-mcp-release-test
/tmp/obd-mcp-release-test/bin/pip install dist/obd_mcp-X.Y.Z-py3-none-any.whl
/tmp/obd-mcp-release-test/bin/obd-mcp &    # should start, read default OBD_PORT
kill %1
rm -rf /tmp/obd-mcp-release-test
```

## 3. PyPI

One-time setup (per machine):

```bash
# Create an API token at https://pypi.org/manage/account/token/
# Scope: "Entire account" for first release, then scope down to "Project: obd-mcp" for subsequent releases.
# Store in ~/.pypirc:
cat > ~/.pypirc <<'EOF'
[pypi]
  username = __token__
  password = pypi-<token>
EOF
chmod 600 ~/.pypirc
```

Upload to **TestPyPI first** to catch metadata issues without burning a version number:

```bash
uv publish --publish-url https://test.pypi.org/legacy/ --token pypi-<testpypi-token> dist/*
# Verify at https://test.pypi.org/project/obd-mcp/
# Install and smoke-test:
uv pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ obd-mcp
```

Then real PyPI:

```bash
uv publish dist/*
# Verify at https://pypi.org/project/obd-mcp/
```

## 4. Smithery

<https://smithery.ai/> indexes MCP servers. Once PyPI is live:

1. Fork/clone <https://github.com/smithery-ai/mcp-servers> (check the
   current path — the registry repo has moved in the past).
2. Add a JSON manifest under the appropriate directory. Template:
   ```json
   {
     "name": "obd-mcp",
     "description": "Bridge an MCP host to a live OBD-II port via an ELM327 adapter. VIN enrichment, DTC decoding, recording, NHTSA recalls.",
     "homepage": "https://github.com/mbohaychuk/OBD-II-MCP-Server",
     "installation": {
       "type": "pip",
       "package": "obd-mcp"
     },
     "config": {
       "env": {
         "OBD_PORT": "pyserial URL — e.g. socket://192.168.0.10:35000",
         "SIDEKICK_URL": "optional — Mechanics Sidekick RAG base URL"
       }
     },
     "tags": ["automotive", "diagnostics", "hardware"]
   }
   ```
3. Open a PR. The Smithery maintainers review manifests before merging.

## 5. mcp.so

<https://mcp.so/> is a second community index. The submission flow is
via the web form at <https://mcp.so/submit> (no PR needed). Fields:

- Name: `obd-mcp`
- Repo: `https://github.com/mbohaychuk/OBD-II-MCP-Server`
- Install command: `pip install obd-mcp` (post-PyPI)
- Description: 1-paragraph pitch; reuse the top of the README.
- Tags: `automotive`, `diagnostics`, `hardware`, `mcp`.

## 6. Announce

- [ ] GitHub release notes at the tag (paste the relevant DECISIONS.md entries for this version window).
- [ ] Update `docs/PLAN.md` status line to reflect the new release.
- [ ] Short post on whatever channel is relevant (personal blog / Mastodon / LinkedIn).

## Rollback

If a release is broken:

```bash
# PyPI does not allow re-uploading the same version. Bump the patch and
# re-release — do NOT `pip index yank` without a replacement, that breaks
# installs.
```
