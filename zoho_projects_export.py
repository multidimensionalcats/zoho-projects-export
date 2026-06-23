#!/usr/bin/env python3
"""
Zoho Projects task/timelog/document exporter.

Pulls tasks (with subtasks, comments, custom fields), time logs, and
WorkDrive-backed documents from a Zoho Projects portal via the REST API.
Auth is cookie-based — export the Cookie header from a browser session.

Requirements: httpx (pip install httpx)

Usage:
    python zoho_projects_export.py scrape \\
        --portal-id 12345 --project-id 67890 --cookies cookies.txt

    python zoho_projects_export.py timelogs \\
        --portal-id 12345 --project-id 67890 --cookies cookies.txt

    python zoho_projects_export.py documents \\
        --portal-id 12345 --project-id 67890 --cookies cookies.txt
"""
import argparse, json, sys, time
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit("httpx is required: pip install httpx")


# ---------------------------------------------------------------------------
# Session / auth
# ---------------------------------------------------------------------------

def load_cookies(cookie_path):
    raw = Path(cookie_path).read_text().strip()
    cookies = {}
    for pair in raw.split(";"):
        if "=" in pair:
            k, v = pair.strip().split("=", 1)
            cookies[k.strip()] = v.strip()
    if "CSRF_TOKEN" not in cookies:
        sys.exit(
            "CSRF_TOKEN not found in cookie file. "
            "Re-export the Cookie header from a logged-in Zoho Projects session."
        )
    return cookies


