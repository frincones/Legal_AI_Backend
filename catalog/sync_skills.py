"""Sync de skills: lee los SKILL.md de claude-for-legal y los carga a Supabase.

Corre LOCALMENTE (donde existe el repo claude-for-legal). Parsea frontmatter
(name/description/argument-hint/user-invocable) + cuerpo, y upserta en `skills`,
habilitándolos para una org. El runtime carga `skills.body_md` al system prompt.

Env:
  SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
  CLAUDE_FOR_LEGAL_PATH   (raíz del repo claude-for-legal)
  SYNC_PLUGINS            (coma-separado; default 'corporate-legal')
  SYNC_ORG_SLUG          (default 'pilot-firm')
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

SB = os.environ["SUPABASE_URL"].rstrip("/")
SR = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
REPO = Path(os.environ["CLAUDE_FOR_LEGAL_PATH"])
PLUGINS = os.environ.get("SYNC_PLUGINS", "corporate-legal").split(",")
ORG_SLUG = os.environ.get("SYNC_ORG_SLUG", "pilot-firm")


def _req(method: str, path: str, body=None, prefer: str | None = None):
    h = {"apikey": SR, "Authorization": f"Bearer {SR}", "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(f"{SB}/rest/v1/{path}", data=data, method=method, headers=h)
    resp = urllib.request.urlopen(r, timeout=30)
    raw = resp.read()
    return json.loads(raw) if raw else None


def parse_skill(md: str) -> dict:
    """Frontmatter + body sin pyyaml. Devuelve description/argument_hint/user_invocable/body."""
    out = {"description": "", "argument_hint": None, "user_invocable": True, "body": md}
    if not md.startswith("---"):
        return out
    end = md.find("\n---", 3)
    if end == -1:
        return out
    fm = md[3:end].strip("\n")
    out["body"] = md[end + 4 :].lstrip("\n")
    lines = fm.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("description:"):
            val = line[len("description:") :].strip()
            if val in (">", "|", ">-", "|-", ""):  # block scalar
                buf = []
                i += 1
                while i < len(lines) and (lines[i].startswith((" ", "\t")) or lines[i].strip() == ""):
                    buf.append(lines[i].strip())
                    i += 1
                out["description"] = " ".join(b for b in buf if b).strip()
                continue
            out["description"] = val.strip().strip('"')
        elif line.startswith("argument-hint:"):
            out["argument_hint"] = line.split(":", 1)[1].strip().strip('"') or None
        elif line.startswith("user-invocable:"):
            out["user_invocable"] = line.split(":", 1)[1].strip().lower() == "true"
        i += 1
    return out


def main():
    org = _req("GET", f"orgs?slug=eq.{ORG_SLUG}&select=id")
    org_id = org[0]["id"]
    rows = []
    for plugin in PLUGINS:
        plugin = plugin.strip()
        skills_dir = REPO / plugin / "skills"
        if not skills_dir.exists():
            print(f"skip {plugin}: no skills dir")
            continue
        for d in sorted(skills_dir.iterdir()):
            sk = d / "SKILL.md"
            if not sk.exists():
                continue
            parsed = parse_skill(sk.read_text(encoding="utf-8"))
            rows.append({
                "plugin_key": plugin, "key": d.name,
                "name": d.name, "description": parsed["description"][:2000],
                "argument_hint": parsed["argument_hint"],
                "user_invocable": parsed["user_invocable"],
                "body_md": parsed["body"],
            })
    print(f"upserting {len(rows)} skills...")
    upserted = _req("POST", "skills?on_conflict=plugin_key,key", rows,
                    prefer="resolution=merge-duplicates,return=representation")
    # enable for org
    org_rows = [{"org_id": org_id, "skill_id": s["id"], "enabled": True} for s in upserted]
    _req("POST", "org_skills?on_conflict=org_id,skill_id", org_rows,
         prefer="resolution=merge-duplicates,return=minimal")
    print(f"done: {len(upserted)} skills enabled for org {ORG_SLUG}")
    for s in upserted:
        print(" -", s["key"])


if __name__ == "__main__":
    main()
