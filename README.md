# Zoho Projects Export

Export task data, time logs, and documents from a Zoho Projects portal
into JSON and Markdown files that can be fed to an LLM for analysis.

## Requirements

- Python 3.9+
- [httpx](https://www.python-httpx.org/) (`pip install httpx`)

## Authentication

This tool uses cookie-based auth — no OAuth setup required.

1. Log into [Zoho Projects](https://projects.zoho.com) in your browser.
2. Open DevTools → Network tab → pick any XHR request → copy the `Cookie` header value.
3. Paste it into a text file (e.g. `cookies.txt`).

The cookie string must contain `CSRF_TOKEN`. It typically expires after ~14 days,
at which point you'll need to re-export.

## Finding your IDs

- **Portal ID:** Open any project → the URL is `projects.zoho.com/portal/<slug>/#...`.
  The numeric portal ID is in the API: visit
  `https://projects.zoho.com/restapi/portals/` (with cookies) to see it.
- **Project ID:** Open the project → the URL contains `...projects/<project_id>/...`.
  Or use the portals API to list projects.

## Usage

```bash
# Pull all tasks (with subtasks, comments, custom fields)
python zoho_projects_export.py scrape \
    --portal-id YOUR_PORTAL_ID \
    --project-id YOUR_PROJECT_ID \
    --portal-slug YOUR_PORTAL_SLUG \
    --cookies cookies.txt \
    --out ./export

# Pull time logs (defaults to last 150 days)
python zoho_projects_export.py timelogs \
    --portal-id YOUR_PORTAL_ID \
    --project-id YOUR_PROJECT_ID \
    --portal-slug YOUR_PORTAL_SLUG \
    --cookies cookies.txt \
    --out ./export

# Pull time logs for a specific date range
python zoho_projects_export.py timelogs \
    --portal-id YOUR_PORTAL_ID \
    --project-id YOUR_PROJECT_ID \
    --portal-slug YOUR_PORTAL_SLUG \
    --cookies cookies.txt \
    --out ./export \
    --start 01-01-2026 --end 06-01-2026

# Pull WorkDrive-backed project documents
python zoho_projects_export.py documents \
    --portal-id YOUR_PORTAL_ID \
    --project-id YOUR_PROJECT_ID \
    --portal-slug YOUR_PORTAL_SLUG \
    --cookies cookies.txt \
    --out ./export
```

## Options

| Flag | Required | Description |
|------|----------|-------------|
| `command` | yes | `scrape`, `timelogs`, or `documents` |
| `--portal-id` | yes | Numeric Zoho portal ID |
| `--project-id` | yes | Zoho project ID string |
| `--cookies` | yes | Path to cookie file |
| `--out` | no | Output directory (default: `.`) |
| `--portal-slug` | yes | Portal URL slug (from `projects.zoho.com/portal/<slug>`) |
| `--base-url` | no | Zoho Projects base URL (default: `https://projects.zoho.com`) |
| `--start` | no | Timelog start date, MM-DD-YYYY |
| `--end` | no | Timelog end date, MM-DD-YYYY |

## Output

### `scrape`
- `tasks.json` — full task tree (subtasks, details, comments, custom fields)
- `tasks.md` — Markdown rendering with headings per task
- `raw/tasks_list_*.json` — raw API response snapshots

### `timelogs`
- `timelogs.json` — all time log entries
- `timelogs.md` — summary tables (by user, by task) + full entry table
- `raw/timelogs_*.json` — raw snapshots

### `documents`
- `documents/` — downloaded files in their folder structure
- `documents/_manifest.json` — download status for each file
- `documents/documents.md` — index table

## Notes

- Zoho caps task list responses at 200 per page. This script paginates
  automatically — all tasks are fetched regardless of project size.
- Time log date ranges are capped by Zoho at ~6 months. For older data,
  pull in chunks using `--start` / `--end`.
- Some project folders may not have a WorkDrive backing (native Zoho
  folders). These are skipped with a message.