def make_client(cookies, portal_slug, base_url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0 Safari/537.36"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Referer": f"{base_url}/portal/{portal_slug}",
        "X-Requested-With": "XMLHttpRequest",
        "X-ZCSRF-TOKEN": f"zpcp={cookies['CSRF_TOKEN']}",
    }
    return httpx.Client(
        cookies=cookies, headers=headers, timeout=30, follow_redirects=False
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def api_path(base_url, portal_id, project_id, *parts):
    return (
        f"{base_url}/restapi/portal/{portal_id}"
        f"/projects/{project_id}/" + "/".join(parts)
    )


def safe_json(client, url):
    r = client.get(url)
    if r.status_code == 204 or not r.content:
        return {}
    if r.status_code in (401, 403):
        sys.exit(
            f"Auth failed ({r.status_code}) fetching {url.split('?')[0]} — "
            "session cookie may have expired. Re-export from browser."
        )
    if r.status_code >= 400:
        print(f"  [warn] HTTP {r.status_code} on {url.split('/')[-2:]}: {r.text[:120]}")
    try:
        return r.json()
    except Exception as e:
        return {"_error": str(e), "_status": r.status_code, "_body": r.text[:500]}


def safe_filename(name):
    name = name.replace("/", "_").replace("\\", "_").strip()
    if name in (".", ".."):
        name = "_"
    return name or "untitled"


def count_nodes(node):
    return 1 + sum(count_nodes(ch) for ch in node["children"])


# ---------------------------------------------------------------------------
# Scrape tasks
# ---------------------------------------------------------------------------

def scrape(client, base_url, portal_id, project_id, out_dir):
    out = Path(out_dir)
    raw_dir = out / "raw"
    out.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(exist_ok=True)

    def _api(*parts):
        return api_path(base_url, portal_id, project_id, *parts)

    def fetch_task(t, depth=0):
        tid = t.get("id_string") or str(t["id"])
        detail = safe_json(client, _api("tasks", tid, ""))
        comments = safe_json(client, _api("tasks", tid, "comments/"))
        node = {
            "id": tid, "depth": depth, "list_entry": t,
            "detail": detail, "comments": comments, "children": [],
        }
        sub = safe_json(client, _api("tasks", tid, "subtasks/"))
        for st in sub.get("tasks") or sub.get("subtasks") or []:
            node["children"].append(fetch_task(st, depth + 1))
        pad = "  " * depth
        print(f"  {pad}[d{depth}] {tid}  {t.get('name', '')[:60]}")
        time.sleep(0.15)
        return node

    tasks = []
    index = 1
    while True:
        r = client.get(_api("tasks/") + f"?range=200&index={index}")
        r.raise_for_status()
        (raw_dir / f"tasks_list_{int(time.time())}_{index}.json").write_text(r.text)
        batch = r.json().get("tasks", [])
        tasks.extend(batch)
        if len(batch) < 200:
            break
        index += len(batch)
        time.sleep(0.15)

    print(f"[+] {len(tasks)} top-level tasks (paginated)")
    tree = [fetch_task(t) for t in tasks]
    total = sum(count_nodes(n) for n in tree)
    (out / "tasks.json").write_text(json.dumps(tree, indent=2))
    write_tasks_markdown(tree, out)
    print(f"[+] Wrote tasks.json ({total} tasks) and tasks.md")


def write_tasks_markdown(tree, out_dir):
    total = sum(count_nodes(n) for n in tree)
    lines = [f"# Zoho Projects — {total} tasks ({len(tree)} top-level)", ""]

    def emit(node):
        t = node["list_entry"]
        depth = node["depth"]
        heading = "#" * min(depth + 2, 6)
        details_list = (node["detail"] or {}).get("tasks", [])
        d = details_list[0] if details_list else {}
        det = d.get("details", {}) or {}
        owners = ", ".join(o.get("name", "?") for o in (det.get("owners") or []))
        status = (d.get("status") or {}).get("name") or t.get("status", "?")
        tl = (d.get("tasklist") or {}).get("name", "?")
        lines.append(f"{heading} {t.get('name', '(no name)')}")
        lines.append(
            f"- id: `{node['id']}` | depth: {depth} | status: {status}"
            f" | tasklist: {tl} | owners: {owners}"
        )
        due = det.get("end_date") or d.get("end_date")
        if due:
            lines.append(f"- due: {due}")
        cfs = [
            (c.get("label_name") or c.get("column_name"), str(c.get("value", "")).strip())
            for c in (d.get("custom_fields") or [])
        ]
        cfs = [(k, v) for k, v in cfs if v and v.lower() != "false"]
        if cfs:
            lines.append("- fields: " + " | ".join(f"{k}: {v}" for k, v in cfs))
        desc = (d.get("description") or t.get("description") or "").strip()
        if desc:
            lines.extend(["", desc, ""])
        comments = (node["comments"] or {}).get("comments", []) or []
        if comments:
            lines.append(f"**{len(comments)} comment(s):**")
            for cm in comments:
                who = cm.get("added_by", "?")
                when = cm.get("created_time", "")
                body = (cm.get("content", "") or "").replace("\n", " ")[:400]
                lines.append(f"- _{who} {when}_: {body}")
        lines.append("")
        for ch in node["children"]:
            emit(ch)

    for n in tree:
        emit(n)
    Path(out_dir, "tasks.md").write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Timelogs
# ---------------------------------------------------------------------------

def fetch_timelogs(client, base_url, portal_id, project_id, out_dir, start, end):
    import urllib.parse

    out = Path(out_dir)
    raw_dir = out / "raw"
    out.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(exist_ok=True)

    def _api(*parts):
        return api_path(base_url, portal_id, project_id, *parts)

    end_date = end or time.strftime("%m-%d-%Y")
    start_date = start or time.strftime(
        "%m-%d-%Y", time.localtime(time.time() - 150 * 86400)
    )

    def fetch_page(component, index=1):
        custom = json.dumps({"start_date": start_date, "end_date": end_date})
        params = urllib.parse.urlencode({
            "users_list": "all",
            "view_type": "custom_date",
            "date": end_date,
            "custom_date": custom,
            "bill_status": "All",
            "component_type": component,
            "index": index,
            "range": 200,
        })
        url = _api("logs/") + "?" + params
        r = client.get(url)
        if r.status_code != 200:
            print(f"  [{component}] status {r.status_code}: {r.text[:200]}")
            return []
        return r.json().get("timelogs", {}).get("date", [])

    all_entries = []
    page_size = 200
    for component in ["task", "bug", "general"]:
        log_key = {"task": "tasklogs", "bug": "buglogs", "general": "generallogs"}[component]
        index = 1
        count = 0
        while True:
            date_groups = fetch_page(component, index)
            if not date_groups:
                break
            page_entries = 0
            for dg in date_groups:
                for entry in dg.get(log_key, []):
                    task_info = entry.get("task", entry.get("bug", {}))
                    all_entries.append({
                        "log_date": entry.get("log_date", dg.get("date", "?")),
                        "created_date": entry.get("created_date", "?"),
                        "owner": entry.get("owner_name", "?"),
                        "task": task_info.get(
                            "name", task_info.get("title", entry.get("name", "(general)"))
                        ),
                        "hours": int(entry.get("hours", 0)),
                        "mins": int(entry.get("minutes", 0)),
                        "total_minutes": int(entry.get("total_minutes", 0)),
                        "notes": (entry.get("notes", "") or "").strip(),
                        "bill_status": entry.get("bill_status", "?"),
                        "approval_status": entry.get("approval_status", "?"),
                        "start_time": entry.get("start_time", ""),
                        "end_time": entry.get("end_time", ""),
                    })
                    page_entries += 1
            count += page_entries
            if page_entries < page_size:
                break
            index += page_size
            time.sleep(0.15)
        print(f"  [{component}] {count} entries")

    (raw_dir / f"timelogs_{int(time.time())}.json").write_text(
        json.dumps(all_entries, indent=2)
    )
    (out / "timelogs.json").write_text(json.dumps(all_entries, indent=2))
    write_timelogs_markdown(all_entries, out)
    print(f"[+] {len(all_entries)} total entries -> timelogs.json + timelogs.md")


def write_timelogs_markdown(entries, out_dir):
    lines = ["# Time Logs", ""]
    total_mins = sum(e["total_minutes"] for e in entries)
    by_user = {}
    by_task = {}
    for e in entries:
        by_user[e["owner"]] = by_user.get(e["owner"], 0) + e["total_minutes"]
        task_key = e["task"][:80]
        by_task[task_key] = by_task.get(task_key, 0) + e["total_minutes"]

    h, m = divmod(total_mins, 60)
    lines.append(f"**Total: {h}h {m}m ({len(entries)} entries)**")
    lines.append("")
    lines.append("## By User")
    for user, mins in sorted(by_user.items(), key=lambda x: -x[1]):
        uh, um = divmod(mins, 60)
        lines.append(f"- {user}: {uh}h {um}m")
    lines.append("")
    lines.append("## By Task (top 25)")
    for task, mins in sorted(by_task.items(), key=lambda x: -x[1])[:25]:
        th, tm = divmod(mins, 60)
        lines.append(f"- {task}: {th}h {tm}m")
    lines.append("")
    lines.append("## All Entries")
    lines.append("")
    lines.append("| Log Date | Owner | Time | Task | Notes | Billable | Approved |")
    lines.append("|----------|-------|------|------|-------|----------|----------|")
    for e in sorted(entries, key=lambda x: x["log_date"]):
        notes = e["notes"][:60].replace("|", "/").replace("\n", " ") if e["notes"] else ""
        task = e["task"][:50].replace("|", "/")
        lines.append(
            f"| {e['log_date']} | {e['owner']} | {e['hours']}h {e['mins']}m"
            f" | {task} | {notes} | {e['bill_status']} | {e['approval_status']} |"
        )
    lines.append("")
    Path(out_dir, "timelogs.md").write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Documents (WorkDrive-backed)
# ---------------------------------------------------------------------------

WD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/136.0 Safari/537.36"
    ),
    "Accept": "application/vnd.api+json",
    "Referer": "https://workdrive.zoho.com/",
}


def fetch_documents(client, base_url, portal_id, project_id, out_dir, cookies):
    out = Path(out_dir)
    docs_out = out / "documents"
    docs_out.mkdir(parents=True, exist_ok=True)
    manifest = []

    v3 = f"{base_url}/api/v3/portal/{portal_id}/projects/{project_id}/documents"
    r = client.get(v3)
    r.raise_for_status()
    folders = r.json().get("documents", {}).get("folders", [])
    print(f"[+] {len(folders)} top-level folder(s)")

    with httpx.Client(
        cookies=cookies, headers=WD_HEADERS, timeout=120, follow_redirects=True
    ) as wd:

        def list_children(wd_id):
            items, off = [], 0
            while True:
                rr = wd.get(
                    f"https://workdrive.zoho.com/api/v1/files/{wd_id}/files",
                    params={"page[limit]": 50, "page[offset]": off},
                )
                if rr.status_code != 200:
                    print(f"    ! list failed {rr.status_code} for {wd_id}: {rr.text[:120]}")
                    break
                data = rr.json().get("data", [])
                items += data
                if len(data) < 50:
                    break
                off += 50
                time.sleep(0.1)
            return items

        def walk(wd_id, relpath):
            for it in list_children(wd_id):
                a = it.get("attributes", {})
                name = a.get("name", it.get("id"))
                cid = it.get("id")
                if a.get("is_folder"):
                    walk(cid, relpath + [safe_filename(name)])
                    continue
                extn = a.get("extn", "") or ""
                si = a.get("storage_info", {}) or {}
                size = si.get("size_in_bytes") or si.get("size") or 0
                destdir = docs_out.joinpath(*relpath)
                destdir.mkdir(parents=True, exist_ok=True)
                fname = safe_filename(name)
                if extn and not fname.lower().endswith("." + extn.lower()):
                    fname = f"{fname}.{extn}"
                dest = destdir / fname
                if not dest.resolve().is_relative_to(docs_out.resolve()):
                    print(f"    [skip] {name} — path escapes output directory")
                    continue
                rec = {
                    "id": cid, "name": name, "extn": extn, "size": size,
                    "path": str(dest.relative_to(docs_out)),
                    "modified": a.get("modified_time_i18") or a.get("modified_time", ""),
                }
                try:
                    dl = wd.get(f"https://workdrive.zoho.com/api/v1/download/{cid}")
                    ctype = dl.headers.get("content-type", "")
                    if (
                        dl.status_code == 200
                        and dl.content
                        and "vnd.api+json" not in ctype
                    ):
                        dest.write_bytes(dl.content)
                        rec["status"] = "ok"
                        rec["bytes"] = len(dl.content)
                        print(f"    [ok]   {'/'.join(relpath)}/{fname}  ({len(dl.content)} B)")
                    else:
                        rec["status"] = f"skip ({dl.status_code}, {ctype[:30]})"
                        print(f"    [skip] {'/'.join(relpath)}/{name}  -> {rec['status']}")
                except Exception as e:
                    rec["status"] = f"error: {e}"
                    print(f"    [err]  {name}: {e}")
                manifest.append(rec)
                time.sleep(0.15)

        for f in folders:
            fname = f.get("name", "folder")
            wd_id = f.get("workdrive_folder_id")
            if not wd_id:
                print(f"  -> folder: {fname} (no WorkDrive id, skipping)")
                continue
            print(f"  -> folder: {fname}")
            walk(wd_id, [safe_filename(fname)])
    (docs_out / "_manifest.json").write_text(json.dumps(manifest, indent=2))
    write_documents_markdown(manifest, docs_out)
    ok = sum(1 for m in manifest if m.get("status") == "ok")
    print(f"[+] {ok}/{len(manifest)} files downloaded to {docs_out}")


def write_documents_markdown(manifest, docs_out):
    from collections import defaultdict

    ok_count = sum(1 for m in manifest if m.get("status") == "ok")
    lines = [f"# Project Documents ({ok_count} files)", ""]
    by_dir = defaultdict(list)
    for m in manifest:
        d = str(Path(m["path"]).parent)
        by_dir[d].append(m)
    for d in sorted(by_dir):
        lines.append(f"## {d}")
        lines.append("")
        lines.append("| File | Type | Size | Modified | Status |")
        lines.append("|------|------|------|----------|--------|")
        for m in sorted(by_dir[d], key=lambda x: x["name"].lower()):
            sz = m.get("bytes") or m.get("size") or ""
            if isinstance(sz, int):
                sz = f"{sz / 1024:.1f} KB" if sz < 1024 * 1024 else f"{sz / 1024 / 1024:.2f} MB"
            nm = m["name"].replace("|", "/")
            lines.append(
                f"| {nm} | {m.get('extn', '')} | {sz}"
                f" | {m.get('modified', '')} | {m.get('status', '')} |"
            )
        lines.append("")
    Path(docs_out, "documents.md").write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "command", choices=["scrape", "timelogs", "documents"],
        help="What to export",
    )
    ap.add_argument("--portal-id", required=True, help="Zoho portal numeric ID")
    ap.add_argument("--project-id", required=True, help="Zoho project ID string")
    ap.add_argument(
        "--cookies", required=True,
        help="Path to cookie file (raw Cookie header from browser)",
    )
    ap.add_argument("--out", default=".", help="Output directory (default: current)")
    ap.add_argument(
        "--portal-slug", required=True,
        help="Portal URL slug (the name in projects.zoho.com/portal/<slug>)",
    )
    ap.add_argument(
        "--base-url", default="https://projects.zoho.com",
        help="Zoho Projects base URL (default: https://projects.zoho.com)",
    )
    ap.add_argument("--start", help="Timelog start date (MM-DD-YYYY)")
    ap.add_argument("--end", help="Timelog end date (MM-DD-YYYY)")
    a = ap.parse_args()

    cookies = load_cookies(a.cookies)
    client = make_client(cookies, a.portal_slug, a.base_url)

    try:
        if a.command == "scrape":
            scrape(client, a.base_url, a.portal_id, a.project_id, a.out)
        elif a.command == "timelogs":
            fetch_timelogs(
                client, a.base_url, a.portal_id, a.project_id,
                a.out, a.start, a.end,
            )
        elif a.command == "documents":
            fetch_documents(
                client, a.base_url, a.portal_id, a.project_id, a.out, cookies,
            )
    finally:
        client.close()


if __name__ == "__main__":
    main()
